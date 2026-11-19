"""profile pictures

Users can pick the profile picture of one of their linked accounts (hotlinked URL).

Revision ID: f2c6e9a41b78
Revises: e8b4c1d92a65
Create Date: 2026-11-19 00:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c6e9a41b78"
down_revision: str | Sequence[str] | None = "e8b4c1d92a65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("avatar_provider", sa.String(length=32), nullable=True))
    op.add_column("linked_accounts", sa.Column("avatar_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("linked_accounts", "avatar_url")
    op.drop_column("users", "avatar_provider")
    op.drop_column("users", "avatar_url")
