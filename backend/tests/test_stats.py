"""Admin statistics (Phase 4.3): request counters, overview numbers, daily series and their endpoints."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from anisync import metrics, permissions, stats, sync
from anisync.models import ProviderStat, SyncRun, UserSession
from anisync.providers import get_provider, mal
from tests.test_ownership import browser, make_user
from tests.test_sync import make_user as make_sync_user
from tests.test_sync import put, title

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def clean_counters():
    metrics.reset()
    yield
    metrics.reset()


async def stored(session) -> dict[str, ProviderStat]:
    return {r.provider: r for r in (await session.scalars(select(ProviderStat))).all()}


# ---- request counters ----------------------------------------------------------------------------------------


async def test_counters_are_written_in_batches_and_added_up(session):
    metrics.record("mal", 200, 0.1)
    metrics.record("mal", 200, 0.3)
    metrics.record("mal", 429, 0.0)
    metrics.record("mal", 503, 0.2)
    metrics.record("mal", None, 1.0)  # no answer at all
    metrics.record("anilist", 200, 0.05)
    assert await metrics.flush(session) == 6
    mal_stat = (await stored(session))["mal"]
    assert (mal_stat.requests, mal_stat.rate_limited, mal_stat.errors, mal_stat.total_ms) == (5, 1, 2, 1600)

    metrics.record("mal", 200, 0.4)  # a later flush adds to the same row
    await metrics.flush(session)
    again = (await stored(session))["mal"]
    await session.refresh(again)  # the upsert bypassed the ORM, which still holds the first values
    assert (again.requests, again.total_ms) == (6, 2000)
    assert metrics.pending() == {}
    assert await metrics.flush(session) == 0


async def test_a_failed_flush_keeps_the_counts_for_the_next_try(session, monkeypatch):
    metrics.record("mal", 200, 0.1)

    async def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(session, "execute", broken)
    with pytest.raises(RuntimeError):
        await metrics.flush(session)
    monkeypatch.undo()
    assert sum(c.requests for c in metrics.pending().values()) == 1
    assert await metrics.flush(session) == 1


@respx.mock
async def test_every_provider_request_is_counted_with_its_outcome():
    route = respx.get(f"{mal.API}/users/@me")
    route.side_effect = [
        httpx.Response(200, json={"id": 1, "name": "x"}),
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(500),
        httpx.ConnectError("down"),  # a read that fails at the network level is asked again twice: three attempts
        httpx.ConnectError("down"),
        httpx.ConnectError("down"),
    ]
    client = get_provider("mal").client("token")
    await client.whoami()
    for exc in ("RateLimited", "HTTPStatusError", "ConnectError"):
        with pytest.raises(Exception) as info:  # noqa: PT011 (three different errors on purpose)
            await client.whoami()
        assert type(info.value).__name__ == exc
    [counts] = metrics.pending().values()
    # every attempt counts as a request: 1 ok, 1 rate limited, 1 server error, 3 failed connections
    assert (counts.requests, counts.rate_limited, counts.errors) == (6, 1, 4)


# ---- sync runs ---------------------------------------------------------------------------------------------


async def test_a_run_remembers_how_many_changes_it_made(session):
    user = await make_sync_user(session)
    put("fa", title(1), title(2), title(3))
    await sync.run_sync(session, user.id, "manual")
    run = (await session.scalars(select(SyncRun))).one()
    assert (run.status, run.changes) == ("ok", 3)
    await sync.run_sync(session, user.id, "manual")
    assert [r.changes for r in (await session.scalars(select(SyncRun).order_by(SyncRun.id))).all()] == [3, 0]


# ---- overview ------------------------------------------------------------------------------------------------


async def test_the_overview_counts_users_accounts_and_activity(session):
    """Take the numbers, add known rows, expect exactly those differences (the database may hold other rows)."""
    before = await stats.overview(session)
    fa_before = next((p for p in before.providers if p.provider == "fa"), None)
    fb_before = next((p for p in before.providers if p.provider == "fb"), None)

    a, accounts_a, _ = await make_user(session, "a")  # signed up just now, seen just now
    b, accounts_b, _ = await make_user(session, "b")  # 3 days ago, seen 10 days ago, banned
    c, accounts_c, _ = await make_user(session, "c")  # 20 days ago, seen 40 days ago
    b.created_at, b.banned_at = NOW - timedelta(days=3), NOW
    c.created_at = NOW - timedelta(days=20)
    accounts_a["fa"].expires_at = NOW - timedelta(days=1)  # expired, can't be refreshed: needs a re-link
    accounts_b["fa"].expires_at = NOW + timedelta(days=10)  # expires soon, can't be refreshed
    accounts_b["fb"].refresh_token = "refreshable"  # expired but renewable by us: not a problem
    accounts_b["fb"].expires_at = NOW - timedelta(days=1)
    for account in (accounts_a["fb"], accounts_c["fa"], accounts_c["fb"]):
        account.expires_at = NOW + timedelta(days=90)  # fine
    c.master_account_id = accounts_c["fb"].id  # a different provider as master
    for user, ago in ((a, 0), (b, 10), (c, 40)):
        row = (await session.scalars(select(UserSession).where(UserSession.user_id == user.id))).one()
        row.last_seen_at = NOW - timedelta(days=ago)
    await session.flush()

    after = await stats.overview(session)
    assert after.users_total - before.users_total == 3
    assert after.users_with_master - before.users_with_master == 3
    assert after.new_today - before.new_today == 1  # only a
    assert after.new_7d - before.new_7d == 2  # a and b
    assert after.banned - before.banned == 1
    assert after.active_7d - before.active_7d == 1  # a
    assert after.active_30d - before.active_30d == 2  # a and b, not c
    fa = next(p for p in after.providers if p.provider == "fa")
    fb = next(p for p in after.providers if p.provider == "fb")

    def gained(now, was, field):
        return getattr(now, field) - (getattr(was, field) if was else 0)

    assert (gained(fa, fa_before, "accounts"), gained(fa, fa_before, "masters")) == (3, 2)
    assert (gained(fa, fa_before, "needs_relink"), gained(fa, fa_before, "expiring_soon")) == (1, 1)
    assert (gained(fb, fb_before, "accounts"), gained(fb, fb_before, "masters")) == (3, 1)
    assert (gained(fb, fb_before, "needs_relink"), gained(fb, fb_before, "expiring_soon")) == (
        0,
        0,
    )  # a refreshable one


# ---- daily series --------------------------------------------------------------------------------------------


async def test_the_daily_series_has_every_day_and_adds_up(session):
    user, _, _ = await make_user(session, "x")
    today = NOW.date()
    session.add_all(
        [
            SyncRun(user_id=user.id, trigger="scheduled", status="ok", summary={}, changes=5, started_at=NOW),
            SyncRun(user_id=user.id, trigger="scheduled", status="error", summary={}, changes=1, started_at=NOW),
            SyncRun(
                user_id=user.id,
                trigger="manual",
                status="ok",
                summary={},
                changes=7,
                started_at=NOW - timedelta(days=2),
            ),
            SyncRun(
                user_id=user.id,
                trigger="manual",
                status="ok",
                summary={},
                changes=9,
                started_at=NOW - timedelta(days=30),
            ),  # outside the window
            ProviderStat(day=today, provider="mal", requests=10, rate_limited=1, errors=2, total_ms=3000),
            ProviderStat(
                day=today - timedelta(days=40), provider="mal", requests=99, rate_limited=0, errors=0, total_ms=1
            ),
        ]
    )
    await session.flush()
    days, requests = await stats.daily(session, days=7)
    assert [d.day for d in days] == [today - timedelta(days=6 - i) for i in range(7)]
    last, two_ago = days[-1], days[-3]
    assert (last.runs_ok, last.runs_error, last.changes) == (1, 1, 6)
    assert (two_ago.runs_ok, two_ago.runs_error, two_ago.changes) == (1, 0, 7)
    assert sum(d.runs_ok + d.runs_error for d in days) == 3
    assert last.new_users >= 1
    [mal_today] = [r for r in requests if r.provider == "mal"]
    assert (mal_today.requests, mal_today.rate_limited, mal_today.errors, mal_today.avg_ms) == (10, 1, 2, 300)


# ---- endpoints -----------------------------------------------------------------------------------------------


async def test_the_stats_endpoints_return_the_numbers_to_admins(session):
    admin, _, token = await make_user(session, "admin")
    admin.role = "admin"
    await session.flush()
    async with browser(token) as c:
        overview = await c.get("/api/admin/stats/overview")
        daily = await c.get("/api/admin/stats/daily?days=3")
    assert overview.status_code == 200 and overview.json()["users_total"] >= 1
    assert {"providers", "banned", "active_7d"} <= set(overview.json())
    body = daily.json()
    assert len(body["days"]) == 3 and set(body["days"][0]) == {"day", "new_users", "runs_ok", "runs_error", "changes"}


@pytest.mark.parametrize("days", [0, 61, "x"])
async def test_the_daily_window_is_bounded(session, days):
    admin, _, token = await make_user(session, "admin")
    admin.role = "admin"
    await session.flush()
    async with browser(token) as c:
        assert (await c.get(f"/api/admin/stats/daily?days={days}")).status_code == 422


async def test_stats_need_the_stats_permission(session):
    permissions.ROLE_PERMISSIONS["banner"] = frozenset({permissions.USERS_BAN})
    try:
        plain, _, plain_token = await make_user(session, "plain")
        other, _, other_token = await make_user(session, "other")
        other.role = "banner"
        await session.flush()
        for token in (plain_token, other_token):
            async with browser(token) as c:
                assert (await c.get("/api/admin/stats/overview")).status_code == 403
                assert (await c.get("/api/admin/stats/daily")).status_code == 403
    finally:
        del permissions.ROLE_PERMISSIONS["banner"]


async def test_stats_refuse_signed_out_visitors(client):
    assert (await client.get("/api/admin/stats/overview")).status_code == 401
