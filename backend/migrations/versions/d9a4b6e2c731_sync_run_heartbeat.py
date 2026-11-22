"""sync run heartbeat

A running sync records when it last made progress, so a deploy closes only runs that stopped, not slow ones. Existing
rows get their start time.

Revision ID: d9a4b6e2c731
Revises: c2e9a4f6d871
Create Date: 2026-11-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9a4b6e2c731"
down_revision: str | Sequence[str] | None = "c2e9a4f6d871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sync_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("update sync_runs set heartbeat_at = started_at")


def downgrade() -> None:
    op.drop_column("sync_runs", "heartbeat_at")
