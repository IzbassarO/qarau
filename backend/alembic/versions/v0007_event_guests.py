"""event guests

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28

DEMO1 (ADR 0034): guest access for an event (the LaunchPad demo).

- `organization.is_event`: set only at INSERT by `python -m app.event create`; a trigger rejects
  any UPDATE that changes it.
- `event_code` (SHA-256 of the code, never the code), `event_code_run` (the real runs shared
  read-only), `guest` (one sandbox; the database refuses a guest or code outside an event
  organization), and a nullable `guest_id` on `finding`, `decision` and `report`.
- The decision append-only trigger of 0001 is reworked: UPDATE stays refused; DELETE is allowed
  only for a guest's decision made by a user of that guest's event organization, so purge can
  remove guest rows and nothing else. `audit_log` keeps the 0001 trigger unchanged.

Plain ADD / DROP COLUMN on `decision`, never batch mode (see 0004). On SQLite the new
`guest_id` columns carry no foreign key (SQLite cannot add or drop a column with one in place);
Postgres has the foreign keys.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUEST_TABLES = ("finding", "decision", "report")

# A deletable decision: its guest is in an event organization and its user is in that same one.
GUEST_DECISION = (
    "SELECT 1 FROM guest g JOIN organization o ON o.id = g.organization_id "
    'JOIN "user" u ON u.org_id = o.id '
    "WHERE g.id = OLD.guest_id AND u.id = OLD.user_id AND o.is_event"
)

PG_UP = [
    """
CREATE OR REPLACE FUNCTION qarau_event_flag_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_event IS DISTINCT FROM OLD.is_event THEN
        RAISE EXCEPTION 'organization.is_event is immutable: set only at creation';
    END IF;
    RETURN NEW;
END
$$
""",
    "CREATE TRIGGER organization_is_event_immutable BEFORE UPDATE ON organization "
    "FOR EACH ROW EXECUTE FUNCTION qarau_event_flag_immutable()",
    """
CREATE OR REPLACE FUNCTION qarau_event_org_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM organization WHERE id = NEW.organization_id AND is_event) THEN
        RAISE EXCEPTION '% must belong to an event organization', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END
$$
""",
    "CREATE TRIGGER event_code_event_org_only BEFORE INSERT OR UPDATE ON event_code "
    "FOR EACH ROW EXECUTE FUNCTION qarau_event_org_only()",
    "CREATE TRIGGER guest_event_org_only BEFORE INSERT OR UPDATE ON guest "
    "FOR EACH ROW EXECUTE FUNCTION qarau_event_org_only()",
    f"""
CREATE OR REPLACE FUNCTION qarau_decision_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND EXISTS ({GUEST_DECISION}) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'decision is append-only: UPDATE and DELETE are refused';
END
$$
""",
    "DROP TRIGGER decision_append_only ON decision",
    "CREATE TRIGGER decision_append_only BEFORE UPDATE OR DELETE ON decision "
    "FOR EACH ROW EXECUTE FUNCTION qarau_decision_append_only()",
]
PG_DOWN = [
    "DROP TRIGGER decision_append_only ON decision",
    "CREATE TRIGGER decision_append_only BEFORE UPDATE OR DELETE ON decision "
    "FOR EACH ROW EXECUTE FUNCTION qarau_append_only()",
    "DROP FUNCTION qarau_decision_append_only()",
    "DROP TRIGGER organization_is_event_immutable ON organization",
    "DROP FUNCTION qarau_event_flag_immutable()",
    # event_code / guest triggers go with their tables
]

EVENT_ORG_ONLY = (
    "WHEN NOT EXISTS (SELECT 1 FROM organization WHERE id = NEW.organization_id AND is_event) "
    "BEGIN SELECT RAISE(ABORT, '{table} must belong to an event organization'); END"
)
SQLITE_UP = [
    "CREATE TRIGGER organization_is_event_immutable BEFORE UPDATE ON organization "
    "WHEN NEW.is_event IS NOT OLD.is_event "
    "BEGIN SELECT RAISE(ABORT, 'organization.is_event is immutable: set only at creation'); END",
    "CREATE TRIGGER event_code_event_org_insert BEFORE INSERT ON event_code "
    + EVENT_ORG_ONLY.format(table="event_code"),
    "CREATE TRIGGER event_code_event_org_update BEFORE UPDATE ON event_code "
    + EVENT_ORG_ONLY.format(table="event_code"),
    "CREATE TRIGGER guest_event_org_insert BEFORE INSERT ON guest "
    + EVENT_ORG_ONLY.format(table="guest"),
    "CREATE TRIGGER guest_event_org_update BEFORE UPDATE ON guest "
    + EVENT_ORG_ONLY.format(table="guest"),
    "DROP TRIGGER decision_no_delete",
    "CREATE TRIGGER decision_no_delete BEFORE DELETE ON decision "
    f"WHEN NOT EXISTS ({GUEST_DECISION}) "
    "BEGIN SELECT RAISE(ABORT, 'decision is append-only'); END",
]
SQLITE_DOWN = [
    "DROP TRIGGER decision_no_delete",
    # the 0001 statement, verbatim
    "CREATE TRIGGER decision_no_delete BEFORE DELETE ON decision "
    "BEGIN SELECT RAISE(ABORT, 'decision is append-only'); END",
    "DROP TRIGGER organization_is_event_immutable",
]


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.add_column(
        "organization",
        sa.Column("is_event", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "event_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(), sa.ForeignKey("organization.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("code_sha256", sa.CHAR(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_guests", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "organization_id"),
        sa.CheckConstraint("max_guests > 0", name="ck_event_code_max_guests"),
    )
    op.create_table(
        "event_code_run",
        sa.Column(
            "event_code_id",
            sa.Integer(),
            sa.ForeignKey("event_code.id"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(length=32), sa.ForeignKey("run.id"), primary_key=True),
    )
    op.create_table(
        "guest",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(), sa.ForeignKey("organization.id"), nullable=False
        ),
        sa.Column("event_code_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_code_id", "organization_id"], ["event_code.id", "event_code.organization_id"]
        ),
    )
    for table in GUEST_TABLES:
        fk = () if sqlite else (sa.ForeignKey("guest.id", name=f"fk_{table}_guest_id"),)
        op.add_column(table, sa.Column("guest_id", sa.Integer(), *fk, nullable=True))
    for stmt in SQLITE_UP if sqlite else PG_UP:
        op.execute(stmt)


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    for stmt in SQLITE_DOWN if sqlite else PG_DOWN:
        op.execute(stmt)
    for table in reversed(GUEST_TABLES):
        if not sqlite:
            op.drop_constraint(f"fk_{table}_guest_id", table, type_="foreignkey")
        op.drop_column(table, "guest_id")
    op.drop_table("guest")
    op.drop_table("event_code_run")
    op.drop_table("event_code")
    if not sqlite:
        op.execute("DROP FUNCTION qarau_event_org_only()")
    op.drop_column("organization", "is_event")
