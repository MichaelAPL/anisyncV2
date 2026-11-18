"""One user must never reach another user's accounts, sessions, lists, runs or previews."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from anisync import sessions
from anisync.main import app
from anisync.models import LinkedAccount, SyncRun, User, UserSession
from anisync.providers.base import ListEntry, Status
from tests.fakes import FakeClient

FUTURE = datetime.now(UTC) + timedelta(days=30)


async def make_user(session, tag: str):
    """A user whose master is fake provider "fa" and who mirrors to "fb", plus a signed-in browser session."""
    user = User(display_name=f"user-{tag}")
    session.add(user)
    accounts = {}
    for provider in ("fa", "fb"):
        account = LinkedAccount(
            user=user,
            provider=provider,
            remote_user_id=f"{provider}-{tag}",
            remote_username=f"{provider}-{tag}",
            expires_at=FUTURE,
        )
        account.access_token = f"token-{provider}-{tag}"
        session.add(account)
        accounts[provider] = account
    await session.flush()
    user.master_account_id = accounts["fa"].id
    await session.flush()
    token = await sessions.start(session, user.id, f"browser-{tag}")
    return user, accounts, token


def browser(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
        cookies={sessions.COOKIE: token},
    )


def titles(tag: str, *names: str) -> None:
    FakeClient.REMOTE[("fa", f"fa-{tag}")] = {
        n: ListEntry(media_id=n, status=Status.WATCHING, title=f"Title {n}", external_ids={"fa": n, "fb": f"b-{n}"})
        for n in names
    }


async def test_each_user_sees_only_their_own_accounts_sessions_and_runs(client, session, fakes):
    a, a_accounts, token_a = await make_user(session, "a")
    b, b_accounts, token_b = await make_user(session, "b")
    session.add_all(
        [SyncRun(user_id=a.id, trigger="manual", summary={}), SyncRun(user_id=b.id, trigger="daily", summary={})]
    )
    await session.flush()
    async with browser(token_a) as ca, browser(token_b) as cb:
        for client_, user, accounts in ((ca, a, a_accounts), (cb, b, b_accounts)):
            me = (await client_.get("/api/me")).json()
            assert me["id"] == user.id and {x["id"] for x in me["accounts"]} == {x.id for x in accounts.values()}
            assert len((await client_.get("/api/sessions")).json()) == 1
            assert [r["trigger"] for r in (await client_.get("/api/sync/runs")).json()] == [
                "manual" if user is a else "daily"
            ]


async def test_changing_someone_elses_resources_fails_and_changes_nothing(client, session, fakes):
    a, a_accounts, token_a = await make_user(session, "a")
    b, _, token_b = await make_user(session, "b")
    a_session = (await session.scalars(select(UserSession).where(UserSession.user_id == a.id))).one()
    a_session_id, a_master, a_id, b_id = a_session.id, a_accounts["fa"].id, a.id, b.id
    target = a_accounts["fb"].id
    async with browser(token_b) as cb:
        attempts = [
            await cb.patch(f"/api/accounts/{target}", json={"sync_enabled": False, "prune_missing": True}),
            await cb.delete(f"/api/accounts/{target}"),
            await cb.put("/api/me/master", json={"account_id": target}),
            await cb.delete(f"/api/sessions/{a_session_id}"),
        ]
    assert [r.status_code for r in attempts] == [404, 404, 404, 404]

    session.expire_all()
    account = await session.get(LinkedAccount, target)
    assert account is not None and account.sync_enabled and not account.prune_missing
    assert (await session.get(User, a_id)).master_account_id == a_master
    assert await session.get(UserSession, a_session_id) is not None
    assert (await session.get(User, b_id)).master_account_id != target


async def test_each_user_reads_only_their_own_list(client, session, fakes):
    _, _, token_a = await make_user(session, "a")
    _, _, token_b = await make_user(session, "b")
    titles("a", "x1", "x2")
    titles("b", "y1")
    async with browser(token_a) as ca, browser(token_b) as cb:
        assert [e["media_id"] for e in (await ca.get("/api/list")).json()] == ["x1", "x2"]
        assert [e["media_id"] for e in (await cb.get("/api/list")).json()] == ["y1"]


async def test_the_list_response_has_no_provider_internals(client, session, fakes):
    _, _, token = await make_user(session, "a")
    titles("a", "x1")
    async with browser(token) as c:
        [entry] = (await c.get("/api/list")).json()
    assert set(entry) == {
        "media_id", "status", "progress", "score", "rewatching", "started_at", "finished_at", "updated_at",
        "title", "total_episodes", "image_url", "external_ids",
    }  # fmt: skip


async def test_a_preview_only_covers_the_callers_own_mirrors(client, session, fakes):
    _, a_accounts, token_a = await make_user(session, "a")
    _, b_accounts, token_b = await make_user(session, "b")
    titles("a", "x1")
    titles("b", "y1", "y2")
    async with browser(token_a) as ca, browser(token_b) as cb:
        preview_a = (await ca.get("/api/sync/preview")).json()
        preview_b = (await cb.get("/api/sync/preview")).json()
    assert set(preview_a) == {str(a_accounts["fb"].id)} and set(preview_b) == {str(b_accounts["fb"].id)}
    assert preview_a[str(a_accounts["fb"].id)]["create"] == 1 and preview_b[str(b_accounts["fb"].id)]["create"] == 2


async def test_a_session_for_a_deleted_user_is_worthless(client, session, fakes):
    user, _, token = await make_user(session, "a")
    await session.delete(user)
    await session.flush()
    async with browser(token) as c:
        assert (await c.get("/api/me")).status_code == 401
