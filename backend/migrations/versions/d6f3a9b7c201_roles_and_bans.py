"""roles, bans and the admin audit log

Phase 4.0 and 4.1.

Revision ID: d6f3a9b7c201
Revises: c5e2b8f94a13
Create Date: 2026-11-18 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f3a9b7c201"
down_revision: str | Sequence[str] | None = "c5e2b8f94a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(length=32), server_default="user", nullable=False))
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("ban_reason", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("banned_by", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_banned_by", "users", "users", ["banned_by"], ["id"], ondelete="SET NULL")
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_actions_actor_id", "admin_actions", ["actor_id"])
    op.create_index("ix_admin_actions_target_user_id", "admin_actions", ["target_user_id"])


def downgrade() -> None:
    op.drop_table("admin_actions")
    op.drop_constraint("fk_users_banned_by", "users", type_="foreignkey")
    for column in ("banned_by", "ban_reason", "banned_at", "role"):
        op.drop_column("users", column)
