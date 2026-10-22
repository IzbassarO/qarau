"""reconstruction stage: COLMAP sparse model via pycolmap, in-process (ADR 0004, 0011)."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pycolmap

from .interfaces import StageError
from .point_filter import filter_points, max_reproj_error_px
from .sky import write_masks

EXHAUSTIVE_MAX = 300  # ~3 min of CPU matching on an M1 Pro
MAPPERS = ("incremental", "global")
MIN_MODEL_SIZE = 10  # COLMAP's min_model_size: fewer images left over do not make a model


def image_sets(models: dict[int, Any]) -> dict[int, set[str]]:
    return {k: {r.image(i).name for i in r.reg_image_ids()} for k, r in models.items()}


def largest(sets: dict[int, set[str]]) -> int:
    """Key of the model with the most registered images (ties: lowest key)."""
    return min(sets, key=lambda k: (-len(sets[k]), k))


def leftovers(names: list[str], main: set[str]) -> list[str]:
    return [n for n in names if n not in main]


def merge_plan(sets: dict[int, set[str]], names: list[str]) -> tuple[int, list[str], bool]:
    """(main model key, images not in it, try leftover registration?). Tried when the mapper
    split the video, or when one model misses at least a model's worth of images; a few
    blurred frames left over are not worth a second mapping run."""
    key = largest(sets)
    left = leftovers(names, sets[key])
    return key, left, bool(left) and (len(sets) > 1 or len(left) >= MIN_MODEL_SIZE)


def separate_models(sets: dict[int, set[str]], main_key: int, main: set[str]) -> list[int]:
    """Other models that still stand apart from the final main model: at least MIN_MODEL_SIZE
    of their images did not get into it. The rest were absorbed (merged)."""
    return [k for k, s in sets.items() if k != main_key and len(s - main) >= MIN_MODEL_SIZE]


def pick(kept: dict[str, set[str]]) -> str:
    """Candidate model that registers the most kept frames; ties go to the earlier one, so the
    plain mapper output ("mapped") is only replaced by a strictly better join."""
    return max(kept, key=lambda k: len(kept[k]))


def register_leftovers(pc: Any, db: Path, frames_dir: Path, main_dir: Path, out_dir: Path) -> Any:
    """Continue the model in main_dir: register every image not yet in it (the other models'
    images and the bridge frames included) with relaxed absolute-pose thresholds. None if
    nothing came out. pycolmap 4.2.1 has no MergeReconstructions binding."""
    opts = pc.IncrementalPipelineOptions()
    opts.multiple_models = False
    opts.mapper.abs_pose_min_num_inliers = 15  # default 30
    opts.mapper.abs_pose_min_inlier_ratio = 0.15  # default 0.25
    opts.mapper.max_reg_trials = 10  # default 3
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    models = pc.incremental_mapping(db, frames_dir, out_dir, opts, input_path=main_dir)
    return max(models.values(), key=lambda r: r.num_reg_images()) if models else None


def drop_bridges(pc: Any, rec: Any, bridges: set[str]) -> int:
    """Deregister the bridge frames (blurred: they tie the map together but are never
    evidence), then a global bundle adjustment with GLOMAP's robust Huber loss (the default
    trivial loss lets outlier points pull the poses). No bridge in the model: the model is
    already adjusted, nothing to do. Returns how many were in the model."""
    used = [rec.image(i) for i in rec.reg_image_ids() if rec.image(i).name in bridges]
    for img in used:
        rec.deregister_frame(img.frame_id)
    if used:
        opts = pc.BundleAdjustmentOptions()
        opts.print_summary = False
        opts.ceres.loss_function_type = pc.LossFunctionType.HUBER
        pc.bundle_adjustment(rec, opts)
    return len(used)


@dataclass
class ColmapReconstructor:
    """Only Reconstructor implementation. mode: 'video' (SINGLE camera) or 'images' (AUTO
    cameras). mapper: 'incremental' (COLMAP) or 'global' (GLOMAP, shipped in pycolmap).
    Exhaustive matching up to EXHAUSTIVE_MAX images, else sequential with quadratic overlap.
    A split video is joined again (_merge), with the frames the quality check dropped
    (bridges) as temporary links (ADR 0011)."""

    mode: str = "video"
    max_image_size: int = 1600
    max_num_features: int | None = None
    peak_threshold: float | None = None
    overlap: int = 20
    mapper: str = "incremental"
    merge: bool = True
    sky_mask: bool = True  # ADR 0013: no features extracted in the sky

    def __post_init__(self) -> None:
        if self.mapper not in MAPPERS:
            raise StageError(f"unknown mapper {self.mapper!r}; expected one of {MAPPERS}")

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def _extract(
        self, db: Path, frames_dir: Path, names: list[str], bridge: bool, masks: Path | None
    ) -> None:
        ext = pycolmap.FeatureExtractionOptions()
        ext.max_image_size = self.max_image_size
        if self.max_num_features:
            ext.sift.max_num_features = self.max_num_features
        if self.peak_threshold:
            ext.sift.peak_threshold = self.peak_threshold
        rd = pycolmap.ImageReaderOptions()
        rd.camera_model = "SIMPLE_RADIAL"
        if masks:
            rd.mask_path = str(masks)  # <masks>/<name>.png, 0 = no features there
        video = self.mode == "video"
        if video and bridge:
            rd.existing_camera_id = 1  # the video's single camera, not a new one per call
        pycolmap.extract_features(
            db, frames_dir, image_names=names,
            camera_mode=pycolmap.CameraMode.SINGLE if video else pycolmap.CameraMode.AUTO,
            reader_options=rd, extraction_options=ext,
        )  # fmt: skip

    def _match(self, db: Path, n: int) -> None:
        """Pairs already in the database are skipped, so a second call matches only new images."""
        if n > EXHAUSTIVE_MAX:
            # ponytail: assumes name order = capture order; quadratic overlap (i, i + 2^k)
            # catches start-end loops without loop detection (its vocabulary tree: unclear licence)
            pairing = pycolmap.SequentialPairingOptions(
                overlap=self.overlap, quadratic_overlap=True
            )
            pycolmap.match_sequential(db, pairing_options=pairing)
        else:
            pycolmap.match_exhaustive(db)

    def _merge(
        self,
        db: Path,
        frames_dir: Path,
        names: list[str],
        bridges: list[str],
        best: Any,
        work: Path,
        masks: Path | None,
    ) -> tuple[Any, dict[str, Any]]:
        """1) add the bridge frames; 2) one global (GLOMAP) mapping over all images: rotation
        averaging crosses fast pans where incremental registration finds too few 3D points;
        3) if kept frames are still missing, relaxed leftover registration into the best model
        so far; 4) keep the candidate with the most kept frames, drop bridges (BA if any)."""
        if bridges:
            self._extract(db, frames_dir, bridges, bridge=True, masks=masks)
            self._match(db, len(names) + len(bridges))
        cands: dict[str, Any] = {"mapped": best}
        if bridges or self.mapper == "incremental":  # else it is the same global run again
            (work / "joined").mkdir()
            joined = pycolmap.global_mapping(db, frames_dir, work / "joined")
            if joined:
                cands["global_join"] = max(joined.values(), key=lambda r: r.num_reg_images())
        kept = {k: image_sets({0: r})[0] & set(names) for k, r in cands.items()}
        start = pick(kept)
        if leftovers(names, kept[start]):
            (work / "main").mkdir()
            cands[start].write_binary(str(work / "main"))
            rec = register_leftovers(pycolmap, db, frames_dir, work / "main", work / "ext")
            if rec is not None:
                cands["leftover_registration"] = rec
                kept["leftover_registration"] = image_sets({0: rec})[0] & set(names)
        choice = pick(kept)
        info: dict[str, Any] = {
            "bridges": len(bridges),
            "candidates": {k: len(v) for k, v in kept.items()},  # kept frames registered
            "accepted": choice,
            "bridges_used": 0,
        }
        if choice != "mapped":
            info["bridges_used"] = drop_bridges(pycolmap, cands[choice], set(bridges))
        return cands[choice], info

    def reconstruct(
        self, frames_dir: Path, names: list[str], out_dir: Path, bridges: list[str] | None = None
    ) -> dict[str, Any]:
        """bridges: frames the quality check dropped; used only to join a split map."""
        if len(names) < 3:
            raise StageError(f"need at least 3 usable frames, got {len(names)}")
        t0 = time.perf_counter()
        db, sparse, work = out_dir / "database.db", out_dir / "sparse", out_dir / "work"
        masks = out_dir / "masks" if self.sky_mask else None
        db.unlink(missing_ok=True)
        for d in (sparse, work, out_dir / "sparse_all", out_dir / "masks"):
            shutil.rmtree(d, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        sky = write_masks(frames_dir, names + (bridges or []), masks) if masks else None
        t_sky = time.perf_counter()
        self._extract(db, frames_dir, names, bridge=False, masks=masks)
        t_feat = time.perf_counter()
        self._match(db, len(names))
        t_match = time.perf_counter()
        (work / "mapped").mkdir(parents=True)
        run = pycolmap.global_mapping if self.mapper == "global" else pycolmap.incremental_mapping
        models = run(db, frames_dir, work / "mapped")
        if not models:
            raise StageError("COLMAP could not build any model (no usable image pair / overlap)")
        sets = image_sets(models)
        main_key, left, attempt = merge_plan(sets, names)
        best: Any = models[main_key]
        main = sets[main_key]
        t_map = time.perf_counter()
        merge: dict[str, Any] = {"attempted": False, "leftover_before": len(left)}
        if self.merge and attempt:
            best, info = self._merge(db, frames_dir, names, bridges or [], best, work, masks)
            merge |= {"attempted": True, **info}
            main = image_sets({0: best})[0]
        t_merge = time.perf_counter()
        if best.num_reg_images() < 3:
            raise StageError(f"COLMAP registered only {best.num_reg_images()} images")
        counts = filter_points(best, max_reproj_error_px())
        t_filter = time.perf_counter()
        apart = separate_models(sets, main_key, main)
        (sparse / "0").mkdir(parents=True)
        best.write_binary(str(sparse / "0"))
        for i, k in enumerate(apart, 1):  # kept for inspection; export uses sparse/0 only
            (sparse / str(i)).mkdir()
            models[k].write_binary(str(sparse / str(i)))
        shutil.rmtree(work)
        t1 = time.perf_counter()
        return {
            "images_in": len(names),
            "registered_images": best.num_reg_images(),
            "models": 1 + len(apart),
            "mapper": self.mapper,
            "mapped_models": {str(k): len(s) for k, s in sets.items()},
            "separate_models": [
                {"sparse": str(i), "images": len(sets[k]), "not_in_main": len(sets[k] - main)}
                for i, k in enumerate(apart, 1)
            ],
            "merge": merge,
            "sky_mask": sky,
            **counts.asdict(),  # points_total/_dropped_*: before / by the point filter
            "points": best.num_points3D(),
            "mean_track_length": round(float(best.compute_mean_track_length()), 3),
            "mean_reproj_error_px": round(float(best.compute_mean_reprojection_error()), 3),
            "seconds": {
                "sky_mask": round(t_sky - t0, 1),
                "features": round(t_feat - t_sky, 1),
                "matching": round(t_match - t_feat, 1),
                "mapping": round(t_map - t_match, 1),
                "merge": round(t_merge - t_map, 1),
                "point_filter": round(t_filter - t_merge, 1),
                "write": round(t1 - t_filter, 1),
            },
        }
