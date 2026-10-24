"""The API's view of object storage (ADR 0024): the pipeline's `S3Storage` built from Settings,
and the storage prefix of a run (`<org>/<site>/<inspection>`, or `_local/_local/<run>` for a run
that is not in the catalog: CLI and benchmark runs served by id)."""

from functools import lru_cache

from qarau_pipeline.storage import S3Storage, local_prefix
from sqlalchemy.orm import Session

from .models import Inspection, Run, Site
from .settings import get_settings


@lru_cache
def storage() -> S3Storage | None:
    s = get_settings()
    if not s.qarau_s3_bucket:
        return None
    st = S3Storage(
        s.qarau_s3_bucket,
        s.qarau_s3_endpoint or None,
        s.qarau_s3_access_key or None,
        s.qarau_s3_secret_key or None,
        s.qarau_s3_region,
    )
    st.ensure_bucket()
    return st


def prefix_of(db: Session, run: Run) -> str:
    insp = db.get(Inspection, run.inspection_id)
    site = db.get(Site, insp.site_id) if insp is not None else None
    if insp is None or site is None:
        raise RuntimeError(f"run {run.id} has no inspection or site")
    return f"{site.org_id}/{site.id}/{insp.id}"


def prefix_for_run_id(db: Session, run_id: str) -> str:
    run = db.get(Run, run_id)
    return prefix_of(db, run) if run is not None else local_prefix(run_id)
