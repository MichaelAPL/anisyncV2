"""A user has one sync at a time, and a sync that dies never stays "running"."""

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select

from anisync import jobs, main, sync
from anisync.models import SyncRun
from anisync.providers.base import RateLimited
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user
from tests.test_sync import make_user as make_sync_user
from tests.test_sync_snapshots import requests, title

usesfakes = pytest.mark.usefixtures("fakes", "client")


async def open_run(session, user_id, started_ago=timedelta(minutes=1), finished=False, heartbeat_ago=None):
    run = SyncRun(
        user_id=user_id,
        trigger="manual",
        summary={},
        started_at=datetime.now(UTC) - started_ago,
        heartbeat_at=datetime.now(UTC) - heartbeat_ago if heartbeat_ago is not None else None,
        finished_at=datetime.now(UTC) if finished else None,
    )
    session.add(run)
    await session.commit()
    return run


# ---- one sync at a time --------------------------------------------------------------------------------------


@usesfakes
async def test_a_second_sync_does_not_start_while_one_is_running(session):
    user = await make_sync_user(session)
    FakeClient.REMOTE[("fa", "fa-user")] = {"a1": title(1)}
    await open_run(session, user.id)
    assert await sync.run_sync(session, user.id, "manual") == {"skipped": "a sync is already running"}
    assert requests() == []  # nothing was read or written
    assert len((await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).all()) == 1


@usesfakes
async def test_a_finished_run_does_not_block_the_next_one(session):
    user = await make_sync_user(session)
    await open_run(session, user.id, finished=True)
    result = await sync.run_sync(session, user.id, "manual")
    assert "skipped" not in result


@usesfakes
async def test_a_run_that_never_finished_stops_blocking_after_fifteen_minutes(session):
    user = await make_sync_user(session)
    await open_run(session, user.id, started_ago=timedelta(minutes=20))
    assert "skipped" not in await sync.run_sync(session, user.id, "manual")


@usesfakes
async def test_only_the_same_users_runs_count(session):
    user = await make_sync_user(session)
    other = await make_sync_user(session, tag="x")
    await open_run(session, other.id)
    assert "skipped" not in await sync.run_sync(session, user.id, "manual")


@usesfakes
async def test_previews_are_allowed_while_a_sync_runs(session):
    user = await make_sync_user(session)
    await open_run(session, user.id)
    assert "skipped" not in await sync.run_sync(session, user.id, "preview", dry_run=True)


@usesfakes
async def test_the_sync_button_endpoint_refuses_while_one_runs_and_queues_otherwise(session):
    user, _, token = await make_user(session, "run")
    with jobs.app.replace_connector(InMemoryConnector()):
        async with browser(token) as c:
            run = await open_run(session, user.id)
            refused = await c.post("/api/sync")
            assert refused.status_code == 409 and "already running" in refused.json()["detail"]
            run.finished_at = datetime.now(UTC)
            await session.commit()
            assert (await c.post("/api/sync")).json() == {"queued": True}


# ---- a sync that dies is closed, not left "running" -----------------------------------------------------------


@usesfakes
async def test_a_sync_that_crashes_is_marked_failed_and_the_error_still_propagates(session):
    user = await make_sync_user(session)
    FakeClient.FETCH_ERROR = httpx.ConnectError("the provider is down")
    with pytest.raises(httpx.ConnectError):
        await sync.run_sync(session, user.id, "manual")
    run = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).one()
    assert run.status == "error" and run.finished_at is not None
    assert await sync.running_run(session, user.id) is None  # so the next sync can start


# ---- provider failures are not "internal server error" ----------------------------------------------------------


@usesfakes
async def test_the_list_says_a_list_site_is_unreachable_instead_of_an_internal_error(session):
    _, _, token = await make_user(session, "down")
    FakeClient.FETCH_ERROR = httpx.ReadTimeout("slow")
    async with browser(token) as c:
        response = await c.get("/api/list")
    assert response.status_code == 502
    assert response.json() == {"detail": "A list site isn't responding right now. Try again in a moment"}


@usesfakes
async def test_the_list_reports_rate_limits_with_the_providers_name(session):
    _, _, token = await make_user(session, "busy")
    FakeClient.FETCH_ERROR = RateLimited("fa", 30)
    async with browser(token) as c:
        response = await c.get("/api/list")
    assert response.status_code == 429 and response.json() == {"detail": "FA is busy right now. Try again in a minute"}


@usesfakes
async def test_a_crash_is_closed_even_when_the_rollback_expires_everything(session, monkeypatch):
    # In production the rollback expires every loaded object; touching one afterwards is I/O outside the right context
    # (it crashed the cleanup and left the run "running"). The test session doesn't expire on rollback, so make it.
    user_id = (await make_sync_user(session)).id
    real_rollback = session.rollback

    async def rollback_and_expire():
        await real_rollback()
        session.expire_all()

    monkeypatch.setattr(session, "rollback", rollback_and_expire)
    FakeClient.FETCH_ERROR = httpx.ReadTimeout("slow")
    with pytest.raises(httpx.ReadTimeout):
        await sync.run_sync(session, user_id, "manual")
    run = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user_id))).one()
    assert run.status == "error" and run.finished_at is not None


# ---- deploys: a restart closes only runs that stopped, and a stopping app closes its own --------------------------


@pytest.fixture
def app_started(session, monkeypatch):
    """The app's real startup and shutdown (its in-process worker on an in-memory queue), on the test session."""

    class Shared:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main, "SessionLocal", Shared)
    monkeypatch.setattr(jobs, "SessionLocal", Shared)
    monkeypatch.setattr(main.settings, "run_worker_in_process", True)
    monkeypatch.setattr(jobs.app.periodic_registry, "periodic_tasks", {})  # no scheduled jobs racing the test
    monkeypatch.setattr(main.app.state, "worker", None, raising=False)
    connector = InMemoryConnector()

    @contextlib.asynccontextmanager
    async def started():
        with jobs.app.replace_connector(connector):
            async with main.lifespan(main.app):
                yield

    return started


@usesfakes
async def test_a_long_run_still_making_progress_survives_a_restart_and_keeps_blocking(session, app_started):
    user = await make_sync_user(session)
    run = await open_run(session, user.id, started_ago=timedelta(minutes=20), heartbeat_ago=timedelta(seconds=30))
    async with app_started():
        await session.refresh(run)
        assert (run.status, run.finished_at) == ("running", None)
        assert await sync.start_run(session, user.id, "manual") is None


@usesfakes
async def test_a_run_without_a_heartbeat_for_sixteen_minutes_is_closed_at_startup(session, app_started):
    user = await make_sync_user(session)
    run = await open_run(session, user.id, started_ago=timedelta(minutes=20), heartbeat_ago=timedelta(minutes=16))
    async with app_started():
        await session.refresh(run)
        assert run.status == "interrupted" and run.finished_at is not None
        assert await sync.start_run(session, user.id, "manual") is not None


@usesfakes
async def test_stopping_the_app_mid_sync_closes_the_run_as_failed_within_the_grace_period(
    session, app_started, monkeypatch
):
    user = await make_sync_user(session)
    FakeClient.REMOTE[("fa", "fa-user")] = {"a1": title(1)}
    writing = asyncio.Event()

    async def endless_write(self, entry):
        writing.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(FakeClient, "save_entry", endless_write)
    monkeypatch.setattr(main, "WORKER_SHUTDOWN_GRACE", 0.2)
    async with asyncio.timeout(20):
        async with app_started():
            await jobs.enqueue_user_sync(user.id, "manual")
            await writing.wait()
            stopping = time.monotonic()
    assert time.monotonic() - stopping < 0.2 + 3  # the grace period plus closing the run, not "until the sync ends"
    run = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).one()
    assert run.status == "error" and run.finished_at is not None


# ---- heartbeats: a run is stale only when it stops making progress ----------------------------------------------


def master_has(n: int) -> None:
    FakeClient.REMOTE[("fa", "fa-user")] = {e.media_id: e for e in (title(i) for i in range(1, n + 1))}


@usesfakes
async def test_every_saved_batch_moves_the_heartbeat_forward(session, monkeypatch):
    user = await make_sync_user(session)
    master_has(60)  # three batches: 25, 25 and 10
    clock = [datetime.now(UTC)]

    def ticking():
        clock[0] += timedelta(seconds=1)
        return clock[0]

    monkeypatch.setattr(sync, "_now", ticking)
    beats: dict[int, datetime | None] = {}  # the run's heartbeat at the last commit seen with each `processed` count
    real_commit = session.commit

    async def spying_commit():
        run = next((o for o in session.identity_map.values() if isinstance(o, SyncRun)), None)
        if run is not None:
            beats[run.processed] = run.heartbeat_at
        await real_commit()

    monkeypatch.setattr(session, "commit", spying_commit)
    await sync.run_sync(session, user.id, "manual")
    assert None not in (beats[25], beats[50], beats[60])
    assert beats[25] < beats[50] < beats[60]


@usesfakes
async def test_a_slow_sync_is_never_treated_as_stale_while_it_makes_progress(session, monkeypatch):
    user = await make_sync_user(session)
    master_has(3)
    FakeClient.RATE_LIMITED = {"b1": 2, "b2": 2, "b3": 2}  # six "slow down" waits while writing
    clock = [datetime.now(UTC)]
    began = clock[0]
    monkeypatch.setattr(sync, "_now", lambda: clock[0])
    seen: list[tuple[bool, int]] = []  # at each step: (a second start refused?, runs closed as interrupted)

    async def time_passes(minutes: int) -> None:
        clock[0] += timedelta(minutes=minutes)
        refused = await sync.start_run(session, user.id, "manual") is None
        seen.append((refused, await sync.close_interrupted_runs(session)))

    real_fetch = FakeClient.fetch_list

    async def slow_read(self, since=None):
        await time_passes(10)
        return await real_fetch(self, since)

    async def slow_wait(seconds: float) -> None:
        await time_passes(6)

    monkeypatch.setattr(FakeClient, "fetch_list", slow_read)
    monkeypatch.setattr(sync, "_pause", slow_wait)
    summary = await sync.run_sync(session, user.id, "manual")
    assert clock[0] - began > 3 * sync.RUN_STALE_AFTER  # far longer than the stale limit, in steps
    assert seen and seen == [(True, 0)] * len(seen)
    runs = (await session.scalars(select(SyncRun).where(SyncRun.user_id == user.id))).all()
    assert [r.status for r in runs] == ["ok"] and summary[str(user.mirrors[0].id)]["create"] == 3
