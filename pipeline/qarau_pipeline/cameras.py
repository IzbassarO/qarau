"""Camera maths shared by export, projection and findings. NumPy only: the API process imports
this through `findings.py`, so nothing heavy (pycolmap, cv2, laspy) may be loaded here."""

from __future__ import annotations

import numpy as np


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


def rot_from_qvec(q: np.ndarray) -> np.ndarray:
    w_, x, y, z = q
    return np.array(
        [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w_), 2 * (x * z + y * w_)],
         [2 * (x * y + z * w_), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w_)],
         [2 * (x * z - y * w_), 2 * (y * z + x * w_), 1 - 2 * (x * x + y * y)]]
    )  # fmt: skip
