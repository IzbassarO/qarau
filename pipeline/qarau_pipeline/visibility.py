"""Frame links for dense points (ADR 0017): which frames saw each dense point.

Two sources, recorded in the bundle as `frame_links_source`:
- `colmap_fusion` (exact): COLMAP stereo_fusion's `fused.ply.vis`, the images whose depth
  maps were fused into the point.
- `sparse_tracks` (fallback, any densifier without .vis): the union of the tracks of the
  nearest sparse points, kept only where the dense point projects inside the image with
  positive depth. No occlusion test: a link means "a camera that saw this surface nearby
  and has the point in view", not proof the point itself was unoccluded.
Either way each point keeps at most `dense.max_frames_per_point` frames, best first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.spatial import cKDTree

from .export import project
from .interfaces import DensePoints

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
DEFAULT_MAX_FRAMES = 8
K_NEAREST = 8  # sparse neighbours whose tracks a dense point inherits (fallback)
RADIUS_X = 5.0  # neighbour radius = RADIUS_X x median sparse nearest-neighbour spacing
CHUNK = 100_000  # dense points per fallback batch (bounds the pair arrays in memory)
FAKE_COMMENT = "qarau dense_fake: synthetic points, not a reconstruction"
PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "<i2",
             "int16": "<i2", "ushort": "<u2", "uint16": "<u2", "int": "<i4", "int32": "<i4",
             "uint": "<u4", "uint32": "<u4", "float": "<f4", "float32": "<f4",
             "double": "<f8", "float64": "<f8"}  # fmt: skip


def max_frames_per_point(path: Path = CONFIG) -> int:
    raw = yaml.safe_load(path.read_text()) or {}
    v = (raw.get("dense") or {}).get("max_frames_per_point", DEFAULT_MAX_FRAMES)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"{path}: dense.max_frames_per_point must be an integer >= 1, got {v!r}")
    return v


# ---------------------------------------------------------------- files


def read_vis(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """fused.ply.vis -> (offsets int64 N+1, image indices uint32). Format: uint64 N, then per
    point uint32 n followed by n uint32 indices into the dense workspace's images."""
    raw = path.read_bytes()
    if len(raw) < 8 or (len(raw) - 8) % 4:
        raise ValueError(f"{path}: not a fused.ply.vis file ({len(raw)} bytes)")
    n = int(np.frombuffer(raw, "<u8", 1)[0])
    buf = np.frombuffer(raw, "<u4", offset=8)
    counts = np.empty(n, np.int64)
    pos = 0
    # ponytail: one Python step per point (~0.5 s per million); vectorize if it shows up
    for i in range(n):
        if pos >= len(buf):
            raise ValueError(f"{path}: truncated at point {i} of {n}")
        counts[i] = buf[pos]
        pos += int(buf[pos]) + 1
    if pos != len(buf):
        raise ValueError(f"{path}: {len(buf) - pos} trailing or missing values after {n} points")
    offsets = np.concatenate([[0], np.cumsum(counts)])
    body = np.ones(len(buf), bool)
    body[np.arange(n) + offsets[:-1]] = False  # the per-point count words
    return offsets, buf[body].astype(np.uint32)


def write_vis(path: Path, offsets: np.ndarray, ids: np.ndarray) -> None:
    counts = np.diff(offsets)
    n = len(counts)
    out = np.empty(n + len(ids), "<u4")
    head = np.arange(n) + offsets[:-1]
    body = np.ones(len(out), bool)
    body[head] = False
    out[head], out[body] = counts, ids
    path.write_bytes(np.uint64(n).astype("<u8").tobytes() + out.tobytes())


def read_ply(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[str]]:
    """Binary little-endian vertex PLY (COLMAP fusion layout) -> xyz f64, rgb u8, normals
    (or None), header comments."""
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"ply":
            raise ValueError(f"{path}: not a PLY file")
        fmt, n, props, comments, element = b"", 0, [], [], b""
        for line in fh:
            t = line.split()
            if not t:
                continue
            if t[0] == b"format":
                fmt = t[1]
            elif t[0] == b"comment":
                comments.append(line[8:].decode().strip())
            elif t[0] == b"element":
                element = t[1]
                if element == b"vertex":
                    n = int(t[2])
                elif int(t[2]):
                    raise ValueError(f"{path}: unsupported element {t[1].decode()}")
            elif t[0] == b"property" and element == b"vertex":
                if t[1] == b"list" or t[1].decode() not in PLY_TYPES:
                    raise ValueError(f"{path}: unsupported vertex property {line!r}")
                props.append((t[2].decode(), PLY_TYPES[t[1].decode()]))
            elif t[0] == b"end_header":
                break
        if n and fmt != b"binary_little_endian":
            raise ValueError(f"{path}: only binary_little_endian PLY is supported, got {fmt!r}")
        arr = np.frombuffer(fh.read(), np.dtype(props), count=n) if n else None
    names = {p for p, _ in props}
    if not n or arr is None:
        return np.zeros((0, 3)), np.zeros((0, 3), np.uint8), None, comments
    if not {"x", "y", "z", "red", "green", "blue"} <= names:
        raise ValueError(f"{path}: needs x y z red green blue, has {sorted(names)}")
    xyz = np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float64)
    rgb = np.stack([arr["red"], arr["green"], arr["blue"]], 1).astype(np.uint8)
    normals = None
    if {"nx", "ny", "nz"} <= names:
        normals = np.stack([arr["nx"], arr["ny"], arr["nz"]], 1).astype(np.float64)
    return xyz, rgb, normals, comments


def write_ply(
    path: Path, xyz: np.ndarray, rgb: np.ndarray, normals: np.ndarray, comment: str = ""
) -> None:
    """COLMAP fused.ply layout: x y z nx ny nz (float) red green blue (uchar)."""
    floats, bytes_ = ("x", "y", "z", "nx", "ny", "nz"), ("red", "green", "blue")
    arr = np.empty(len(xyz), [(k, "<f4") for k in floats] + [(k, "u1") for k in bytes_])
    for k, col in zip(floats + bytes_, [*xyz.T, *normals.T, *rgb.T], strict=True):
        arr[k] = col
    head = (
        "ply\nformat binary_little_endian 1.0\n"
        + (f"comment {comment}\n" if comment else "")
        + f"element vertex {len(xyz)}\n"
        + "".join(f"property float {k}\n" for k in floats)
        + "".join(f"property uchar {k}\n" for k in bytes_)
        + "end_header\n"
    )
    path.write_bytes(head.encode() + arr.tobytes())


# ---------------------------------------------------------------- links


@dataclass
class View:
    """A registered frame in COLMAP world: x_cam = R x + t."""

    R: np.ndarray
    t: np.ndarray
    model: str
    params: list[float]
    width: int
    height: int


def centers_of(views: dict[int, View], n_frames: int) -> np.ndarray:
    c = np.full((n_frames, 3), np.nan)
    for fi, v in views.items():
        c[fi] = -v.R.T @ v.t
    return c


def rank_links(
    pt: np.ndarray,
    fr: np.ndarray,
    xyz: np.ndarray,
    normals: np.ndarray | None,
    centers: np.ndarray,
    cap: int,
) -> tuple[np.ndarray, np.ndarray]:
    """(point, frame) pairs -> CSR (offsets u32 len(xyz)+1, frame ids u32): deduplicated, best
    first, at most cap per point. Best = smallest angle between the point normal and the ray
    to the camera; without normals, the nearest camera. Ties: lower frame index first."""
    nf = len(centers)
    key = np.unique(pt.astype(np.int64) * nf + fr)
    pt, fr = key // nf, key % nf
    ray = centers[fr] - xyz[pt]
    dist = np.linalg.norm(ray, axis=1)
    if normals is None:
        score = dist
    else:
        nn = normals[pt]
        cos = np.einsum("ij,ij->i", nn, ray) / np.maximum(np.linalg.norm(nn, axis=1) * dist, 1e-12)
        score = -cos
    order = np.lexsort((fr, score, pt))
    pt, fr = pt[order], fr[order]
    counts = np.bincount(pt, minlength=len(xyz))
    starts = np.concatenate([[0], np.cumsum(counts)])
    keep = np.arange(len(pt)) - starts[pt] < cap
    offsets = np.concatenate([[0], np.cumsum(np.minimum(counts, cap))])
    return offsets.astype(np.uint32), fr[keep].astype(np.uint32)


def vis_links(
    offsets: np.ndarray,
    ids: np.ndarray,
    index_to_frame: np.ndarray,
    xyz: np.ndarray,
    normals: np.ndarray | None,
    centers: np.ndarray,
    cap: int,
) -> tuple[np.ndarray, np.ndarray]:
    """.vis rows -> frame CSR. index_to_frame[workspace image index] = frame index or -1."""
    if len(offsets) - 1 != len(xyz):
        raise ValueError(f"fused.ply.vis has {len(offsets) - 1} rows for {len(xyz)} points")
    if len(ids) and int(ids.max()) >= len(index_to_frame):
        raise ValueError(
            f"fused.ply.vis names image index {int(ids.max())}, "
            f"the workspace has {len(index_to_frame)} images"
        )
    pt = np.repeat(np.arange(len(xyz)), np.diff(offsets))
    fr = index_to_frame[ids]
    ok = fr >= 0
    return rank_links(pt[ok], fr[ok], xyz, normals, centers, cap)


def in_view(xyz: np.ndarray, v: View) -> np.ndarray:
    """Points that project inside v's image with positive depth."""
    xc = xyz @ v.R.T + v.t
    ok = xc[:, 2] > 1e-9
    uv = project(v.model, v.params, xc[ok])
    inside = (uv[:, 0] >= 0) & (uv[:, 0] < v.width) & (uv[:, 1] >= 0) & (uv[:, 1] < v.height)
    ok[ok] = inside
    return ok


def sparse_track_links(
    xyz: np.ndarray,
    normals: np.ndarray | None,
    sparse_xyz: np.ndarray,
    track_offsets: np.ndarray,
    track_frames: np.ndarray,
    views: dict[int, View],
    n_frames: int,
    cap: int,
    k: int = K_NEAREST,
) -> tuple[np.ndarray, np.ndarray]:
    """Fallback links: union of the tracks (frame CSR) of the k nearest sparse points within
    RADIUS_X x median sparse spacing, kept where the dense point is in the frame's view."""
    tree = cKDTree(sparse_xyz)
    k = min(k, len(sparse_xyz))
    spacing = float(np.median(tree.query(sparse_xyz, k=2)[0][:, 1])) if len(sparse_xyz) > 1 else 0
    radius = RADIUS_X * spacing if spacing > 0 else np.inf
    centers = centers_of(views, n_frames)
    t_off = track_offsets.astype(np.int64)
    out_off, out_ids, base = [np.zeros(1, np.uint32)], [], 0
    for s in range(0, len(xyz), CHUNK):
        xc = xyz[s : s + CHUNK]
        _, nb = tree.query(xc, k=k, distance_upper_bound=radius)
        nb = nb.reshape(len(xc), -1)
        rows, cols = np.nonzero(nb < len(sparse_xyz))
        sp = nb[rows, cols]
        cnt = t_off[sp + 1] - t_off[sp]
        pt = np.repeat(rows, cnt)
        within = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        fr = track_frames[np.repeat(t_off[sp], cnt) + within].astype(np.int64)
        key = np.unique(pt * n_frames + fr)
        pt, fr = key // n_frames, key % n_frames
        keep = np.zeros(len(pt), bool)
        order = np.argsort(fr, kind="stable")
        bounds = np.searchsorted(fr[order], np.arange(n_frames + 1))
        for f in range(n_frames):
            sel = order[bounds[f] : bounds[f + 1]]
            if len(sel) and f in views:
                keep[sel] = in_view(xc[pt[sel]], views[f])
        nc = None if normals is None else normals[s : s + CHUNK]
        off, ids = rank_links(pt[keep], fr[keep], xc, nc, centers, cap)
        out_off.append(off[1:] + base)
        out_ids.append(ids)
        base += len(ids)
    return np.concatenate(out_off).astype(np.uint32), np.concatenate(
        out_ids or [np.zeros(0, np.uint32)]
    ).astype(np.uint32)


# ---------------------------------------------------------------- COLMAP glue


def registered_views(rec: Any, frames: list[dict[str, Any]]) -> dict[int, View]:
    """Frame index -> View for every registered image whose name is in frames."""
    by_name = {f["name"]: f["i"] for f in frames}
    views = {}
    for iid in rec.reg_image_ids():
        im = rec.images[iid]
        if im.name not in by_name:
            continue
        cam, pose = rec.cameras[im.camera_id], im.cam_from_world()
        views[by_name[im.name]] = View(
            np.asarray(pose.rotation.matrix()), np.asarray(pose.translation),
            cam.model_name, [float(x) for x in cam.params], cam.width, cam.height,
        )  # fmt: skip
    return views


def workspace_frame_map(rec: Any, frames: list[dict[str, Any]]) -> np.ndarray:
    """Dense workspace image index -> frame index (-1: not a bundle frame), by image name.
    image_undistorter keeps the sparse model's image ids and names, and COLMAP's mvs::Model
    lists the registered images in ascending image id, which is the .vis index."""
    # ponytail: order taken from COLMAP 4.x (RegImageIds is an ordered set); verify on the
    # first real GPU run (P4a-2) by checking a fused point reprojects into its linked frames.
    by_name = {f["name"]: f["i"] for f in frames}
    names = [rec.images[i].name for i in sorted(rec.reg_image_ids())]
    return np.array([by_name.get(n, -1) for n in names], np.int64)


def sparse_tracks(rec: Any, frames: list[dict[str, Any]]) -> tuple[np.ndarray, ...]:
    """(sparse xyz, track frame offsets, track frame indices) over registered bundle frames."""
    by_name = {f["name"]: f["i"] for f in frames}
    reg = {iid: by_name[rec.images[iid].name] for iid in rec.reg_image_ids()
           if rec.images[iid].name in by_name}  # fmt: skip
    pids = sorted(rec.points3D)
    xyz = np.array([rec.points3D[p].xyz for p in pids], np.float64).reshape(-1, 3)
    rows = [sorted({reg[e.image_id] for e in rec.points3D[p].track.elements if e.image_id in reg})
            for p in pids]  # fmt: skip
    offsets = np.concatenate([[0], np.cumsum([len(r) for r in rows])]).astype(np.int64)
    ids = np.array([f for r in rows for f in r], np.int64)
    return xyz, offsets, ids


def load_dense(dense_dir: Path, rec: Any, frames: list[dict[str, Any]], cap: int) -> DensePoints:
    """fused.ply in dense_dir + links: from fused.ply.vis when present, else sparse tracks."""
    xyz, rgb, normals, comments = read_ply(dense_dir / "fused.ply")
    views = registered_views(rec, frames)
    vis = dense_dir / "fused.ply.vis"
    if vis.exists():
        off, ids = read_vis(vis)
        centers = centers_of(views, len(frames))
        fo, fi = vis_links(off, ids, workspace_frame_map(rec, frames), xyz, normals, centers, cap)
        source = "colmap_fusion"
    else:
        s_xyz, t_off, t_fr = sparse_tracks(rec, frames)
        fo, fi = sparse_track_links(xyz, normals, s_xyz, t_off, t_fr, views, len(frames), cap)
        source = "sparse_tracks"
    densifier = "fake" if FAKE_COMMENT in comments else "colmap" if vis.exists() else "unknown"
    return DensePoints(xyz, rgb, normals, fo, fi, source, cap, densifier)
