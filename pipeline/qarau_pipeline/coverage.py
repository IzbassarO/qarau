"""coverage step (COV1, ADR 0002): which regions of the map were inspected, have a finding, or
were not seen. Plan and rules: docs/notes/cov1-plan.md.

The bundle's points are split into voxel regions (bundle units). A frame counts for a region when
it is usable (registered, kept, in no bad quality-report issue), the region centroid is inside its
frustum and it observed one of the region's points (COLMAP tracks: no wall in between). State:
`finding` when a finding's point is in the region, else `inspected_ok` with enough frames and
points, else `not_seen`. Writes `<run>/coverage/regions.json` and the per-point array
`points.coverage.u8` of the bundle (ADR 0003). NumPy only.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from . import layout
from .findings import load_bundle
from .interfaces import StageError

STATES = ("not_seen", "inspected_ok", "finding")  # index = code in points.coverage.u8
SCHEMA_VERSION = 1
N_NEAREST = 3
DEFAULTS: dict[str, Any] = {  # config.yaml `coverage:` overrides each key
    "voxel_size": 1.0,
    "min_frames": 3,
    "min_points_per_region": 20,
    "max_view_distance": None,
    "unusable_issues": ["blur", "too_fast", "low_texture"],
}


class View(NamedTuple):
    """A registered frame: x_cam = R x + t (COLMAP world-to-camera), pinhole K, image size."""

    R: np.ndarray
    t: np.ndarray
    K: np.ndarray
    width: int
    height: int


def k_matrix(model: str, params: Sequence[float]) -> np.ndarray:
    """3x3 intrinsics of a COLMAP camera; the radial terms are left out (see cov1-plan.md)."""
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        fx = fy = params[0]
        cx, cy = params[1], params[2]
    elif model == "PINHOLE":
        fx, fy, cx, cy = params[:4]
    else:
        raise StageError(f"coverage: unsupported camera model {model}")
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], float)


def point_in_frustum(
    points: np.ndarray,
    R: np.ndarray,
    t: np.ndarray,
    K: np.ndarray,
    width: int,
    height: int,
    near: float = 1e-9,
    far: float = np.inf,
) -> np.ndarray:
    """Bool per point: depth in (near, far] in front of the camera and the projection with K
    inside [0, width) x [0, height)."""
    xc = np.asarray(points, float).reshape(-1, 3) @ np.asarray(R, float).T + np.asarray(t, float)
    z = xc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        uvw = xc @ np.asarray(K, float).T
        u, v = uvw[:, 0] / z, uvw[:, 1] / z
    return np.asarray((z > near) & (z <= far) & (u >= 0) & (u < width) & (v >= 0) & (v < height))


def voxel_regions(
    points: np.ndarray, voxel_size: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(region per point, integer voxel key per region (R x 3), bounds per region (R x 2 x 3):
    the cell [key * size, (key + 1) * size]). Regions are the occupied cells, keys ascending."""
    cells = np.floor(np.asarray(points, float).reshape(-1, 3) / voxel_size).astype(np.int64)
    keys, region = np.unique(cells, axis=0, return_inverse=True)
    return region.reshape(-1), keys, np.stack([keys * voxel_size, (keys + 1) * voxel_size], 1)


def classify_regions(
    centroids: np.ndarray,
    n_points: np.ndarray,
    finding: np.ndarray,
    views: Sequence[View | None],
    usable: np.ndarray,
    min_frames: int,
    min_points: int,
    far: float | None = None,
    observed: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(state code per region, usable frames per region, seen R x F). `seen[r, f]`: the centroid
    of region r is in frame f's frustum (and f observed one of its points, when `observed` R x F
    is given). A frame counts only when seen and usable; None in `views` = not registered."""
    c = np.asarray(centroids, float).reshape(-1, 3)
    seen = np.zeros((len(c), len(views)), bool)
    for f, v in enumerate(views):
        if v is not None:
            seen[:, f] = point_in_frustum(c, v.R, v.t, v.K, v.width, v.height,
                                          far=np.inf if far is None else far)  # fmt: skip
    if observed is not None:
        seen &= observed
    frame_count = (seen & np.asarray(usable, bool)[None]).sum(1)
    ok = (frame_count >= min_frames) & (np.asarray(n_points) >= min_points)
    codes = np.where(np.asarray(finding, bool), 2, np.where(ok, 1, 0)).astype(np.uint8)
    return codes, frame_count, seen


def nearest_frames(
    centroids: np.ndarray, seen: np.ndarray, centers: np.ndarray, k: int = N_NEAREST
) -> list[list[int]]:
    """Per region up to k frames, nearest camera centre first: among the frames that see it, or
    every registered frame (finite centre) when none does."""
    c = np.asarray(centroids, float).reshape(-1, 3)
    reg = np.isfinite(centers).all(1)
    cc = np.where(reg[:, None], centers, 0.0)
    d2 = (c * c).sum(1)[:, None] + (cc * cc).sum(1)[None] - 2 * c @ cc.T
    d2[:, ~reg] = np.inf
    pick = np.where(seen.any(1, keepdims=True), np.where(seen, d2, np.inf), d2)
    order = np.argsort(pick, axis=1, kind="stable")[:, :k]
    return [[int(f) for f in row if np.isfinite(pick[r, f])] for r, row in enumerate(order)]


# ---------------------------------------------------------------- the step


def settings(config: dict[str, Any]) -> dict[str, Any]:
    """The `coverage:` block of config.yaml over DEFAULTS, checked."""
    s = {**DEFAULTS, **(config.get("coverage") or {})}
    size, far = s["voxel_size"], s["max_view_distance"]
    if isinstance(size, bool) or not isinstance(size, int | float) or size <= 0:
        raise StageError(f"coverage.voxel_size must be a positive number, got {size!r}")
    for key in ("min_frames", "min_points_per_region"):
        if isinstance(s[key], bool) or not isinstance(s[key], int) or s[key] < 1:
            raise StageError(f"coverage.{key} must be an integer >= 1, got {s[key]!r}")
    if far is not None and (isinstance(far, bool) or not isinstance(far, int | float) or far <= 0):
        raise StageError(f"coverage.max_view_distance must be empty or positive, got {far!r}")
    return s


def usable_frames(
    run: Path, man: dict[str, Any], bad_issues: Sequence[str]
) -> tuple[np.ndarray, bool]:
    """(usable per frame, whether a quality report was there). Usable: registered, kept (a
    quality-drop frame that COLMAP registered as a bridge is not) and in no issue of a bad kind.
    An image-set run has no quality report: its registered kept frames count, marked unchecked."""
    usable = np.array([bool(f.get("registered")) and bool(f.get("kept")) for f in man["frames"]])
    rep = layout.quality_report(run)
    if not rep.is_file():
        return usable, False
    for issue in json.loads(rep.read_text()).get("issues", []):
        if issue["kind"] in bad_issues:
            idx = [i for i in issue["frames"] if 0 <= i < len(usable)]
            usable[idx] = False
    return usable, True


def finding_points(run: Path, n: int) -> np.ndarray:
    """Bool per bundle point: labelled by the project stage (findings/points.finding.u16)."""
    p = run / "findings" / "points.finding.u16"
    if not p.is_file():
        return np.zeros(n, bool)
    lab = np.fromfile(p, "<u2")
    if len(lab) != n:
        raise StageError(
            f"findings/points.finding.u16 has {len(lab)} points, the map has {n}: the findings "
            "are from an older export; run the project step again"
        )
    return np.asarray(lab > 0)


def run_coverage(run: Path, config: dict[str, Any]) -> dict[str, Any]:
    """regions.json + points.coverage.u8 for the run's bundle; returns the metrics."""
    s = settings(config)
    mpath = layout.manifest_path(run)
    if not mpath.is_file():
        raise StageError("coverage needs the map: no viewer bundle (the export step has not run)")
    man = json.loads(mpath.read_text())
    b = load_bundle(run)
    n, n_frames = len(b.positions), len(b.images)
    usable, checked = usable_frames(run, man, s["unusable_issues"])

    region, keys, bounds = voxel_regions(b.positions, float(s["voxel_size"]))
    n_reg = len(keys)
    n_points = np.bincount(region, minlength=n_reg)
    centroids = np.stack([np.bincount(region, b.positions[:, a], n_reg) for a in range(3)], 1)
    centroids /= n_points[:, None]
    finding = np.bincount(region[finding_points(run, n)], minlength=n_reg) > 0
    observed = np.zeros((n_reg, n_frames), bool)
    observed[region[np.repeat(np.arange(n), np.diff(b.offsets))], b.frame_ids] = True
    views: list[View | None] = [None] * n_frames
    centers = np.full((n_frames, 3), np.nan)
    for f, cam in b.cameras.items():
        views[f] = View(cam.R, cam.t, k_matrix(cam.model, cam.params), cam.width, cam.height)
        centers[f] = cam.center
    codes, frame_count, seen = classify_regions(
        centroids, n_points, finding, views, usable, s["min_frames"], s["min_points_per_region"],
        s["max_view_distance"], observed,
    )  # fmt: skip
    near = nearest_frames(centroids, seen, centers)

    regions = [
        {"id": "_".join(str(int(x)) for x in keys[r]),
         "bounds": [[round(float(x), 6) for x in bounds[r, 0]],
                    [round(float(x), 6) for x in bounds[r, 1]]],
         "state": STATES[codes[r]], "frame_count": int(frame_count[r]),
         "point_count": int(n_points[r]),
         "nearest_frames": [Path(b.images[f]).name for f in near[r]]}
        for r in range(n_reg)
    ]  # fmt: skip
    totals = {st: {"count": int((codes == STATES.index(st)).sum()),
                   "percent": round(100 * float((codes == STATES.index(st)).mean()), 1)}
              for st in ("inspected_ok", "finding", "not_seen")}  # fmt: skip
    doc = {
        "schema_version": SCHEMA_VERSION,
        "units": "bundle",
        "voxel_size": s["voxel_size"],
        "thresholds": {
            k: s[k]
            for k in ("min_frames", "min_points_per_region", "max_view_distance", "unusable_issues")
        },  # fmt: skip
        "quality_checked": checked,
        "frames": {"registered": len(b.cameras), "usable": int(usable.sum())},
        "regions": regions,
        "totals": totals,
    }
    out = run / "coverage"
    out.mkdir(parents=True, exist_ok=True)
    (out / "regions.json").write_text(json.dumps(doc, indent=1))

    web_rel = mpath.parent.relative_to(run).as_posix()
    rel = f"{web_rel}/points.coverage.u8"
    codes[region].astype("u1").tofile(run / rel)
    man["points"]["coverage"] = rel
    mpath.write_text(json.dumps(man))
    dev = run / "web" / "manifest.json"  # export's dev copy (export.write_dev_manifest_copy)
    if dev != mpath and dev.is_file():
        shutil.copyfile(mpath, dev)
    return {"regions": n_reg, "usable_frames": doc["frames"]["usable"], "quality_checked": checked,
            **{st: t["count"] for st, t in totals.items()}}  # fmt: skip
