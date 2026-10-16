from pathlib import Path

import cv2
import numpy as np

from qarau_pipeline.sky import sky_mask, write_masks

H, W = 480, 640


def textured(h: int, w: int, seed: int = 0) -> np.ndarray:
    """Ground-like texture: random blocks of mid-grey green/brown, strong local gradients."""
    rng = np.random.default_rng(seed)
    small = rng.integers(20, 160, (h // 4, w // 4, 3)).astype(np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def sky_frame(sky_rows: int) -> np.ndarray:
    """Smooth blue-to-white gradient above row sky_rows, texture below."""
    img = textured(H, W)
    t = np.linspace(0, 1, sky_rows)[:, None, None]
    blue, white = np.array([230, 160, 90.0]), np.array([250, 245, 240.0])  # BGR
    img[:sky_rows] = np.broadcast_to(blue * (1 - t) + white * t, (sky_rows, W, 3)).astype(np.uint8)
    return img


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / (a | b).sum())


def test_known_sky_region() -> None:
    m, why = sky_mask(sky_frame(H // 2))
    truth = np.zeros((H, W), bool)
    truth[: H // 2] = True
    assert why == "sky" and m.dtype == np.uint8 and m.shape == (H, W)
    assert set(np.unique(m)) == {0, 255}
    assert iou(m == 0, truth) >= 0.9
    assert (m[H // 2 + 20 :] == 255).all()  # ground well below the horizon is kept


def test_textured_frame_keeps_all() -> None:
    m, why = sky_mask(textured(H, W, seed=1))
    assert why == "no_sky" and (m == 255).all()


def test_almost_all_sky_capped() -> None:
    m, why = sky_mask(sky_frame(H - 40))
    assert why == "cap" and (m == 255).all()


def test_masks_written_with_colmap_names(tmp_path: Path) -> None:
    frames, masks = tmp_path / "frames", tmp_path / "masks"
    frames.mkdir()
    cv2.imwrite(str(frames / "000000.jpg"), sky_frame(H // 2))
    cv2.imwrite(str(frames / "000001.jpg"), textured(H, W))
    stats = write_masks(frames, ["000000.jpg", "000001.jpg", "missing.jpg"], masks)
    assert sorted(p.name for p in masks.iterdir()) == ["000000.jpg.png", "000001.jpg.png"]
    assert (stats["sky"], stats["no_sky"], stats["cap"], stats["unreadable"]) == (1, 1, 0, 1)
    m = cv2.imread(str(masks / "000000.jpg.png"), cv2.IMREAD_GRAYSCALE)
    assert m is not None and m.shape == (H, W) and (m[0] == 0).all() and (m[-1] == 255).all()
