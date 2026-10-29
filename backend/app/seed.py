"""Seed the dev organization, users admin / engineer / viewer and a demo site.

    QARAU_ENV=dev QARAU_DEV_ADMIN_TOKEN=... QARAU_DEV_ENGINEER_TOKEN=... \
    QARAU_DEV_VIEWER_TOKEN=... uv run python -m app.seed

Idempotent: re-running rotates the three users' secrets to the given tokens. The demo site
(owner, 2026-10-27: tester screenshots showed no finding because there was no site) is added
once, and only when the notre-dame-p3e run is under QARAU_RUNS_DIR. Its coverage (DEMO-cov) is
computed once by the pipeline's coverage step and stored as the S1 workflow stores it; an
existing demo site without coverage rows gets it on the next run.
"""

import sys
from pathlib import Path

import numpy as np
from qarau_pipeline.coverage import run_coverage
from qarau_pipeline.findings import load_bundle
from qarau_pipeline.workflow import load_config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import hash_token, new_salt
from .db import engine_for
from .findings import FindingIn, mark_finding
from .models import (
    AuditLog,
    CoverageRegion,
    CoverageState,
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
from .workflow import fill_coverage

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


def coverage_counts(db: Session, run_id: str) -> dict[CoverageState, int]:
    rows = db.execute(
        select(CoverageRegion.state, func.count())
        .where(CoverageRegion.run_id == run_id)
        .group_by(CoverageRegion.state)
    ).all()
    found = {CoverageState(st): int(n) for st, n in rows}
    return {st: found.get(st, 0) for st in CoverageState}


def ensure_demo_coverage(
    db: Session, settings: Settings, insp: Inspection
) -> dict[CoverageState, int] | None:
    """Region count per state of the demo run's coverage. Without stored regions: run the
    pipeline's coverage step on the run's frames and poses with the demo findings' points as the
    `finding` regions, and store them with `fill_coverage`, as S1 does. An existing
    <run>/coverage/regions.json is read, never rewritten: the run dir is shared data. No
    per-point coverage array in the bundle: S1's write_coverage_layer rewrites manifest.json.
    None, with one plain line printed, when the step fails."""
    run = db.scalar(select(Run).where(Run.inspection_id == insp.id).order_by(Run.id))
    if run is None:
        print(f"Coverage for '{DEMO_SITE}' was not computed: the inspection has no run")
        return None
    counts = coverage_counts(db, run.id)
    if sum(counts.values()):
        return counts
    run_dir = Path(settings.qarau_runs_dir) / run.run_dir
    # ponytail: a regions.json already in the run is trusted as is; delete it to recompute
    if not (run_dir / "coverage" / "regions.json").is_file():
        findings = db.scalars(
            select(Finding).where(Finding.run_id == run.id, Finding.guest_id.is_(None))
        )
        try:
            run_coverage(run_dir, load_config(), finding_points={f.id: f.points for f in findings})
        except Exception as e:  # noqa: BLE001 - any step failure is printed; the seed finishes
            print(
                f"Coverage for '{DEMO_SITE}' was not computed: the coverage step failed on run "
                f"{run.run_dir} ({type(e).__name__}: {e})"
            )
            return None
    if not fill_coverage(run.id, run_dir):
        print(f"Coverage for '{DEMO_SITE}' was not computed: {run.run_dir} has no readable "
              "coverage/regions.json")  # fmt: skip
        return None
    return coverage_counts(db, run.id)


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
        site = db.scalar(select(Site).where(Site.org_id == org.id, Site.name == DEMO_SITE))
        insp = None if site is None else db.scalar(
            select(Inspection).where(Inspection.site_id == site.id).order_by(Inspection.id)
        )  # fmt: skip
        counts = None if insp is None else ensure_demo_coverage(db, s, insp)
    print(f"seeded organization '{DEV_ORG}' with users admin, engineer, viewer")
    if demo_id is not None:
        print(f"seeded '{DEMO_SITE}': run {DEMO_RUN}, finding {demo_id}")
    else:
        print(f"'{DEMO_SITE}' not added: it exists, or {DEMO_RUN} is not under QARAU_RUNS_DIR")
    if counts is not None:
        total = sum(counts.values())
        parts = ", ".join(f"{st.value} {n} ({round(100 * n / total, 1)}%)"
                          for st, n in counts.items())  # fmt: skip
        print(f"coverage of '{DEMO_SITE}': {total} regions: {parts}")


if __name__ == "__main__":
    main()
