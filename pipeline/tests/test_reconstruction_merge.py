from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qarau_pipeline import reconstruction as rc
from qarau_pipeline.cli import main
from qarau_pipeline.interfaces import StageError


def imgs(a: int, b: int) -> set[str]:
    return {f"{i:06d}.jpg" for i in range(a, b)}


class FakeRec:
    """pycolmap.Reconstruction stand-in: registered image names + counters."""

    def __init__(self, names: set[str], pts: int = 1000) -> None:
        self.names, self.pts = sorted(names), pts

    def reg_image_ids(self) -> list[int]:
        return [i for i, n in enumerate(self.names) if n]

    points3D: dict[int, Any] = {}  # noqa: RUF012 - the point filter sees an empty model

    def update_point_3d_errors(self) -> None:
        pass

    def image(self, i: int) -> SimpleNamespace:
        return SimpleNamespace(name=self.names[i], frame_id=i, projection_center=list)

    def deregister_frame(self, frame_id: int) -> None:
        self.names[frame_id] = ""  # ids stay stable while iterating

    def reg_names(self) -> set[str]:
        return {n for n in self.names if n}

    def num_reg_images(self) -> int:
        return len(self.reg_image_ids())

    def num_points3D(self) -> int:
        return self.pts

    def compute_mean_track_length(self) -> float:
        return 3.0

    def compute_mean_reprojection_error(self) -> float:
        return 0.5

    def write_binary(self, path: str) -> None:
        (Path(path) / "points3D.bin").write_bytes(b"")


NAMES = sorted(imgs(0, 100))


def test_largest_and_leftovers() -> None:
    sets = {0: imgs(0, 30), 1: imgs(25, 95), 2: imgs(95, 97)}
    assert rc.largest(sets) == 1
    assert rc.largest({3: imgs(0, 5), 1: imgs(10, 15)}) == 1  # tie: lowest key
    assert rc.leftovers(NAMES, sets[1]) == sorted(imgs(0, 25) | imgs(95, 100))


def test_merge_plan() -> None:
    one_full = {0: imgs(0, 100)}
    assert rc.merge_plan(one_full, NAMES) == (0, [], False)
    one_few_left = {0: imgs(0, 95)}  # a few blurred frames: not worth a second mapping run
    assert rc.merge_plan(one_few_left, NAMES)[2] is False
    one_many_left = {0: imgs(0, 80)}
    assert rc.merge_plan(one_many_left, NAMES)[2] is True
    split = {0: imgs(0, 30), 1: imgs(25, 99)}
    key, left, attempt = rc.merge_plan(split, NAMES)
    assert (key, attempt) == (1, True) and left == sorted(imgs(0, 25) | imgs(99, 100))


def test_separate_models_counts_only_unabsorbed() -> None:
    sets = {0: imgs(0, 30), 1: imgs(25, 95), 2: imgs(95, 97)}
    # nothing merged: model 0 has 25 own images (separate), model 2 only 2 (below a model)
    assert rc.separate_models(sets, 1, sets[1]) == [0]
    # after leftover registration the main model holds 0..98: model 0 is absorbed
    assert rc.separate_models(sets, 1, imgs(0, 98)) == []
    # partly absorbed: 12 of model 0's images still outside -> still its own model
    assert rc.separate_models(sets, 1, imgs(12, 98)) == [0]


def test_unknown_mapper_refused() -> None:
    assert rc.ColmapReconstructor(mapper="global").mapper == "global"
    with pytest.raises(StageError, match="unknown mapper 'glomap'"):
        rc.ColmapReconstructor(mapper="glomap")
    with pytest.raises(SystemExit):  # argparse choices
        main(["run", "--video", "x.mp4", "--out", "o", "--mapper", "sequential"])


def test_pick_prefers_mapper_output_on_tie() -> None:
    kept = {"mapped": imgs(0, 70), "global_join": imgs(0, 70), "leftover_registration": imgs(0, 9)}
    assert rc.pick(kept) == "mapped"
    kept["global_join"] = imgs(0, 71)
    assert rc.pick(kept) == "global_join"


def stub_pycolmap(result: dict[int, FakeRec]) -> tuple[Any, dict[str, Any]]:
    calls: dict[str, Any] = {}

    def incremental_mapping(db: Path, img: Path, out: Path, opts: Any, input_path: Path) -> Any:
        calls["map"] = (db, img, out, opts, input_path)
        return result

    pc = SimpleNamespace(
        IncrementalPipelineOptions=lambda: SimpleNamespace(
            multiple_models=True,
            mapper=SimpleNamespace(
                abs_pose_min_num_inliers=30, abs_pose_min_inlier_ratio=0.25, max_reg_trials=3
            ),
        ),
        incremental_mapping=incremental_mapping,
        bundle_adjustment=lambda rec, opts: calls.setdefault("ba", (rec, opts)),
        BundleAdjustmentOptions=lambda: SimpleNamespace(
            print_summary=True, ceres=SimpleNamespace(loss_function_type="TRIVIAL")
        ),
        LossFunctionType=SimpleNamespace(HUBER="HUBER"),
    )
    return pc, calls


def test_register_leftovers_relaxed(tmp_path: Path) -> None:
    big = FakeRec(imgs(0, 98))
    pc, calls = stub_pycolmap({0: FakeRec(imgs(0, 3)), 1: big})
    out = tmp_path / "ext"
    rec = rc.register_leftovers(pc, tmp_path / "db", tmp_path / "f", tmp_path / "main", out)
    assert rec is big and out.is_dir()
    _, _, mapped_out, opts, input_path = calls["map"]
    assert (mapped_out, input_path) == (out, tmp_path / "main")  # continues the main model
    assert opts.multiple_models is False
    assert opts.mapper.abs_pose_min_num_inliers < 30
    assert opts.mapper.abs_pose_min_inlier_ratio < 0.25
    assert opts.mapper.max_reg_trials > 3


def test_register_leftovers_nothing_built(tmp_path: Path) -> None:
    pc, _ = stub_pycolmap({})
    assert rc.register_leftovers(pc, tmp_path, tmp_path, tmp_path, tmp_path / "ext") is None


def test_drop_bridges_then_global_ba() -> None:
    rec = FakeRec(imgs(0, 98) | {"b1.jpg", "b2.jpg"})  # two bridge frames got registered
    pc, calls = stub_pycolmap({})
    assert rc.drop_bridges(pc, rec, {"b1.jpg", "b2.jpg", "b3.jpg"}) == 2
    assert rec.reg_names() == imgs(0, 98) and calls["ba"][0] is rec
    assert calls["ba"][1].ceres.loss_function_type == "HUBER"  # robust, like GLOMAP's own BA


def test_no_bridge_in_model_no_second_ba() -> None:
    rec = FakeRec(imgs(0, 98))
    pc, calls = stub_pycolmap({})
    assert rc.drop_bridges(pc, rec, {"b1.jpg"}) == 0 and "ba" not in calls


def patch_colmap(
    monkeypatch: pytest.MonkeyPatch,
    mapped: dict[int, FakeRec],
    joined: dict[int, FakeRec],
    extended: dict[int, FakeRec],
) -> dict[str, int]:
    """pycolmap with canned models: first mapping, global join, leftover continuation."""
    pc, n = rc.pycolmap, {"incremental": 0, "global": 0, "extend": 0, "extract": 0, "match": 0}

    def count(key: str, ret: Any = None) -> Any:
        def f(*a: Any, **k: Any) -> Any:
            n[key] += 1
            return ret

        return f

    monkeypatch.setattr(pc, "extract_features", count("extract"))
    monkeypatch.setattr(pc, "match_exhaustive", count("match"))
    monkeypatch.setattr(pc, "bundle_adjustment", lambda *a, **k: None)

    def incremental(*a: Any, **k: Any) -> dict[int, FakeRec]:
        return count("extend", extended)() if "input_path" in k else count("incremental", mapped)()

    monkeypatch.setattr(pc, "incremental_mapping", incremental)
    # the first global call is the mapping itself when mapper="global", else the join
    monkeypatch.setattr(pc, "global_mapping", lambda *a, **k: count("global", joined)())
    return n


SPLIT = {0: FakeRec(imgs(0, 30)), 1: FakeRec(imgs(25, 95))}


def test_global_join_with_bridges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    joined = {0: FakeRec(imgs(0, 100) | {"b1.jpg"}, 5000)}
    n = patch_colmap(monkeypatch, SPLIT, joined, {})
    m = rc.ColmapReconstructor().reconstruct(tmp_path, NAMES, tmp_path / "colmap", ["b1.jpg"])
    # bridges extracted + matched in a second pass; no leftovers -> no continuation
    assert n == {"incremental": 1, "global": 1, "extend": 0, "extract": 2, "match": 2}
    assert (m["models"], m["registered_images"], m["points"]) == (1, 100, 5000)
    assert m["mapped_models"] == {"0": 30, "1": 70} and m["separate_models"] == []
    assert m["merge"] == {"attempted": True, "leftover_before": 30, "bridges": 1,
                          "candidates": {"mapped": 70, "global_join": 100},
                          "accepted": "global_join", "bridges_used": 1}  # fmt: skip
    assert sorted(p.name for p in (tmp_path / "colmap/sparse").iterdir()) == ["0"]


def test_leftover_registration_after_partial_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    joined = {0: FakeRec(imgs(0, 90))}
    n = patch_colmap(monkeypatch, SPLIT, joined, {0: FakeRec(imgs(0, 97))})
    m = rc.ColmapReconstructor().reconstruct(tmp_path, NAMES, tmp_path / "colmap")
    assert (n["global"], n["extend"]) == (1, 1)
    assert m["merge"]["candidates"] == {"mapped": 70, "global_join": 90,
                                        "leftover_registration": 97}  # fmt: skip
    assert (m["merge"]["accepted"], m["models"], m["registered_images"]) == (
        "leftover_registration", 1, 97,
    )  # fmt: skip


def test_reconstruct_keeps_unmerged_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worse = {0: FakeRec(imgs(25, 90))}
    n = patch_colmap(monkeypatch, SPLIT, SPLIT, worse)
    m = rc.ColmapReconstructor(mapper="global").reconstruct(tmp_path, NAMES, tmp_path / "c")
    # global mapper, no bridges: no second global run; the continuation is worse -> rejected
    assert (n["incremental"], n["global"], n["extend"], n["extract"]) == (0, 1, 1, 1)
    assert (m["models"], m["registered_images"], m["merge"]["accepted"]) == (2, 70, "mapped")
    assert m["separate_models"] == [{"sparse": "1", "images": 30, "not_in_main": 25}]
    assert sorted(p.name for p in (tmp_path / "c/sparse").iterdir()) == ["0", "1"]


def test_reconstruct_merge_off_is_old_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = patch_colmap(monkeypatch, SPLIT, {}, {})
    m = rc.ColmapReconstructor(merge=False).reconstruct(tmp_path, NAMES, tmp_path / "c", ["b"])
    assert (n["incremental"], n["global"], n["extract"], n["match"]) == (1, 0, 1, 1)
    assert m["merge"]["attempted"] is False and (m["models"], m["registered_images"]) == (2, 70)


@pytest.mark.parametrize("sky", [True, False])
def test_sky_masks_reach_feature_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sky: bool
) -> None:
    import cv2
    import numpy as np

    frames = tmp_path / "frames"
    frames.mkdir()
    img = np.random.default_rng(0).integers(0, 255, (60, 80, 3)).astype(np.uint8)
    img[:20] = (230, 180, 120)  # flat blue band at the top
    for n in NAMES:
        cv2.imwrite(str(frames / n), img)
    patch_colmap(monkeypatch, {0: FakeRec(imgs(0, 100))}, {}, {})
    seen: list[str] = []
    monkeypatch.setattr(
        rc.pycolmap, "extract_features", lambda *a, **k: seen.append(k["reader_options"].mask_path)
    )
    m = rc.ColmapReconstructor(sky_mask=sky).reconstruct(frames, NAMES, tmp_path / "colmap")
    masks = tmp_path / "colmap/masks"
    assert seen == [masks if sky else Path("")]  # pycolmap's default: no mask
    if sky:
        assert (masks / "000000.jpg.png").is_file() and m["sky_mask"]["sky"] == 100
    else:
        assert not masks.exists() and m["sky_mask"] is None
    with pytest.raises(SystemExit):  # the switch exists on the CLI
        main(["run", "--no-sky-mask", "--out", "o"])
