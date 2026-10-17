import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .auth import require_role
from .settings import get_settings

router = APIRouter(prefix="/api/runs", dependencies=[Depends(require_role("viewer"))])
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CACHE = {"Cache-Control": "public, max-age=3600"}


def run_dir(run_id: str) -> Path:
    if not RUN_ID.fullmatch(run_id):
        raise HTTPException(404, "run not found")
    root = Path(get_settings().qarau_runs_dir).resolve()
    d = (root / run_id).resolve()
    if not d.is_relative_to(root) or not d.is_dir():
        raise HTTPException(404, "run not found")
    return d


def read_manifest(d: Path) -> dict[str, Any]:
    f = d / "web" / "manifest.json"
    if not f.is_file():
        raise HTTPException(404, "manifest not found")
    data: dict[str, Any] = json.loads(f.read_text())
    return data


def exported_at(d: Path) -> str:
    """Fallback for runs exported before the manifest had created_at: the file's mtime."""
    t = (d / "web" / "manifest.json").stat().st_mtime
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


@router.get("")
def list_runs() -> list[dict[str, Any]]:
    root = Path(get_settings().qarau_runs_dir)
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if RUN_ID.fullmatch(d.name) and (d / "web" / "manifest.json").is_file():
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


@router.get("/{run_id}/manifest.json")
def manifest(run_id: str) -> dict[str, Any]:
    return read_manifest(run_dir(run_id))


@router.get("/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str) -> FileResponse:
    d = run_dir(run_id)
    f = (d / path).resolve()
    if not f.is_relative_to(d) or not f.is_file():
        raise HTTPException(404, "file not found")
    # Typed arrays (.f32, .u8, ...) have no registered type, so they come back as octet-stream.
    return FileResponse(f, headers=CACHE)
