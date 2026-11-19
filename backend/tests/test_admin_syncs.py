"""Admin sync status, run history and event log (Phase 4.4)."""

from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector

from anisync import jobs, permissions, sync
from anisync.models import SyncRun, SyncRunEvent
from tests.test_ownership import browser, make_user

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


@pytest.fixture
async def admin(session):
    user, _, token = await make_user(session, "admin")
    user.role = "admin"
    await session.flush()
    return user, token


async def run(session, user, trigger="scheduled", status="ok", ago=timedelta(0), changes=0, summary=None):
    row = SyncRun(
        user_id=user.id,
        trigger=trigger,
        status=status,
        summary=summary if summary is not None else {},
        changes=changes,
        started_at=NOW - ago,
    )
    session.add(row)
    await session.flush()
    return row


async def event(session, run_row, level="error", kind="change_failed", provider="fb", message="boom", media="b1"):
    row = SyncRunEvent(run_id=run_row.id, provider=provider, level=level, kind=kind, message=message, media_ref=media)
    session.add(row)
    await session.flush()
    return row


async def get(token, url):
    async with browser(token) as c:
        return await c.get(url)


# ---- runs ----------------------------------------------------------------------------------------------------


async def test_runs_are_listed_newest_first_with_their_event_counts(session, admin):
    _, token = admin
    user, _, _ = await make_user(session, "u")
    old = await run(session, user, ago=timedelta(hours=3), changes=4, summary={"1": {}, "2": {}})
    new = await run(session, user, status="error", ago=timedelta(hours=1), changes=1)
    await event(session, new)
    await event(session, new, level="warning", kind="rate_limited")
    await event(session, new, level="info", kind="unmapped")
    rows = (await get(token, f"/api/admin/syncs/runs?user_id={user.id}")).json()
    assert [r["id"] for r in rows] == [new.id, old.id]
    assert (rows[0]["errors"], rows[0]["warnings"], rows[0]["display_name"]) == (1, 1, "user-u")
    assert (rows[1]["errors"], rows[1]["mirrors"], rows[1]["changes"]) == (0, 2, 4)


async def test_runs_can_be_filtered_and_paged(session, admin):
    _, token = admin
    user, _, _ = await make_user(session, "u")
    kinds = [("ok", "scheduled"), ("error", "scheduled"), ("error", "manual"), ("ok", "edit")]
    ids = [(await run(session, user, status=s, trigger=t)).id for s, t in kinds]
    base = f"/api/admin/syncs/runs?user_id={user.id}"

    def ids_of(response):
        return [r["id"] for r in response.json()]

    assert ids_of(await get(token, base + "&status=error")) == [ids[2], ids[1]]
    assert ids_of(await get(token, base + "&trigger=manual")) == [ids[2]]
    assert ids_of(await get(token, base + "&limit=2")) == [ids[3], ids[2]]
    assert ids_of(await get(token, base + f"&limit=2&before_id={ids[2]}")) == [ids[1], ids[0]]
    assert ids_of(await get(token, base + f"&limit=2&before_id={ids[0]}")) == []


@pytest.mark.parametrize("query", ["status=bogus", "trigger=nightly", "limit=0", "limit=101"])
async def test_run_filters_are_validated(admin, query):
    assert (await get(admin[1], f"/api/admin/syncs/runs?{query}")).status_code == 422


async def test_a_run_shows_its_summary_and_events(session, admin):
    _, token = admin
    user, _, _ = await make_user(session, "u")
    row = await run(session, user, status="error", summary={"7": {"provider": "fb", "create": 2, "errors": ["boom"]}})
    await event(session, row, message="create b1: boom")
    await event(session, row, level="info", kind="unmapped", media=None)
    body = (await get(token, f"/api/admin/syncs/runs/{row.id}")).json()
    assert body["run"]["id"] == row.id and body["summary"]["7"]["errors"] == ["boom"]
    assert [(e["level"], e["kind"]) for e in body["events"]] == [("error", "change_failed"), ("info", "unmapped")]
    assert body["events"][0]["display_name"] == "user-u"
    assert (await get(token, "/api/admin/syncs/runs/99999999")).status_code == 404


# ---- events --------------------------------------------------------------------------------------------------


async def test_the_event_log_filters_by_level_kind_provider_and_user(session, admin):
    _, token = admin
    one, _, _ = await make_user(session, "one")
    two, _, _ = await make_user(session, "two")
    r1, r2 = await run(session, one), await run(session, two)
    a = await event(session, r1, "error", "change_failed", "fb")
    b = await event(session, r1, "warning", "rate_limited", "fa")
    c = await event(session, r2, "error", "reauth", "fb")

    def ids(body):
        return [e["id"] for e in body]

    mine = f"user_id={one.id}"
    assert ids((await get(token, f"/api/admin/syncs/events?{mine}")).json()) == [b.id, a.id]  # newest first
    assert ids((await get(token, f"/api/admin/syncs/events?{mine}&level=error")).json()) == [a.id]
    assert ids((await get(token, f"/api/admin/syncs/events?{mine}&kind=rate_limited")).json()) == [b.id]
    assert ids((await get(token, "/api/admin/syncs/events?provider=fb&level=error&limit=200")).json())[:2] == [
        c.id,
        a.id,
    ]
    assert (await get(token, "/api/admin/syncs/events?level=fatal")).status_code == 422
    assert (await get(token, "/api/admin/syncs/events?kind=weird")).status_code == 422


# ---- status --------------------------------------------------------------------------------------------------


@pytest.fixture
def queue():
    with jobs.app.replace_connector(InMemoryConnector()):
        yield


async def test_status_reports_the_last_batch_the_next_batch_and_the_queue(session, admin, queue):
    _, token = admin
    user, _, _ = await make_user(session, "u")
    this_hour = NOW.replace(minute=0, second=0, microsecond=0)
    await run(session, user, ago=NOW - this_hour + timedelta(hours=2), changes=7)  # an older hour: ignored
    await run(session, user, status="ok", changes=3)
    await run(session, user, status="error", changes=2)
    await run(session, user, trigger="manual", changes=100)  # not part of a scheduled batch
    await jobs.enqueue_user_sync(user.id, "scheduled")
    body = (await get(token, "/api/admin/syncs/status")).json()
    batch = body["last_batch"]
    assert (batch["runs"], batch["ok"], batch["error"], batch["changes"]) == (2, 1, 1, 5)
    assert datetime.fromisoformat(batch["hour"]) == this_hour
    assert datetime.fromisoformat(body["next_batch_at"]) == sync.next_batch_at(NOW)
    assert body["queue_available"] and body["queues"] == [{"queue": "sync", "todo": 1, "doing": 0, "failed": 0}]
    assert body["stalled_jobs"] == 0 and body["users_in_next_batch"] >= 0


async def test_status_counts_accounts_whose_full_read_is_overdue(session, admin, queue):
    _, token = admin
    before = (await get(token, "/api/admin/syncs/status")).json()["full_reads_overdue"]
    user, accounts, _ = await make_user(session, "u")
    user.created_at = NOW - timedelta(days=30)
    accounts["fa"].last_full_read_at = NOW - timedelta(days=2)  # fine
    accounts["fb"].last_full_read_at = NOW - timedelta(days=9)  # overdue
    other, other_accounts, _ = await make_user(session, "never-read")
    other.created_at = NOW - timedelta(days=3)  # never fully read, and older than a day: overdue (both accounts)
    young, _, _ = await make_user(session, "young")  # just signed up: its first sync hasn't come around yet
    banned, banned_accounts, _ = await make_user(session, "banned")
    banned.banned_at = NOW
    banned_accounts["fa"].last_full_read_at = NOW - timedelta(days=60)
    await session.flush()
    after = (await get(token, "/api/admin/syncs/status")).json()["full_reads_overdue"]
    assert after - before == 3
    assert sync.FULL_READ_EVERY == timedelta(days=7) and young.id


async def test_status_still_works_when_the_job_queue_cannot_be_read(session, admin):
    """No queue connector is open in tests unless a fixture opens one: the page must degrade, not fail."""
    user, _, _ = await make_user(session, "u")
    await run(session, user)
    response = await get(admin[1], "/api/admin/syncs/status")
    body = response.json()
    assert response.status_code == 200
    assert body["queue_available"] is False and body["queues"] == [] and body["last_batch"]["runs"] >= 1


# ---- access --------------------------------------------------------------------------------------------------

URLS = ["/api/admin/syncs/status", "/api/admin/syncs/runs", "/api/admin/syncs/runs/1", "/api/admin/syncs/events"]


@pytest.mark.parametrize("url", URLS)
async def test_only_users_with_syncs_read_may_look(session, client, url):
    plain, _, plain_token = await make_user(session, "plain")
    permissions.ROLE_PERMISSIONS["stats-only"] = frozenset({permissions.STATS_READ})
    try:
        other, _, other_token = await make_user(session, "other")
        other.role = "stats-only"
        await session.flush()
        assert (await client.get(url)).status_code == 401
        assert (await get(plain_token, url)).status_code == 403
        assert (await get(other_token, url)).status_code == 403
    finally:
        del permissions.ROLE_PERMISSIONS["stats-only"]
