import json
from pathlib import Path

import numpy as np
import pycolmap
import pytest

from qarau_pipeline.export import (
    export_bundle,
    project,
    qvec_from_matrix,
    rot_from_qvec,
    rotation_between,
)

F, N = 4, 40
REGISTERED = [0, 1, 3]  # frame 2 is dropped/unregistered
F_PX, CX, CY, K1 = 500.0, 320.0, 240.0, 0.02


def rx(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def make_model(tmp: Path) -> tuple[Path, dict]:
    """COLMAP text model: 3 registered cameras tilted 20 deg, points seen by 1-3 of them."""
    rng = np.random.default_rng(1)
    pts = rng.uniform([-1, -1, 4], [1, 1, 6], (N, 3))
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


@pytest.fixture
def bundle(tmp_path: Path):
    sparse, truth = make_model(tmp_path)
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
    m = json.loads((out / "web/manifest.json").read_text())
    return out, m, truth, rec, metrics


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

    las = laspy.read(out / "cloud.laz")
    assert len(las.x) == N and (out / "cloud.ply").stat().st_size > 0


def test_reexport_drops_findings(bundle, tmp_path: Path) -> None:
    out, m, _, rec, _ = bundle
    mp = out / "web/manifest.json"
    mp.write_text(json.dumps({**m, "version": 2, "findings": "findings/findings.json"}))
    frames = [{"i": i, "name": f"f{i}.jpg", "file": f"frames/{i:06d}.jpg", "t": 0.0,
               "kept": i != 2, "drop_reason": None, "blur": 1.0} for i in range(F)]  # fmt: skip
    export_bundle(rec, frames, out, m["source"], "run")
    again = json.loads(mp.read_text())
    assert "findings" not in again and again["version"] == 1
