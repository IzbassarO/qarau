"""run progress fields and per-step rows (S1, ADR 0022)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23

`run` gains current_step, error and created_at (the worker writes them after every step);
`run_step` holds one row per workflow step (name, status, started_at, finished_at, duration_s,
error), mirrored from <run>/<step>/step.json.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:  # batch: SQLite cannot ALTER in place
        batch.add_column(sa.Column("current_step", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("error", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    op.create_table(
        "run_step",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("run_step")
    with op.batch_alter_table("run") as batch:
        batch.drop_column("created_at")
        batch.drop_column("error")
        batch.drop_column("current_step")
