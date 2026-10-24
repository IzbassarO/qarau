"""decision verdicts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24

REV1-api: a `reclass` decision carries the new class and/or severity. Plain ADD / DROP COLUMN,
never batch mode: on SQLite batch mode recreates the table and would silently drop the
append-only triggers of 0001.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEVERITY = sa.Enum("critical", "watch", name="severity", native_enum=False, length=16)


def upgrade() -> None:
    op.add_column("decision", sa.Column("new_class", sa.String(length=64), nullable=True))
    op.add_column("decision", sa.Column("new_severity", SEVERITY, nullable=True))


def downgrade() -> None:
    op.drop_column("decision", "new_severity")
    op.drop_column("decision", "new_class")
