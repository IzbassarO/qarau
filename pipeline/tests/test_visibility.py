"""Frame links for dense points (ADR 0017): .vis parsing, id -> frame mapping, the
sparse-track fallback, the per-point cap and best-first order."""

import struct
from pathlib import Path

import numpy as np
import pycolmap
import pytest
import synth
from test_export import make_model

from qarau_pipeline.visibility import (
    FAKE_COMMENT,
    View,
    in_view,
    load_dense,
    max_frames_per_point,
    rank_links,
    read_ply,
    read_vis,
    sparse_track_links,
    vis_links,
    workspace_frame_map,
    write_ply,
    write_vis,
)

# hand-written fused.ply.vis: 3 points seen by [5, 0], [], [1, 2, 3]
VIS = (
    struct.pack("<Q", 3)
    + struct.pack("<III", 2, 5, 0)
    + struct.pack("<I", 0)
    + struct.pack("<IIII", 3, 1, 2, 3)
)


def test_vis_parse_and_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "fused.ply.vis"
    p.write_bytes(VIS)
    off, ids = read_vis(p)
    assert off.tolist() == [0, 2, 2, 5] and ids.tolist() == [5, 0, 1, 2, 3]
    write_vis(tmp_path / "again.vis", off, ids)
    assert (tmp_path / "again.vis").read_bytes() == VIS


@pytest.mark.parametrize("raw", [VIS[:-4], VIS + struct.pack("<I", 7), b"\0" * 5])
def test_vis_truncated_or_trailing_is_an_error(tmp_path: Path, raw: bytes) -> None:
    p = tmp_path / "bad.vis"
    p.write_bytes(raw)
    with pytest.raises(ValueError):
        read_vis(p)


def test_empty_vis(tmp_path: Path) -> None:
    p = tmp_path / "e.vis"
    p.write_bytes(struct.pack("<Q", 0))
    off, ids = read_vis(p)
    assert off.tolist() == [0] and len(ids) == 0


def test_workspace_index_maps_to_frames_by_name(tmp_path: Path) -> None:
    sparse, _ = make_model(tmp_path)  # images f0 (id 1), f1 (id 2), f3 (id 4)
    rec = pycolmap.Reconstruction(str(sparse))
    frames = [{"i": 0, "name": "f3.jpg"}, {"i": 1, "name": "f0.jpg"},
              {"i": 2, "name": "other.jpg"}]  # fmt: skip
    m = workspace_frame_map(rec, frames)  # workspace order: ascending image id
    assert m.tolist() == [1, -1, 0]  # f0 -> frame 1, f1 not a bundle frame, f3 -> frame 0
    xyz = np.zeros((3, 3))
    off, ids = np.array([0, 2, 2, 5]), np.array([2, 0, 1, 0, 2], np.uint32)
    fo, fi = vis_links(off, ids, m, xyz, None, np.zeros((3, 3)), cap=8)
    rows = [fi[fo[i] : fo[i + 1]].tolist() for i in range(3)]
    assert rows == [[0, 1], [], [0, 1]]  # unknown image dropped, never a wrong frame
    with pytest.raises(ValueError, match="image index"):
        vis_links(off, np.array([0, 0, 9, 0, 0], np.uint32), m, xyz, None, np.zeros((3, 3)), 8)


def test_fallback_links_only_frames_that_see_the_point() -> None:
    s = synth.wall_scene()
    rng = np.random.default_rng(3)
    dense = np.repeat(s["xyz"], 4, 0) + rng.normal(0, 0.03, (4 * len(s["xyz"]), 3))
    fo, fi = sparse_track_links(dense, None, s["xyz"], s["offsets"], s["frames"],
                                s["views"], n_frames=len(s["views"]), cap=8)  # fmt: skip
    assert len(fo) == len(dense) + 1 and fo[-1] == len(fi)
    for i in range(len(dense)):
        for f in fi[fo[i] : fo[i + 1]]:
            assert in_view(dense[i : i + 1], s["views"][int(f)])[0], (i, int(f))
    linked = np.diff(fo) > 0
    assert linked.mean() >= 0.95
    # and the fallback finds most true views (only neighbour tracks limit it)
    truth = sum(int(np.stack([in_view(dense[i : i + 1], v) for v in s["views"].values()]).sum())
                for i in range(0, len(dense), 37))  # fmt: skip
    found = sum(int(fo[i + 1] - fo[i]) for i in range(0, len(dense), 37))
    assert found >= 0.8 * truth


def test_fallback_far_from_sparse_has_no_links() -> None:
    s = synth.wall_scene()
    fo, fi = sparse_track_links(np.array([[0.0, 0, 50.0]]), None, s["xyz"], s["offsets"],
                                s["frames"], s["views"], len(s["views"]), 8)  # fmt: skip
    assert fo.tolist() == [0, 0] and len(fi) == 0


def test_cap_and_best_first_order() -> None:
    xyz = np.array([[0.0, 0, 0], [0, 0, 0]])
    # frame f's camera sits at distance d[f] on +z, except frame 4 which looks from the side
    centers = np.array([[0, 0, 5.0], [0, 0, 1.0], [0, 0, 3.0], [0, 0, 2.0], [4, 0, 0.5]])
    pt = np.array([0, 0, 0, 0, 0, 0, 1])
    fr = np.array([0, 1, 2, 3, 4, 1, 2])  # frame 1 twice for point 0: deduplicated
    off, ids = rank_links(pt, fr, xyz, None, centers, cap=3)
    assert off.tolist() == [0, 3, 4] and ids.tolist() == [1, 3, 2, 2]  # nearest first, capped
    normals = np.array([[0, 0, 1.0], [0, 0, 1.0]])  # facing +z: the side camera ranks last
    off, ids = rank_links(pt, fr, xyz, normals, centers, cap=8)
    assert ids[: off[1]].tolist() == [0, 1, 2, 3, 4] and ids[off[1] :].tolist() == [2]


def test_cap_from_repo_config_and_validation(tmp_path: Path) -> None:
    assert max_frames_per_point() == 8
    (tmp_path / "c.yaml").write_text("dense:\n  max_frames_per_point: 0\n")
    with pytest.raises(ValueError, match="max_frames_per_point"):
        max_frames_per_point(tmp_path / "c.yaml")
    (tmp_path / "d.yaml").write_text("other: 1\n")
    assert max_frames_per_point(tmp_path / "d.yaml") == 8


def test_ply_round_trip(tmp_path: Path) -> None:
    xyz = np.array([[1.0, 2, 3], [4, 5, 6]])
    rgb = np.array([[1, 2, 3], [250, 251, 252]], np.uint8)
    nrm = np.array([[0, 0, 1.0], [1, 0, 0]])
    write_ply(tmp_path / "a.ply", xyz, rgb, nrm, FAKE_COMMENT)
    x2, c2, n2, comments = read_ply(tmp_path / "a.ply")
    assert np.allclose(x2, xyz) and (c2 == rgb).all() and n2 is not None and np.allclose(n2, nrm)
    assert comments == [FAKE_COMMENT]


def test_load_dense_picks_source(tmp_path: Path) -> None:
    sparse, truth = make_model(tmp_path)
    rec = pycolmap.Reconstruction(str(sparse))
    frames = [{"i": i, "name": f"f{i}.jpg"} for i in range(4)]
    d = tmp_path / "dense"
    d.mkdir()
    xyz = truth["pts"][:5]
    write_ply(d / "fused.ply", xyz, np.zeros((5, 3), np.uint8), np.tile([0, 0, -1.0], (5, 1)))
    dp = load_dense(d, rec, frames, cap=8)
    assert dp.links_source == "sparse_tracks" and dp.densifier == "unknown"
    # workspace indices 0, 1, 2 = images f0, f1, f3 = frames 0, 1, 3
    write_vis(d / "fused.ply.vis", np.array([0, 1, 2, 3, 3, 6]),
              np.array([0, 1, 2, 0, 1, 2], np.uint32))  # fmt: skip
    dp = load_dense(d, rec, frames, cap=8)
    assert dp.links_source == "colmap_fusion" and dp.densifier == "colmap"
    rows = [sorted(dp.frame_ids[dp.frame_offsets[i] : dp.frame_offsets[i + 1]].tolist())
            for i in range(5)]  # fmt: skip
    assert rows == [[0], [1], [3], [], [0, 1, 3]]


def test_view_rejects_points_behind_and_outside() -> None:
    v = View(np.eye(3), np.zeros(3), "PINHOLE", [100, 100, 50, 50], 100, 100)
    pts = np.array([[0, 0, 1.0], [0, 0, -1.0], [5, 0, 1.0]])
    assert in_view(pts, v).tolist() == [True, False, False]
