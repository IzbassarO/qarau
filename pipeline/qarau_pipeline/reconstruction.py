"""reconstruction stage: COLMAP sparse model via pycolmap, in-process (ADR 0004)."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pycolmap

from .interfaces import StageError

EXHAUSTIVE_MAX = 300  # ~3 min of CPU matching on an M1 Pro


@dataclass
class ColmapReconstructor:
    """Only Reconstructor implementation. mode: 'video' (SINGLE camera) or 'images' (AUTO
    cameras). Exhaustive matching up to EXHAUSTIVE_MAX images, else sequential + loop detection
    (sequential alone splits hand-held videos that revisit a place: room test 136 vs 257)."""

    mode: str = "video"
    max_image_size: int = 1600
    max_num_features: int | None = None
    peak_threshold: float | None = None
    overlap: int = 10

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def reconstruct(self, frames_dir: Path, names: list[str], out_dir: Path) -> dict[str, Any]:
        if len(names) < 3:
            raise StageError(f"need at least 3 usable frames, got {len(names)}")
        t0 = time.perf_counter()
        db, sparse = out_dir / "database.db", out_dir / "sparse"
        db.unlink(missing_ok=True)
        shutil.rmtree(sparse, ignore_errors=True)
        shutil.rmtree(out_dir / "sparse_all", ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        video = self.mode == "video"
        ext = pycolmap.FeatureExtractionOptions()
        ext.max_image_size = self.max_image_size
        if self.max_num_features:
            ext.sift.max_num_features = self.max_num_features
        if self.peak_threshold:
            ext.sift.peak_threshold = self.peak_threshold
        rd = pycolmap.ImageReaderOptions()
        rd.camera_model = "SIMPLE_RADIAL"
        pycolmap.extract_features(
            db, frames_dir, image_names=names,
            camera_mode=pycolmap.CameraMode.SINGLE if video else pycolmap.CameraMode.AUTO,
            reader_options=rd, extraction_options=ext,
        )  # fmt: skip
        t_feat = time.perf_counter()
        if len(names) > EXHAUSTIVE_MAX:
            # ponytail: image sets > EXHAUSTIVE_MAX assume name order = capture order;
            # loop detection downloads COLMAP's vocabulary tree once (bundle it for on-prem)
            pairing = pycolmap.SequentialPairingOptions(
                overlap=self.overlap, quadratic_overlap=True, loop_detection=True
            )
            pycolmap.match_sequential(db, pairing_options=pairing)
        else:
            pycolmap.match_exhaustive(db)
        t_match = time.perf_counter()
        (out_dir / "sparse_all").mkdir()
        models = pycolmap.incremental_mapping(db, frames_dir, out_dir / "sparse_all")
        if not models:
            raise StageError("COLMAP could not build any model (no usable image pair / overlap)")
        best = max(models.values(), key=lambda r: r.num_reg_images())
        shutil.rmtree(out_dir / "sparse_all")
        if best.num_reg_images() < 3:
            raise StageError(f"COLMAP registered only {best.num_reg_images()} images")
        (sparse / "0").mkdir(parents=True)
        best.write_binary(str(sparse / "0"))
        t1 = time.perf_counter()
        return {
            "images_in": len(names),
            "registered_images": best.num_reg_images(),
            "models": len(models),
            "points": best.num_points3D(),
            "mean_track_length": round(float(best.compute_mean_track_length()), 3),
            "mean_reproj_error_px": round(float(best.compute_mean_reprojection_error()), 3),
            "seconds": {
                "features": round(t_feat - t0, 1),
                "matching": round(t_match - t_feat, 1),
                "mapping": round(t1 - t_match, 1),
            },
        }
