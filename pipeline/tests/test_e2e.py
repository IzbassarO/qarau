import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import synth

from qarau_pipeline.cli import main

VIDEO = (
    Path(__file__).resolve().parents[2].parent.parent.parent
    / "data/samples/notre_dame_oubiels.webm"
)
# worktree lives in <repo>/.claude/worktrees/<name>; fall back to the env-free common location
CANDIDATES = [VIDEO, Path(__file__).resolve().parents[2] / "data/samples/notre_dame_oubiels.webm"]


def dense_links(run: Path) -> tuple[dict, np.ndarray]:
    """manifest.dense + points-with-at-least-one-frame mask."""
    d = json.loads((run / "export/web/manifest.json").read_text())["dense"]
    fo = np.fromfile(run / d["frame_offsets"]["path"], "<u4")
    assert len(fo) == d["count"] + 1
    return d, np.diff(fo) > 0


def fake_dense_export(images: Path, sparse: Path, tmp: Path) -> Path:
    """dense-remote with the fake provider (no GPU, no money), then export --dense."""
    dense, run = tmp / "dense", tmp / "run"
    main(["dense-remote", "--provider", "fake", "--frames", str(images), "--sparse", str(sparse),
          "--out", str(dense)])  # fmt: skip
    main(["export", "--sparse", str(sparse), "--images", str(images), "--out", str(run),
          "--dense", str(dense)])  # fmt: skip
    return dense


def test_fake_dense_path_links_points(tmp_path: Path) -> None:
    """Synthetic wall model (tracks = true views) -> fake dense -> bundle with links."""
    s = synth.wall_scene()
    sparse, images = tmp_path / "sparse", tmp_path / "images"
    synth.write_wall_model(s, sparse)
    images.mkdir()
    for i in s["views"]:
        cv2.imwrite(str(images / f"f{i}.jpg"), np.zeros((240, 320, 3), np.uint8))
    dense = fake_dense_export(images, sparse, tmp_path)
    d, linked = dense_links(tmp_path / "run")
    assert d["frame_links_source"] == "colmap_fusion" and d["densifier"] == "fake"
    assert d["count"] > 0 and linked.mean() >= 0.95
    # without the .vis the same points get fallback links
    (dense / "fused.ply.vis").unlink()
    main(["export", "--sparse", str(sparse), "--images", str(images), "--out",
          str(tmp_path / "run"), "--dense", str(dense)])  # fmt: skip
    d, linked = dense_links(tmp_path / "run")
    assert d["frame_links_source"] == "sparse_tracks" and linked.mean() >= 0.95


@pytest.mark.slow
def test_church_video_end_to_end(tmp_path: Path) -> None:
    video = next((p for p in CANDIDATES if p.exists()), None)
    if video is None:
        pytest.skip("sample video not present")
    main(["run", "--video", str(video), "--out", str(tmp_path)])
    m = json.loads((tmp_path / "export/web/manifest.json").read_text())
    assert m["stats"]["frames_registered"] > 20 and m["stats"]["points"] > 1000
    assert (tmp_path / "export/cloud.laz").exists()
    assert {s["name"] for s in json.loads((tmp_path / "run.json").read_text())} == {
        "frames",
        "reconstruction",
        "export",
    }
    for step in ("probe", "frames", "sparse", "export"):
        assert json.loads((tmp_path / step / "step.json").read_text())["status"] == "done"
    # the fake dense path on real footage: >= 95 % of dense points name a frame
    dense = tmp_path / "dense"
    main(["dense-remote", "--provider", "fake", "--frames", str(tmp_path / "frames"),
          "--sparse", str(tmp_path / "sparse/sparse/0"), "--out", str(dense)])  # fmt: skip
    main(["export", "--sparse", str(tmp_path / "sparse/sparse/0"), "--out", str(tmp_path),
          "--dense", str(dense)])  # fmt: skip
    d, linked = dense_links(tmp_path)
    assert d["frame_links_source"] == "colmap_fusion" and linked.mean() >= 0.95
