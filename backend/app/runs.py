"""Exported runs for the viewer. A run is served from its local cache dir under QARAU_RUNS_DIR
when the file is there, else from object storage (ADR 0024): streamed through the API, or a 302
to a short-lived signed URL when QARAU_FILES_MODE=redirect. Paths, roles and shapes are the same
in every mode, so the web app needs no change."""

import json
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from qarau_pipeline.storage import key_for

from .auth import check_run, shared_runs
from .crud import Db, ViewerOrGuest
from .models import Role
from .settings import get_settings
from .storage import prefix_for_run_id, storage

# Guests read only the runs shared with their event (check_run: any other run is 404).
router = APIRouter(prefix="/api/runs")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CACHE = {"Cache-Control": "public, max-age=3600"}
MANIFEST = "export/web/manifest.json"


def local_dir(run_id: str) -> Path | None:
    """The run's cache dir, or None when it is not there (the run may still be in storage)."""
    if not RUN_ID.fullmatch(run_id):
        raise HTTPException(404, "run not found")
    root = Path(get_settings().qarau_runs_dir).resolve()
    d = (root / run_id).resolve()
    return d if d.is_relative_to(root) and d.is_dir() else None


def run_dir(run_id: str) -> Path:
    d = local_dir(run_id)
    if d is None:
        raise HTTPException(404, "run not found")
    return d


def manifest_file(d: Path) -> Path | None:
    """<run>/export/web/manifest.json (ADR 0022), or <run>/web/manifest.json for older runs."""
    for rel in (MANIFEST, "web/manifest.json"):
        if (d / rel).is_file():
            return d / rel
    return None


def read_manifest(d: Path) -> dict[str, Any]:
    f = manifest_file(d)
    if f is None:
        raise HTTPException(404, "manifest not found")
    data: dict[str, Any] = json.loads(f.read_text())
    return data


def exported_at(d: Path) -> str:
    """Fallback for runs exported before the manifest had created_at: the file's mtime."""
    f = manifest_file(d)
    t = f.stat().st_mtime if f else 0.0
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def from_storage(db: Db, run_id: str, rel: str) -> Response:
    """Cache miss: the object for the run-relative path `rel`, streamed or as a 302."""
    st = storage()
    key = key_for(prefix_for_run_id(db, run_id), rel) if st is not None else None
    if st is None or key is None:
        raise HTTPException(404, "file not found")
    s = get_settings()
    if s.qarau_files_mode == "redirect":
        return RedirectResponse(st.signed_url(key, s.qarau_signed_url_ttl_s), status_code=302)
    try:
        chunks = st.stream(key)
    except KeyError as e:
        raise HTTPException(404, "file not found") from e
    media = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    return StreamingResponse(chunks, media_type=media, headers=CACHE)


@router.get(
    "",
    description="Role: viewer. Exported pipeline runs found under QARAU_RUNS_DIR (a guest: only "
    "the runs shared with its event).",
)
def list_runs(db: Db, who: ViewerOrGuest) -> list[dict[str, Any]]:
    root = Path(get_settings().qarau_runs_dir)
    guest = who.role == Role.guest
    shared = {n for r in shared_runs(db, who) for n in (r.id, r.run_dir)} if guest else set()
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if guest and d.name not in shared:
            continue
        if RUN_ID.fullmatch(d.name) and manifest_file(d) is not None:
            m = read_manifest(d)
            out.append(
                {
                    "run_id": d.name,
                    "source": m.get("source"),
                    "stats": m.get("stats"),
                    "created_at": m.get("created_at") or exported_at(d),
                }
            )
    return out


@router.get(
    "/{run_id}/manifest.json",
    response_model=dict[str, Any],
    description="Role: viewer. The run's viewer manifest (from the cache, or from storage: "
    "streamed, or a 302 to a signed URL with QARAU_FILES_MODE=redirect). A guest: 404 unless "
    "the run is shared with its event.",
)
def manifest(run_id: str, db: Db, who: ViewerOrGuest) -> Any:
    check_run(db, who, run_id)
    d = local_dir(run_id)
    if d is not None and manifest_file(d) is not None:
        return read_manifest(d)
    return from_storage(db, run_id, MANIFEST)


@router.get(
    "/{run_id}/files/{path:path}",
    description="Role: viewer. A file of the run (from the cache, or from storage: streamed, "
    "or a 302 to a signed URL with QARAU_FILES_MODE=redirect). A guest: 404 unless the run is "
    "shared with its event.",
)
def run_file(run_id: str, path: str, db: Db, who: ViewerOrGuest) -> Response:
    check_run(db, who, run_id)
    d = local_dir(run_id)
    if d is not None:
        f = (d / path).resolve()
        if f.is_relative_to(d) and f.is_file():
            # Typed arrays (.f32, .u8, ...) have no registered type: octet-stream.
            return FileResponse(f, headers=CACHE)
    return from_storage(db, run_id, path)
