"""storage.py against moto's in-memory S3: layout keys, round trip, listing, signed URLs, the
immutable video rule. No endpoint is set, so moto intercepts every call (never a real server)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from moto import mock_aws

from qarau_pipeline import storage as st
from qarau_pipeline.storage import S3Storage, artifact_key, key_for, storage_from_env

ENV = {"QARAU_S3_BUCKET": "qarau-test", "QARAU_S3_ACCESS_KEY": "testing",
       "QARAU_S3_SECRET_KEY": "testing", "QARAU_S3_REGION": "us-east-1"}  # fmt: skip


@pytest.fixture
def s3() -> Iterator[S3Storage]:
    with mock_aws():
        s = storage_from_env(ENV)
        assert s is not None
        yield s


def test_artifact_key_layout() -> None:
    assert (
        artifact_key("acme", "site-1", "insp-1", "video", "IMG_8315.MOV")
        == "acme/site-1/insp-1/video/IMG_8315.MOV"
    )
    assert artifact_key("1", "2", "3", "bundle", "web/m.json") == "1/2/3/bundle/web/m.json"
    with pytest.raises(ValueError, match="unknown artifact kind 'colmap'"):
        artifact_key("acme", "site-1", "insp-1", "colmap", "x")
    assert st.local_prefix("notre-dame") == "_local/_local/notre-dame"
    assert st.KINDS == ("video", "frames", "sparse", "dense", "bundle")


def test_key_for_maps_run_paths_and_rejects_traversal() -> None:
    p = "_local/_local/r1"
    assert key_for(p, "export/web/manifest.json") == "_local/_local/r1/bundle/web/manifest.json"
    assert key_for(p, "frames/000012.jpg") == "_local/_local/r1/frames/000012.jpg"
    assert key_for(p, "sparse/sparse/0/p.bin") == "_local/_local/r1/sparse/sparse/0/p.bin"
    assert key_for(p, "dense/fused.ply") == "_local/_local/r1/dense/fused.ply"
    for bad in ("web/manifest.json", "video.mp4", "export", "export/../x", "export//x",
                "export/./x", "../export/web/manifest.json", "frames/"):  # fmt: skip
        assert key_for(p, bad) is None, bad


def test_put_get_round_trip_and_exists(s3: S3Storage, tmp_path: Path) -> None:
    key = "acme/site-1/insp-1/frames/frames.json"
    assert s3.exists(key) is False
    s3.put(key, b'{"frames": []}')
    assert s3.exists(key) is True
    s3.get(key, tmp_path / "a/b/frames.json")
    assert (tmp_path / "a/b/frames.json").read_bytes() == b'{"frames": []}'
    assert sorted(p.name for p in (tmp_path / "a/b").iterdir()) == ["frames.json"]  # no .part
    src = tmp_path / "cloud.ply"
    src.write_bytes(b"ply\n" + bytes(range(256)))
    s3.put("acme/site-1/insp-1/bundle/cloud.ply", src)
    assert b"".join(s3.stream("acme/site-1/insp-1/bundle/cloud.ply")) == src.read_bytes()
    with pytest.raises(KeyError):
        s3.stream("acme/site-1/insp-1/bundle/missing.ply")


def test_list_is_sorted_and_delete_removes(s3: S3Storage) -> None:
    for name in ("000002.jpg", "000000.jpg", "frames.json", "000001.jpg"):
        s3.put(f"acme/site-1/insp-1/frames/{name}", b"x")
    s3.put("acme/site-1/insp-2/frames/000000.jpg", b"y")
    assert s3.list("acme/site-1/insp-1/frames/") == [
        "acme/site-1/insp-1/frames/000000.jpg",
        "acme/site-1/insp-1/frames/000001.jpg",
        "acme/site-1/insp-1/frames/000002.jpg",
        "acme/site-1/insp-1/frames/frames.json",
    ]
    assert s3.list("acme/site-1/insp-1/sparse/") == []
    s3.delete("acme/site-1/insp-1/frames/000001.jpg")
    assert s3.exists("acme/site-1/insp-1/frames/000001.jpg") is False
    assert len(s3.list("acme/site-1/insp-1/frames/")) == 3


def test_signed_url(s3: S3Storage) -> None:
    key = "acme/site-1/insp-1/bundle/web/points.position.f32"
    s3.put(key, b"\x00\x01")
    url = s3.signed_url(key, 300)
    assert url.startswith("https://") and "/qarau-test/" + key + "?" in url
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url
    assert "X-Amz-Expires=60" in s3.signed_url(key, 60)


def test_video_is_immutable(s3: S3Storage) -> None:
    key = "acme/site-1/insp-1/video/IMG_8315.MOV"
    s3.put(key, b"first")
    with pytest.raises(FileExistsError, match="videos are immutable"):
        s3.put(key, b"second")
    assert b"".join(s3.stream(key)) == b"first"
    s3.put("acme/site-1/insp-1/bundle/web/manifest.json", b"v1")
    s3.put("acme/site-1/insp-1/bundle/web/manifest.json", b"v2")  # rebuilt kinds may change
    assert b"".join(s3.stream("acme/site-1/insp-1/bundle/web/manifest.json")) == b"v2"


def test_storage_from_env_is_none_without_a_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QARAU_S3_BUCKET", raising=False)
    monkeypatch.setattr("boto3.client", lambda *a, **k: pytest.fail("boto3 touched"))
    assert storage_from_env() is None
    assert storage_from_env({"QARAU_S3_BUCKET": ""}) is None
    assert "QARAU_S3_BUCKET" not in os.environ
