from pathlib import Path
from typing import Any

from conftest import bearer
from fastapi.testclient import TestClient

CHUNKS = [b"a" * 1000, b"b" * 1000, b"c" * 500]
TOTAL = b"".join(CHUNKS)
SHA256 = "5b0a939ca491d0a52118d3d0f3814d3c65992cc7e54eb08da6979f960b56d93d"  # of TOTAL
ENG = bearer("engineer")


def start(c: TestClient, name: str = "walkdown.MP4", size: int = 2500) -> tuple[int, str]:
    r = c.post("/api/inspections/1/video", json={"name": name, "size": size}, headers=ENG)
    assert r.status_code == 201, r.text
    return r.json()["upload_id"], r.json()["run_id"]


def put(c: TestClient, upload_id: int, offset: int, data: bytes) -> tuple[int, dict[str, Any]]:
    r = c.put(
        f"/api/inspections/1/video/chunks?upload_id={upload_id}&offset={offset}",
        content=data,
        headers={**ENG, "Content-Type": "application/octet-stream"},
    )
    return r.status_code, r.json()


def test_chunked_upload_with_one_retried_chunk(client: TestClient, runs_root: Path) -> None:
    upload_id, run_id = start(client)
    assert len(run_id) == 32
    assert client.get(
        f"/api/inspections/1/video/chunks?upload_id={upload_id}", headers=bearer("viewer")
    ).json() == {"next_offset": 0}

    assert put(client, upload_id, 0, CHUNKS[0]) == (200, {"next_offset": 1000})
    assert put(client, upload_id, 1000, CHUNKS[1]) == (200, {"next_offset": 2000})
    # Chunk 2 again (a client retry after a lost response): acknowledged, not written twice.
    assert put(client, upload_id, 1000, CHUNKS[1]) == (200, {"next_offset": 2000})
    r = client.get(f"/api/inspections/1/video/chunks?upload_id={upload_id}", headers=ENG)
    assert r.json() == {"next_offset": 2000}
    # A chunk from the future is refused with the expected offset.
    status, body = put(client, upload_id, 2500, CHUNKS[2])
    assert status == 409
    assert body["detail"] == "expected offset 2000, got 2500"
    assert put(client, upload_id, 2000, CHUNKS[2]) == (200, {"next_offset": 2500})

    # Wrong checksum: 422 and the partial file is kept.
    r = client.post(
        "/api/inspections/1/video/complete",
        json={"upload_id": upload_id, "sha256": "0" * 64},
        headers=ENG,
    )
    assert r.status_code == 422
    assert (runs_root / run_id / "video.mp4.part").read_bytes() == TOTAL
    assert not (runs_root / run_id / "video.mp4").exists()

    r = client.post(
        "/api/inspections/1/video/complete",
        json={"upload_id": upload_id, "sha256": SHA256},
        headers=ENG,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": run_id, "file": "video.mp4", "size": 2500, "sha256": SHA256}
    assert (runs_root / run_id / "video.mp4").read_bytes() == TOTAL
    assert not (runs_root / run_id / "video.mp4.part").exists()

    runs = client.get("/api/inspections/1/runs", headers=bearer("viewer")).json()
    assert runs == [
        {
            "id": run_id,
            "inspection_id": 1,
            "run_dir": run_id,
            "status": "uploaded",
            "progress": 0,
            "current_step": None,
            "error": None,
            "rq_job_id": None,
        }
    ]
    # Finished: more chunks or a second complete are refused, next_offset is the size.
    assert put(client, upload_id, 2500, b"x")[0] == 409
    r = client.get(f"/api/inspections/1/video/chunks?upload_id={upload_id}", headers=ENG)
    assert r.json() == {"next_offset": 2500}


def test_complete_before_all_bytes_is_422(client: TestClient, runs_root: Path) -> None:
    upload_id, run_id = start(client, name="clip.mov", size=3)
    assert put(client, upload_id, 0, b"ab") == (200, {"next_offset": 2})
    r = client.post(
        "/api/inspections/1/video/complete",
        json={"upload_id": upload_id, "sha256": "0" * 64},
        headers=ENG,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "upload incomplete: 2 of 3 bytes"
    assert (runs_root / run_id / "video.mov.part").read_bytes() == b"ab"


def test_chunk_past_declared_size_and_bad_ids(client: TestClient) -> None:
    upload_id, _ = start(client, name="noext", size=2)
    assert put(client, upload_id, 0, b"abc")[0] == 409  # 3 bytes into a 2-byte upload
    assert (
        client.get("/api/inspections/1/video/chunks?upload_id=999", headers=ENG).status_code == 404
    )
    # Upload id of another inspection path.
    assert (
        client.get(
            f"/api/inspections/2/video/chunks?upload_id={upload_id}", headers=ENG
        ).status_code
        == 404
    )
    r = client.post("/api/inspections/999/video", json={"name": "a.mp4", "size": 1}, headers=ENG)
    assert r.status_code == 404
