"""sync run events

Phase 4.2: notable events per sync run (failed changes, re-auth, rate limits, unmapped titles).

Revision ID: e8b4c1d92a65
Revises: d6f3a9b7c201
Create Date: 2026-11-18 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b4c1d92a65"
down_revision: str | Sequence[str] | None = "d6f3a9b7c201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("linked_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("media_ref", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sync_run_events_run_id", "sync_run_events", ["run_id"])
    # Stats count events per kind per day (4.3), and the admin log filters by level.
    op.create_index("ix_sync_run_events_kind_created", "sync_run_events", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_table("sync_run_events")
