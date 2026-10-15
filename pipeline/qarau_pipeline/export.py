"""export stage: COLMAP sparse model -> exchange formats (LAZ, PLY) + viewer bundle (ADR 0003)."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import cv2
import laspy
import numpy as np
import pycolmap

from .interfaces import stage_seconds

NEVER = 65535
THUMB_W = 960


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


def project(model: str, params: list[float], xc: np.ndarray) -> np.ndarray:
    """Project camera-space points (N,3) to pixels, independent of pycolmap."""
    u, v = xc[:, 0] / xc[:, 2], xc[:, 1] / xc[:, 2]
    if model == "SIMPLE_PINHOLE":
        f, cx, cy = params
        return np.stack([f * u + cx, f * v + cy], 1)
    if model == "PINHOLE":
        fx, fy, cx, cy = params
        return np.stack([fx * u + cx, fy * v + cy], 1)
    if model in ("SIMPLE_RADIAL", "RADIAL"):
        f, cx, cy, k1 = params[:4]
        k2 = params[4] if model == "RADIAL" else 0.0
        r2 = u * u + v * v
        d = 1 + k1 * r2 + k2 * r2 * r2
        return np.stack([f * u * d + cx, f * v * d + cy], 1)
    raise ValueError(f"unsupported camera model {model}")


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
    fr = json.loads((out / "frames.json").read_text())["frames"]
    return [
        {"i": f["i"], "name": Path(f["file"]).name, "file": f["file"], "t": f["t"],
         "kept": f["kept"], "drop_reason": f["drop_reason"], "blur": f["blur"]}
        for f in fr
    ]  # fmt: skip


# ---------------------------------------------------------------- tracks (CSR)

MAX_DENSE = 2_000_000  # dense points kept in a bundle (ADR 0008)
MAX_TRACK = 4  # frames kept per dense point


def thin_track(trk: np.ndarray, k: int) -> np.ndarray:
    """Keep <= k frames per row: the first (first_seen stays exact), the last, evenly spaced
    in between. Rows are sorted ascending, padded last; shorter rows are kept whole."""
    n = (trk != 65535).sum(1)
    pos = np.round(np.arange(k)[None, :] * (np.maximum(n, 1)[:, None] - 1) / max(k - 1, 1))
    pos = np.where(n[:, None] > k, pos, np.arange(k)[None, :]).astype(np.int64)
    out = np.take_along_axis(trk, np.minimum(pos, trk.shape[1] - 1), 1)
    out[np.arange(k)[None, :] >= np.minimum(n, k)[:, None]] = 65535
    return out


def bound_dense(
    xyz: np.ndarray,
    rgb: np.ndarray,
    trk: np.ndarray,
    max_points: int,
    max_track: int,
    centers: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cap the dense cloud: keep detail where the camera was close, thin tracks.

    centers: F x 3 camera centres (same frame as xyz, NaN = unregistered). A point is kept with
    probability min(1, s / d), d = distance to its nearest observing camera and s chosen so the
    expected count is max_points (fixed seed: deterministic). Without centers: uniform.
    Tracks: thin_track().
    """
    n = len(xyz)
    if n > max_points:
        rng = np.random.default_rng(0)
        if centers is None:
            w = np.ones(n)
        else:
            d = np.full(n, np.inf)
            for j in range(trk.shape[1]):
                f = trk[:, j]
                ok = f != 65535
                dj = np.linalg.norm(xyz[ok] - centers[f[ok]], axis=1)
                d[ok] = np.fmin(d[ok], np.where(np.isnan(dj), np.inf, dj))
            w = 1.0 / np.maximum(np.where(np.isfinite(d), d, np.nanmax(d[np.isfinite(d)])), 1e-9)
        lo, hi = 0.0, max_points / w.min() * 2
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if np.minimum(1, mid * w).sum() < max_points else (lo, mid)
        keep = rng.random(n) < np.minimum(1, hi * w)
        xyz, rgb, trk = xyz[keep], rgb[keep], trk[keep]
    return xyz, rgb, thin_track(trk, max_track)


def dense_pairs(tracks: np.ndarray, first: int, pad: int = NEVER) -> tuple[np.ndarray, np.ndarray]:
    """Dense (N x T, padded) frame lists -> (point index, frame) pairs; points start at `first`."""
    k, j = np.nonzero(tracks != pad)
    return k + first, tracks[k, j].astype(np.int64)


def build_csr(pt: np.ndarray, fr: np.ndarray, N: int, F: int) -> dict[str, np.ndarray]:
    """(point, frame) pairs -> both CSR directions (deduplicated, sorted) and per-point stats."""
    kp = np.unique(pt * F + fr)  # point-major, frames ascending, deduplicated
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
    return {
        "point_offsets": point_offsets, "point_frames": point_frames,
        "frame_offsets": frame_offsets, "frame_points": frame_points,
        "first_seen": first_seen, "num_obs": np.minimum(counts, 255).astype(np.uint8),
    }  # fmt: skip


# ---------------------------------------------------------------- export


def export_bundle(
    rec: pycolmap.Reconstruction,
    frames: list[dict[str, Any]],
    out: Path,
    source: dict[str, Any],
    run_id: str,
    dense: Path | None = None,
    max_dense: int = MAX_DENSE,
    max_track: int = MAX_TRACK,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    web = out / "web"
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
    rgb = np.array([rec.points3D[int(p)].color for p in pids], dtype=np.uint8)

    # poses (COLMAP world)
    R = {iid: np.asarray(rec.images[iid].cam_from_world().rotation.matrix()) for iid in reg}
    t = {iid: np.asarray(rec.images[iid].cam_from_world().translation) for iid in reg}
    Rs = np.stack(list(R.values()))
    centers = np.stack([-R[i].T @ t[i] for i in reg])

    fcent = np.full((F, 3), np.nan)
    for i_, fi_ in zip(reg, range(len(reg)), strict=True):
        fcent[reg[i_]] = centers[fi_]

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
    # dense points (bundle v2): appended after the sparse ones, same alignment
    N_sparse, dense_info, psrc = N, None, None
    if dense is not None:
        dxyz, drgb, dtrk = bound_dense(
            np.load(dense / "points.npy"), np.load(dense / "colors.npy"),
            np.load(dense / "tracks.npy"), max_dense, max_track, fcent,
        )  # fmt: skip
        dxyz = dxyz.astype(np.float64)
        pd_, fd_ = dense_pairs(dtrk, N_sparse, 65535)
        dense_info = json.loads((dense / "dense.json").read_text())
        pt_a, fr_a = np.concatenate([pt_a, pd_]), np.concatenate([fr_a, fd_])
        xyz_a = np.concatenate([xyz_a, dxyz @ A.T - c])
        rgb = np.concatenate([rgb, drgb])
        N = len(xyz_a)
        psrc = np.concatenate([np.zeros(N_sparse, np.uint8), np.ones(N - N_sparse, np.uint8)])
    csr = build_csr(pt_a, fr_a, N, F)
    point_offsets, point_frames = csr["point_offsets"], csr["point_frames"]
    frame_offsets, frame_points = csr["frame_offsets"], csr["frame_points"]
    first_seen, num_obs = csr["first_seen"], csr["num_obs"]

    def w(name: str, arr: np.ndarray, dtype: str) -> str:
        rel = f"web/{name}"
        np.ascontiguousarray(arr, dtype=dtype).tofile(out / rel)
        return rel

    # frame entries
    entries = []
    for f in frames:
        i = f["i"]
        e: dict[str, Any] = {
            "i": i, "t": f["t"], "image": f["file"], "thumb": f"web/thumbs/{i:06d}.jpg",
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
        "version": 2,
        "run_id": run_id,
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
            "mean_track_length": round(mean_track, 3),
            "mean_reproj_error_px": round(mean_err, 3),
            "processing_s": {k: v for k, v in secs.items() if k in ("frames", "reconstruction")},
        },
    }  # fmt: skip

    if psrc is not None and dense_info is not None:
        manifest["points"]["source"] = w("points.source.u8", psrc, "u1")
        manifest["dense"] = {
            "method": dense_info["method"], "frames_used": dense_info["frames_used"],
            "points": N - N_sparse,
        }  # fmt: skip
        manifest["stats"]["points_sparse"] = N_sparse
    write_laz_ply(out, xyz_a, rgb)
    manifest["stats"]["processing_s"]["export"] = round(time.perf_counter() - t0, 2)
    (web / "manifest.json").write_text(json.dumps(manifest))

    check = check_projection(rec, out, manifest, reg)
    return {**manifest["stats"], "reproj_check_px": check}


def write_laz_ply(out: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    hdr = laspy.LasHeader(point_format=2, version="1.2")
    hdr.scales = np.array([0.001] * 3)
    hdr.offsets = xyz.min(axis=0)
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
    n: int = 3000,
) -> dict[str, float]:
    """Project points with the STORED aligned pose + camera params (read back from the
    written files) and compare with COLMAP's own 2D observations. Returns px error."""
    pos = np.fromfile(out / manifest["points"]["position"], dtype="<f4").reshape(-1, 3)
    pids = np.array(sorted(rec.points3D.keys()))
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


def rot_from_qvec(q: np.ndarray) -> np.ndarray:
    w_, x, y, z = q
    return np.array(
        [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w_), 2 * (x * z + y * w_)],
         [2 * (x * y + z * w_), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w_)],
         [2 * (x * z - y * w_), 2 * (y * z + x * w_), 1 - 2 * (x * x + y * y)]]
    )  # fmt: skip
