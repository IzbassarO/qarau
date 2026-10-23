"""manual findings

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23

DEF1-api (ADR 0022): a finding's area is a number in bundle units² (`area_units` was a unit
label string in 0001; the table was empty before any finding route existed), plus the marked
region, the engineer's comment and the scale source. An observation may carry an outline
polygon instead of a raster mask.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("finding") as b:
        b.drop_column("area_units")
        b.add_column(sa.Column("area_units", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("scale_source", sa.String(length=100), nullable=True))
        b.add_column(sa.Column("comment", sa.Text(), nullable=True))
        b.add_column(sa.Column("region", sa.JSON(), nullable=True))
    with op.batch_alter_table("observation") as b:
        b.alter_column("mask_path", existing_type=sa.String(length=500), nullable=True)
        b.add_column(sa.Column("outline", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("observation") as b:
        b.drop_column("outline")
        b.alter_column("mask_path", existing_type=sa.String(length=500), nullable=False)
    with op.batch_alter_table("finding") as b:
        b.drop_column("region")
        b.drop_column("comment")
        b.drop_column("scale_source")
        b.drop_column("area_units")
        b.add_column(
            sa.Column("area_units", sa.String(length=16), nullable=False, server_default="rel")
        )
