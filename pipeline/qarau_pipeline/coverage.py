"""coverage step (COV1, ADR 0002): which regions of the map were inspected, have a finding, or
were not seen. Plan: docs/notes/cov1-plan.md; format: docs/architecture.md.

The bundle's points are split into voxel regions (bundle units). A frame sees a point when the
point is inside its frustum (K, R, t of the bundle's own poses: export already applied the
gravity alignment, so poses and points share one frame) and, on a real run, the frame tracked
the point (COLMAP matched it there, so no wall stands in between). A frame counts for a region
when it is usable (registered, kept, in no bad quality-report issue, score >= min_frame_quality)
and sees one of the region's points. State: `finding` when a finding's point is in the region,
else `inspected_ok` with >= min_frames frames and >= min_points points, else `not_seen`. Every
threshold comes from config.yaml `coverage:`. Writes `<run>/coverage/regions.json`. NumPy only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from . import layout
from .findings import load_bundle
from .interfaces import StageError

VERSION = 1
NUMBERS = ("voxel_size", "min_frames", "min_frame_quality", "min_points", "near")


class View(NamedTuple):
    """A registered frame: x_cam = R x + t (bundle world), pinhole K, image size in pixels."""

    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    width: int
    height: int


def points_in_frustum(
    points: Any,
    K: Any,
    R: Any,
    t: Any,
    width: int,
    height: int,
    near: float,
    far: float | None,
) -> np.ndarray:
    """Bool per point: near < depth < far (far None = no limit) and the projection with K inside
    [0, width) x [0, height). Radial distortion is left out: mild for phone and drone video."""
    xc = np.asarray(points, float).reshape(-1, 3) @ np.asarray(R, float).T + np.asarray(t, float)
    z = xc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        uvw = xc @ np.asarray(K, float).T
        u, v = uvw[:, 0] / z, uvw[:, 1] / z
    depth_ok = (z > near) & (z < (np.inf if far is None else far))
    return np.asarray(depth_ok & (u >= 0) & (u < width) & (v >= 0) & (v < height))


def voxelize(points: Any, voxel_size: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(region per point, integer voxel key per region R x 3, bounds per region R x 2 x 3: the
    cell [key * size, (key + 1) * size]). Regions are the occupied cells, keys ascending."""
    cells = np.floor(np.asarray(points, float).reshape(-1, 3) / voxel_size).astype(np.int64)
    keys, region = np.unique(cells, axis=0, return_inverse=True)
    return region.reshape(-1), keys, np.stack([keys * voxel_size, (keys + 1) * voxel_size], 1)


def settings(config: dict[str, Any]) -> dict[str, Any]:
    """The `coverage:` block of config.yaml, checked; no defaults in code."""
    s = config.get("coverage")
    if not isinstance(s, dict):
        raise StageError("config.yaml has no `coverage:` section: coverage cannot run")
    for key in (*NUMBERS, "far", "unusable_issues"):
        if key not in s:
            raise StageError(f"config.yaml `coverage:` misses {key}: coverage cannot run")
    for key in NUMBERS:
        if isinstance(s[key], bool) or not isinstance(s[key], int | float):
            raise StageError(f"coverage.{key} must be a number, got {s[key]!r}")
    if s["voxel_size"] <= 0 or s["min_frames"] < 1 or s["min_points"] < 1 or s["near"] < 0:
        raise StageError("coverage: voxel_size must be > 0, min_frames and min_points >= 1, "
                         "near >= 0")  # fmt: skip
    far = s["far"]
    if far is not None and (isinstance(far, bool) or not isinstance(far, int | float) or far <= 0):
        raise StageError(f"coverage.far must be empty or a positive number, got {far!r}")
    return dict(s)


def classify_regions(
    points: Any,
    views: dict[int, View],
    quality: Any,
    point_finding: Any,
    s: dict[str, Any],
    tracks: tuple[np.ndarray, np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    """One dict per region: id "i_j_k", bounds {min, max}, state, frame_count, point_count,
    finding_ids. `quality[f]`: the frame's quality score (NaN = no score: an image set; -inf =
    unusable); a frame counts only with a score >= s["min_frame_quality"] or no score.
    `point_finding[i]`: finding id of point i, 0 = none. `tracks` (offsets, frame ids; CSR per
    point): when given, a frame sees only the points it tracked."""
    pts = np.asarray(points, float).reshape(-1, 3)
    region, keys, bounds = voxelize(pts, float(s["voxel_size"]))
    n_reg = len(keys)
    q = np.asarray(quality, float)
    if tracks is not None:
        track_pt = np.repeat(np.arange(len(pts)), np.diff(tracks[0]))
    frame_count = np.zeros(n_reg, np.int64)
    for f, v in views.items():
        if not (np.isnan(q[f]) or q[f] >= s["min_frame_quality"]):
            continue
        idx = track_pt[tracks[1] == f] if tracks is not None else np.arange(len(pts))
        sees = points_in_frustum(pts[idx], v.K, v.R, v.t, v.width, v.height, s["near"], s["far"])
        frame_count[np.unique(region[idx[sees]])] += 1
    point_count = np.bincount(region, minlength=n_reg)
    lab = np.asarray(point_finding, np.int64)
    finding_ids: list[list[int]] = [[] for _ in range(n_reg)]
    for r, fid in np.unique(np.stack([region[lab > 0], lab[lab > 0]], 1), axis=0):
        finding_ids[r].append(int(fid))
    ok = (frame_count >= s["min_frames"]) & (point_count >= s["min_points"])
    return [
        {"id": "_".join(str(int(x)) for x in keys[r]),
         "bounds": {"min": [round(float(x), 6) for x in bounds[r, 0]],
                    "max": [round(float(x), 6) for x in bounds[r, 1]]},
         "state": "finding" if finding_ids[r] else "inspected_ok" if ok[r] else "not_seen",
         "frame_count": int(frame_count[r]), "point_count": int(point_count[r]),
         "finding_ids": finding_ids[r]}
        for r in range(n_reg)
    ]  # fmt: skip


# ---------------------------------------------------------------- the step


def frame_quality(run: Path, frames: list[dict[str, Any]], bad_issues: list[str]) -> np.ndarray:
    """Quality score per frame: the frame check's blur score (Laplacian variance), NaN when the
    run has none (an image set); -inf for a frame that is not kept (a quality-drop frame COLMAP
    used as a bridge) or is in a quality-report issue of a bad kind (blur, too fast, ...)."""
    q = np.array([np.nan if f.get("blur") is None else float(f["blur"]) for f in frames])
    q[[not f.get("kept", True) for f in frames]] = -np.inf
    rep = layout.quality_report(run)
    if rep.is_file():
        for issue in json.loads(rep.read_text()).get("issues", []):
            if issue["kind"] in bad_issues:
                q[[i for i in issue["frames"] if 0 <= i < len(q)]] = -np.inf
    return q


def finding_labels(run: Path, n: int) -> np.ndarray:
    """Finding id per bundle point from the project stage (findings/points.finding.u16)."""
    p = run / "findings" / "points.finding.u16"
    if not p.is_file():
        return np.zeros(n, np.int64)
    lab = np.fromfile(p, "<u2").astype(np.int64)
    if len(lab) != n:
        raise StageError(
            f"findings/points.finding.u16 has {len(lab)} points, the map has {n}: the findings "
            "are from an older export; run the project step again"
        )
    return lab


def run_coverage(
    run_dir: Path,
    config: dict[str, Any],
    out_dir: Path | None = None,
    finding_points: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    """Coverage of the run's bundle -> <out_dir or run>/coverage/regions.json; returns the state
    counts and the region count. `finding_points` (finding id -> bundle point indices: manual
    findings of the API, app/seed.py) replaces the project step's findings/points.finding.u16."""
    s = settings(config)
    mpath = layout.manifest_path(run_dir)
    if not mpath.is_file():
        raise StageError("coverage needs the map: no viewer bundle (the export step has not run)")
    frames = json.loads(mpath.read_text())["frames"]
    b = load_bundle(run_dir)
    views = {}
    for f, cam in b.cameras.items():
        fx, fy, cx, cy = frames[f]["camera"]["K"]  # export's pinhole intrinsics
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], float)
        views[f] = View(K, cam.R, cam.t, cam.width, cam.height)
    if finding_points is None:
        labels = finding_labels(run_dir, len(b.positions))
    else:
        labels = np.zeros(len(b.positions), np.int64)
        for fid, idx in finding_points.items():
            labels[np.asarray(idx, np.int64)] = fid
    regions = classify_regions(
        b.positions, views, frame_quality(run_dir, frames, s["unusable_issues"]),
        labels, s, (b.offsets, b.frame_ids),
    )  # fmt: skip
    summary = {st: sum(r["state"] == st for r in regions)
               for st in ("inspected_ok", "finding", "not_seen")}  # fmt: skip
    doc = {"version": VERSION, "units": "bundle", "voxel_size": s["voxel_size"],
           "thresholds": {k: s[k] for k in ("min_frames", "min_frame_quality", "min_points",
                                            "near", "far", "unusable_issues")},
           "summary": summary, "regions": regions}  # fmt: skip
    out = (out_dir or run_dir) / "coverage"
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / ".regions.json.tmp"
    tmp.write_text(json.dumps(doc, indent=1))
    os.replace(tmp, out / "regions.json")
    return {"regions": len(regions), **summary}
