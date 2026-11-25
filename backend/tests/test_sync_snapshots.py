"""Syncing through snapshots: what each kind of run costs in provider requests, and what it notices."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from anisync import snapshots, sync
from anisync.models import SyncRun
from anisync.providers.base import ListEntry, Status
from tests.fakes import FakeClient
from tests.test_sync import make_user


def title(n: int, provider: str = "fa", **kw) -> ListEntry:
    """Title n as `provider` reports it, last updated 'now' on the provider's clock."""
    kw.setdefault("status", Status.WATCHING)
    kw.setdefault("updated_at", FakeClient.stamp())
    return ListEntry(
        media_id=f"{provider[1]}{n}", title=f"Title {n}", external_ids={"fa": f"a{n}", "fb": f"b{n}"}, **kw
    )


def put(provider: str, *entries: ListEntry, tag: str = "") -> None:
    FakeClient.REMOTE.setdefault((provider, f"{provider}-user{tag}"), {}).update({e.media_id: e for e in entries})


def remote(provider: str, tag: str = "") -> dict[str, ListEntry]:
    return FakeClient.REMOTE.get((provider, f"{provider}-user{tag}"), {})


def requests() -> list[tuple[str, str]]:
    """The provider requests since the last call (and forget them)."""
    seen, FakeClient.REQUESTS = FakeClient.REQUESTS, []
    return seen


async def synced(session, fakes, count=3):
    """A user whose master 'fa' has `count` titles and whose first full sync to mirror 'fb' has run."""
    user = await make_user(session)
    put("fa", *(title(n, progress=n) for n in range(1, count + 1)))
    await sync.run_sync(session, user.id, "manual")
    requests()
    return user


# ---- what a run costs ---------------------------------------------------------------------------------------


async def test_the_first_sync_reads_both_lists_in_full_and_writes_the_mirror(session, fakes):
    user = await make_user(session)
    put("fa", title(1), title(2))
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert result["read"] == "full" and result["create"] == 2 and result["errors"] == []
    assert requests() == [("fa", "full"), ("fb", "full"), ("fb", "save"), ("fb", "save")]
    assert sorted(remote("fb")) == ["b1", "b2"]
    assert user.master.last_full_read_at and user.mirrors[0].last_full_read_at
    assert user.master.sync_cursor == max(e.updated_at for e in remote("fa").values())


async def test_a_sync_with_nothing_new_costs_one_request(session, fakes):
    user = await synced(session, fakes)
    [result] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert requests() == [("fa", "since")]  # the master's incremental read; the mirror isn't asked at all
    assert (result["create"], result["update"], result["delete"], result["read"]) == (0, 0, 0, "snapshot")


async def test_only_the_changed_title_is_read_and_written(session, fakes):
    user = await synced(session, fakes)
    put("fa", title(2, progress=99))
    [result] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert requests() == [("fa", "since"), ("fb", "save")]
    assert result["update"] == 1 and remote("fb")["b2"].progress == 99


async def test_the_cursor_follows_the_provider_clock_and_never_moves_back(session, fakes):
    user = await synced(session, fakes)
    first = user.master.sync_cursor
    put("fa", title(1, progress=50))
    await sync.run_sync(session, user.id, "scheduled")
    second = user.master.sync_cursor
    await sync.run_sync(session, user.id, "scheduled")  # nothing new
    assert second > first and user.master.sync_cursor == second


async def test_a_failed_change_is_retried_by_the_next_run_without_the_master_changing(session, fakes):
    user = await make_user(session)
    put("fa", title(1), title(2))
    FakeClient.FAIL_ON = {"b2"}
    [first] = (await sync.run_sync(session, user.id, "manual")).values()
    assert len(first["errors"]) == 1 and sorted(remote("fb")) == ["b1"]
    FakeClient.FAIL_ON = set()
    requests()
    [second] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert second["create"] == 1 and second["errors"] == [] and sorted(remote("fb")) == ["b1", "b2"]
    assert requests() == [("fa", "since"), ("fb", "save")]  # nothing re-read: the plan came from the snapshots


async def test_a_preview_reads_but_never_writes_and_the_sync_after_it_reuses_what_it_read(session, fakes):
    user = await make_user(session)
    put("fa", title(1), title(2))
    [preview] = (await sync.run_sync(session, user.id, "preview", dry_run=True)).values()
    assert preview["create"] == 2 and preview["read"] == "full" and requests() == [("fa", "full"), ("fb", "full")]
    assert remote("fb") == {} and (await session.scalars(select(SyncRun))).all() == []
    await sync.run_sync(session, user.id, "manual")
    assert requests() == [("fa", "since"), ("fb", "save"), ("fb", "save")]  # no second full read of either list


async def test_providers_that_cannot_read_incrementally_are_read_in_full_every_time(session, fakes):
    user = await make_user(session, master="fb", mirrors=("fa",))
    put("fb", title(1, "fb"))
    await sync.run_sync(session, user.id, "manual")
    requests()
    await sync.run_sync(session, user.id, "scheduled")
    assert requests() == [("fb", "full")]


async def test_a_full_recheck_can_be_forced(session, fakes):
    user = await synced(session, fakes)
    await sync.run_sync(session, user.id, "manual", full=True)
    assert requests() == [("fa", "full"), ("fb", "full")]


# ---- what only a full read notices ---------------------------------------------------------------------------


async def test_edits_made_on_the_mirrors_site_are_overwritten_at_the_weekly_full_read(session, fakes):
    user = await synced(session, fakes)
    mirror = user.mirrors[0]
    put(
        "fb",
        ListEntry(
            media_id="b1",
            status=Status.DROPPED,
            progress=0,
            external_ids={"fa": "a1", "fb": "b1"},
            title="Title 1",
            updated_at=FakeClient.stamp(),
        ),
    )

    [quiet] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert quiet["update"] == 0 and remote("fb")["b1"].status == Status.DROPPED  # not looked at yet

    mirror.last_full_read_at = datetime.now(UTC) - timedelta(days=8)
    [weekly] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert weekly["read"] == "full" and weekly["drifted"] == 1 and weekly["update"] == 1
    assert remote("fb")["b1"].status == Status.WATCHING and remote("fb")["b1"].progress == 1


async def test_a_full_read_that_finds_nothing_changed_reports_no_drift(session, fakes):
    user = await synced(session, fakes)
    user.mirrors[0].last_full_read_at = datetime.now(UTC) - timedelta(days=8)
    [result] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert result["read"] == "full" and result["drifted"] == 0 and result["update"] == 0


async def test_removals_on_the_master_reach_a_pruning_mirror_at_the_next_full_read(session, fakes):
    user = await make_user(session, prune=True)
    put("fa", title(1), title(2))
    await sync.run_sync(session, user.id, "manual")
    del FakeClient.REMOTE[("fa", "fa-user")]["a2"]  # removed on the master's own site
    [quiet] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert quiet["delete"] == 0 and sorted(remote("fb")) == ["b1", "b2"]  # an incremental read can't see removals
    [full] = (await sync.run_sync(session, user.id, "manual", full=True)).values()
    assert full["delete"] == 1 and sorted(remote("fb")) == ["b1"]
    assert [e.media_id for e in await snapshots.load(session, user.master.id)] == ["a1"]


async def test_the_weekly_schedule(session, fakes):
    user = await synced(session, fakes)
    account = user.master
    now = datetime.now(UTC)
    assert not sync.full_read_due(account, now)
    assert sync.full_read_due(account, now + sync.FULL_READ_EVERY + timedelta(minutes=1))
    account.last_full_read_at = None
    assert sync.full_read_due(account, now)


# ---- editing through AniSyncHub ---------------------------------------------------------------------------------


async def test_a_pushed_edit_updates_the_mirrors_snapshot_so_no_resync_is_needed(session, fakes):
    user = await synced(session, fakes)
    edited = title(1, progress=77)
    put("fa", edited)  # the master was edited through AniSyncHub (written there and in its snapshot)
    await snapshots.upsert(session, user.master.id, [edited], 1)
    await sync.push_entry(session, user.mirrors[0].id, "fa", "a1", edited)
    assert remote("fb")["b1"].progress == 77
    requests()
    [result] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert result["update"] == 0 and requests() == [("fa", "since")]  # the mirror's snapshot already matches


async def test_a_pruning_push_removes_the_title_from_the_mirrors_snapshot(session, fakes):
    user = await make_user(session, prune=True)
    put("fa", title(1))
    await sync.run_sync(session, user.id, "manual")
    mirror = user.mirrors[0]
    await sync.push_entry(session, mirror.id, "fa", "a1", None)
    assert remote("fb") == {} and await snapshots.load(session, mirror.id) == []


# ---- scheduling and retention ---------------------------------------------------------------------------------


def test_every_user_has_one_hour_a_day_and_the_next_run_is_in_it():
    now = datetime(2026, 11, 18, 22, 30, tzinfo=UTC)
    for user_id in (1, 7, 23, 24, 25, 1000):
        nxt = sync.next_sync_at(user_id, now)
        assert nxt.hour == user_id % 24 and nxt.minute == 0 and now < nxt <= now + timedelta(hours=24)
    assert sync.next_sync_at(22, now) == datetime(2026, 11, 19, 22, 0, tzinfo=UTC)  # this hour is already half over
    assert sync.next_sync_at(23, now) == datetime(2026, 11, 18, 23, 0, tzinfo=UTC)


async def test_each_tick_picks_only_the_users_of_that_hour(session, fakes):
    users = [await make_user(session, tag=f"-{i}") for i in range(30)]
    by_hour: dict[int, set[int]] = {}
    for hour in range(24):
        by_hour[hour] = set(await sync.users_in_slot(session, hour)) & {u.id for u in users}
    assert set().union(*by_hour.values()) == {u.id for u in users}  # everyone is covered...
    assert sum(len(v) for v in by_hour.values()) == len(users)  # ...exactly once a day
    assert all(sync.sync_hour(uid) == hour for hour, ids in by_hour.items() for uid in ids)
    assert max(map(len, by_hour.values())) <= 2  # 30 sequential users are spread out, not bunched


async def test_users_without_a_master_are_never_scheduled(session, fakes):
    user = await make_user(session)
    user.master_account_id = None
    await session.flush()
    assert user.id not in await sync.users_in_slot(session, sync.sync_hour(user.id))


async def test_sync_history_retention(session, fakes):
    a, b = await make_user(session, tag="-a"), await make_user(session, tag="-b")
    now = datetime.now(UTC)
    session.add_all(
        [SyncRun(user_id=a.id, trigger="scheduled", summary={}, started_at=now - timedelta(days=40))]  # too old
        + [
            SyncRun(user_id=a.id, trigger="scheduled", summary={}, started_at=now - timedelta(minutes=i))
            for i in range(60)
        ]
        + [SyncRun(user_id=b.id, trigger="scheduled", summary={}, started_at=now - timedelta(days=1))]
    )
    await session.flush()
    assert await sync.purge_runs(session) == 1 + 10  # the 40-day-old one, and the 10 beyond the newest 50
    kept = (await session.scalars(select(SyncRun.user_id))).all()
    assert kept.count(a.id) == 50 and kept.count(b.id) == 1


async def test_a_master_whose_snapshot_was_purged_is_read_in_full_at_its_next_sync(session, fakes):
    from anisync.providers import get_provider

    user = await make_user(session)
    master = user.master
    put("fa", title(1), title(2))
    client = get_provider(master.provider).client("t", master.remote_user_id)
    await sync.refresh_snapshot(session, master, client, is_master=True)
    master.last_full_read_at = datetime.now(UTC) - timedelta(days=90)

    assert await snapshots.purge_stale(session) == 1
    assert await snapshots.count(session, master.id) == 0
    FakeClient.REQUESTS.clear()

    read = await sync.refresh_snapshot(session, master, client, is_master=True)

    assert read.mode == "full"  # the next sync rebuilds the whole snapshot from the provider
    assert await snapshots.count(session, master.id) == 2
