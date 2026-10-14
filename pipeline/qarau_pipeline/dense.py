"""dense stage: monocular depth scaled to the sparse COLMAP points, fused into a dense cloud.

Per registered frame: Depth Anything V2 Small (Apache-2.0) -> disparity; robust fit
1/z = a*disparity + b to the frame's sparse points (held-out check, bad fits rejected);
multi-view consistency against neighbouring frames' depth; voxel fusion across frames.
Outputs (COLMAP world coordinates, export applies the bundle alignment): points.npy,
colors.npy, tracks.npy (N x T uint16, padded with PAD), dense.json. ADR 0008.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pycolmap

# pycolmap and torch each bundle libomp on macOS; the second one aborts without this.
# ponytail: unsupported but only numpy/torch inference runs after the pycolmap load
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

PAD = 65535  # empty slot in tracks.npy


def fit_scale_shift(
    d: np.ndarray, inv_z: np.ndarray, seed: int = 0, tol: float = 0.1, holdout: float = 0.2
) -> dict[str, Any]:
    """Robust fit inv_z ~ a*d + b (RANSAC on 2 points, then least squares on inliers).

    20% of the points are held out; `heldout_rel_err` is the median |z_pred - z| / z there.
    Returns a, b, n_fit, n_inliers, heldout_rel_err (a = nan when there is nothing to fit).
    """
    rng = np.random.default_rng(seed)
    n = len(d)
    bad = {"a": np.nan, "b": np.nan, "n_fit": 0, "n_inliers": 0, "heldout_rel_err": np.inf}
    if n < 10:
        return bad
    perm = rng.permutation(n)
    k = max(int(n * holdout), 3)
    te, tr = perm[:k], perm[k:]
    dt, it = d[tr], inv_z[tr]
    best: np.ndarray | None = None
    for _ in range(200):
        i, j = rng.choice(len(tr), 2, replace=False)
        if abs(dt[i] - dt[j]) < 1e-9:
            continue
        a = (it[i] - it[j]) / (dt[i] - dt[j])
        b = it[i] - a * dt[i]
        inl = np.abs(a * dt + b - it) < tol * it
        if a > 0 and (best is None or inl.sum() > best.sum()):
            best = inl
    if best is None or best.sum() < 5:
        return bad
    A = np.stack([dt[best], np.ones(best.sum())], 1)
    a, b = np.linalg.lstsq(A, it[best], rcond=None)[0]
    pred = a * d[te] + b
    with np.errstate(divide="ignore", invalid="ignore"):
        err = np.abs(1 / pred - 1 / inv_z[te]) * inv_z[te]
    err = np.where(pred > 0, err, np.inf)
    return {
        "a": float(a), "b": float(b), "n_fit": len(tr), "n_inliers": int(best.sum()),
        "heldout_rel_err": float(np.median(err)),
    }  # fmt: skip


def voxel_first(keys: np.ndarray) -> np.ndarray:
    """Indices of the first point in each occupied voxel (keys: int64 voxel ids)."""
    return np.unique(keys, return_index=True)[1]


def voxel_keys(xyz: np.ndarray, origin: np.ndarray, size: float) -> np.ndarray:
    g = np.floor((xyz - origin) / size).astype(np.int64)
    return (g[:, 0] * 2_000_003 + g[:, 1]) * 2_000_003 + g[:, 2]  # ponytail: collisions ~never


class DepthAnythingDensifier:
    def __init__(
        self,
        model: str = "depth-anything/Depth-Anything-V2-Small-hf",
        width: int = 960,
        pixel_stride: int = 3,
        frame_stride: int = 1,
        n_neighbors: int = 8,
        min_agree: int = 3,
        rel_tol: float = 0.02,
        max_fit_err: float = 0.1,
        voxel_frac: float = 0.0013,
    ) -> None:
        self.model, self.width = model, width
        self.pixel_stride, self.frame_stride = pixel_stride, frame_stride
        self.n_neighbors, self.min_agree, self.rel_tol = n_neighbors, min_agree, rel_tol
        self.max_fit_err, self.voxel_frac = max_fit_err, voxel_frac

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items()}

    # ------------------------------------------------------------ inputs
    def _load(self, run_dir: Path) -> tuple[pycolmap.Reconstruction, list[dict[str, Any]]]:
        rec = pycolmap.Reconstruction(str(run_dir / "colmap/sparse/0"))
        frames = json.loads((run_dir / "frames.json").read_text())["frames"]
        return rec, frames

    def _depth_model(self) -> Any:
        import torch
        from transformers import AutoModelForDepthEstimation

        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        m = AutoModelForDepthEstimation.from_pretrained(self.model).to(dev).eval()
        return m, dev

    # ------------------------------------------------------------ stage
    def densify(self, run_dir: Path, out_dir: Path) -> dict[str, Any]:
        import torch

        out_dir.mkdir(parents=True, exist_ok=True)
        times: dict[str, float] = {}
        t0 = time.perf_counter()
        rec, frames = self._load(run_dir)
        by_name = {Path(f["file"]).name: f for f in frames}
        ims = sorted(
            (im for im in rec.images.values() if im.name in by_name),
            key=lambda im: by_name[im.name]["i"],
        )[:: self.frame_stride]
        xyz_s = np.array([p.xyz for p in rec.points3D.values()])
        lo, hi = np.percentile(xyz_s, [2, 98], axis=0)
        diag = float(np.linalg.norm(hi - lo))
        voxel = diag * self.voxel_frac
        model, dev = self._depth_model()
        mean = np.array([0.485, 0.456, 0.406], np.float32)
        std = np.array([0.229, 0.224, 0.225], np.float32)

        # ---- per-frame cameras + depth maps scaled to COLMAP
        cams: list[dict[str, Any]] = []
        depths: list[np.ndarray] = []
        fits: list[dict[str, Any]] = []
        for im in ims:
            cam = rec.cameras[im.camera_id]
            fi = by_name[im.name]["i"]
            img = _read(run_dir / by_name[im.name]["file"])
            s = self.width / cam.width
            W, H = self.width, round(cam.height * s)
            fx, fy, cx, cy = (v * s for v in _intr(cam))
            K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
            dist = _dist(cam)
            if dist is not None:
                img = cv2.undistort(img, K, dist)
            Tcw = im.cam_from_world()
            R, t = np.asarray(Tcw.rotation.matrix()), np.asarray(Tcw.translation)
            pts = np.array([p.xyz for p in (rec.points3D[q.point3D_id] for q in im.points2D
                                            if q.has_point3D())]).reshape(-1, 3)  # fmt: skip
            xc = pts @ R.T + t
            xc = xc[xc[:, 2] > 0]
            u = np.round(fx * xc[:, 0] / xc[:, 2] + cx).astype(int)
            v = np.round(fy * xc[:, 1] / xc[:, 2] + cy).astype(int)
            ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
            xc, u, v = xc[ok], u[ok], v[ok]

            tm = time.perf_counter()
            mh, mw = round(H * 1008 / W / 14) * 14, 1008
            x = ((cv2.resize(img[:, :, ::-1], (mw, mh)) / 255.0 - mean) / std).astype(np.float32)
            with torch.no_grad():
                pd = model(pixel_values=torch.from_numpy(x.transpose(2, 0, 1))[None].to(dev))
            disp = cv2.resize(pd.predicted_depth[0].float().cpu().numpy(), (W, H))
            times["model"] = times.get("model", 0) + time.perf_counter() - tm

            fit = fit_scale_shift(disp[v, u], 1 / xc[:, 2], seed=fi)
            fit["frame"] = fi
            fit["accepted"] = bool(fit["heldout_rel_err"] < self.max_fit_err and fit["a"] > 0)
            fits.append(fit)
            if not fit["accepted"]:
                continue
            inv = fit["a"] * disp + fit["b"]
            # far/sky limit relative to the frame
            zmax = 3 * np.percentile(xc[:, 2], 98)
            z = np.where(inv > 1 / zmax, 1 / np.maximum(inv, 1e-9), np.nan).astype(np.float32)
            cams.append({
                "i": fi, "K": K, "R": R, "t": t, "c": -R.T @ t,
                "file": by_name[im.name]["file"], "W": W, "H": H, "dist": dist,
            })  # fmt: skip
            depths.append(z)
        times["depth+fit"] = time.perf_counter() - t0
        if len(cams) < 3:
            raise RuntimeError(f"only {len(cams)} frames passed the depth fit; cannot densify")

        # ---- consistency + fusion
        t1 = time.perf_counter()
        centers = np.stack([c["c"] for c in cams])
        axes = np.stack([c["R"][2] for c in cams])
        acc_xyz = np.zeros((0, 3), np.float32)
        acc_rgb = np.zeros((0, 3), np.uint8)
        acc_trk = np.zeros((0, 1 + self.n_neighbors), np.uint16)
        origin = lo - voxel
        cand = kept = 0
        agree_hist = np.zeros(self.n_neighbors + 1, np.int64)
        for a, c in enumerate(cams):
            d2 = np.linalg.norm(centers - centers[a], axis=1)
            d2[axes @ axes[a] < np.cos(np.radians(40))] = np.inf
            d2[a] = np.inf
            near = np.argsort(d2)[: 2 * self.n_neighbors : 2]  # skip every 2nd: wider baseline
            nb = [j for j in near if np.isfinite(d2[j])]
            z = depths[a]
            H, W = z.shape
            st = self.pixel_stride
            vv, uu = np.mgrid[st // 2 : H : st, st // 2 : W : st]
            zz = z[vv, uu]
            # drop depth discontinuities (flying pixels at edges)
            lz = np.log(z)
            gx = np.abs(np.roll(lz, 1, 1) - np.roll(lz, -1, 1))
            gy = np.abs(np.roll(lz, 1, 0) - np.roll(lz, -1, 0))
            ok = np.isfinite(zz) & (np.maximum(gx, gy)[vv, uu] < 0.1)
            uu, vv, zz = uu[ok], vv[ok], zz[ok]
            K = c["K"]
            xc = np.stack([(uu - K[0, 2]) / K[0, 0] * zz, (vv - K[1, 2]) / K[1, 1] * zz, zz], 1)
            Xw = (xc - c["t"]) @ c["R"]  # R^T (xc - t)
            cand += len(zz)
            agree = np.zeros(len(zz), int)
            trk = np.full((len(zz), 1 + self.n_neighbors), PAD, np.uint16)
            trk[:, 0] = c["i"]
            for j in nb:
                o = cams[j]
                xj = Xw @ o["R"].T + o["t"]
                with np.errstate(divide="ignore", invalid="ignore"):
                    pu = np.round(o["K"][0, 0] * xj[:, 0] / xj[:, 2] + o["K"][0, 2]).astype(int)
                    pv = np.round(o["K"][1, 1] * xj[:, 1] / xj[:, 2] + o["K"][1, 2]).astype(int)
                vis = (xj[:, 2] > 0) & (pu >= 0) & (pu < o["W"]) & (pv >= 0) & (pv < o["H"])
                zj = np.full(len(zz), np.nan, np.float32)
                zj[vis] = depths[j][pv[vis], pu[vis]]
                hit = np.abs(xj[:, 2] - zj) < self.rel_tol * zj  # False for nan
                trk[hit, 1 + agree[hit]] = o["i"]
                agree += hit
            sel = agree >= self.min_agree
            agree_hist += np.bincount(agree[sel], minlength=self.n_neighbors + 1)
            kept += int(sel.sum())
            if not sel.any():
                continue
            img = _read(run_dir / c["file"])
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
            if c["dist"] is not None:
                img = cv2.undistort(img, K, c["dist"])
            rgb = img[vv[sel], uu[sel], ::-1]
            xyz_n, trk_n = Xw[sel].astype(np.float32), trk[sel]
            key = voxel_keys(np.concatenate([acc_xyz, xyz_n]), origin, voxel)
            first = np.sort(voxel_first(key))  # acc points first -> earlier frames win
            acc_xyz = np.concatenate([acc_xyz, xyz_n])[first]
            acc_rgb = np.concatenate([acc_rgb, rgb])[first]
            acc_trk = np.concatenate([acc_trk, trk_n])[first]
        times["consistency+fuse"] = time.perf_counter() - t1

        trk_sorted = np.sort(acc_trk, axis=1)  # PAD (max) goes last
        np.save(out_dir / "points.npy", acc_xyz)
        np.save(out_dir / "colors.npy", acc_rgb)
        np.save(out_dir / "tracks.npy", trk_sorted)
        tl = (trk_sorted != PAD).sum(1)
        acc = [f for f in fits if f["accepted"]]
        metrics: dict[str, Any] = {
            "method": f"{self.model} + scale/shift to COLMAP + multi-view consistency",
            "points": len(acc_xyz),
            "frames_used": len(cams),
            "frames_rejected": len(fits) - len(acc),
            "voxel": voxel,
            "scene_diag": diag,
            "heldout_rel_err_median": float(np.median([f["heldout_rel_err"] for f in acc])),
            "heldout_rel_err_p90": float(np.percentile([f["heldout_rel_err"] for f in acc], 90)),
            "consistency": {
                "candidates": cand,
                "kept": kept,
                "kept_frac": kept / max(cand, 1),
                "agree_hist": agree_hist.tolist(),
            },  # fmt: skip
            "track_len": {
                "min": int(tl.min()),
                "median": float(np.median(tl)),
                "max": int(tl.max()),
            },
            "track_rule": (
                f"own frame + up to {self.n_neighbors} frames (every 2nd of the 2x nearest, view "
                f"angle < 40 deg) whose depth agrees within {self.rel_tol:.0%}"
            ),
            "seconds": {k: round(v, 1) for k, v in times.items()},
        }
        (out_dir / "dense.json").write_text(json.dumps({**metrics, "fits": fits}, indent=1))
        return metrics


def _read(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(path)
    return img


def _intr(cam: pycolmap.Camera) -> list[float]:
    p = list(cam.params)
    if cam.model_name in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        return [p[0], p[0], p[1], p[2]]
    return p[:4]


def _dist(cam: pycolmap.Camera) -> np.ndarray | None:
    p = list(cam.params)
    if cam.model_name == "SIMPLE_RADIAL":
        return np.array([p[3], 0, 0, 0], np.float64)
    if cam.model_name == "RADIAL":
        return np.array([p[3], p[4], 0, 0], np.float64)
    if cam.model_name in ("SIMPLE_PINHOLE", "PINHOLE"):
        return None
    raise ValueError(f"unsupported camera model {cam.model_name}")
