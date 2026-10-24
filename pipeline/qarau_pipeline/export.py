"""export stage: COLMAP sparse model -> exchange formats (LAZ, PLY) + viewer bundle (ADR 0003)."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import laspy
import numpy as np
import pycolmap

from . import layout
from .cameras import project, rot_from_qvec  # also imported from here by visibility, projection
from .interfaces import DensePoints, stage_seconds

NEVER = 65535
THUMB_W = 960
FAR_K = 2.5  # far layer: nearest-camera distance > FAR_K x median (ADR 0013)


# ---------------------------------------------------------------- geometry


def qvec_from_matrix(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion [w, x, y, z] (w >= 0)."""
    t = np.trace(R)
    if t > 0:
        s = 2 * np.sqrt(t + 1)
        q = [s / 4, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2 * np.sqrt(1 + R[i, i] - R[j, j] - R[k, k])
        q = [0.0] * 4
        q[0] = (R[k, j] - R[j, k]) / s
        q[1 + i] = s / 4
        q[1 + j] = (R[j, i] + R[i, j]) / s
        q[1 + k] = (R[k, i] + R[i, k]) / s
    q_arr = np.array(q)
    q_arr /= np.linalg.norm(q_arr)
    return -q_arr if q_arr[0] < 0 else q_arr


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector a to unit vector b (Rodrigues)."""
    v, c = np.cross(a, b), float(np.dot(a, b))
    if c < -1 + 1e-9:  # antiparallel: half-turn about any perpendicular axis
        axis = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def estimate_up(Rs: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Up vector in COLMAP world. Mean of cameras' -y axes; plane-fit normal if degenerate."""
    up = -Rs[:, 1, :].mean(axis=0)  # row 1 of R_cam_from_world = world direction of cam +y
    if np.linalg.norm(up) > 0.3 or len(centers) < 3:
        return up / (np.linalg.norm(up) or 1.0)
    # ponytail: nadir/planar flights only; sign taken from the weak mean, arbitrary if ~0
    _, _, vt = np.linalg.svd(centers - centers.mean(axis=0))
    n = vt[2]
    return n if np.dot(n, up) >= 0 else -n


# ---------------------------------------------------------------- cameras


def intrinsics(model: str, p: list[float]) -> list[float]:
    """[fx, fy, cx, cy]"""
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        return [p[0], p[0], p[1], p[2]]
    return [p[0], p[1], p[2], p[3]]


# ---------------------------------------------------------------- frames input


def frames_from_images(images: Path, out: Path) -> list[dict[str, Any]]:
    """Image-set input: frames = images sorted by name, t = index, linked into frames/."""
    names = sorted(
        p.name for p in images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    (out / "frames").mkdir(parents=True, exist_ok=True)
    res = []
    for i, n in enumerate(names):
        dst = out / "frames" / f"{i:06d}.jpg"
        if not dst.exists():
            try:
                os.link(images / n, dst)
            except OSError:
                shutil.copyfile(images / n, dst)
        res.append(
            {
                "i": i,
                "name": n,
                "file": f"frames/{i:06d}.jpg",
                "t": float(i),
                "kept": True,
                "drop_reason": None,
                "blur": None,
            }  # fmt: skip
        )
    return res


def frames_from_json(out: Path) -> list[dict[str, Any]]:
    fr = json.loads(layout.frames_json(out).read_text())["frames"]
    return [
        {"i": f["i"], "name": Path(f["file"]).name, "file": f["file"], "t": f["t"],
         "kept": f["kept"], "drop_reason": f["drop_reason"], "blur": f["blur"]}
        for f in fr
    ]  # fmt: skip


# ---------------------------------------------------------------- export


def sanitize_points(xyz: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    """Mask of points safe to export + counts. Drops non-finite rows and far outliers:
    distance from the per-axis median > max(100 x the 99th-percentile distance, 1)."""
    finite = np.isfinite(xyz).all(axis=1)
    keep = finite.copy()
    if finite.any():
        # ponytail: if >1% of points are outliers r99 is one of them and nothing is dropped;
        # write_laz_ply still fits any finite extent, only precision suffers
        d = np.linalg.norm(xyz[finite] - np.median(xyz[finite], axis=0), axis=1)
        keep[finite] = d <= max(100 * float(np.percentile(d, 99)), 1.0)
    report = {
        "non_finite": int((~finite).sum()),
        "far_outliers": int(finite.sum() - keep.sum()),
        "kept": int(keep.sum()),
        "total": len(xyz),
    }
    if not report["kept"]:
        raise ValueError(f"export: no valid points to write ({report})")
    return keep, report


def far_mask(xyz: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Points farther from every registered camera than FAR_K x the median such distance:
    horizon, distant hills, sky remnants. Nearest camera, not the trajectory centroid, so a
    long walk along a pipeline keeps its ends."""
    # ponytail: dense N x C distance matrix in chunks; a KD-tree if C grows to thousands
    d = np.concatenate(
        [
            np.sqrt(((xyz[i : i + 8192, None] - centers[None]) ** 2).sum(-1).min(1))
            for i in range(0, len(xyz), 8192)
        ]
    )
    return np.asarray(d > FAR_K * np.median(d))


def las_offset_scale(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Offset = middle of the extent, scale >= 1 mm and coarse enough that every quantized
    coordinate (x - offset) / scale fits in int32 (10% margin for rounding)."""
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    return (lo + hi) / 2, np.maximum(0.001, (hi - lo) / 2 / (2**31 - 1) * 1.1)


def filter_rows(
    offsets: np.ndarray, ids: np.ndarray, keep: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Keep the CSR rows where keep is True, so rows stay in step with the kept points."""
    counts = np.diff(offsets).astype(np.int64)
    new = np.concatenate([[0], np.cumsum(counts[keep])]).astype(np.uint32)
    return new, ids[np.repeat(keep, counts)].astype(np.uint32)


def dense_layer(
    dense: DensePoints,
    centers: np.ndarray,
    A: np.ndarray,
    c: np.ndarray,
    n_frames: int,
    w: Callable[[str, np.ndarray, str], str],
) -> dict[str, Any]:
    """Manifest key `dense` (ADR 0017): same sanitize and far rules as the sparse points; the
    far ones are dropped (counted), never moved to the far layer. Rows filtered in step."""
    keep, rep = sanitize_points(dense.xyz)
    far = np.zeros(len(keep), bool)
    far[keep] = far_mask(dense.xyz[keep], centers)
    keep &= ~far
    off, ids = filter_rows(dense.frame_offsets, dense.frame_ids, keep)
    if len(ids) and int(ids.max()) >= n_frames:
        raise ValueError(f"dense frame link {int(ids.max())} >= {n_frames} frames")
    n = int(keep.sum())
    return {
        "count": n,
        "position": w("dense.position.f32", dense.xyz[keep] @ A.T - c, "<f4"),
        "color": w("dense.color.u8", dense.rgb[keep], "u1"),
        "densifier": dense.densifier,
        "frame_links_source": dense.links_source,
        "max_frames_per_point": dense.max_frames_per_point,
        "frame_offsets": {"path": w("dense.frame_offsets.u32", off, "<u4"), "dtype": "<u4",
                          "length": n + 1},
        "frame_ids": {"path": w("dense.frame_ids.u32", ids, "<u4"), "dtype": "<u4",
                      "length": len(ids)},
        "linked_points": int((np.diff(off) > 0).sum()),
        "dropped": {"non_finite": rep["non_finite"], "far_outliers": rep["far_outliers"],
                    "far": int(far.sum())},
    }  # fmt: skip


def export_bundle(
    rec: pycolmap.Reconstruction,
    frames: list[dict[str, Any]],
    out: Path,
    source: dict[str, Any],
    run_id: str,
    dense: DensePoints | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    exp = layout.export_dir(out)  # <run>/export (or the run root of a legacy run, ADR 0022)
    web = exp / "web"
    web_rel = web.relative_to(out).as_posix()  # manifest paths stay relative to the run root
    (web / "thumbs").mkdir(parents=True, exist_ok=True)
    F = len(frames)
    assert F < NEVER
    idx_by_name = {f["name"]: f["i"] for f in frames}

    # registered images -> frame index
    reg: dict[int, int] = {}  # colmap image_id -> frame index
    for iid, im in rec.images.items():
        if im.name in idx_by_name:
            reg[iid] = idx_by_name[im.name]

    pids = np.array(sorted(rec.points3D.keys()), dtype=np.int64)
    N = len(pids)
    if N == 0 or not reg:
        raise ValueError("reconstruction has no registered images or no points")
    xyz = np.array([rec.points3D[int(p)].xyz for p in pids], dtype=np.float64)
    keep, report = sanitize_points(xyz)
    dropped = {"non_finite": report["non_finite"], "far_outliers": report["far_outliers"]}
    if report["kept"] < N:
        print(f"export: dropped {N - report['kept']} of {N} points {dropped}", flush=True)
    pids, xyz = pids[keep], xyz[keep]

    # poses (COLMAP world)
    R = {iid: np.asarray(rec.images[iid].cam_from_world().rotation.matrix()) for iid in reg}
    t = {iid: np.asarray(rec.images[iid].cam_from_world().translation) for iid in reg}
    Rs = np.stack(list(R.values()))
    centers = np.stack([-R[i].T @ t[i] for i in reg])

    # far layer (ADR 0013): own arrays, never in the object cloud
    far = far_mask(xyz, centers)
    far_xyz = xyz[far]
    far_rgb = np.array([rec.points3D[int(p)].color for p in pids[far]], np.uint8).reshape(-1, 3)
    pids, xyz = pids[~far], xyz[~far]  # every per-point array below derives from pids
    N = len(pids)
    rgb = np.array([rec.points3D[int(p)].color for p in pids], dtype=np.uint8)

    # alignment: up -> +Z, point centroid -> origin, no scale
    A = rotation_between(estimate_up(Rs, centers), np.array([0.0, 0, 1]))
    c = (xyz @ A.T).mean(axis=0)  # centroid in rotated frame
    # x' = A x - c
    xyz_a = xyz @ A.T - c
    W = np.eye(4)
    W[:3, :3], W[:3, 3] = A, -c

    # CSR both directions from tracks
    pt_l: list[int] = []
    fr_l: list[int] = []
    for k, p in enumerate(pids):
        for te in rec.points3D[int(p)].track.elements:
            fi = reg.get(te.image_id)
            if fi is not None:
                pt_l.append(k)
                fr_l.append(fi)
    pt_a, fr_a = np.array(pt_l, dtype=np.int64), np.array(fr_l, dtype=np.int64)
    kp = np.unique(pt_a * F + fr_a)  # point-major, frames ascending, deduplicated
    pf_pt, pf_fr = kp // F, kp % F
    point_offsets = np.concatenate([[0], np.cumsum(np.bincount(pf_pt, minlength=N))])
    point_frames = pf_fr.astype(np.uint16)
    kf = np.unique(pf_fr * N + pf_pt)  # frame-major, points ascending
    fp_fr, frame_points = kf // N, (kf % N).astype(np.uint32)
    frame_offsets = np.concatenate([[0], np.cumsum(np.bincount(fp_fr, minlength=F))])
    counts = np.diff(point_offsets)
    first_seen = np.full(N, NEVER, dtype=np.uint16)
    has = counts > 0
    first_seen[has] = point_frames[point_offsets[:-1][has]]
    num_obs = np.minimum(counts, 255).astype(np.uint8)

    def w(name: str, arr: np.ndarray, dtype: str) -> str:
        rel = f"{web_rel}/{name}"
        np.ascontiguousarray(arr, dtype=dtype).tofile(out / rel)
        return rel

    # frame entries
    entries = []
    for f in frames:
        i = f["i"]
        e: dict[str, Any] = {
            "i": i, "t": f["t"], "image": f["file"], "thumb": f"{web_rel}/thumbs/{i:06d}.jpg",
            "kept": f["kept"], "drop_reason": f["drop_reason"], "blur": f["blur"],
            "registered": False,
        }  # fmt: skip
        entries.append(e)
        src = out / f["file"]
        if src.exists():
            img = cv2.imread(str(src))
            if img is not None:
                h, wd = img.shape[:2]
                if wd > THUMB_W:
                    img = cv2.resize(
                        img, (THUMB_W, round(h * THUMB_W / wd)), interpolation=cv2.INTER_AREA
                    )
                cv2.imwrite(str(out / e["thumb"]), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    for iid, fi in reg.items():
        im, cam = rec.images[iid], rec.cameras[rec.images[iid].camera_id]
        Rn, tn = R[iid] @ A.T, t[iid] + R[iid] @ (A.T @ c)
        params = [float(x) for x in cam.params]
        entries[fi].update(
            registered=True,
            camera={"width": cam.width, "height": cam.height, "model": cam.model_name,
                    "params": params, "K": intrinsics(cam.model_name, params)},
            pose={"qvec": qvec_from_matrix(Rn).tolist(), "tvec": tn.tolist()},
            center=(-Rn.T @ tn).tolist(),
            n_points=int(frame_offsets[fi + 1] - frame_offsets[fi]),
        )  # fmt: skip
        assert im.name == frames[fi]["name"]

    zr = np.percentile(xyz_a[:, 2], [2, 98])
    mean_track = float(rec.compute_mean_track_length())
    mean_err = float(rec.compute_mean_reprojection_error())
    secs = stage_seconds(out)
    manifest: dict[str, Any] = {
        "version": 2,  # 2: optional `dense` layer with frame links (ADR 0017)
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),  # when exported
        "source": source,
        "world": {
            "up": [0, 0, 1], "world_from_colmap": W.flatten().tolist(),
            "bounds": {"min": xyz_a.min(0).tolist(), "max": xyz_a.max(0).tolist()},
            "z_range": zr.tolist(),
        },
        "points": {
            "count": N,
            "position": w("points.position.f32", xyz_a, "<f4"),
            "color": w("points.color.u8", rgb, "u1"),
            "first_seen": w("points.first_seen.u16", first_seen, "<u2"),
            "num_obs": w("points.num_obs.u8", num_obs, "u1"),
        },
        "far": {
            "count": len(far_xyz),
            "position": w("far.position.f32", far_xyz @ A.T - c, "<f4"),
            "color": w("far.color.u8", far_rgb, "u1"),
            "rule": f"nearest camera distance > {FAR_K:g} x median",
        },
        "tracks": {
            "frame_offsets": w("tracks.frame_offsets.u32", frame_offsets, "<u4"),
            "frame_points": w("tracks.frame_points.u32", frame_points, "<u4"),
            "point_offsets": w("tracks.point_offsets.u32", point_offsets, "<u4"),
            "point_frames": w("tracks.point_frames.u16", point_frames, "<u2"),
        },
        "frames": entries,
        "stats": {
            "frames_extracted": F,
            "frames_dropped": sum(1 for f in frames if not f["kept"]),
            "frames_registered": len(reg),
            "points": N,
            "far_points": len(far_xyz),
            "dropped_points": dropped,
            "mean_track_length": round(mean_track, 3),
            "mean_reproj_error_px": round(mean_err, 3),
            "processing_s": {k: v for k, v in secs.items() if k in ("frames", "reconstruction")},
        },
    }  # fmt: skip
    extra: dict[str, Any] = {}
    if dense is not None:
        d = manifest["dense"] = dense_layer(dense, centers, A, c, F, w)
        extra = {"dense_points": d["count"], "dense_linked_points": d["linked_points"],
                 "frame_links_source": d["frame_links_source"]}  # fmt: skip

    write_laz_ply(exp, xyz_a, rgb)
    manifest["stats"]["processing_s"]["export"] = round(time.perf_counter() - t0, 2)
    (web / "manifest.json").write_text(json.dumps(manifest))
    write_dev_manifest_copy(out, web)

    check = check_projection(rec, out, manifest, reg, pids)
    return {**manifest["stats"], **extra, "reproj_check_px": check}


def write_dev_manifest_copy(out: Path, web: Path) -> None:
    """A byte-identical copy of the manifest at the pre-S1 path `<run>/web/manifest.json`.

    The web app's dev-only stand-in for the API (`web/vite-dev-runs.ts`, used for screenshots
    and e2e runs without a backend) and the e2e specs still look for the manifest there; the
    paths inside are relative to the run root, so the arrays load through `files/export/web/…`.
    The API and `layout` never read the copy while `export/web/manifest.json` exists (ADR 0022
    treats a run as legacy only when the root manifest is the only one), and it is not part of
    the stored bundle (ADR 0024). A legacy run (web == out/web) needs no copy."""
    if web == out / "web":
        return
    (out / "web").mkdir(exist_ok=True)
    shutil.copyfile(web / "manifest.json", out / "web" / "manifest.json")


def write_laz_ply(out: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    hdr = laspy.LasHeader(point_format=2, version="1.2")
    hdr.offsets, hdr.scales = las_offset_scale(xyz)
    las = laspy.LasData(hdr)
    las.x, las.y, las.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    las.red, las.green, las.blue = (rgb[:, 0].astype("u2") << 8, rgb[:, 1].astype("u2") << 8,
                                    rgb[:, 2].astype("u2") << 8)  # fmt: skip
    las.write(out / "cloud.laz")
    dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    arr = np.empty(len(xyz), dtype=dt)
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    arr["r"], arr["g"], arr["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    head = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(xyz)}\nproperty float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    with open(out / "cloud.ply", "wb") as fh:
        fh.write(head.encode())
        arr.tofile(fh)


def check_projection(
    rec: pycolmap.Reconstruction,
    out: Path,
    manifest: dict[str, Any],
    reg: dict[int, int],
    pids: np.ndarray,
    n: int = 3000,
) -> dict[str, float]:
    """Project points with the STORED aligned pose + camera params (read back from the
    written files) and compare with COLMAP's own 2D observations. Returns px error.
    pids = COLMAP point ids of the written points, in file order."""
    pos = np.fromfile(out / manifest["points"]["position"], dtype="<f4").reshape(-1, 3)
    sel = np.random.default_rng(0).choice(len(pids), size=min(n, len(pids)), replace=False)
    errs = []
    for k in sel:
        for e in rec.points3D[int(pids[k])].track.elements:
            fi = reg.get(e.image_id)
            if fi is None:
                continue
            fe = manifest["frames"][fi]
            xc = rot_from_qvec(np.array(fe["pose"]["qvec"])) @ pos[k] + fe["pose"]["tvec"]
            cam = fe["camera"]
            px = project(cam["model"], cam["params"], xc[None])[0]
            errs.append(
                float(np.linalg.norm(px - rec.images[e.image_id].points2D[e.point2D_idx].xy))
            )
    a_ = np.array(errs)
    return {"samples": len(a_), "mean": float(a_.mean()), "max": float(a_.max())}
