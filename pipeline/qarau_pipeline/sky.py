"""Sky mask for COLMAP feature extraction (ADR 0013): classical OpenCV + NumPy, no model.

Sky = smooth (low gradient) pixels that are bright, blue or grey, connected to the top border.
Mask PNGs follow COLMAP's convention: <mask_dir>/<image_name>.png, 0 = no features, 255 = keep.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

WORK_W = 480  # detect at this width, upscale the mask
TEXTURE_MAX = 12.0  # mean Sobel magnitude (grey levels) in a TEXTURE_WIN window
TEXTURE_WIN = 9
BRIGHT_V = 170  # HSV value: bright sky, any hue
BLUE_H = (85, 135)  # OpenCV hue (0..180): cyan..blue
BLUE_MIN_V = 90
GREY_S, GREY_V = 40, 120  # overcast: unsaturated and not dark
CLOSE_PX = 7  # fill small holes (wires, birds) at work resolution
DILATE_PX = 2  # widen the sky edge so the horizon line itself is not kept
MAX_SKY = 0.70  # more than this would leave too little to match: keep the whole frame


def sky_mask(image_bgr: np.ndarray) -> tuple[np.ndarray, str]:
    """(mask HxW uint8, 255 = keep / 0 = sky, reason). reason: 'sky', 'no_sky' or 'cap'."""
    h, w = image_bgr.shape[:2]
    keep_all = np.full((h, w), 255, np.uint8)
    s = min(1.0, WORK_W / w)
    img = cv2.resize(image_bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    grey = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    mag = cv2.magnitude(cv2.Sobel(grey, cv2.CV_32F, 1, 0), cv2.Sobel(grey, cv2.CV_32F, 0, 1))
    smooth = cv2.blur(mag, (TEXTURE_WIN, TEXTURE_WIN)) < TEXTURE_MAX
    blue = (hue >= BLUE_H[0]) & (hue <= BLUE_H[1]) & (val >= BLUE_MIN_V)
    colour = (val >= BRIGHT_V) | blue | ((sat <= GREY_S) & (val >= GREY_V))
    cand = (smooth & colour).astype(np.uint8)

    _, labels = cv2.connectedComponents(cand, connectivity=8)
    top = np.unique(labels[0][cand[0] > 0])
    if not len(top):
        return keep_all, "no_sky"
    sky: np.ndarray = np.isin(labels, top).astype(np.uint8)
    sky = cv2.morphologyEx(sky, cv2.MORPH_CLOSE, np.ones((CLOSE_PX, CLOSE_PX), np.uint8))
    sky = cv2.dilate(sky, np.ones((2 * DILATE_PX + 1,) * 2, np.uint8))
    if sky.mean() > MAX_SKY:
        return keep_all, "cap"
    sky = cv2.resize(sky, (w, h), interpolation=cv2.INTER_NEAREST)
    return np.where(sky > 0, 0, 255).astype(np.uint8), "sky"


def write_masks(frames_dir: Path, names: list[str], mask_dir: Path) -> dict[str, Any]:
    """One PNG per image (COLMAP: <mask_dir>/<name>.png). Unreadable frames get no mask:
    COLMAP then extracts features from the whole image."""
    mask_dir.mkdir(parents=True, exist_ok=True)
    reasons = {"sky": 0, "no_sky": 0, "cap": 0, "unreadable": 0}
    sky_px = 0.0
    for n in names:
        img = cv2.imread(str(frames_dir / n))
        if img is None:
            reasons["unreadable"] += 1
            continue
        m, why = sky_mask(img)
        reasons[why] += 1
        sky_px += float((m == 0).mean())
        cv2.imwrite(str(mask_dir / f"{n}.png"), m)
    if reasons["cap"]:
        print(f"sky mask: {reasons['cap']} frame(s) over {MAX_SKY:.0%} sky, kept whole", flush=True)
    return {**reasons, "mean_sky_fraction": round(sky_px / max(1, len(names)), 3)}
