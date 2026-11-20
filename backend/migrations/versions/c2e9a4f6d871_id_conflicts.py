"""id conflicts

Cross-references from list reads that disagree with the ID dataset: recorded for an admin to review and report.

Revision ID: c2e9a4f6d871
Revises: b7e2f5a8c316
Create Date: 2026-11-20 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e9a4f6d871"
down_revision: str | Sequence[str] | None = "b7e2f5a8c316"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "id_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(length=400), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ids", sa.JSON(), nullable=False),
        sa.Column("media_ids", sa.JSON(), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("seen", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_id_conflicts_fingerprint", "id_conflicts", ["fingerprint"], unique=True)
    op.create_index("ix_id_conflicts_status", "id_conflicts", ["status", "last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_id_conflicts_status", table_name="id_conflicts")
    op.drop_index("ix_id_conflicts_fingerprint", table_name="id_conflicts")
    op.drop_table("id_conflicts")
