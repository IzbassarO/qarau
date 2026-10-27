"""Seed the dev organization, users admin / engineer / viewer and a demo site.

    QARAU_ENV=dev QARAU_DEV_ADMIN_TOKEN=... QARAU_DEV_ENGINEER_TOKEN=... \
    QARAU_DEV_VIEWER_TOKEN=... uv run python -m app.seed

Idempotent: re-running rotates the three users' secrets to the given tokens. The demo site
(owner, 2026-10-27: tester screenshots showed no finding because there was no site) is added
once, and only when the notre-dame-p3e run is under QARAU_RUNS_DIR.
"""

import sys
from pathlib import Path

import numpy as np
from qarau_pipeline.findings import load_bundle
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_token, new_salt
from .db import engine_for
from .findings import FindingIn, mark_finding
from .models import (
    AuditLog,
    Finding,
    FindingClass,
    Inspection,
    Mode,
    Organization,
    Role,
    Run,
    Severity,
    Site,
    User,
)
from .runs import manifest_file
from .settings import Settings, get_settings

DEV_ORG = "dev"
DEMO_SITE = "Demo site"
DEMO_RUN = "notre-dame-p3e"  # a run dir under QARAU_RUNS_DIR (Kerege: data/runs)
DEMO_POINTS = 400  # the region: this many points around the run's most-seen point
DEMO_COMMENT = (
    "Dev seed: a region marked by app/seed.py on the notre-dame-p3e map to show the evidence "
    "panel; not an inspected defect."
)


def seed_dev_users(db: Session, tokens: dict[Role, str]) -> Organization:
    org = db.scalar(select(Organization).where(Organization.name == DEV_ORG))
    if org is None:
        org = Organization(name=DEV_ORG)
        db.add(org)
        db.flush()
    for role, token in tokens.items():
        user = db.scalar(select(User).where(User.org_id == org.id, User.name == role.value))
        if user is None:
            user = User(org_id=org.id, name=role.value, role=role)
            db.add(user)
        user.salt = new_salt()
        user.secret_hash = hash_token(token, user.salt)
    db.commit()
    return org


def seed_demo(db: Session, org: Organization, settings: Settings) -> Finding | None:
    """The "Demo site": one quick-scan inspection on the notre-dame-p3e run and one manual
    finding with its 3 evidence frames, made by `mark_finding` as POST
    /api/inspections/{id}/findings makes it (the engineer in the audit log). No decision: the
    decision log stays empty. None when the site exists or the run is not there (CI)."""
    if settings.qarau_env != "dev":
        raise RuntimeError("the demo site is dev data: QARAU_ENV must be 'dev'")
    if db.scalar(select(Site).where(Site.org_id == org.id, Site.name == DEMO_SITE)) is not None:
        return None
    run_dir = Path(settings.qarau_runs_dir) / DEMO_RUN
    if manifest_file(run_dir) is None:
        return None
    engineer = db.scalar(select(User).where(User.org_id == org.id, User.role == Role.engineer))
    if engineer is None:
        raise RuntimeError("seed the dev users first")
    bundle = load_bundle(run_dir)
    anchor = bundle.positions[int(np.argmax(np.diff(bundle.offsets)))]  # seen in most frames
    near = np.argsort(np.linalg.norm(bundle.positions - anchor, axis=1))[:DEMO_POINTS]
    site = Site(org_id=org.id, name=DEMO_SITE)
    db.add(site)
    db.flush()
    insp = Inspection(site_id=site.id, mode=Mode.quick_scan, status="done")
    db.add(insp)
    db.flush()
    # id = dir name, as an upload makes it: the viewer route /viewer/notre-dame-p3e is the run id
    db.add(Run(id=DEMO_RUN, inspection_id=insp.id, run_dir=DEMO_RUN, status="done", progress=100))
    body = FindingIn(
        class_=FindingClass.other,
        severity=Severity.watch,
        comment=DEMO_COMMENT,
        point_indices=sorted(int(i) for i in near),
    )
    finding, _, _ = mark_finding(db, settings, insp.id, body)
    db.add(
        AuditLog(
            user_id=engineer.id,
            route="python -m app.seed",
            object_type="finding",
            object_id=str(finding.id),
            action="create",
        )
    )
    db.commit()
    return finding


def main() -> None:
    s = get_settings()
    if s.qarau_env != "dev":
        sys.exit("refusing to seed: QARAU_ENV is not 'dev'")
    tokens = {
        Role.admin: s.qarau_dev_admin_token,
        Role.engineer: s.qarau_dev_engineer_token,
        Role.viewer: s.qarau_dev_viewer_token,
    }
    missing = [f"QARAU_DEV_{r.name.upper()}_TOKEN" for r, t in tokens.items() if not t]
    if missing:
        sys.exit(f"refusing to seed: missing {', '.join(missing)}")
    with Session(engine_for(s.database_url)) as db:
        org = seed_dev_users(db, tokens)
        demo = seed_demo(db, org, s)
        demo_id = None if demo is None else demo.id
    print(f"seeded organization '{DEV_ORG}' with users admin, engineer, viewer")
    if demo_id is not None:
        print(f"seeded '{DEMO_SITE}': run {DEMO_RUN}, finding {demo_id}")
    else:
        print(f"'{DEMO_SITE}' not added: it exists, or {DEMO_RUN} is not under QARAU_RUNS_DIR")


if __name__ == "__main__":
    main()
