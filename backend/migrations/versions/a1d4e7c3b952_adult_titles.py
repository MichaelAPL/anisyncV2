"""adult titles

Per-title adult flag (read from each provider), a per-mirror "don't copy adult titles" switch, and a per-user choice
to show adult covers. Existing snapshots don't know the flag yet, so every account gets one full read next time.

Revision ID: a1d4e7c3b952
Revises: f7c3d9a2e158
Create Date: 2026-11-20 02:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4e7c3b952"
down_revision: str | Sequence[str] | None = "f7c3d9a2e158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("list_snapshots", sa.Column("adult", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("linked_accounts", sa.Column("skip_adult", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("users", sa.Column("show_adult_covers", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE linked_accounts SET last_full_read_at = NULL")  # fills the new flag from the providers


def downgrade() -> None:
    op.drop_column("users", "show_adult_covers")
    op.drop_column("linked_accounts", "skip_adult")
    op.drop_column("list_snapshots", "adult")
