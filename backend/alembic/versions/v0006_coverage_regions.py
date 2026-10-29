"""coverage regions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28

COV1-b: one row per map region of a run's coverage (<run>/coverage/regions.json): its key, state
(inspected_ok | finding | not_seen, checked by the database), frame count and bounds.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coverage_region",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=32), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("region_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("bounds_min", sa.JSON(), nullable=False),
        sa.Column("bounds_max", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "region_key"),
        sa.CheckConstraint(
            "state IN ('inspected_ok', 'finding', 'not_seen')", name="ck_coverage_region_state"
        ),
    )
    op.create_index("ix_coverage_region_run_id", "coverage_region", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_coverage_region_run_id", table_name="coverage_region")
    op.drop_table("coverage_region")
