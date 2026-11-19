"""The sync log: notable things per run are stored as events, never one row per entry (Phase 4.2)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from anisync import sync
from anisync.models import SyncRun, SyncRunEvent
from anisync.providers.base import ListEntry, RateLimited, Status
from tests.fakes import FakeClient
from tests.test_sync import make_user, put, title


async def events(session) -> list[SyncRunEvent]:
    return list((await session.scalars(select(SyncRunEvent).order_by(SyncRunEvent.id))).all())


async def test_a_clean_run_writes_no_events(session, fakes):
    user = await make_user(session)
    put("fa", *(title(n) for n in range(1, 40)))
    await sync.run_sync(session, user.id, "scheduled")
    assert await events(session) == []  # 39 titles synced, nothing notable


async def test_failed_changes_are_logged_with_the_title_they_concern(session, fakes):
    user = await make_user(session)
    put("fa", title(1), title(2))
    FakeClient.FAIL_ON = {"b2"}
    await sync.run_sync(session, user.id, "manual")
    [event] = await events(session)
    run = (await session.scalars(select(SyncRun))).one()
    assert (event.run_id, event.level, event.kind, event.media_ref, event.provider) == (
        run.id,
        "error",
        "change_failed",
        "b2",
        "fb",
    )
    assert "b2" in event.message and event.account_id == user.mirrors[0].id


async def test_rate_limits_are_their_own_kind(session, fakes, monkeypatch):
    user = await make_user(session)
    put("fa", title(1))

    async def limited(self, entry):
        raise RateLimited("fb", 30.0)

    monkeypatch.setattr(FakeClient, "save_entry", limited)
    await sync.run_sync(session, user.id, "manual")
    [event] = await events(session)
    assert (event.kind, event.level) == ("rate_limited", "warning")


async def test_unmapped_titles_and_reauth_are_logged_once_per_mirror(session, fakes):
    user = await make_user(session)
    put(
        "fa",
        title(1),
        ListEntry(media_id="a98", status=Status.PLANNING, title="Obscure OVA", external_ids={"fa": "a98"}),
        ListEntry(media_id="a99", status=Status.PLANNING, title="Obscure ONA", external_ids={"fa": "a99"}),
    )
    await sync.run_sync(session, user.id, "manual")
    [event] = await events(session)
    assert (event.kind, event.level) == ("unmapped", "info") and event.message.startswith("2 titles")

    user.mirrors[0].refresh_token = None
    user.mirrors[0].expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await sync.run_sync(session, user.id, "manual")
    assert [e.kind for e in await events(session)] == ["unmapped", "reauth"]


async def test_a_run_that_fails_everywhere_keeps_a_bounded_log(session, fakes, monkeypatch):
    user = await make_user(session)
    count = sync.MAX_EVENTS_PER_MIRROR + 25
    put("fa", *(title(n) for n in range(1, count + 1)))
    FakeClient.FAIL_ON = {f"b{n}" for n in range(1, count + 1)}
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert len(await events(session)) == sync.MAX_EVENTS_PER_MIRROR
    assert result["events_dropped"] == 25 and len(result["errors"]) == count  # the summary still has everything


async def test_a_preview_stores_no_events(session, fakes):
    user = await make_user(session)
    put("fa", title(1))
    FakeClient.FAIL_ON = {"b1"}
    await sync.run_sync(session, user.id, "manual", dry_run=True)
    assert await events(session) == []


async def test_events_go_away_with_their_run(session, fakes):
    user = await make_user(session)
    put("fa", title(1))
    FakeClient.FAIL_ON = {"b1"}
    await sync.run_sync(session, user.id, "manual")
    run = (await session.scalars(select(SyncRun))).one()
    run.started_at = datetime.now(UTC) - timedelta(days=31)
    await session.flush()
    assert await sync.purge_runs(session) == 1
    assert await events(session) == []
