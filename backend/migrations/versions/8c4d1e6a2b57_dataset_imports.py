"""dataset imports

Tracks runs of the offline ID dataset import (Phase 1.7).

Revision ID: 8c4d1e6a2b57
Revises: 3f1a7c2b9d40
Create Date: 2026-11-18 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c4d1e6a2b57"
down_revision: str | Sequence[str] | None = "3f1a7c2b9d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dataset_imports_dataset"), "dataset_imports", ["dataset"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_dataset_imports_dataset"), table_name="dataset_imports")
    op.drop_table("dataset_imports")
