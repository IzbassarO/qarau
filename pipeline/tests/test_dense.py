"""Dense stage: scale/shift fit, voxel fusion helpers, dense CSR merge into bundle v2. No ML."""

import json
from pathlib import Path

import numpy as np
import pycolmap
from test_export import F, N, make_model

from qarau_pipeline.dense import PAD, fit_scale_shift, voxel_first, voxel_keys
from qarau_pipeline.export import build_csr, dense_pairs, export_bundle


def test_fit_recovers_scale_shift_with_outliers() -> None:
    rng = np.random.default_rng(0)
    z = rng.uniform(2, 20, 400)
    d = (1 / z - 0.03) / 0.5  # true: 1/z = 0.5*d + 0.03
    inv = 1 / z
    inv[:60] = rng.uniform(0.01, 0.5, 60)  # 15% gross outliers
    fit = fit_scale_shift(d, inv)
    assert abs(fit["a"] - 0.5) < 0.02 and abs(fit["b"] - 0.03) < 0.01
    clean = fit_scale_shift(d, 1 / z)
    assert clean["heldout_rel_err"] < 1e-6 and clean["n_fit"] == 320


def test_fit_rejects_degenerate() -> None:
    assert fit_scale_shift(np.ones(5), np.ones(5))["heldout_rel_err"] == np.inf
    d = np.linspace(0, 1, 50)
    assert np.isnan(fit_scale_shift(d, 1 - 0.9 * d)["a"])  # negative slope is rejected


def test_voxel_first_keeps_earliest() -> None:
    xyz = np.array([[0.1, 0.1, 0.1], [0.2, 0.2, 0.2], [5.1, 0, 0]])
    first = voxel_first(voxel_keys(xyz, np.zeros(3), 1.0))
    assert sorted(first.tolist()) == [0, 2]


def test_dense_pairs_and_csr() -> None:
    tr = np.array([[1, 3, PAD], [PAD, PAD, PAD], [0, PAD, PAD]], np.uint16)
    pt, fr = dense_pairs(tr, 10)
    assert pt.tolist() == [10, 10, 12] and fr.tolist() == [1, 3, 0]
    csr = build_csr(pt - 10, fr, 3, 4)
    assert csr["point_offsets"].tolist() == [0, 2, 2, 3]
    assert csr["first_seen"].tolist() == [1, PAD, 0]
    assert csr["frame_offsets"].tolist() == [0, 1, 2, 2, 3]
    assert csr["frame_points"].tolist() == [2, 0, 0]


def test_export_merges_dense(tmp_path: Path) -> None:
    sparse, _ = make_model(tmp_path)
    rec = pycolmap.Reconstruction(str(sparse))
    frames = [
        {"i": i, "name": f"f{i}.jpg", "file": f"frames/{i:06d}.jpg", "t": float(i),
         "kept": True, "drop_reason": None, "blur": 1.0}
        for i in range(F)
    ]  # fmt: skip
    out = tmp_path / "run"
    dense = out / "dense"
    dense.mkdir(parents=True)
    rng = np.random.default_rng(2)
    nd = 25
    np.save(dense / "points.npy", rng.uniform([-1, -1, 4], [1, 1, 6], (nd, 3)).astype(np.float32))
    np.save(dense / "colors.npy", rng.integers(0, 255, (nd, 3), dtype=np.uint8))
    trk = np.full((nd, 3), PAD, np.uint16)
    trk[:, 0] = 0
    trk[::2, 1] = 3
    np.save(dense / "tracks.npy", trk)
    (dense / "dense.json").write_text(json.dumps({"method": "test", "frames_used": 2}))
    src = {
        "kind": "images",
        "name": "x",
        "duration_s": 1.0,
        "fps": 1.0,
        "width": 640,
        "height": 480,
    }
    metrics = export_bundle(rec, frames, out, src, "run", dense)
    m = json.loads((out / "web/manifest.json").read_text())
    assert m["version"] == 2 and m["points"]["count"] == N + nd == metrics["points"]
    assert m["dense"] == {"method": "test", "frames_used": 2, "points": nd}
    s = np.fromfile(out / m["points"]["source"], "u1")
    assert s.tolist() == [0] * N + [1] * nd
    po = np.fromfile(out / m["tracks"]["point_offsets"], "<u4")
    pf = np.fromfile(out / m["tracks"]["point_frames"], "<u2")
    assert len(po) == N + nd + 1 and po[-1] == len(pf)
    assert pf[po[N] : po[N + 1]].tolist() == [0, 3] and pf[po[N + 1] : po[N + 2]].tolist() == [0]
    nobs = np.fromfile(out / m["points"]["num_obs"], "u1")
    assert nobs[N] == 2 and nobs[N + 1] == 1
    assert metrics["reproj_check_px"]["max"] < 0.05  # still checks the sparse points


def test_thin_track_keeps_first_last_and_spread() -> None:
    from qarau_pipeline.export import thin_track

    trk = np.array(
        [list(range(10, 19)) + [PAD], [3, 4, PAD] + [PAD] * 7, [5] + [PAD] * 9], np.uint16
    )
    t = thin_track(trk, 4)
    assert t[0].tolist() == [10, 13, 15, 18]  # first, spread, last
    assert t[1].tolist() == [3, 4, PAD, PAD] and t[2].tolist() == [5, PAD, PAD, PAD]


def test_bound_dense_prefers_close_cameras() -> None:
    from qarau_pipeline.export import bound_dense

    rng = np.random.default_rng(1)
    n = 20000
    xyz = rng.uniform(-1, 1, (n, 3)).astype(np.float32)
    xyz[: n // 2] += 100  # far half is seen from camera 1 at distance ~100 from origin cam
    centers = np.array([[0.0, 0, -3], [100.0, 100, 90]])
    trk = np.full((n, 2), PAD, np.uint16)
    trk[: n // 2, 0], trk[n // 2 :, 0] = 1, 0
    trk[:, 1] = trk[:, 0]
    col = np.zeros((n, 3), np.uint8)
    x, c, t = bound_dense(xyz, col, trk, 4000, 3, centers)
    assert len(x) == len(c) == len(t)
    assert abs(len(x) - 4000) < 300 and t.shape == (len(x), 3)
    near = (x[:, 0] < 50).sum()
    assert near > 2 * (len(x) - near)  # close-camera points kept at a higher rate
    x2, _, _ = bound_dense(xyz, col, trk, 4000, 3, centers)
    assert np.array_equal(x, x2)  # deterministic
    assert bound_dense(xyz, col, trk, 40000, 9)[2].shape == (n, 9)
