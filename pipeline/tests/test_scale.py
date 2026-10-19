"""Metric scale + gravity (ADR 0018) on synthetic scenes with known truth."""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pycolmap
import pytest

from qarau_pipeline import scale
from qarau_pipeline.cli import main
from qarau_pipeline.export import project, qvec_from_matrix, rot_from_qvec
from qarau_pipeline.interfaces import StageError, read_run

W_, H_, F_PX, K1 = 1280, 720, 900.0, 0.03
PARAMS = [F_PX, W_ / 2, H_ / 2, K1]
K_TRUE = 2.5  # metres per COLMAP unit in the synthetic scenes
A4 = np.array([[0, 0, 0], [0.297, 0, 0], [0.297, 0.210, 0], [0, 0.210, 0]])  # metres, floor
DIST = np.array([[-1.0, 0.5, 0.8], [1.2, -0.3, 0.1]])  # metres


def rx(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])


def look_at(center: np.ndarray, target: np.ndarray) -> np.ndarray:
    """cam_from_world rotation of an upright camera (no roll) at center looking at target;
    world z = up. Rows: camera x (right), y (image down), z (forward)."""
    f = (target - center) / np.linalg.norm(target - center)
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    return np.array([r, np.cross(f, r), f])


def scene(tilt_deg: float = 25.0, seed: int = 0) -> dict[str, Any]:
    """Metric truth: a 6 x 6 m floor (z = 0) with clutter on it, 8 upright cameras on a 60 deg
    arc at 1.5 m height looking at the middle (pitched down ~20 deg, so the cameras alone
    misjudge up). COLMAP world = truth / K_TRUE, rotated by tilt_deg about x."""
    rng = np.random.default_rng(seed)
    floor = np.column_stack([rng.uniform(-3, 3, (1500, 2)), np.zeros(1500)])
    clutter = rng.uniform([-1, -1, 0.2], [1, 1, 1.5], (500, 3))
    pts = np.vstack([floor, clutter])
    Q = rx(tilt_deg)
    cams = []
    for a in np.radians(np.linspace(-120, -60, 8)):
        c = np.array([4 * math.cos(a), 4 * math.sin(a), 1.5])
        R = look_at(c, np.array([0, 0, 0.3])) @ Q.T  # world = Q truth / K
        cw = Q @ c / K_TRUE
        cams.append((R, -R @ cw))
    return {"world": pts @ Q.T / K_TRUE, "cams": cams, "Q": Q, "n_floor": len(floor),
            "to_world": lambda x: np.asarray(x) @ Q.T / K_TRUE}  # fmt: skip


def pixels(s: dict[str, Any], x_metric: np.ndarray) -> list[np.ndarray]:
    """Pixel coordinates of metric points in every camera."""
    xw = s["to_world"](x_metric)
    return [project("SIMPLE_RADIAL", PARAMS, xw @ R.T + t) for R, t in s["cams"]]


def write_model(s: dict[str, Any], d: Path) -> None:
    """COLMAP text model; image i+1 = {i:06d}.jpg; a point's track = cameras that see it."""
    d.mkdir(parents=True)
    (d / "cameras.txt").write_text("1 SIMPLE_RADIAL {} {} {} {} {} {}\n".format(W_, H_, *PARAMS))
    obs: dict[int, list[tuple[int, np.ndarray]]] = {i: [] for i in range(len(s["cams"]))}
    tracks: dict[int, list[tuple[int, int]]] = {}
    for i, (R, t) in enumerate(s["cams"]):
        xc = s["world"] @ R.T + t
        px = project("SIMPLE_RADIAL", PARAMS, xc)
        ok = (xc[:, 2] > 0) & (px[:, 0] > 0) & (px[:, 0] < W_) & (px[:, 1] > 0) & (px[:, 1] < H_)
        for p in np.nonzero(ok)[0]:
            tracks.setdefault(int(p), []).append((i + 1, len(obs[i])))
            obs[i].append((int(p), px[p]))
    lines = []
    for i, (R, t) in enumerate(s["cams"]):
        q = qvec_from_matrix(R)
        lines += [f"{i + 1} {q[0]} {q[1]} {q[2]} {q[3]} {t[0]} {t[1]} {t[2]} 1 {i:06d}.jpg",
                  " ".join(f"{uv[0]} {uv[1]} {p + 1}" for p, uv in obs[i])]  # fmt: skip
    (d / "images.txt").write_text("\n".join(lines) + "\n")
    pl = []
    for p, tr in sorted(tracks.items()):
        x = s["world"][p]
        el = " ".join(f"{im} {k}" for im, k in tr)
        pl.append(f"{p + 1} {x[0]} {x[1]} {x[2]} 120 120 120 0.1 {el}")
    (d / "points3D.txt").write_text("\n".join(pl) + "\n")


def ref_doc(s: dict[str, Any], kind: str, noise_px: float = 0.0, views=(0, 3, 7)) -> dict:
    x = A4 if kind == "a4" else DIST
    px = pixels(s, x)
    rng = np.random.default_rng(7)
    points = []
    for k in range(len(x)):
        o = []
        for v in views:
            uv = px[v][k] + rng.normal(0, noise_px, 2) if noise_px else px[v][k]
            o.append({"frame": f"{v:06d}.jpg", "x": float(uv[0]), "y": float(uv[1])})
        points.append({"id": f"p{k + 1}", "observations": o})
    doc: dict[str, Any] = {"version": 1, "kind": kind, "points": points}
    if kind == "distance":
        doc["distance_m"] = float(np.linalg.norm(DIST[0] - DIST[1]))
    return doc


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Any, set[str]]:
    s = scene()
    d = tmp_path_factory.mktemp("m") / "sparse"
    write_model(s, d)
    names = {f"{i:06d}.jpg" for i in range(len(s["cams"]) + 1)}  # 000008.jpg: unregistered
    return s, pycolmap.Reconstruction(str(d)), names


# ---------------------------------------------------------------- triangulation + scale


def test_triangulation_exact() -> None:
    rng = np.random.default_rng(1)
    X = rng.uniform(-1, 1, (20, 3)) + [0, 0, 6]
    poses = []
    for k in range(4):
        R = look_at(np.array([k - 1.5, -6.0, 1.0 + k]), np.array([0, 0, 6.0]))
        c = np.array([k - 1.5, -6.0, 1.0 + k])
        poses.append(np.column_stack([R, -R @ c]))
    for x in X:
        xy = np.array([(P[:, :3] @ x + P[:, 3])[:2] / (P[:, :3] @ x + P[:, 3])[2] for P in poses])
        assert np.linalg.norm(scale.triangulate(poses, xy) - x) / np.linalg.norm(x) < 1e-6


def test_distance_scale_within_0_1_percent(model) -> None:
    s, rec, names = model
    ref = scale.parse_reference(ref_doc(s, "distance"))
    pts, reproj = scale.triangulate_reference(rec, ref, names)
    assert np.allclose(pts, s["to_world"](DIST), atol=1e-6)  # through the radial distortion
    assert reproj < 1e-3
    assert ref.distance_m is not None
    k = scale.distance_scale(pts, ref.distance_m)
    assert abs(k / K_TRUE - 1) < 1e-3


def test_a4_scale_and_residual_cm(model) -> None:
    s, rec, names = model
    pts, _ = scale.triangulate_reference(rec, scale.parse_reference(ref_doc(s, "a4")), names)
    k, err_cm = scale.a4_scale(pts)
    assert abs(k / K_TRUE - 1) < 1e-4 and err_cm < 0.01
    k2, err2 = scale.a4_scale(pts[[1, 2, 3, 0]])  # starting on the short edge: same answer
    assert k2 == pytest.approx(k) and err2 == pytest.approx(err_cm, abs=1e-3)
    noisy = scale.parse_reference(ref_doc(s, "a4", noise_px=1.0))
    k3, err3 = scale.a4_scale(scale.triangulate_reference(rec, noisy, names)[0])
    assert err3 > err_cm and err3 > 0 and abs(k3 / K_TRUE - 1) < 0.05


# ---------------------------------------------------------------- gravity + transform


def test_floor_tilted_25_deg_ends_level() -> None:
    s = scene(tilt_deg=25.0)
    Rs = np.stack([R for R, _ in s["cams"]])
    centers = np.stack([-R.T @ t for R, t in s["cams"]])
    n_true = s["Q"] @ [0, 0, 1.0]  # floor normal in COLMAP world
    cam_err = math.degrees(math.acos(scale.estimate_up(Rs, centers) @ n_true))
    assert cam_err > 5  # pitched cameras alone are off: the plane has to fix it
    al = scale.align(s["world"], Rs, centers)
    assert al.info["gravity_source"] == "plane"
    n_new = al.A @ n_true
    assert math.degrees(math.acos(n_new[2])) < 2.0
    floor = al.apply(s["world"][: s["n_floor"]])
    assert np.abs(floor[:, 2]).max() < 1e-6  # floor at z = 0
    assert (al.apply(centers)[:, 2] > 0).all()  # cameras above it


def test_no_floor_falls_back_to_cameras() -> None:
    s = scene()
    Rs = np.stack([R for R, _ in s["cams"]])
    centers = np.stack([-R.T @ t for R, t in s["cams"]])
    blob = np.random.default_rng(3).normal(0, 0.5, (2000, 3)) + centers.mean(0)
    al = scale.align(blob, Rs, centers)
    assert al.info["gravity_source"] == "cameras" and al.metrics["floor_inliers"] == 0


def test_transform_keeps_reprojection() -> None:
    rng = np.random.default_rng(4)
    X = rng.uniform(-1, 1, (200, 3)) + [0, 0, 5]
    A = rot_from_qvec(qvec_from_matrix(rx(37) @ look_at(np.zeros(3), np.array([1.0, 2, 3]))))
    al = scale.Alignment(3.7, A, np.array([4.0, -2.0, 0.5]))
    for c in (np.array([0.0, -3, 0]), np.array([1.0, -3.5, 0.3])):
        R = look_at(c, np.array([0, 0, 5.0]))
        t = -R @ c
        before = project("SIMPLE_RADIAL", PARAMS, X @ R.T + t)
        Rn, tn = al.pose(R, t)
        after = project("SIMPLE_RADIAL", PARAMS, al.apply(X) @ Rn.T + tn)
        assert np.abs(after - before).max() < 1e-6
        assert np.allclose(Rn @ Rn.T, np.eye(3))
    assert np.allclose(al.matrix()[:3, :3] @ X[0] + al.matrix()[:3, 3], al.apply(X[:1])[0])


# ---------------------------------------------------------------- invalid references


def good() -> dict:
    o = [{"frame": "000000.jpg", "x": 10.0, "y": 20.0}, {"frame": "000001.jpg", "x": 11, "y": 21}]
    return {"version": 1, "kind": "distance", "distance_m": 1.0,
            "points": [{"id": "a", "observations": o}, {"id": "b", "observations": o}]}  # fmt: skip


def bad(**change: Any) -> dict:
    return {**good(), **change}


def one_obs() -> dict:
    d = good()
    d["points"][0]["observations"] = d["points"][0]["observations"][:1]
    return d


def same_frame() -> dict:
    d = good()
    d["points"][1]["observations"] = [{"frame": "000000.jpg", "x": 1, "y": 2}] * 2
    return d


def bad_x(v: Any) -> dict:
    d = good()
    d["points"][0]["observations"][1] = {"frame": "000001.jpg", "x": v, "y": 2}
    return d


@pytest.mark.parametrize(
    ("doc", "msg"),
    [
        (bad(version=2), "unsupported version"),
        (bad(kind="box"), "kind must be"),
        (bad(points=good()["points"] * 2), "'distance' needs exactly 2 points"),
        ({**bad(kind="a4"), "points": good()["points"]}, "'a4' needs exactly 4 points"),
        (one_obs(), "point a needs observations in at least 2 different frames"),
        (same_frame(), "point b needs observations in at least 2 different frames"),
        (bad_x(float("inf")), "point a x must be a finite number"),
        (bad_x("12"), "point a x must be a finite number"),
        (bad_x(True), "point a x must be a finite number"),
        ({k: v for k, v in good().items() if k != "distance_m"}, "distance_m must be a finite"),
        (bad(distance_m=-2), "distance_m must be > 0"),
        ([1, 2], "expected a JSON object"),
    ],
)
def test_invalid_reference_rejected(doc: Any, msg: str) -> None:
    with pytest.raises(StageError, match=msg):
        scale.parse_reference(doc)


def test_invalid_reference_files(tmp_path: Path, model) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps(bad(distance_m=float("nan"))))  # JSON NaN: Python parses it
    with pytest.raises(StageError, match="distance_m must be a finite number"):
        scale.load_reference(p)
    p.write_text("{not json")
    with pytest.raises(StageError, match="cannot read it"):
        scale.load_reference(p)
    a4 = {**ref_doc(model[0], "a4"), "distance_m": 0.3}
    with pytest.raises(StageError, match="distance_m is only for kind 'distance'"):
        scale.parse_reference(a4)
    s, rec, names = model
    for frame, msg in (("000042.jpg", "unknown frame '000042.jpg'"),
                       ("000008.jpg", "frame '000008.jpg' is not registered")):  # fmt: skip
        d = ref_doc(s, "distance")
        d["points"][1]["observations"][0]["frame"] = frame
        with pytest.raises(StageError, match=msg):
            scale.triangulate_reference(rec, scale.parse_reference(d), names)
    d = ref_doc(s, "distance")
    d["points"][0]["observations"][0]["x"] = W_ + 5.0
    with pytest.raises(StageError, match="outside frame 000000.jpg"):
        scale.triangulate_reference(rec, scale.parse_reference(d), names)


# ---------------------------------------------------------------- stage: export + scale CLI


@pytest.fixture
def run_dir(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    s = scene()
    run = tmp_path / "run"
    write_model(s, run / "colmap/sparse/0")
    frames = [{"i": i, "file": f"frames/{i:06d}.jpg", "t": float(i), "kept": True,
               "drop_reason": None, "blur": 1.0} for i in range(len(s["cams"]) + 1)]  # fmt: skip
    src = {"kind": "video", "name": "synthetic", "duration_s": 8.0, "fps": 1.0,
           "width": W_, "height": H_}  # fmt: skip
    (run / "frames.json").write_text(json.dumps({"source": src, "frames": frames}))
    main(["export", "--sparse", str(run / "colmap/sparse/0"), "--out", str(run)])
    return run, s


def bundle(run: Path) -> tuple[dict[str, Any], bytes]:
    m = json.loads((run / "web/manifest.json").read_text())
    return m, (run / m["points"]["position"]).read_bytes()


def test_scale_stage_metric_and_idempotent(run_dir, tmp_path: Path) -> None:
    run, s = run_dir
    m0, _ = bundle(run)
    w0 = m0["world"]
    assert {k: w0[k] for k in ("metric", "scale_source", "scale_factor", "gravity_source",
            "up_axis", "scale_error_cm")} == {"metric": False, "scale_source": None,
            "scale_factor": 1.0, "gravity_source": "plane", "up_axis": "+z",
            "scale_error_cm": None}  # fmt: skip
    ref = tmp_path / "a4.json"
    ref.write_text(json.dumps(ref_doc(s, "a4")))
    main(["scale", "--run", str(run), "--ref", str(ref)])
    m1, pos1 = bundle(run)
    main(["scale", "--run", str(run), "--ref", str(ref)])  # again: same result, no compounding
    m2, pos2 = bundle(run)
    assert pos1 == pos2 and m1["world"] == m2["world"] and m1["frames"] == m2["frames"]
    w = m2["world"]
    assert w["metric"] is True and w["scale_source"] == "a4" and w["gravity_source"] == "plane"
    assert w["scale_factor"] == pytest.approx(K_TRUE, rel=1e-4) and w["scale_error_cm"] < 0.01
    assert m2["points"]["count"] == m0["points"]["count"]  # point counts never change
    # metres: the A4 corners land 0.297 / 0.210 m apart on the floor (z = 0)
    W = np.array(w["world_from_colmap"]).reshape(4, 4)
    c = s["to_world"](A4) @ W[:3, :3].T + W[:3, 3]
    assert np.linalg.norm(c[1] - c[0]) == pytest.approx(0.297, abs=1e-4)
    assert np.abs(c[:, 2]).max() < 1e-4
    exp = next(x for x in read_run(run) if x["name"] == "export")
    assert exp["metrics"]["reproj_check_px"]["max"] < 0.05  # stored poses still reproject
    assert exp["metrics"]["scale_error_cm"] == w["scale_error_cm"]
    assert json.loads((run / "scale_ref.json").read_text()) == json.loads(ref.read_text())
    # a plain re-export keeps the stored reference
    main(["export", "--sparse", str(run / "colmap/sparse/0"), "--out", str(run)])
    assert bundle(run)[1] == pos1


def test_scale_stage_refuses_bad_reference_without_touching_the_run(run_dir, tmp_path) -> None:
    run, s = run_dir
    before = (run / "run.json").read_text(), bundle(run)[1]
    d = ref_doc(s, "distance")
    d["points"][0]["observations"][0]["frame"] = "000042.jpg"
    ref = tmp_path / "bad.json"
    ref.write_text(json.dumps(d))
    with pytest.raises(SystemExit) as e:
        main(["scale", "--run", str(run), "--ref", str(ref)])
    assert e.value.code == 1
    assert ((run / "run.json").read_text(), bundle(run)[1]) == before
    assert not (run / "scale_ref.json").exists()


def test_distance_reference_has_no_error_estimate(run_dir, tmp_path: Path) -> None:
    run, s = run_dir
    ref = tmp_path / "d.json"
    ref.write_text(json.dumps(ref_doc(s, "distance")))
    main(["scale", "--run", str(run), "--ref", str(ref)])
    w = bundle(run)[0]["world"]
    assert w["scale_source"] == "distance" and w["scale_error_cm"] is None
    assert w["scale_factor"] == pytest.approx(K_TRUE, rel=1e-3)
