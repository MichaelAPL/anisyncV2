"""Long syncs: waiting out rate limits, saving progress in batches, and closing runs that stopped halfway."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anisync import jobs, snapshots, sync
from anisync.models import SyncRun
from tests.fakes import FakeClient
from tests.test_sync import make_user
from tests.test_sync_snapshots import title

usesfakes = pytest.mark.usefixtures("fakes")


def master_has(n: int) -> None:
    FakeClient.REMOTE[("fa", "fa-user")] = {e.media_id: e for e in (title(i) for i in range(1, n + 1))}


async def mirror_snapshot(session, user) -> dict:
    mirror = next(a for a in user.accounts if a.provider == "fb")
    return {e.media_id for e in await snapshots.load(session, mirror.id)}


# ---- waiting out a rate limit -----------------------------------------------------------------------------------


@usesfakes
async def test_a_rate_limited_change_waits_for_the_retry_time_and_then_succeeds(session, no_real_waiting):
    user = await make_user(session)
    master_has(3)
    FakeClient.RATE_LIMITED = {"b2": 2}  # refused twice, accepted the third time
    summary = (await sync.run_sync(session, user.id, "manual"))[str(user.mirrors[0].id)]
    assert summary["create"] == 3 and not summary["errors"]
    assert no_real_waiting == [31.0, 31.0]  # "retry after 30s" plus a second
    assert await mirror_snapshot(session, user) == {"b1", "b2", "b3"}


@usesfakes
async def test_a_change_that_stays_rate_limited_fails_alone_after_a_few_waits(session, no_real_waiting):
    user = await make_user(session)
    master_has(3)
    FakeClient.RATE_LIMITED = {"b2": 99}
    summary = (await sync.run_sync(session, user.id, "manual"))[str(user.mirrors[0].id)]
    assert len(summary["errors"]) == 1 and "b2" in summary["errors"][0]  # only that one; the others went through
    assert len(no_real_waiting) == sync.RATE_LIMIT_RETRIES
    assert await mirror_snapshot(session, user) == {"b1", "b3"}  # the failed one is planned again next time


@usesfakes
async def test_the_wait_is_capped(session, no_real_waiting):
    user = await make_user(session)
    master_has(1)

    class Huge(Exception):  # stands in for a provider asking for an hour
        pass

    from anisync.providers.base import RateLimited

    original = FakeClient.save_entry
    calls = {"n": 0}

    async def slow(self, entry):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited(self.provider_id, 3600)
        return await original(self, entry)

    FakeClient.save_entry = slow
    try:
        await sync.run_sync(session, user.id, "manual")
    finally:
        FakeClient.save_entry = original
    assert no_real_waiting == [sync.MAX_WAIT + 1]


# ---- progress and batches ---------------------------------------------------------------------------------------


@usesfakes
async def test_a_run_records_how_much_it_planned_and_processed(session):
    user = await make_user(session)
    master_has(7)
    FakeClient.FAIL_ON = {"b3"}
    await sync.run_sync(session, user.id, "manual")
    run = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).one()
    assert (run.planned, run.processed) == (7, 7)  # failures count as processed: the run moved past them


@usesfakes
async def test_what_was_written_is_saved_even_if_the_sync_stops_halfway(session):
    user = await make_user(session)
    master_has(60)
    FakeClient.REAUTH_ON = "b40"  # the token is revoked part way through
    result = (await sync.run_sync(session, user.id, "manual"))[str(user.mirrors[0].id)]
    assert result["reauth_required"] is True
    saved = await mirror_snapshot(session, user)
    run = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).one()
    # the changes are made in the planner's order; everything before the failing one is remembered, nothing after it
    assert "b40" not in saved and 0 < len(saved) < 60 and run.processed == len(saved)


@usesfakes
async def test_progress_is_committed_every_batch_not_only_at_the_end(session, monkeypatch):
    user = await make_user(session)
    master_has(60)
    committed: list[int] = []  # the run's `processed` at each commit, i.e. what other connections could see then
    real_commit = session.commit

    async def spying_commit():
        run = next((o for o in session.identity_map.values() if isinstance(o, SyncRun)), None)
        if run is not None:
            committed.append(run.processed)
        await real_commit()

    monkeypatch.setattr(session, "commit", spying_commit)
    await sync.run_sync(session, user.id, "manual")
    assert sync.BATCH in committed and sync.BATCH * 2 in committed  # saved at 25 and 50, not just at 60


@usesfakes
async def test_a_batch_boundary_does_not_lose_or_duplicate_changes(session):
    user = await make_user(session)
    master_has(sync.BATCH * 2 + 1)
    await sync.run_sync(session, user.id, "manual")
    assert len(await mirror_snapshot(session, user)) == sync.BATCH * 2 + 1
    assert [r for r in FakeClient.REQUESTS if r[1] == "save"].count(("fb", "save")) == sync.BATCH * 2 + 1


# ---- runs that stopped halfway ----------------------------------------------------------------------------------


async def run_row(session, user_id, ago, finished=False, status="running"):
    row = SyncRun(
        user_id=user_id,
        trigger="manual",
        status=status,
        summary={},
        started_at=datetime.now(UTC) - ago,
        finished_at=datetime.now(UTC) if finished else None,
    )
    session.add(row)
    await session.commit()
    return row


@usesfakes
async def test_old_unfinished_runs_become_interrupted_and_other_runs_are_left_alone(session):
    user = await make_user(session)
    old = await run_row(session, user.id, timedelta(minutes=30))
    young = await run_row(session, user.id, timedelta(minutes=2))
    done = await run_row(session, user.id, timedelta(hours=2), finished=True, status="ok")
    assert await sync.close_interrupted_runs(session) >= 1
    for row in (old, young, done):
        await session.refresh(row)
    assert (old.status, old.finished_at is not None) == ("interrupted", True)
    assert (young.status, young.finished_at) == ("running", None)
    assert (done.status) == "ok"


@usesfakes
async def test_after_a_restart_every_open_run_is_closed(session):
    user = await make_user(session)
    young = await run_row(session, user.id, timedelta(minutes=2))
    await sync.close_interrupted_runs(session, everything=True)
    await session.refresh(young)
    assert young.status == "interrupted" and young.finished_at is not None


@usesfakes
async def test_the_daily_housekeeping_closes_stale_runs(session, monkeypatch):
    user = await make_user(session)
    old = await run_row(session, user.id, timedelta(hours=1))

    # housekeeping opens its own session; point it at the test session
    class Shared:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(jobs, "SessionLocal", Shared)
    await jobs.housekeeping(0)
    await session.refresh(old)
    assert old.status == "interrupted"


@usesfakes
async def test_the_dashboard_stops_treating_an_interrupted_run_as_running(session):
    user = await make_user(session)
    row = await run_row(session, user.id, timedelta(minutes=30))
    await sync.close_interrupted_runs(session)
    assert await sync.running_run(session, user.id) is None
    assert isinstance(row.id, int)
