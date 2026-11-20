"""account hides adult titles

A provider account can hide adult titles from other apps (Kitsu's "Adult Content" setting). We remember it so the user
can be told, and so a master that hides titles never causes deletions on the mirrors.

Revision ID: f7c3d9a2e158
Revises: e5b8c2d94f17
Create Date: 2026-11-20 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c3d9a2e158"
down_revision: str | Sequence[str] | None = "e5b8c2d94f17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("linked_accounts", sa.Column("hides_adult", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("linked_accounts", "hides_adult")
