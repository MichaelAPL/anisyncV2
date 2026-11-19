"""user theme

Phase 5: the light/dark choice follows the user across devices (`system`, `light` or `dark`).

Revision ID: d3a7f1b8e264
Revises: c8d2e5a1f947
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a7f1b8e264"
down_revision: str | Sequence[str] | None = "c8d2e5a1f947"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("theme", sa.String(length=10), server_default="system", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "theme")
