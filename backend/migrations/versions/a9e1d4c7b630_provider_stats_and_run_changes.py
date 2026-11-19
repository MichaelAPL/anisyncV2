"""provider request stats, run change counts

Phase 4.3: per-day request counters per provider, and the number of changes a sync run made.

Revision ID: a9e1d4c7b630
Revises: f2c6e9a41b78
Create Date: 2026-11-19 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9e1d4c7b630"
down_revision: str | Sequence[str] | None = "f2c6e9a41b78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_stats",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("rate_limited", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "provider"),
    )
    op.add_column("sync_runs", sa.Column("changes", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_sync_runs_started_at", "sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_started_at", table_name="sync_runs")
    op.drop_column("sync_runs", "changes")
    op.drop_table("provider_stats")
