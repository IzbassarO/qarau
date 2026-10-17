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
