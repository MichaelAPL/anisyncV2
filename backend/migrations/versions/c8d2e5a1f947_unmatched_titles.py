"""unmatched titles

Phase 4.8: titles with no equivalent on a mirror's provider, for the admin to link by hand.

Revision ID: c8d2e5a1f947
Revises: b4f7a2c9d815
Create Date: 2026-11-19 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d2e5a1f947"
down_revision: str | Sequence[str] | None = "b4f7a2c9d815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unmatched_titles",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id", "target"),
    )
    op.create_index("ix_unmatched_titles_last_seen_at", "unmatched_titles", ["last_seen_at"])
    op.create_table(
        "unmatched_title_users",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "source_id", "target"],
            ["unmatched_titles.source", "unmatched_titles.source_id", "unmatched_titles.target"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source", "source_id", "target", "user_id"),
    )
    op.create_index("ix_unmatched_title_users_user_id", "unmatched_title_users", ["user_id"])


def downgrade() -> None:
    op.drop_table("unmatched_title_users")
    op.drop_table("unmatched_titles")
