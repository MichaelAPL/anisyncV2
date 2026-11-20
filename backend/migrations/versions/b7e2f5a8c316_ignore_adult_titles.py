"""ignore adult titles

A per-user switch: adult titles on the master list are not stored and not copied anywhere.

Revision ID: b7e2f5a8c316
Revises: a1d4e7c3b952
Create Date: 2026-11-20 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2f5a8c316"
down_revision: str | Sequence[str] | None = "a1d4e7c3b952"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ignore_adult_titles", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "ignore_adult_titles")
