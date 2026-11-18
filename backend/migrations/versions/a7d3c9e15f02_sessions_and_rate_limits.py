"""server-side sessions and rate limit hits

Phase 2: browser sessions move from a signed cookie holding the user id to a random token whose SHA-256 is stored
here (revocable, listable, expiring), and rate limiting gets a small table that works across processes.

Revision ID: a7d3c9e15f02
Revises: 8c4d1e6a2b57
Create Date: 2026-11-18 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3c9e15f02"
down_revision: str | Sequence[str] | None = "8c4d1e6a2b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"], unique=False)
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rate_limit_hits_key_created_at", "rate_limit_hits", ["key", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_key_created_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
