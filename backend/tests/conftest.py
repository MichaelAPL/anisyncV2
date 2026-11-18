"""Shared test setup.

The environment is configured *before* anything from `anisync` is imported, so the app, the migrations and the
tests all talk to a dedicated `anisync_test` database and never to the dev database.

DB tests run inside a transaction that is rolled back at the end of each test. Code under test may call
`session.commit()`: it only releases a SAVEPOINT. If Postgres isn't reachable, DB tests are skipped locally and
fail in CI (`CI=true`).
"""

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from cryptography.fernet import Fernet

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://anisync:anisync@localhost:5434/anisync_test")
assert urlparse(TEST_DATABASE_URL).path.lstrip("/").endswith("test"), "TEST_DATABASE_URL must name a *_test database"

os.environ.update(
    DATABASE_URL=TEST_DATABASE_URL,
    TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    SESSION_SECRET="test-session-secret",
    PUBLIC_URL="http://localhost:5173",
    MAL_CLIENT_ID="mal-test-id",
    MAL_CLIENT_SECRET="mal-test-secret",
    ANILIST_CLIENT_ID="anilist-test-id",
    ANILIST_CLIENT_SECRET="anilist-test-secret",
    KITSU_CLIENT_ID="",
    KITSU_CLIENT_SECRET="",
)

import httpx  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def with_database(url: str, name: str) -> str:
    return urlunparse(urlparse(url)._replace(path=f"/{name}"))


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("postgresql://", "postgresql+psycopg://", 1))
    return cfg


def ensure_database(name: str) -> str:
    """Create database `name` on the test server if it doesn't exist. Returns its URL."""
    try:
        with psycopg.connect(with_database(TEST_DATABASE_URL, "postgres"), autocommit=True, connect_timeout=5) as conn:
            if not conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone():
                conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable ({exc}). Run `docker compose up -d --wait` from the repo root.")
    return with_database(TEST_DATABASE_URL, name)


def recreate_database(name: str) -> str:
    """Drop and recreate database `name`, so a test never depends on what an earlier run left behind."""
    ensure_database(name)
    with psycopg.connect(with_database(TEST_DATABASE_URL, "postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    return with_database(TEST_DATABASE_URL, name)


@pytest.fixture(scope="session")
def database() -> str:
    """The migrated test database (created on first use)."""
    url = ensure_database(urlparse(TEST_DATABASE_URL).path.lstrip("/"))
    command.upgrade(alembic_config(), "head")
    return url


@pytest.fixture
async def engine(database):
    from anisync.config import get_settings

    # A fresh engine per test: connections can't be shared across the per-test event loops.
    eng = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    """A session whose work is rolled back after the test, even if the code under test commits."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False) as s:
            yield s
        await outer.rollback()


@pytest.fixture
async def client(session):
    """HTTP client for the FastAPI app (no lifespan: no job worker), using the rolled-back test session."""
    from anisync.db import get_session
    from anisync.main import app

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def acme():
    """Registers the fake "acme" provider for one test."""
    from tests import fakes

    fakes.register_acme()
    yield fakes.AcmeClient
    fakes.unregister_acme()


@pytest.fixture
def fakes():
    """Two in-memory providers: "fa" (score step 1, no token refresh) and "fb" (step 10, refreshable)."""
    from datetime import UTC, datetime, timedelta

    from anisync.providers.base import TokenSet
    from tests import fakes as f

    f.reset_fakes()
    calls = []

    async def refresh(refresh_token: str) -> TokenSet:
        calls.append(refresh_token)
        return TokenSet("fresh-access", "fresh-refresh", datetime.now(UTC) + timedelta(days=30))

    f.register_fake("fa", score_step=1)
    f.register_fake("fb", score_step=10, refresh=refresh)
    f.FakeClient.refresh_calls = calls
    yield f.FakeClient
    f.unregister_fakes()
    f.reset_fakes()
