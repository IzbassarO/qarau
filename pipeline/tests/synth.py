"""Synthetic camera sequences (640x480 grey, 6 fps) for keyframe and quality tests."""

import cv2
import numpy as np

H, W, FPS = 480, 640, 6.0


def texture(h: int = 1200, w: int = 2400, seed: int = 0) -> np.ndarray:
    noise = np.random.default_rng(seed).random((h, w)) * 255
    return cv2.GaussianBlur(noise.astype(np.uint8), (0, 0), 1.5)


def times(n: int) -> list[float]:
    return [i / FPS for i in range(n)]


def static(n: int) -> list[np.ndarray]:
    img = texture()[:H, :W].copy()
    return [img] * n


def translate(n: int, speeds: tuple[float, ...] = (2, 4, 2, 4, 2, 4)) -> list[np.ndarray]:
    """Sideways camera over a scene with depth: horizontal bands at alternating distances move
    at different speeds (px per frame), so no single homography explains the motion (~0.8 px
    of parallax per frame with the default speeds). A flat crop would be a homography."""
    tex, band = texture(), H // len(speeds)
    out = []
    for k in range(n):
        img = np.empty((H, W), np.uint8)
        for b, v in enumerate(speeds):
            x = round(k * v)
            img[b * band : (b + 1) * band] = tex[b * band : (b + 1) * band, x : x + W]
        out.append(img)
    return out


def rotate(n: int, deg_per_frame: float = 0.75, f: float = 500.0) -> list[np.ndarray]:
    """Camera turning on the spot: frame k = K R(yaw) K^-1 warp of one wide image."""
    tex = texture()
    ks = np.array([[f, 0, tex.shape[1] / 2], [0, f, tex.shape[0] / 2], [0, 0, 1]])
    ko = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
    out = []
    for k in range(n):
        r = cv2.Rodrigues(np.array([0.0, np.radians(k * deg_per_frame), 0.0]))[0]
        out.append(cv2.warpPerspective(tex, ko @ r @ np.linalg.inv(ks), (W, H)))
    return out


def wall_scene(n: int = 400, n_cams: int = 6, seed: int = 0) -> dict:
    """A wall (z = 5, x in [-4, 4], y in [-1, 1], normal -z) and pinhole cameras on the x axis
    looking +z, each seeing only part of it. Track of a point = the cameras that truly see it."""
    from qarau_pipeline.visibility import View, in_view

    rng = np.random.default_rng(seed)
    xyz = np.column_stack([rng.uniform(-4, 4, n), rng.uniform(-1, 1, n), np.full(n, 5.0)])
    views = {
        i: View(np.eye(3), np.array([-x, 0.0, 0.0]), "PINHOLE", [300, 300, 160, 120], 320, 240)
        for i, x in enumerate(np.linspace(-4, 4, n_cams))
    }
    seen = np.stack([in_view(xyz, v) for v in views.values()], 1)  # n x cams
    offsets = np.concatenate([[0], np.cumsum(seen.sum(1))])
    frames = np.nonzero(seen)[1]  # row-major: grouped by point, cameras ascending
    return {"xyz": xyz, "views": views, "seen": seen, "offsets": offsets, "frames": frames}


def write_wall_model(s: dict, d) -> None:
    """wall_scene as a COLMAP text model in d: image i+1 = f{i}.jpg, tracks = true views."""
    from qarau_pipeline.export import project

    d.mkdir(parents=True)
    (d / "cameras.txt").write_text("1 PINHOLE 320 240 300 300 160 120\n")
    obs: dict = {i: [] for i in s["views"]}
    lines = []
    for i, v in s["views"].items():
        for p in np.nonzero(s["seen"][:, i])[0]:
            obs[i].append((p, project(v.model, v.params, (s["xyz"][p] + v.t)[None])[0]))
        o = " ".join(f"{uv[0]} {uv[1]} {p + 1}" for p, uv in obs[i])
        lines += [f"{i + 1} 1 0 0 0 {v.t[0]} {v.t[1]} {v.t[2]} 1 f{i}.jpg", o]
    (d / "images.txt").write_text("\n".join(lines) + "\n")
    pts = []
    for p, x in enumerate(s["xyz"]):
        ims = np.nonzero(s["seen"][p])[0]
        tr = " ".join(f"{i + 1} {[q for q, _ in obs[i]].index(p)}" for i in ims)
        pts.append(f"{p + 1} {x[0]} {x[1]} {x[2]} 90 90 90 0.1 {tr}")
    (d / "points3D.txt").write_text("\n".join(pts) + "\n")
