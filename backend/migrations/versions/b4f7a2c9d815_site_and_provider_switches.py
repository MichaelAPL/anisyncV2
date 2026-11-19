"""site and provider switches

Phase 4.6, 4.7, 4.9: provider kill switch, pause scheduled syncs, maintenance mode and site banner.

Revision ID: b4f7a2c9d815
Revises: a9e1d4c7b630
Create Date: 2026-11-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f7a2c9d815"
down_revision: str | Sequence[str] | None = "a9e1d4c7b630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scheduled_syncs_paused", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("maintenance", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("maintenance_message", sa.String(length=500), nullable=True),
        sa.Column("banner_text", sa.String(length=500), nullable=True),
        sa.Column("banner_level", sa.String(length=10), server_default="info", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_table(
        "provider_settings",
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("paused_reason", sa.String(length=500), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("provider_settings")
    op.drop_table("site_settings")
