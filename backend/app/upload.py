"""Resumable video upload into a Run's directory (protocol: docs/architecture.md, ADR 0021).

POST  /api/inspections/{id}/video            {name, size}         -> {upload_id, run_id}
PUT   /api/inspections/{id}/video/chunks?upload_id=&offset=  raw bytes -> {next_offset}
GET   /api/inspections/{id}/video/chunks?upload_id=           -> {next_offset}
POST  /api/inspections/{id}/video/complete   {upload_id, sha256}  -> {run_id, file, size, sha256}

Bytes go to <QARAU_RUNS_DIR>/<run_id>/video.<ext>.part; `complete` verifies size + sha256 and
renames it to video.<ext>. The .part file's size is the truth for next_offset; the row mirrors it.
"""

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .crud import Db, Engineer, Viewer, audit, get_or_404
from .models import Inspection, Run, VideoUpload, new_id
from .settings import Settings, get_settings

router = APIRouter(prefix="/api/inspections/{inspection_id}/video")
Cfg = Annotated[Settings, Depends(get_settings)]
EXT = re.compile(r"[a-z0-9]{1,8}")


class StartIn(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="Original file name")
    size: int = Field(gt=0, description="Total size in bytes")


class StartOut(BaseModel):
    upload_id: int
    run_id: str


class Offset(BaseModel):
    next_offset: int


class CompleteIn(BaseModel):
    upload_id: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompleteOut(BaseModel):
    run_id: str
    file: str
    size: int
    sha256: str


def ext_of(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lstrip(".").lower()
    return suffix if EXT.fullmatch(suffix) else "bin"


def run_path(settings: Settings, run: Run) -> Path:
    return Path(settings.qarau_runs_dir) / run.run_dir


def get_upload(db: Session, inspection_id: int, upload_id: int) -> tuple[VideoUpload, Run]:
    up = db.get(VideoUpload, upload_id)
    run = db.get(Run, up.run_id) if up is not None else None
    if up is None or run is None or run.inspection_id != inspection_id:
        raise HTTPException(404, "upload not found")
    return up, run


def part_size(part: Path) -> int:
    return part.stat().st_size if part.exists() else 0


@router.post(
    "",
    status_code=201,
    description="Role: engineer. Start a resumable video upload: creates the Run and its "
    "directory under QARAU_RUNS_DIR and returns the upload id.",
)
def start_upload(
    inspection_id: int, body: StartIn, db: Db, who: Engineer, request: Request, settings: Cfg
) -> StartOut:
    get_or_404(db, Inspection, inspection_id)
    run_id = new_id()
    run = Run(id=run_id, inspection_id=inspection_id, run_dir=run_id, status="uploading")
    up = VideoUpload(run_id=run_id, filename=body.name, ext=ext_of(body.name), size=body.size)
    run_path(settings, run).mkdir(parents=True, exist_ok=True)
    db.add_all([run, up])
    db.flush()
    audit(db, who, request, "video_upload", up.id, "start")
    db.commit()
    return StartOut(upload_id=up.id, run_id=run_id)


@router.put(
    "/chunks",
    description="Role: engineer. Append the raw request body at `offset`. A chunk that was "
    "already received (offset + length <= next_offset) is acknowledged without writing; an "
    "offset past next_offset is 409. Returns the next expected offset.",
)
async def put_chunk(
    inspection_id: int,
    upload_id: int,
    request: Request,
    db: Db,
    who: Engineer,
    settings: Cfg,
    offset: Annotated[int, Query(ge=0)],
) -> Offset:
    up, run = get_upload(db, inspection_id, upload_id)
    if up.status != "uploading":
        raise HTTPException(409, "upload already complete")
    data = await request.body()
    part = run_path(settings, run) / f"video.{up.ext}.part"
    have = part_size(part)
    end = offset + len(data)
    if offset == have:
        if end > up.size:
            raise HTTPException(409, f"chunk ends at {end}, past the declared size {up.size}")
        with part.open("ab") as f:
            f.write(data)
        have = end
    elif end > have:  # not a plain re-send of stored bytes
        raise HTTPException(409, f"expected offset {have}, got {offset}")
    up.next_offset = have
    db.commit()
    return Offset(next_offset=have)


@router.get("/chunks", description="Role: viewer. Next expected byte offset of an upload.")
def next_offset(inspection_id: int, upload_id: int, db: Db, _: Viewer, settings: Cfg) -> Offset:
    up, run = get_upload(db, inspection_id, upload_id)
    if up.status != "uploading":
        return Offset(next_offset=up.size)
    return Offset(next_offset=part_size(run_path(settings, run) / f"video.{up.ext}.part"))


@router.post(
    "/complete",
    description="Role: engineer. Finish an upload: the size and sha256 must match, then the "
    "file becomes <run>/video.<ext>. On a mismatch: 422, the partial file is kept.",
)
def complete_upload(
    inspection_id: int, body: CompleteIn, db: Db, who: Engineer, request: Request, settings: Cfg
) -> CompleteOut:
    up, run = get_upload(db, inspection_id, body.upload_id)
    if up.status != "uploading":
        raise HTTPException(409, "upload already complete")
    part = run_path(settings, run) / f"video.{up.ext}.part"
    have = part_size(part)
    if have != up.size:
        raise HTTPException(422, f"upload incomplete: {have} of {up.size} bytes")
    digest = hashlib.sha256()
    with part.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != body.sha256:
        raise HTTPException(422, "sha256 mismatch; partial file kept, re-upload the bad chunks")
    final = part.with_name(f"video.{up.ext}")
    part.rename(final)
    up.sha256, up.status, up.next_offset = body.sha256, "complete", up.size
    run.status = "uploaded"
    audit(db, who, request, "video_upload", up.id, "complete")
    db.commit()
    return CompleteOut(run_id=run.id, file=final.name, size=up.size, sha256=body.sha256)
