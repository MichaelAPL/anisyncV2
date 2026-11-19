"""A user has one sync at a time, and a sync that dies never stays "running"."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select

from anisync import jobs, sync
from anisync.models import SyncRun
from anisync.providers.base import RateLimited
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user
from tests.test_sync import make_user as make_sync_user
from tests.test_sync_snapshots import requests, title

usesfakes = pytest.mark.usefixtures("fakes", "client")


async def open_run(session, user_id, started_ago=timedelta(minutes=1), finished=False):
    run = SyncRun(
        user_id=user_id,
        trigger="manual",
        summary={},
        started_at=datetime.now(UTC) - started_ago,
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
