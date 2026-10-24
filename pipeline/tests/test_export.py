import json
from pathlib import Path

import laspy
import numpy as np
import pycolmap
import pytest

from qarau_pipeline.export import (
    export_bundle,
    project,
    qvec_from_matrix,
    rot_from_qvec,
    rotation_between,
    sanitize_points,
    write_laz_ply,
)
from qarau_pipeline.interfaces import DensePoints

F, N = 4, 40
REGISTERED = [0, 1, 3]  # frame 2 is dropped/unregistered
F_PX, CX, CY, K1 = 500.0, 320.0, 240.0, 0.02


def rx(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def make_model(tmp: Path, far: float | None = None) -> tuple[Path, dict]:
    """COLMAP text model: 3 registered cameras tilted 20 deg, points seen by 1-3 of them.
    far: put point 0 that far along the view axis (a degenerate triangulation)."""
    rng = np.random.default_rng(1)
    pts = rng.uniform([-1, -1, 4], [1, 1, 6], (N, 3))
    if far:
        pts[0] = [0.0, 0.0, far]
    Rm = rx(20)
    poses = {i: (Rm, np.array([-0.5 * k, 0.1, 0.0])) for k, i in enumerate(REGISTERED)}
    seen = {p: [i for i in REGISTERED if (p + i) % 4 != 0] for p in range(N)}
    obs: dict[int, list] = {i: [] for i in REGISTERED}
    for p in range(N):
        for i in seen[p]:
            R, t = poses[i]
            uv = project("SIMPLE_RADIAL", [F_PX, CX, CY, K1], (R @ pts[p] + t)[None])[0]
            obs[i].append((p, uv))
    d = tmp / "sparse"
    d.mkdir()
    (d / "cameras.txt").write_text(f"1 SIMPLE_RADIAL 640 480 {F_PX} {CX} {CY} {K1}\n")
    lines = []
    for i in REGISTERED:
        R, t = poses[i]
        q = qvec_from_matrix(R)
        o = " ".join(f"{uv[0]} {uv[1]} {p + 1}" for p, uv in obs[i])
        lines += [f"{i + 1} {q[0]} {q[1]} {q[2]} {q[3]} {t[0]} {t[1]} {t[2]} 1 f{i}.jpg", o]
    (d / "images.txt").write_text("\n".join(lines) + "\n")
    pl = []
    for p in range(N):
        tr = " ".join(f"{i + 1} {[q for q, _ in obs[i]].index(p)}" for i in seen[p])
        pl.append(f"{p + 1} {pts[p][0]} {pts[p][1]} {pts[p][2]} 10 20 30 0.1 {tr}")
    (d / "points3D.txt").write_text("\n".join(pl) + "\n")
    return d, {"pts": pts, "seen": seen, "poses": poses}


def run_export(tmp_path: Path, far: float | None = None):
    sparse, truth = make_model(tmp_path, far)
    rec = pycolmap.Reconstruction(str(sparse))
    frames = [
        {"i": i, "name": f"f{i}.jpg", "file": f"frames/{i:06d}.jpg", "t": i * 0.4,
         "kept": i != 2, "drop_reason": None if i != 2 else "blur", "blur": 1.0}
        for i in range(F)
    ]  # fmt: skip
    out = tmp_path / "run"
    out.mkdir()
    src = {
        "kind": "images",
        "name": "x",
        "duration_s": 1.2,
        "fps": 1.0,
        "width": 640,
        "height": 480,
    }
    metrics = export_bundle(rec, frames, out, src, "run")
    m = json.loads((out / "export/web/manifest.json").read_text())
    return out, m, truth, rec, metrics


@pytest.fixture
def bundle(tmp_path: Path):
    return run_export(tmp_path)


def test_quaternion_order_matches_pycolmap() -> None:
    R = rx(20) @ np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]])
    w, x, y, z = qvec_from_matrix(R)
    assert np.allclose(pycolmap.Rotation3d(np.array([x, y, z, w])).matrix(), R)
    assert np.allclose(rot_from_qvec(np.array([w, x, y, z])), R)


def test_rotation_between() -> None:
    a = np.array([0.3, -0.5, 0.8])
    a /= np.linalg.norm(a)
    b = np.array([0.0, 0, 1])
    assert np.allclose(rotation_between(a, b) @ a, b)
    assert np.allclose(rotation_between(b, -b) @ b, -b)


def test_csr_consistent_both_directions(bundle) -> None:
    out, m, truth, *_ = bundle
    po = np.fromfile(out / m["tracks"]["point_offsets"], "<u4")
    pf = np.fromfile(out / m["tracks"]["point_frames"], "<u2")
    fo = np.fromfile(out / m["tracks"]["frame_offsets"], "<u4")
    fp = np.fromfile(out / m["tracks"]["frame_points"], "<u4")
    assert len(po) == N + 1 and len(fo) == F + 1 and len(pf) == len(fp) == po[-1] == fo[-1]
    pairs_a = {(p, int(f)) for p in range(N) for f in pf[po[p] : po[p + 1]]}
    pairs_b = {(int(p), f) for f in range(F) for p in fp[fo[f] : fo[f + 1]]}
    assert pairs_a == pairs_b == {(p, f) for p in range(N) for f in truth["seen"][p]}
    assert fo[3] == fo[2]  # unregistered frame 2: empty range
    first = np.fromfile(out / m["points"]["first_seen"], "<u2")
    nobs = np.fromfile(out / m["points"]["num_obs"], "u1")
    assert [int(first[p]) for p in range(N)] == [min(truth["seen"][p]) for p in range(N)]
    assert [int(nobs[p]) for p in range(N)] == [len(truth["seen"][p]) for p in range(N)]


def test_frames_flags_and_stats(bundle) -> None:
    _, m, *_ = bundle
    assert [f["registered"] for f in m["frames"]] == [True, True, False, True]
    assert "pose" not in m["frames"][2] and m["frames"][2]["drop_reason"] == "blur"
    assert m["stats"]["frames_registered"] == 3 and m["stats"]["frames_dropped"] == 1
    assert m["world"]["z_range"][0] <= m["world"]["z_range"][1]


def test_up_alignment_and_round_trip(bundle) -> None:
    out, m, truth, *_ = bundle
    pos = np.fromfile(out / m["points"]["position"], "<f4").reshape(-1, 3)
    W = np.array(m["world"]["world_from_colmap"]).reshape(4, 4)
    assert np.allclose(pos.mean(axis=0), 0, atol=1e-4)  # centroid at origin
    assert np.allclose(truth["pts"] @ W[:3, :3].T + W[:3, 3], pos, atol=1e-4)
    for f in m["frames"]:
        if f["registered"]:
            R = rot_from_qvec(np.array(f["pose"]["qvec"]))
            assert np.allclose(-R[1], [0, 0, 1], atol=1e-6)  # camera up == world +Z
            assert np.allclose(-R.T @ np.array(f["pose"]["tvec"]), f["center"])


def test_reprojection_with_stored_pose(bundle) -> None:
    *_, rec, metrics = bundle
    chk = metrics["reproj_check_px"]
    assert chk["samples"] > 50 and chk["max"] < 0.05  # float32 positions only


def test_outputs_exist(bundle) -> None:
    out, *_ = bundle
    import laspy

    las = laspy.read(out / "export/cloud.laz")
    assert len(las.x) == N and (out / "export/cloud.ply").stat().st_size > 0


def test_manifest_records_when_it_was_exported(bundle) -> None:
    from datetime import UTC, datetime, timedelta

    _out, m, *_ = bundle
    t = datetime.fromisoformat(m["created_at"])
    assert t.tzinfo is not None and abs(datetime.now(UTC) - t) < timedelta(minutes=5)


def test_reexport_drops_findings(bundle, tmp_path: Path) -> None:
    out, m, _, rec, _ = bundle
    mp = out / "export/web/manifest.json"
    mp.write_text(json.dumps({**m, "version": 2, "findings": "findings/findings.json"}))
    frames = [{"i": i, "name": f"f{i}.jpg", "file": f"frames/{i:06d}.jpg", "t": 0.0,
               "kept": i != 2, "drop_reason": None, "blur": 1.0} for i in range(F)]  # fmt: skip
    export_bundle(rec, frames, out, m["source"], "run")
    again = json.loads(mp.read_text())
    assert "findings" not in again and again["version"] == 2


# ---------------------------------------------------------------- far / invalid points


def cloud(center: list[float], spread: float, n: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(2)
    xyz = np.asarray(center) + rng.uniform(-spread / 2, spread / 2, (n, 3))
    return xyz, rng.integers(0, 256, (n, 3)).astype(np.uint8)


def test_far_from_origin_round_trip(tmp_path: Path) -> None:
    xyz, rgb = cloud([5e6, 7e6, 300], 10.0)
    write_laz_ply(tmp_path, xyz, rgb)
    las = laspy.read(tmp_path / "cloud.laz")
    back = np.stack([las.x, las.y, las.z], 1)
    assert np.all(np.abs(back - xyz) <= las.header.scales)
    assert np.allclose(las.header.scales, 0.001)


def test_non_finite_dropped_and_counted() -> None:
    xyz, _ = cloud([0, 0, 0], 10.0)
    xyz[[3, 7]] = np.nan
    xyz[11, 1], xyz[12, 2] = np.inf, -np.inf
    keep, rep = sanitize_points(xyz)
    assert rep == {"non_finite": 4, "far_outliers": 0, "kept": len(xyz) - 4, "total": len(xyz)}
    assert not keep[[3, 7, 11, 12]].any() and np.isfinite(xyz[keep]).all()


def test_1e12_outlier_dropped_and_never_overflows(tmp_path: Path) -> None:
    xyz, rgb = cloud([0, 0, 0], 10.0)
    xyz[5] = [1e12, 0, 0]
    keep, rep = sanitize_points(xyz)
    assert (rep["far_outliers"], rep["kept"]) == (1, len(xyz) - 1) and not keep[5]
    write_laz_ply(tmp_path, xyz[keep], rgb[keep])
    assert len(laspy.read(tmp_path / "cloud.laz").x) == len(xyz) - 1
    write_laz_ply(tmp_path, xyz, rgb)  # even an outlier that slips through must fit in int32
    assert laspy.read(tmp_path / "cloud.laz").x.max() == pytest.approx(1e12, rel=1e-6)


def test_clean_cloud_drops_nothing() -> None:
    xyz = np.random.default_rng(3).normal(0, 5, (20000, 3))
    keep, rep = sanitize_points(xyz)
    assert keep.all() and rep["non_finite"] == rep["far_outliers"] == 0


def test_all_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="export: no valid points"):
        sanitize_points(np.full((5, 3), np.nan))


def test_dropped_points_in_metadata(bundle) -> None:
    out, m, *_, metrics = bundle
    assert m["stats"]["dropped_points"] == {"non_finite": 0, "far_outliers": 0}
    assert metrics["dropped_points"] == m["stats"]["dropped_points"]


def test_export_bundle_survives_far_point(tmp_path: Path) -> None:
    """The mykolaiv crash: a far point used to overflow the LAS int32 coordinates."""
    out, m, *_ = run_export(tmp_path, far=1e12)
    assert len(laspy.read(out / "export/cloud.laz").x) == m["points"]["count"]
    s = m["stats"]
    assert s["points"] + s["far_points"] + sum(s["dropped_points"].values()) == N
    assert s["far_points"] == 1  # 1 of 40 is > 1 %: not a sanitize outlier, still kept out


def test_far_layer_own_arrays(tmp_path: Path) -> None:
    """A point 200 units out (others at depth 4-6) goes to manifest key `far`, not the cloud."""
    out, m, truth, *_ = run_export(tmp_path, far=200.0)
    W = np.array(m["world"]["world_from_colmap"]).reshape(4, 4)
    aligned = truth["pts"] @ W[:3, :3].T + W[:3, 3]
    far = m["far"]
    assert far["count"] == m["stats"]["far_points"] == 1
    fpos = np.fromfile(out / far["position"], "<f4").reshape(-1, 3)
    fcol = np.fromfile(out / far["color"], "u1").reshape(-1, 3)
    assert np.allclose(fpos, aligned[:1], atol=1e-3) and fcol.tolist() == [[10, 20, 30]]
    # the object cloud, its tracks and the LAZ hold every other point and never the far one
    assert m["points"]["count"] == m["stats"]["points"] == N - 1
    pos = np.fromfile(out / m["points"]["position"], "<f4").reshape(-1, 3)
    assert np.allclose(pos, aligned[1:], atol=1e-4)
    assert len(np.fromfile(out / m["tracks"]["point_offsets"], "<u4")) == N
    assert len(laspy.read(out / "export/cloud.laz").x) == N - 1


def test_no_far_points_in_a_compact_scene(bundle) -> None:
    out, m, *_ = bundle
    assert m["far"]["count"] == 0 and (out / m["far"]["position"]).stat().st_size == 0
    assert m["points"]["count"] == N


# ---------------------------------------------------------------- dense layer (ADR 0017)

V1_KEYS = {"version", "run_id", "created_at", "source", "world", "points", "far", "tracks",
           "frames", "stats"}  # fmt: skip


def dense_points(truth: dict, extra: np.ndarray) -> DensePoints:
    """Dense = every sparse point twice (jittered) + the given extra rows. Row k links frames
    [k % 4, (k + 1) % 4] so a row that drifted to another point is detectable."""
    rng = np.random.default_rng(5)
    xyz = np.vstack([np.repeat(truth["pts"], 2, 0) + rng.normal(0, 0.01, (2 * N, 3)), extra])
    n = len(xyz)
    ids = np.array([[k % F, (k + 1) % F] for k in range(n)], np.uint32).ravel()
    return DensePoints(xyz, np.full((n, 3), 7, np.uint8), None,
                       np.arange(0, 2 * n + 1, 2, dtype=np.uint32), ids, "sparse_tracks", 8,
                       "fake")  # fmt: skip


def export_dense(tmp_path: Path, extra: np.ndarray):
    sparse, truth = make_model(tmp_path)
    rec = pycolmap.Reconstruction(str(sparse))
    frames = [{"i": i, "name": f"f{i}.jpg", "file": f"frames/{i:06d}.jpg", "t": float(i),
               "kept": True, "drop_reason": None, "blur": 1.0} for i in range(F)]  # fmt: skip
    out = tmp_path / "run"
    out.mkdir()
    dp = dense_points(truth, extra)
    metrics = export_bundle(rec, frames, out, {"kind": "images"}, "run", dp)
    return out, json.loads((out / "export/web/manifest.json").read_text()), dp, metrics


def test_dense_links_in_bundle(tmp_path: Path) -> None:
    out, m, dp, metrics = export_dense(tmp_path, np.zeros((0, 3)))
    assert m["version"] == 2 and V1_KEYS <= set(m)
    d = m["dense"]
    assert d["frame_links_source"] == "sparse_tracks" and d["max_frames_per_point"] == 8
    assert d["count"] == 2 * N and d["densifier"] == "fake"
    fo = np.fromfile(out / d["frame_offsets"]["path"], d["frame_offsets"]["dtype"])
    fi = np.fromfile(out / d["frame_ids"]["path"], d["frame_ids"]["dtype"])
    assert len(fo) == d["frame_offsets"]["length"] == d["count"] + 1
    assert len(fi) == d["frame_ids"]["length"] and fo[0] == 0 and fo[-1] == len(fi)
    assert (np.diff(fo.astype(np.int64)) >= 0).all() and (fi < len(m["frames"])).all()
    pos = np.fromfile(out / d["position"], "<f4").reshape(-1, 3)
    W = np.array(m["world"]["world_from_colmap"]).reshape(4, 4)
    assert np.allclose(pos, dp.xyz @ W[:3, :3].T + W[:3, 3], atol=1e-4)
    assert metrics["dense_points"] == d["linked_points"] == 2 * N


def test_dense_filtering_keeps_rows_in_step(tmp_path: Path) -> None:
    """A non-finite and a far dense point are dropped together with their link rows."""
    extra = np.array([[np.nan, 0, 0], [0.0, 0, 300.0], [0.0, 0, 5.0]])
    out, m, dp, _ = export_dense(tmp_path, extra)
    d = m["dense"]
    assert d["dropped"] == {"non_finite": 1, "far_outliers": 0, "far": 1}
    keep = np.r_[np.arange(2 * N), 2 * N + 2]  # original rows that survive
    assert d["count"] == len(keep)
    fo = np.fromfile(out / d["frame_offsets"]["path"], "<u4")
    fi = np.fromfile(out / d["frame_ids"]["path"], "<u4")
    assert len(fo) == d["count"] + 1 and fo[-1] == len(fi) and (fi < F).all()
    for row, k in enumerate(keep):  # no index drift: row `row` is original point k
        assert fi[fo[row] : fo[row + 1]].tolist() == [k % F, (k + 1) % F]
    pos = np.fromfile(out / d["position"], "<f4").reshape(-1, 3)
    W = np.array(m["world"]["world_from_colmap"]).reshape(4, 4)
    assert np.allclose(pos, dp.xyz[keep] @ W[:3, :3].T + W[:3, 3], atol=1e-4)


def test_no_dense_layer_without_dense_input(bundle) -> None:
    _, m, *_ = bundle
    assert "dense" not in m and m["version"] == 2 and set(m) == V1_KEYS
