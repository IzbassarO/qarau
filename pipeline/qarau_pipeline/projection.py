"""project stage: 2D masks -> labelled 3D points -> findings (ADR 0009).

Reads the viewer bundle (positions, poses, cameras, `tracks` CSR) and `segmentation/`.
Writes `findings/findings.json` + `findings/points.finding.u16` and patches the manifest.

Visibility: a point is a candidate in frame f only if frame f is in its track (the CSR
`frame_points`). The reconstruction triangulated it from a keypoint in that frame, so it was
seen there and not occluded. No depth test is needed, and it works for sparse and dense bundles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import layout
from .export import project, rot_from_qvec
from .interfaces import StageError, run_stage

PARAMS = {"min_frames": 2, "min_angle_deg": 15.0, "min_points": 3, "min_frame_points": 2,
          "voxel": None}  # fmt: skip


def components(pos: np.ndarray, voxel: float) -> np.ndarray:
    """Cluster id per point: 26-connected occupied voxels (label propagation, numpy only)."""
    v = np.floor((pos - pos.min(0)) / voxel).astype(np.int64)
    uv, inv = np.unique(v, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    dims = uv.max(0) + 3

    def key(a: np.ndarray) -> np.ndarray:
        return ((a[:, 0] + 1) * dims[1] + a[:, 1] + 1) * dims[2] + a[:, 2] + 1

    order = np.argsort(key(uv))
    sk = key(uv)[order]
    ei, ej = [], []
    for d in np.array(np.meshgrid([-1, 0, 1], [-1, 0, 1], [-1, 0, 1])).reshape(3, -1).T:
        if tuple(d) <= (0, 0, 0):  # half of the 26 neighbours; edges are undirected
            continue
        nk = key(uv + d)
        at = np.minimum(np.searchsorted(sk, nk), len(sk) - 1)
        ok = sk[at] == nk
        ei.append(np.nonzero(ok)[0])
        ej.append(order[at[ok]])
    i, j = np.concatenate(ei), np.concatenate(ej)
    lab = np.arange(len(uv))
    while True:
        m = np.minimum(lab[i], lab[j])
        new = lab.copy()
        np.minimum.at(new, i, m)
        np.minimum.at(new, j, m)
        new = new[new]
        if np.array_equal(new, lab):
            return lab[inv]
        lab = new


def plane_area(pts: np.ndarray, cell: float) -> float:
    """Occupied cells of the cluster projected on its best-fit plane, times cell area.
    ponytail: flat-ish surfaces; a strongly curved or folded cluster is under-measured."""
    if len(pts) < 3:
        return 0.0
    _, _, vt = np.linalg.svd(pts - pts.mean(0), full_matrices=False)
    uv = (pts - pts.mean(0)) @ vt[:2].T
    return float(len(np.unique(np.floor(uv / cell).astype(np.int64), axis=0)) * cell * cell)


def _auto_voxel(pos: np.ndarray) -> float:
    lo, hi = np.percentile(pos, [2, 98], axis=0)
    return float(np.linalg.norm(hi - lo) / 150)


def run_project(run: Path, scale: float | None = None, **over: Any) -> dict[str, Any]:
    P = {**PARAMS, **over}
    mpath = layout.manifest_path(run)
    man = json.loads(mpath.read_text())
    obs_doc = json.loads((run / "segmentation/observations.json").read_text())
    obs = obs_doc["observations"]
    if not obs:
        raise StageError("segmentation found nothing: no observations to project")
    N = man["points"]["count"]
    pos = np.fromfile(run / man["points"]["position"], dtype="<f4").reshape(-1, 3).astype(float)
    fo = np.fromfile(run / man["tracks"]["frame_offsets"], dtype="<u4")
    fpts = np.fromfile(run / man["tracks"]["frame_points"], dtype="<u4")
    voxel = P["voxel"] or _auto_voxel(pos)
    prompts = obs_doc["prompts"]
    pidx = {p["text"]: k for k, p in enumerate(prompts)}
    seg_frames = obs_doc.get("frames") or sorted({o["frame"] for o in obs})

    # per frame: visible points (tracks) -> pixel coordinates; seen = frames that could see p
    seen = np.zeros(N, np.int32)
    visible: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for f in seg_frames:
        fe = man["frames"][f]
        if not fe["registered"]:
            continue
        idx = fpts[fo[f] : fo[f + 1]].astype(np.int64)
        R, t = rot_from_qvec(np.array(fe["pose"]["qvec"])), np.array(fe["pose"]["tvec"])
        xc = pos[idx] @ R.T + t
        front = xc[:, 2] > 1e-9
        idx, xc = idx[front], xc[front]
        cam = fe["camera"]
        px = project(cam["model"], cam["params"], xc)
        ok = (
            (px[:, 0] >= 0)
            & (px[:, 0] < cam["width"])
            & (px[:, 1] >= 0)
            & (px[:, 1] < cam["height"])
        )
        visible[f] = (idx[ok], px[ok])
        seen[idx[ok]] += 1

    # label: per observation the visible points inside the mask; per (frame, prompt) union
    obs_hits: list[np.ndarray] = []
    fp_hits: dict[tuple[int, int], np.ndarray] = {}
    for o in obs:
        f = o["frame"]
        if f not in visible:
            obs_hits.append(np.zeros(0, np.int64))
            continue
        m = cv2.imread(str(run / o["mask"]), cv2.IMREAD_GRAYSCALE)
        if m is None:
            raise StageError(f"cannot read mask {o['mask']}")
        idx, px = visible[f]
        cam = man["frames"][f]["camera"]
        ix = np.floor(px[:, 0] * m.shape[1] / cam["width"]).astype(int)
        iy = np.floor(px[:, 1] * m.shape[0] / cam["height"]).astype(int)
        hit = idx[m[np.clip(iy, 0, m.shape[0] - 1), np.clip(ix, 0, m.shape[1] - 1)] > 127]
        obs_hits.append(hit)
        key = (f, pidx[o["prompt"]])
        fp_hits[key] = np.union1d(fp_hits.get(key, hit[:0]), hit)

    point_finding = np.zeros(N, np.uint16)
    findings: list[dict[str, Any]] = []
    dropped = 0
    diag = float(np.linalg.norm(np.array(man["world"]["bounds"]["max"])
                                - np.array(man["world"]["bounds"]["min"])))  # fmt: skip
    for pk, pr in enumerate(prompts):
        hits = np.zeros(N, np.int32)  # frames in which a mask of this prompt hit the point
        for (_, k), h in fp_hits.items():
            if k == pk:
                hits[h] += 1
        cand = np.nonzero(hits)[0]
        if not len(cand):
            continue
        comp = components(pos[cand], voxel)
        clusters = []
        for c in np.unique(comp):
            pts = cand[comp == c]
            if len(pts) < P["min_points"]:
                dropped += 1
                continue
            clusters.append(pts)
        for pts in sorted(clusters, key=len, reverse=True):
            fid = len(findings) + 1
            cset, cen = set(pts.tolist()), pos[pts].mean(0)
            o_list, frames, confs = [], set(), []
            for o, h in zip(obs, obs_hits, strict=True):
                if pidx[o["prompt"]] != pk:
                    continue
                n = int(np.isin(h, pts).sum()) if len(h) else 0
                if n >= P["min_frame_points"]:
                    o_list.append({"frame": o["frame"], "mask": o["mask"],
                                   "confidence": o["confidence"], "bbox": o["bbox"],
                                   "n_points": n})  # fmt: skip
                    frames.add(o["frame"])
                    confs.append(o["confidence"])
            rays = np.array([cen - np.array(man["frames"][f]["center"]) for f in sorted(frames)])
            if len(rays) >= 2:
                rays /= np.linalg.norm(rays, axis=1, keepdims=True)
                angle = float(np.degrees(np.arccos(np.clip((rays @ rays.T).min(), -1, 1))))
            else:
                angle = 0.0
            confirmed = len(frames) >= P["min_frames"] and angle >= P["min_angle_deg"]
            a = plane_area(pos[pts], voxel)
            size = ({"value": a * scale**2, "unit": "m2"} if scale
                    else {"value": a / diag**2, "unit": "rel"})  # fmt: skip
            point_finding[pts] = fid
            findings.append({
                "id": fid, "class": pr["class"], "prompt": pr["text"], "severity": pr["severity"],
                "status": "confirmed" if confirmed else "low_evidence",
                "confidence": round(float(np.mean(confs)) if confs else 0.0, 4),
                "n_points": len(cset), "centroid": cen.tolist(),
                "bbox": {"min": pos[pts].min(0).tolist(), "max": pos[pts].max(0).tolist()},
                "size": size, "frames": sorted(frames), "max_angle_deg": round(angle, 2),
                "support": round(float((hits[pts] / np.maximum(seen[pts], 1)).mean()), 3),
                "observations": o_list,
            })  # fmt: skip

    (run / "findings").mkdir(exist_ok=True)
    point_finding.tofile(run / "findings/points.finding.u16")
    doc = {
        "version": 1, "segmenter": obs_doc["segmenter"], "prompts": prompts,
        "scale": ({"source": "reference", "m_per_unit": scale} if scale
                  else {"source": "none", "m_per_unit": None}),
        "point_finding": "findings/points.finding.u16", "points": N,
        "params": {**P, "voxel": voxel}, "findings": findings,
    }  # fmt: skip
    (run / "findings/findings.json").write_text(json.dumps(doc, indent=1))
    man["findings"] = "findings/findings.json"
    man["version"] = max(man["version"], 2)
    mpath.write_text(json.dumps(man))
    return {"findings": len(findings),
            "confirmed": sum(f["status"] == "confirmed" for f in findings),
            "low_evidence": sum(f["status"] == "low_evidence" for f in findings),
            "labelled_points": int((point_finding > 0).sum()), "dropped_small_clusters": dropped,
            "voxel": voxel}  # fmt: skip


def project_stage(run: Path, scale: float | None = None, **over: Any) -> dict[str, Any]:
    params = {**PARAMS, **over, "scale": scale}
    return run_stage(run, "project", "numpy", params,
                     lambda: run_project(run, scale, **over)).metrics  # fmt: skip
