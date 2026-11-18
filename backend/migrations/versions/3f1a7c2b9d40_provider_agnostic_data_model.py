"""provider-agnostic data model

Phase 1 of the roadmap:
- `linked_accounts.service` (Postgres enum) becomes `provider` (plain string validated against the provider registry),
  keeping existing values.
- `anime_ids` (one column per provider) is replaced by `media` + `media_external_ids` (one row per provider ID).
  The old cache table held nothing that can't be relearned from list entries or the offline dataset import, so its
  rows are dropped rather than converted.
- `list_snapshots` stores what we last read from or wrote to each account.
- Adds the foreign key `users.master_account_id -> linked_accounts.id` (ON DELETE SET NULL). The initial migration
  declared it with `use_alter=True`, which `op.create_table` silently skips, so it never existed in the database.

Downgrading restores the enum, which only works while every account uses one of the three original providers.

Revision ID: 3f1a7c2b9d40
Revises: 09c0ce98d575
Create Date: 2026-11-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1a7c2b9d40"
down_revision: str | Sequence[str] | None = "09c0ce98d575"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("anime_ids")

    op.alter_column(
        "linked_accounts",
        "service",
        type_=sa.String(32),
        existing_type=sa.Enum("mal", "anilist", "kitsu", name="service"),
        existing_nullable=False,
        postgresql_using="service::text",
    )
    op.alter_column("linked_accounts", "service", new_column_name="provider")
    op.execute("DROP TYPE service")
    op.execute(
        "ALTER TABLE linked_accounts RENAME CONSTRAINT linked_accounts_service_remote_user_id_key "
        "TO linked_accounts_provider_remote_user_id_key"
    )

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("episodes", sa.Integer(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("cover_color", sa.String(length=9), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "media_external_ids",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider", "external_id"),
        sa.UniqueConstraint("media_id", "provider"),
    )
    op.create_table(
        "list_snapshots",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("remote_entry_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("rewatching", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("finished_at", sa.Date(), nullable=True),
        sa.Column("remote_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_written_hash", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("total_episodes", sa.Integer(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("external_ids", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["linked_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("account_id", "external_id"),
    )
    op.create_index(op.f("ix_list_snapshots_media_id"), "list_snapshots", ["media_id"], unique=False)

    op.execute(
        "UPDATE users SET master_account_id = NULL "
        "WHERE master_account_id IS NOT NULL AND master_account_id NOT IN (SELECT id FROM linked_accounts)"
    )
    op.create_foreign_key(
        "fk_users_master_account_id",
        "users",
        "linked_accounts",
        ["master_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_master_account_id", "users", type_="foreignkey")
    op.drop_index(op.f("ix_list_snapshots_media_id"), table_name="list_snapshots")
    op.drop_table("list_snapshots")
    op.drop_table("media_external_ids")
    op.drop_table("media")

    op.execute(
        "ALTER TABLE linked_accounts RENAME CONSTRAINT linked_accounts_provider_remote_user_id_key "
        "TO linked_accounts_service_remote_user_id_key"
    )
    op.alter_column("linked_accounts", "provider", new_column_name="service")
    op.execute("CREATE TYPE service AS ENUM ('mal', 'anilist', 'kitsu')")
    op.alter_column(
        "linked_accounts",
        "service",
        type_=sa.Enum("mal", "anilist", "kitsu", name="service"),
        existing_type=sa.String(32),
        existing_nullable=False,
        postgresql_using="service::service",
    )

    op.create_table(
        "anime_ids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mal_id", sa.Integer(), nullable=True),
        sa.Column("anilist_id", sa.Integer(), nullable=True),
        sa.Column("kitsu_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("anilist_id"),
        sa.UniqueConstraint("kitsu_id"),
        sa.UniqueConstraint("mal_id"),
    )
