"""Admin user lookup (Phase 4.5): search, detail and "sync now"."""

from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select

from anisync import jobs, moderation, permissions
from anisync.models import AdminAction, ListSnapshot, SyncRun, SyncRunEvent, UserSession
from tests.test_ownership import browser, make_user

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


@pytest.fixture
async def admin(session):
    user, _, token = await make_user(session, "root")
    user.role = "admin"
    await session.flush()
    return user, token


@pytest.fixture
def queue():
    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector):
        yield connector


async def get(token, url):
    async with browser(token) as c:
        return await c.get(url)


async def post(token, url, **kwargs):
    async with browser(token) as c:
        return await c.post(url, **kwargs)


def ids(response) -> list[int]:
    return [row["id"] for row in response.json()]


# ---- search --------------------------------------------------------------------------------------------------


async def test_users_are_found_by_id_name_or_a_linked_username(session, admin):
    _, token = admin
    kaito, kaito_accounts, _ = await make_user(session, "kaito")
    aki, aki_accounts, _ = await make_user(session, "aki")
    kaito.display_name = "Kaito the Great"
    aki_accounts["fb"].remote_username = "WatcherOfKaito"
    await session.flush()

    assert ids(await get(token, f"/api/admin/users?q={kaito.id}")) == [kaito.id]
    assert ids(await get(token, "/api/admin/users?q=kaito the")) == [kaito.id]
    assert ids(await get(token, "/api/admin/users?q=KAITO THE GREAT")) == [kaito.id]  # not case sensitive
    assert set(ids(await get(token, "/api/admin/users?q=watcherof"))) == {aki.id}  # a provider username
    assert ids(await get(token, "/api/admin/users?q=nobody-has-this-name")) == []
    assert kaito_accounts and aki.id


async def test_search_text_is_not_a_pattern(session, admin):
    _, token = admin
    plain, _, _ = await make_user(session, "plain")
    odd, _, _ = await make_user(session, "odd")
    odd.display_name = "100%_real\\"
    await session.flush()
    async with browser(token) as c:
        for q in ("%", "_", "\\", "%_", "100%_real\\"):
            found = ids(await c.get("/api/admin/users", params={"q": q, "limit": 100}))
            assert plain.id not in found, q  # wildcards in the text match themselves, not everything
        assert ids(await c.get("/api/admin/users", params={"q": "100%_real\\"})) == [odd.id]


async def test_no_query_lists_the_newest_users_and_pages(session, admin):
    _, token = admin
    created = [(await make_user(session, f"n{i}"))[0] for i in range(4)]
    first = ids(await get(token, "/api/admin/users?limit=2"))
    assert first == [created[3].id, created[2].id]
    assert ids(await get(token, "/api/admin/users?limit=2&offset=2"))[:2] == [created[1].id, created[0].id]
    assert (await get(token, "/api/admin/users?limit=0")).status_code == 422
    assert (await get(token, "/api/admin/users?limit=101")).status_code == 422
    assert (await get(token, "/api/admin/users?offset=-1")).status_code == 422
    assert (await get(token, "/api/admin/users?q=" + "x" * 101)).status_code == 422


async def test_banned_users_can_be_listed_on_their_own(session, admin):
    root, token = admin
    fine, _, _ = await make_user(session, "fine")
    bad, _, _ = await make_user(session, "bad")
    await moderation.ban(session, root, bad.id, "spam")
    assert bad.id in ids(await get(token, "/api/admin/users?banned=true&limit=100"))
    assert fine.id not in ids(await get(token, "/api/admin/users?banned=true&limit=100"))
    assert bad.id not in ids(await get(token, "/api/admin/users?banned=false&limit=100"))


async def test_rows_show_providers_master_and_last_seen(session, admin):
    _, token = admin
    user, accounts, _ = await make_user(session, "u")
    row = (await session.scalars(select(UserSession).where(UserSession.user_id == user.id))).one()
    row.last_seen_at = NOW - timedelta(days=2)
    await session.flush()
    [found] = (await get(token, f"/api/admin/users?q={user.id}")).json()
    assert (found["master_provider"], found["providers"], found["role"]) == ("fa", ["fa", "fb"], "user")
    assert datetime.fromisoformat(found["last_seen_at"]) == row.last_seen_at and accounts


# ---- detail --------------------------------------------------------------------------------------------------


async def test_the_detail_page_has_everything_an_admin_needs_and_no_secrets(session, admin):
    root, token = admin
    user, accounts, user_token = await make_user(session, "u")
    await sessions_extra(session, user)
    accounts["fa"].last_full_read_at = NOW - timedelta(days=1)
    accounts["fb"].expires_at = NOW - timedelta(days=1)  # expired, no refresh token: needs a re-link
    accounts["fb"].prune_missing = True
    session.add_all(
        [ListSnapshot(account_id=accounts["fa"].id, external_id=str(n), status="watching") for n in range(1, 4)]
    )
    manual = SyncRun(user_id=user.id, trigger="manual", status="error", summary={}, started_at=NOW)
    old_manual = SyncRun(
        user_id=user.id, trigger="manual", status="ok", summary={}, started_at=NOW - timedelta(days=30)
    )
    session.add_all([manual, old_manual])
    await session.flush()
    session.add(SyncRunEvent(run_id=manual.id, level="error", kind="change_failed", message="x", provider="fb"))
    await moderation.ban(session, root, user.id, "scraping")

    response = await get(token, f"/api/admin/users/{user.id}")
    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["banned_by"], body["banned_by_name"], body["ban_reason"]) == (
        "user-u",
        root.id,
        "user-root",
        "scraping",
    )
    assert body["sessions"] == 0  # banning signed them out (the extra session too)
    fa, fb = body["accounts"]
    assert (fa["provider"], fa["is_master"], fa["entries"], fa["needs_relink"]) == ("fa", True, 3, False)
    assert (fb["provider"], fb["is_master"], fb["entries"], fb["needs_relink"], fb["prune_missing"]) == (
        "fb",
        False,
        0,
        True,
        True,
    )
    assert body["manual_syncs_7d"] == 1
    assert [r["id"] for r in body["recent_runs"]] == [manual.id, old_manual.id]
    assert body["recent_runs"][0]["errors"] == 1
    [action] = body["actions"]
    assert (action["action"], action["actor_name"], action["note"]) == ("user.ban", "user-root", "scraping")
    text = response.text
    for secret in ("token-fa-u", "token-fb-u", user_token):
        assert secret not in text
    assert "access_token" not in text and "refresh_token" not in text


async def sessions_extra(session, user):
    from anisync import sessions

    await sessions.start(session, user.id, "second device")


async def test_a_user_that_was_never_touched_has_empty_histories(session, admin):
    _, token = admin
    user, _, _ = await make_user(session, "calm")
    body = (await get(token, f"/api/admin/users/{user.id}")).json()
    assert body["actions"] == [] and body["recent_runs"] == [] and body["banned_at"] is None
    assert body["sessions"] == 1 and body["banned_by_name"] is None


async def test_unknown_users_are_404(admin):
    assert (await get(admin[1], "/api/admin/users/99999999")).status_code == 404


# ---- sync now ------------------------------------------------------------------------------------------------


async def test_an_admin_can_queue_a_sync_and_it_is_audited(session, admin, queue):
    root, token = admin
    user, _, _ = await make_user(session, "u")
    assert (await post(token, f"/api/admin/users/{user.id}/sync")).json() == {"queued": True}
    assert (await post(token, f"/api/admin/users/{user.id}/sync")).json() == {"queued": False}  # already waiting
    [job] = queue.jobs.values()
    assert job["args"] == {"user_id": user.id, "trigger": "manual"}
    rows = (await session.scalars(select(AdminAction).where(AdminAction.action == "user.sync"))).all()
    assert [(r.actor_id, r.target_user_id, r.details) for r in rows] == [
        (root.id, user.id, {"queued": True}),
        (root.id, user.id, {"queued": False}),
    ]


async def test_banned_users_and_users_without_a_master_are_not_queued(session, admin, queue):
    root, token = admin
    banned, _, _ = await make_user(session, "banned")
    await moderation.ban(session, root, banned.id, "x")
    nomaster, _, _ = await make_user(session, "nomaster")
    nomaster.master_account_id = None
    await session.flush()
    assert (await post(token, f"/api/admin/users/{banned.id}/sync")).status_code == 409
    assert (await post(token, f"/api/admin/users/{nomaster.id}/sync")).status_code == 409
    assert (await post(token, "/api/admin/users/99999999/sync")).status_code == 404
    assert not queue.jobs


# ---- access --------------------------------------------------------------------------------------------------


async def test_reading_needs_users_read_and_queueing_needs_syncs_retry(session, client, queue):
    permissions.ROLE_PERMISSIONS["reader"] = frozenset({permissions.USERS_READ})
    permissions.ROLE_PERMISSIONS["retrier"] = frozenset({permissions.SYNCS_RETRY})
    try:
        target, _, _ = await make_user(session, "target")
        plain, _, plain_token = await make_user(session, "plain")
        reader, _, reader_token = await make_user(session, "reader")
        retrier, _, retrier_token = await make_user(session, "retrier")
        reader.role, retrier.role = "reader", "retrier"
        await session.flush()
        reads = ["/api/admin/users", f"/api/admin/users/{target.id}"]
        for url in reads:
            assert (await client.get(url)).status_code == 401
            assert (await get(plain_token, url)).status_code == 403
            assert (await get(retrier_token, url)).status_code == 403
            assert (await get(reader_token, url)).status_code == 200
        sync_url = f"/api/admin/users/{target.id}/sync"
        assert (await client.post(sync_url)).status_code == 401
        assert (await post(plain_token, sync_url)).status_code == 403
        assert (await post(reader_token, sync_url)).status_code == 403
        assert (await post(retrier_token, sync_url)).status_code == 200
    finally:
        del permissions.ROLE_PERMISSIONS["reader"], permissions.ROLE_PERMISSIONS["retrier"]
