"""A throwaway AniSync server for the browser tests (Playwright). Never used anywhere else.

It is the real application, with three differences:
- its own database (`anisync_e2e`, dropped and rebuilt at every start), never the dev or production one;
- the three providers keep their real names, colours and settings, but their clients are in-memory fakes, so no test
  ever talks to MyAnimeList, AniList or Kitsu;
- a few `/__e2e/*` routes let a test create a signed-in user with a ready-made list, change what the fake providers
  hold, slow their writes down, and read back what a sync left on them.

Run it from `backend/`:  uv run python -m e2e.server   (port 8100; the frontend e2e server proxies to it)
"""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date

from cryptography.fernet import Fernet

PORT = int(os.environ.get("E2E_API_PORT", "8100"))
FRONTEND = os.environ.get("E2E_PUBLIC_URL", "http://localhost:5180")
SERVER_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://anisync:anisync@localhost:5434/anisync_test")
DB_NAME = "anisync_e2e"

# The environment is set before anything from `anisync` is imported (the settings are read once).
os.environ.update(
    DATABASE_URL=SERVER_URL.rsplit("/", 1)[0] + f"/{DB_NAME}",
    TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    SESSION_SECRET="e2e-session-secret",
    PUBLIC_URL=FRONTEND,
    MAL_CLIENT_ID="e2e",
    MAL_CLIENT_SECRET="e2e",
    ANILIST_CLIENT_ID="e2e",
    ANILIST_CLIENT_SECRET="e2e",
    KITSU_CLIENT_ID="",
    KITSU_CLIENT_SECRET="",
)


def prepare_database() -> None:
    import psycopg
    from psycopg import sql

    admin = SERVER_URL.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(DB_NAME)))
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(DB_NAME)))
    for command in (["alembic", "upgrade", "head"], ["procrastinate", "--app=anisync.jobs.app", "schema", "--apply"]):
        subprocess.run(["uv", "run", *command], check=True, env=os.environ, capture_output=True)  # noqa: S603, S607


if __name__ == "__main__":
    prepare_database()

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from anisync import sessions  # noqa: E402
from anisync.db import SessionLocal  # noqa: E402
from anisync.main import app  # noqa: E402
from anisync.mapping import IdMapper  # noqa: E402
from anisync.models import IdConflict, LinkedAccount, User  # noqa: E402
from anisync.providers import get_provider  # noqa: E402
from anisync.providers.base import ListEntry, Status, TokenSet  # noqa: E402
from anisync.providers.registry import register, unregister  # noqa: E402
from tests.fakes import FakeClient, reset_fakes  # noqa: E402


class Config:
    save_delay = 0.0  # seconds each write takes, so a test can look at the "sync running" screens


async def fake_password_login(username: str, password: str) -> TokenSet:
    if password == "wrong":
        response = httpx.Response(400, request=httpx.Request("POST", "http://fake"))
        raise httpx.HTTPStatusError("rejected", request=response.request, response=response)
    return TokenSet("fake-access", "fake-refresh", None)


def install_fake_providers() -> None:
    for provider_id in ("mal", "anilist", "kitsu"):
        real = get_provider(provider_id)

        class Fake(FakeClient):
            async def save_entry(self, entry: ListEntry) -> None:
                if Config.save_delay:
                    await asyncio.sleep(Config.save_delay)
                await super().save_entry(entry)

        Fake.provider_id = provider_id
        unregister(provider_id)
        register(
            replace(
                real,
                client=Fake,
                lookup_from=frozenset(),
                refresh=None,
                password_login=fake_password_login if real.password_login else None,
                image_hosts=real.image_hosts,
            )
        )


install_fake_providers()

router = APIRouter(prefix="/__e2e")
IDS = {"mal": "m", "anilist": "a", "kitsu": "k"}


class SeedEntry(BaseModel):
    n: int  # the same number is the same title on every provider
    title: str = ""
    status: str = "watching"
    progress: int = 0
    total: int | None = 12
    score: int | None = None  # 0-100
    adult: bool = False


class Seed(BaseModel):
    name: str
    master: str = "mal"
    accounts: list[str] = ["mal", "anilist"]
    role: str = "user"
    lists: dict[str, list[SeedEntry]] = {}  # provider -> what that provider holds
    hides_adult: list[str] = []
    cover: str = "https://s4.anilist.co/e2e-cover.png"


def entry_for(provider: str, e: SeedEntry, cover: str) -> ListEntry:
    ids = {p: f"{IDS[p]}{e.n}" for p in IDS}
    return ListEntry(
        media_id=ids[provider],
        status=Status(e.status),
        progress=e.progress,
        score=e.score,
        title=e.title or f"Title {e.n}",
        total_episodes=e.total,
        image_url=cover,
        adult=e.adult,
        external_ids=ids,
        updated_at=FakeClient.stamp(),
        started_at=date(2026, 1, 1) if e.status != "planning" else None,
    )


@router.post("/reset")
async def reset() -> dict:
    reset_fakes()
    Config.save_delay = 0.0
    async with SessionLocal() as session:
        await session.execute(delete(User))
        await session.execute(delete(IdConflict))  # decisions made in one test must not leak into the next
        await session.commit()
    return {"ok": True}


@router.post("/seed")
async def seed(body: Seed) -> dict:
    """A signed-in user with linked accounts, a master, and lists already on the fake providers."""
    FakeClient.HIDES_ADULT = set(body.hides_adult)
    async with SessionLocal() as session:
        user = User(display_name=body.name, role=body.role)
        session.add(user)
        await session.flush()
        made = {}
        for provider in body.accounts:
            account = LinkedAccount(
                user_id=user.id,
                provider=provider,
                remote_user_id=f"{provider}-{body.name}",
                remote_username=f"{body.name}_{provider}",
            )
            account.access_token = "fake-token"
            account.hides_adult = provider in body.hides_adult
            session.add(account)
            made[provider] = account
        await session.flush()
        user.master_account_id = made[body.master].id
        async with IdMapper(session) as mapper:
            for provider, entries in body.lists.items():
                FakeClient.REMOTE[(provider, f"{provider}-{body.name}")] = {
                    e.media_id: e for e in (entry_for(provider, x, body.cover) for x in entries)
                }
            seen = {e.n for entries in body.lists.values() for e in entries}
            for n in seen:
                await mapper.remember({p: f"{IDS[p]}{n}" for p in IDS})
        token = await sessions.start(session, user.id, "e2e")
        await session.commit()
        return {"session": token, "user_id": user.id}


class Config_(BaseModel):
    save_delay: float = 0.0


@router.post("/conflicts")
async def conflicts() -> dict:
    """Two ID conflicts for the admin screen: the dataset's id differs from a hint, and two titles claimed to be one."""
    async with SessionLocal() as session, IdMapper(session) as mapper:
        await mapper.remember({"mal": "m1", "kitsu": "k1"}, title="Conflict Alpha")
        await mapper.remember({"mal": "m2", "kitsu": "k2"}, title="Conflict Beta")
        await mapper.remember({"mal": "m1", "kitsu": "k9"})
        await mapper.remember({"mal": "m1", "kitsu": "k2"})
        await session.commit()
    return {"ok": True}


@router.post("/config")
async def configure(body: Config_) -> dict:
    Config.save_delay = body.save_delay
    return {"ok": True}


@router.get("/remote/{provider}/{name}")
async def remote(provider: str, name: str) -> dict:
    """What a fake provider holds for a user right now: title id -> {status, progress}."""
    entries = FakeClient.REMOTE.get((provider, f"{provider}-{name}"), {})
    return {k: {"status": v.status.value, "progress": v.progress, "title": v.title} for k, v in entries.items()}


@router.get("/accounts/{name}")
async def accounts(name: str) -> list[dict]:
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(LinkedAccount).where(LinkedAccount.remote_user_id.like(f"%-{name}")))
        ).all()
        return [{"provider": a.provider, "hides_adult": a.hides_adult, "skip_adult": a.skip_adult} for a in rows]


app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    sys.exit(0)
