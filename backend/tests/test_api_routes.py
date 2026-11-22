"""The /api endpoints end to end (fake providers, in-memory job queue): responses must satisfy their schemas."""

import asyncio

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from anisync import jobs, sync
from anisync.models import LinkedAccount, ListSnapshot, SyncRun, SyncRunEvent, UnmatchedTitle, UnmatchedTitleUser
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user, titles


@pytest.fixture
async def alice(client, session, fakes):  # `client` wires the app to the test session
    user, accounts, token = await make_user(session, "al")
    titles("al", "x1", "x2")
    async with browser(token) as c:
        yield c, user, accounts


async def test_choosing_a_master_and_editing_mirror_settings(alice):
    c, user, accounts = alice
    fb = accounts["fb"].id
    me = (await c.put("/api/me/master", json={"account_id": fb})).json()
    assert me["master_account_id"] == fb and [a["is_master"] for a in me["accounts"]] == [False, True]
    me = (
        await c.patch(f"/api/accounts/{accounts['fa'].id}", json={"sync_enabled": False, "prune_missing": True})
    ).json()
    fa = next(a for a in me["accounts"] if a["provider"] == "fa")
    assert (fa["sync_enabled"], fa["prune_missing"]) == (False, True)
    assert all("token" not in k for a in me["accounts"] for k in a)


async def test_unlinking_accounts(alice, session):
    c, user, accounts = alice
    me = (await c.delete(f"/api/accounts/{accounts['fb'].id}")).json()
    assert [a["provider"] for a in me["accounts"]] == ["fa"]
    only = await c.delete(f"/api/accounts/{accounts['fa'].id}")
    assert only.status_code == 400 and "only account" in only.json()["detail"]


async def test_unlinking_the_master_leaves_no_master(alice):
    c, user, accounts = alice
    me = (await c.delete(f"/api/accounts/{accounts['fa'].id}")).json()
    assert me["master_account_id"] is None
    assert (await c.get("/api/list")).status_code == 400  # "Pick a master list first"
    assert (await c.post("/api/sync")).status_code == 400
    assert (await c.get("/api/sync/preview")).status_code == 400


async def test_editing_the_list_writes_the_master_and_queues_the_mirrors(alice):
    c, user, accounts = alice
    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector):
        saved = await c.put("/api/list/x1", json={"status": "completed", "progress": 12, "score": 90})
        removed = await c.delete("/api/list/x2")
    assert saved.json() == {"ok": True, "queued_mirrors": 1} and removed.json() == {"ok": True}
    remote = FakeClient.REMOTE[("fa", "fa-al")]
    assert remote["x1"].progress == 12 and "x2" not in remote
    queued = [j["args"] for j in connector.jobs.values()]
    assert [a["master_media_id"] for a in queued] == ["x1", "x2"] and queued[1]["entry"] is None


async def test_edits_are_validated(alice):
    c, *_ = alice
    assert (await c.put("/api/list/x1", json={"status": "nonsense", "progress": 1})).status_code == 422
    assert (await c.put("/api/list/x1", json={"status": "watching", "progress": -1})).status_code == 422
    assert (await c.put("/api/list/x1", json={"status": "watching", "score": 101})).status_code == 422


async def test_queueing_a_sync_is_deduplicated(alice):
    c, *_ = alice
    with jobs.app.replace_connector(InMemoryConnector()):
        assert (await c.post("/api/sync")).json() == {"queued": True}
        assert (await c.post("/api/sync")).json() == {"queued": False}


async def test_the_preview_describes_the_changes_and_writes_nothing(alice):
    c, user, accounts = alice
    summary = (await c.get("/api/sync/preview")).json()
    [mirror] = summary.values()
    assert mirror["provider"] == "fb" and mirror["create"] == 2 and "errors" not in mirror  # errors: applied syncs only
    assert {ch["op"] for ch in mirror["changes"]} == {"create"} and {ch["title"] for ch in mirror["changes"]} == {
        "Title x1",
        "Title x2",
    }
    assert "error" not in mirror and FakeClient.REMOTE.get(("fb", "fb-al"), {}) == {}


async def test_a_revoked_token_makes_the_preview_ask_for_a_relink(alice):
    c, *_ = alice
    FakeClient.REJECT_TOKENS = True
    response = await c.get("/api/sync/preview")
    assert response.status_code == 409


async def test_sync_history_follows_the_response_schema(alice, session):
    c, user, accounts = alice
    await sync.run_sync(session, user.id, "manual")
    FakeClient.FAIL_ON = {"b-x1"}
    FakeClient.REMOTE.pop(("fb", "fb-al"))
    await sync.run_sync(session, user.id, "daily")
    session.add(
        SyncRun(
            user_id=user.id,
            trigger="daily",
            status="error",
            summary={"9": {"provider": "fb", "username": "x", "error": "token expired", "reauth_required": True}},
        )
    )
    await session.flush()
    runs = (await c.get("/api/sync/runs")).json()
    assert len(runs) == 3 and {r["trigger"] for r in runs} == {"manual", "daily"}
    by_status = {r["status"]: r for r in runs}
    [ok] = [r for r in runs if r["status"] == "ok" and r["trigger"] == "manual"]
    assert next(iter(ok["summary"].values()))["create"] == 2
    errored = [r for r in runs if r["status"] == "error"][0]
    assert next(iter(errored["summary"].values()))["reauth_required"] is True and "changes" not in next(
        iter(errored["summary"].values())
    )
    assert by_status["ok"]["finished_at"] is not None and set(by_status["ok"]) == {
        "id",
        "trigger",
        "status",
        "summary",
        "started_at",
        "finished_at",
        "planned",
        "processed",
    }


async def test_signing_the_account_pages_never_expose_tokens(alice, session):
    c, user, accounts = alice
    text = (await c.get("/api/me")).text + (await c.get("/api/sessions")).text
    stored = (await session.scalars(select(LinkedAccount.provider))).all()
    assert "token-fa-al" not in text and stored  # the plaintext token isn't in any response


async def test_unlinking_deletes_everything_we_hold_about_that_account(alice, session):
    """Provider terms (MAL 3(e)): our copy of a provider's data goes when the user unlinks it."""
    c, user, accounts = alice
    fa, fb = accounts["fa"], accounts["fb"]
    run = SyncRun(user_id=user.id, status="ok", trigger="manual")
    session.add(run)
    session.add(UnmatchedTitle(source="fa", source_id="1", target="fb", title="Some title"))
    await session.flush()
    session.add_all(
        [
            ListSnapshot(account_id=fb.id, external_id="1", status="watching"),
            ListSnapshot(account_id=fa.id, external_id="2", status="watching"),
            SyncRunEvent(run_id=run.id, account_id=fb.id, level="info", kind="unmapped", message="x"),
            SyncRunEvent(run_id=run.id, account_id=fa.id, level="info", kind="unmapped", message="y"),
            UnmatchedTitleUser(source="fa", source_id="1", target="fb", user_id=user.id),
        ]
    )
    await session.flush()
    assert (await c.delete(f"/api/accounts/{fb.id}")).status_code == 200
    for model, own in ((ListSnapshot, "account_id"), (SyncRunEvent, "account_id")):
        assert len((await session.scalars(select(model).where(getattr(model, own) == fb.id))).all()) == 0
        assert len((await session.scalars(select(model).where(getattr(model, own) == fa.id))).all()) == 1  # untouched
    assert (await session.scalars(select(UnmatchedTitleUser).where(UnmatchedTitleUser.user_id == user.id))).all() == []


# ---- health check (public: an outside uptime check and the deploy proxy call it) -------------------------------

UNHEALTHY = {"detail": "Service unavailable"}


@pytest.fixture
def worker_task(monkeypatch):
    """Puts a task where the app keeps its in-process worker."""
    from anisync.main import app

    def install(task):
        monkeypatch.setattr(app.state, "worker", task, raising=False)

    return install


async def test_health_is_ok_with_a_reachable_database_and_a_running_worker(client, worker_task):
    running = asyncio.create_task(asyncio.Event().wait())
    worker_task(running)
    try:
        response = await client.get("/api/health")
    finally:
        running.cancel()
    assert response.status_code == 200 and response.json() == {"ok": True}


async def test_health_fails_when_the_worker_has_stopped(client, worker_task):
    async def crash():
        raise RuntimeError("worker crashed at /srv/secret.py")

    stopped = asyncio.create_task(crash())
    await asyncio.wait([stopped])
    worker_task(stopped)
    response = await client.get("/api/health")
    assert response.status_code == 503 and response.json() == UNHEALTHY


async def test_health_fails_when_the_database_does_not_answer(client, session, monkeypatch):
    async def unreachable(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("connection to db:5432 failed for user anisync"))

    monkeypatch.setattr(session, "execute", unreachable)
    response = await client.get("/api/health")
    assert response.status_code == 503 and response.json() == UNHEALTHY
    assert "anisync" not in response.text and "5432" not in response.text
