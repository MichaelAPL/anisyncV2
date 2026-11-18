"""Migrations apply, match the models, keep data and reverse cleanly (on a scratch database)."""

import psycopg
from alembic import command

from tests.conftest import alembic_config, recreate_database

OLD_REVISION = "09c0ce98d575"  # before Phase 1
PHASE1_REVISION = "3f1a7c2b9d40"


def test_migrations_roundtrip_and_match_models():
    url = recreate_database("anisync_migrations_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, OLD_REVISION)

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("insert into users (display_name) values ('kaito')")
        conn.execute(
            "insert into linked_accounts (user_id, service, remote_user_id, remote_username, access_token, "
            "sync_enabled, prune_missing) values (1, 'anilist', '7', 'kaito', 'x', true, false)"
        )

    command.upgrade(cfg, PHASE1_REVISION)
    command.upgrade(cfg, "head")
    command.check(cfg)  # fails if the models and the migrations disagree
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("select provider from linked_accounts").fetchone() == ("anilist",)
        tables = {r[0] for r in conn.execute("select tablename from pg_tables where schemaname = 'public'")}
        assert {"media", "media_external_ids", "list_snapshots"} <= tables and "anime_ids" not in tables
        assert conn.execute("select 1 from pg_type where typname = 'service'").fetchone() is None

    command.downgrade(cfg, OLD_REVISION)  # all the way back through both revisions
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("select service::text from linked_accounts").fetchone() == ("anilist",)
        assert conn.execute("select to_regclass('anime_ids')").fetchone() != (None,)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
