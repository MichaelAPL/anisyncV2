"""incremental reads

Phase 3: per-account read cursor and last full read time, and a negative cache for ID lookups.

Revision ID: c5e2b8f94a13
Revises: a7d3c9e15f02
Create Date: 2026-11-18 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e2b8f94a13"
down_revision: str | Sequence[str] | None = "a7d3c9e15f02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("linked_accounts", sa.Column("sync_cursor", sa.DateTime(timezone=True), nullable=True))
    op.add_column("linked_accounts", sa.Column("last_full_read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "lookup_misses",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id", "target"),
    )


def downgrade() -> None:
    op.drop_table("lookup_misses")
    op.drop_column("linked_accounts", "last_full_read_at")
    op.drop_column("linked_accounts", "sync_cursor")
