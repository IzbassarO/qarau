"""run progress fields and the step list (S1, ADR 0022)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23

`run` gains current_step, error, rq_job_id, created_at and `steps`: a JSON list of
{name, status, started_at, finished_at, duration_s, error} in workflow order, mirrored from
<run>/<step>/step.json by the worker after every step. `progress` becomes an integer 0-100
(finished steps / total); 0001 had it as a float that nothing wrote.
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
        batch.add_column(sa.Column("rq_job_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.alter_column(
            "progress", existing_type=sa.Float(), type_=sa.Integer(), existing_nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.alter_column(
            "progress", existing_type=sa.Integer(), type_=sa.Float(), existing_nullable=False
        )
        batch.drop_column("created_at")
        batch.drop_column("steps")
        batch.drop_column("rq_job_id")
        batch.drop_column("error")
        batch.drop_column("current_step")
