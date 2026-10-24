"""Object storage behind the API (ADR 0024) on moto's in-memory S3: the upload lands at
<org>/<site>/<inspection>/video/<filename>; the viewer routes answer from the cache, then from
storage after the cache dir is deleted (streamed, or a 302 to a signed URL); 401 without a token.
No endpoint is set, so moto intercepts every call (never the dev Versity)."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy.orm import Session

from app.db import engine_for
from app.models import Run
from app.settings import get_settings
from app.storage import storage

BUCKET = "qarau-test"
S3_ENV = {"QARAU_S3_BUCKET": BUCKET, "QARAU_S3_ENDPOINT": "", "QARAU_S3_REGION": "us-east-1",
          "QARAU_S3_ACCESS_KEY": "testing", "QARAU_S3_SECRET_KEY": "testing"}  # fmt: skip
ENG, VIEW = bearer("engineer"), bearer("viewer")
MANIFEST = b'{"version": 2, "stats": {"points": 3}, "points": {"count": 3}}'
ARRAY = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
LOCAL = "_local/_local/local-run"  # a run served by id, outside the catalog (CLI, benchmark)
RUN_ID = "a" * 32  # a catalog run of inspection 1 (org 1, site 1)


@pytest.fixture
def s3(db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for k, v in S3_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    storage.cache_clear()
    with mock_aws():
        assert storage() is not None  # the app's own client makes the bucket
        yield boto3.client("s3", region_name="us-east-1")
    get_settings.cache_clear()
    storage.cache_clear()


def set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("QARAU_FILES_MODE", mode)
    get_settings.cache_clear()


def upload(c: TestClient, name: str = "walkdown.MP4") -> str:
    r = c.post("/api/inspections/1/video", json={"name": name, "size": 3}, headers=ENG)
    upload_id, run_id = r.json()["upload_id"], r.json()["run_id"]
    c.put(f"/api/inspections/1/video/chunks?upload_id={upload_id}&offset=0", content=b"abc",
          headers={**ENG, "Content-Type": "application/octet-stream"})  # fmt: skip
    sha = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # sha256(b"abc")
    r = c.post("/api/inspections/1/video/complete", json={"upload_id": upload_id, "sha256": sha},
               headers=ENG)  # fmt: skip
    assert r.status_code == 200, r.text
    return str(run_id)


def keys(s3: Any, prefix: str) -> list[str]:
    page = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(o["Key"] for o in page.get("Contents", []))


def write_cache(run: Path) -> None:
    (run / "export/web").mkdir(parents=True)
    (run / "export/web/manifest.json").write_bytes(MANIFEST)
    (run / "export/web/points.position.f32").write_bytes(ARRAY)


def put_bundle(s3: Any, prefix: str) -> None:
    """What the pipeline's export step pushes: the bundle under <prefix>/bundle/."""
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/bundle/web/manifest.json", Body=MANIFEST)
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/bundle/web/points.position.f32", Body=ARRAY)


def test_upload_lands_at_the_video_key(s3: Any, client: TestClient, runs_root: Path) -> None:
    run_id = upload(client)
    assert keys(s3, "1/") == ["1/1/1/video/walkdown.MP4"]  # org 1 / site 1 / inspection 1
    assert s3.get_object(Bucket=BUCKET, Key="1/1/1/video/walkdown.MP4")["Body"].read() == b"abc"
    assert (runs_root / run_id / "video.mp4").read_bytes() == b"abc"  # the cache copy stays
    # A second video for the same inspection is refused: the video is immutable (409), the
    # partial file is kept for the client.
    r = client.post("/api/inspections/1/video", json={"name": "walkdown.MP4", "size": 3},
                    headers=ENG)  # fmt: skip
    up2, run2 = r.json()["upload_id"], r.json()["run_id"]
    client.put(f"/api/inspections/1/video/chunks?upload_id={up2}&offset=0", content=b"abc",
               headers={**ENG, "Content-Type": "application/octet-stream"})  # fmt: skip
    sha = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    r = client.post("/api/inspections/1/video/complete", json={"upload_id": up2, "sha256": sha},
                    headers=ENG)  # fmt: skip
    assert r.status_code == 409 and "already stored" in r.json()["detail"]
    assert (runs_root / run2 / "video.mp4.part").exists()
    assert keys(s3, "1/") == ["1/1/1/video/walkdown.MP4"]


def test_path_in_the_file_name_is_reduced_to_its_name(s3: Any, client: TestClient) -> None:
    upload(client, name="../../etc/passwd.mov")
    assert keys(s3, "1/") == ["1/1/1/video/passwd.mov"]


def test_manifest_and_files_survive_a_wiped_cache_in_stream_mode(
    s3: Any, client: TestClient, runs_root: Path
) -> None:
    write_cache(runs_root / "local-run")
    put_bundle(s3, LOCAL)
    assert client.get("/api/runs/local-run/manifest.json", headers=VIEW).json()["stats"] == {
        "points": 3
    }
    shutil.rmtree(runs_root / "local-run")
    assert client.get("/api/runs", headers=VIEW).json() == []  # the listing is the cache only
    r = client.get("/api/runs/local-run/manifest.json", headers=VIEW)
    assert r.status_code == 200, r.text
    assert r.content == MANIFEST and r.json() == json.loads(MANIFEST)
    assert r.headers["content-type"] == "application/json"
    r = client.get("/api/runs/local-run/files/export/web/points.position.f32", headers=VIEW)
    assert r.status_code == 200
    assert r.content == ARRAY
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["cache-control"] == "public, max-age=3600"
    files = "/api/runs/local-run/files/"
    assert client.get(files + "export/web/nope.bin", headers=VIEW).status_code == 404
    assert client.get("/api/runs/no-such-run/manifest.json", headers=VIEW).status_code == 404
    for bad in ("web/manifest.json", "video.mp4", "export/%2e%2e/export/web/manifest.json",
                "export//web/x", "%2e%2e/%2e%2e/etc/passwd", "export/./web/x"):  # fmt: skip
        assert client.get(files + bad, headers=VIEW).status_code == 404, bad


def test_catalog_run_is_served_from_its_inspection_prefix(
    s3: Any, client: TestClient, db_url: str
) -> None:
    with Session(engine_for(db_url)) as db:
        db.add(Run(id=RUN_ID, inspection_id=1, run_dir=RUN_ID, status="done"))
        db.commit()
    put_bundle(s3, "1/1/1")
    r = client.get(f"/api/runs/{RUN_ID}/manifest.json", headers=VIEW)
    assert r.status_code == 200 and r.content == MANIFEST
    r = client.get(f"/api/runs/{RUN_ID}/files/export/web/points.position.f32", headers=VIEW)
    assert r.status_code == 200 and r.content == ARRAY


def test_redirect_mode_sends_a_signed_url(
    s3: Any, client: TestClient, runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_cache(runs_root / "local-run")
    put_bundle(s3, LOCAL)
    set_mode(monkeypatch, "redirect")
    # A cache hit is still served from disk.
    r = client.get("/api/runs/local-run/files/export/web/points.position.f32", headers=VIEW)
    assert r.status_code == 200 and r.content == ARRAY
    shutil.rmtree(runs_root / "local-run")
    r = client.get("/api/runs/local-run/files/export/web/points.position.f32", headers=VIEW,
                   follow_redirects=False)  # fmt: skip
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/qarau-test/_local/_local/local-run/bundle/web/points.position.f32?" in loc
    assert "X-Amz-Signature=" in loc and "X-Amz-Expires=300" in loc
    r = client.get("/api/runs/local-run/manifest.json", headers=VIEW, follow_redirects=False)
    assert r.status_code == 302
    assert "/_local/_local/local-run/bundle/web/manifest.json?" in r.headers["location"]
    assert client.get("/api/runs/local-run/files/web/x.bin", headers=VIEW,
                      follow_redirects=False).status_code == 404  # fmt: skip
    monkeypatch.setenv("QARAU_SIGNED_URL_TTL_S", "60")
    get_settings.cache_clear()
    r = client.get("/api/runs/local-run/manifest.json", headers=VIEW, follow_redirects=False)
    assert "X-Amz-Expires=60" in r.headers["location"]


def test_unauthenticated_requests_get_401(s3: Any, client: TestClient, runs_root: Path) -> None:
    put_bundle(s3, LOCAL)
    assert client.get("/api/runs/local-run/manifest.json").status_code == 401
    assert client.get("/api/runs/local-run/files/export/web/points.position.f32").status_code == 401
    assert client.get("/api/runs/local-run/manifest.json",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401  # fmt: skip


def test_without_a_bucket_storage_is_off(client: TestClient, runs_root: Path) -> None:
    assert storage() is None
    assert client.get("/api/runs/local-run/manifest.json", headers=VIEW).status_code == 404
