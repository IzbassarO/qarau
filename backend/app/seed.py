"""Seed the dev organization and users admin / engineer / viewer.

    QARAU_ENV=dev QARAU_DEV_ADMIN_TOKEN=... QARAU_DEV_ENGINEER_TOKEN=... \
    QARAU_DEV_VIEWER_TOKEN=... uv run python -m app.seed

Idempotent: re-running rotates the three users' secrets to the given tokens.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_token, new_salt
from .db import engine_for
from .models import Organization, Role, User
from .settings import get_settings

DEV_ORG = "dev"


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
        seed_dev_users(db, tokens)
    print(f"seeded organization '{DEV_ORG}' with users admin, engineer, viewer")


if __name__ == "__main__":
    main()
