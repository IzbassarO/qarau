"""guest sessions

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28

DEMO1 part 2 (ADR 0034): guest sign-in.

- `guest.token_sha256`: the guest's session is its row; only the SHA-256 of the bearer token is
  stored (unique index; ADD COLUMN cannot carry UNIQUE on SQLite).
- `audit_log.user_id` becomes nullable (an anonymous request: a guest code use, which has no user
  yet and may name no event at all) and `audit_log.detail` (JSON: result, IP) is added.
  On SQLite changing nullability rebuilds the table, which drops its triggers, so the 0001
  append-only triggers are created again right after; on Postgres ALTER keeps them.
  Downgrade fails while an audit row has no user: the audit log is never edited to make it pass.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The 0001 statements, verbatim.
SQLITE_AUDIT_TRIGGERS = (
    "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
    "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
)


def audit_log(nullable: bool) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("audit_log", recreate="always") as b:
            b.alter_column("user_id", existing_type=sa.Integer(), nullable=nullable)
            if nullable:
                b.add_column(sa.Column("detail", sa.JSON(), nullable=True))
            else:
                b.drop_column("detail")
        for stmt in SQLITE_AUDIT_TRIGGERS:
            op.execute(stmt)
        return
    if nullable:
        op.add_column("audit_log", sa.Column("detail", sa.JSON(), nullable=True))
    else:
        op.drop_column("audit_log", "detail")
    op.alter_column("audit_log", "user_id", existing_type=sa.Integer(), nullable=nullable)


def upgrade() -> None:
    op.add_column("guest", sa.Column("token_sha256", sa.CHAR(length=64), nullable=True))
    op.create_index("ix_guest_token_sha256", "guest", ["token_sha256"], unique=True)
    audit_log(nullable=True)


def downgrade() -> None:
    audit_log(nullable=False)
    op.drop_index("ix_guest_token_sha256", "guest")
    op.drop_column("guest", "token_sha256")
