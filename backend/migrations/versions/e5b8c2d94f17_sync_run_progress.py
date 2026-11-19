"""sync run progress

Live progress of a running sync (changes planned and processed so far), and runs that stop halfway get the status
`interrupted`.

Revision ID: e5b8c2d94f17
Revises: d3a7f1b8e264
Create Date: 2026-11-19 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b8c2d94f17"
down_revision: str | Sequence[str] | None = "d3a7f1b8e264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sync_runs", sa.Column("planned", sa.Integer(), server_default="0", nullable=False))
    op.add_column("sync_runs", sa.Column("processed", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("sync_runs", "processed")
    op.drop_column("sync_runs", "planned")
