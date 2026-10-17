import numpy as np
import pytest
import synth

from qarau_pipeline import keyframes as kf
from qarau_pipeline.interfaces import StageError


def select(greys: list[np.ndarray], cap: int | None = None, **kw: object) -> list[int]:
    prm = kf.KeyframeParams(**kw)  # type: ignore[arg-type]
    n = len(greys)
    return kf.select(kf.track(greys), synth.times(n), [True] * n, kf.diagonal(greys[0]), prm, cap)


def test_static_camera_first_and_last_only() -> None:
    assert select(synth.static(30)) == [0, 29]


def test_translation_spaced_by_threshold() -> None:
    greys = synth.translate(40)
    fine, coarse = select(greys, parallax_frac=0.005), select(greys, parallax_frac=0.01)
    gap_f, gap_c = np.diff(fine[:-1]), np.diff(coarse[:-1])
    # ~0.8 px of parallax per frame against a 4 px / 8 px threshold (diagonal 800 px)
    assert set(gap_f) <= {4, 5, 6, 7} and set(gap_c) <= {8, 9, 10, 11, 12}
    assert 1.6 < np.mean(gap_c) / np.mean(gap_f) < 2.5
    assert len(select(greys, mode="fixed")) == 40


def test_rotation_only_gives_few_keyframes() -> None:
    greys = synth.rotate(40)  # 30 deg turn: no parallax, tracks leave the view
    keys = select(greys)
    assert keys[0] == 0 and keys[-1] == 39 and len(keys) <= 5
    # a moving camera over a scene with depth, same defaults: many more
    assert len(select(synth.translate(40))) > 3 * len(keys)


def test_max_frames_cap() -> None:
    greys = synth.translate(40)
    full = select(greys, parallax_frac=0.005)
    capped = select(greys, cap=6, parallax_frac=0.005)
    assert len(full) > 6 and len(capped) == 6
    assert capped[0] == 0 and capped[-1] == 39 and set(capped) <= set(full)
    assert select(greys, cap=6, mode="fixed") == [0, 8, 16, 23, 31, 39]


def test_unusable_frames_never_keyframes() -> None:
    greys = synth.translate(20)
    usable = [i not in (0, 5, 19) for i in range(20)]
    keys = kf.select(kf.track(greys), synth.times(20), usable, 800.0, kf.KeyframeParams())
    assert keys[0] == 1 and keys[-1] == 18 and 5 not in keys


def test_unknown_mode() -> None:
    with pytest.raises(StageError, match="keyframe mode"):
        kf.KeyframeParams(mode="every-third")
