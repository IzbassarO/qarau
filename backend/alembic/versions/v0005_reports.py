"""reports

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27

RPT1: one row per generated PDF inspection report: language, SHA-256 of the file, report time
(the only clock in the PDF), report number and version, and the user who asked for it (null for
the shared read-only token).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inspection_id", sa.Integer(), sa.ForeignKey("inspection.id"), nullable=False),
        sa.Column("lang", sa.String(length=2), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_number", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )
    op.create_index("ix_report_inspection_id", "report", ["inspection_id"])


def downgrade() -> None:
    op.drop_index("ix_report_inspection_id", table_name="report")
    op.drop_table("report")
