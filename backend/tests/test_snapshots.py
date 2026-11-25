from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from anisync import snapshots
from anisync.models import LinkedAccount, ListSnapshot, User
from anisync.providers.base import ListEntry, Status


async def make_account(session, provider="fa", remote="u1") -> LinkedAccount:
    user = User(display_name=remote)
    session.add(user)
    account = LinkedAccount(user=user, provider=provider, remote_user_id=remote, remote_username=remote)
    account.access_token = "token"
    session.add(account)
    await session.flush()
    return account


def entry(n, **kw) -> ListEntry:
    kw.setdefault("status", Status.WATCHING)
    kw.setdefault("title", f"Title {n}")
    return ListEntry(media_id=f"a{n}", external_ids={"fa": f"a{n}"}, **kw)


async def test_entries_roundtrip_through_the_snapshot(session, fakes):
    account = await make_account(session)
    original = entry(
        1,
        progress=5,
        score=80,
        rewatching=True,
        started_at=date(2026, 1, 2),
        updated_at=datetime(2026, 5, 6, tzinfo=UTC),
        total_episodes=12,
        image_url="https://img.example/1.jpg",
        remote_entry_id="r1",
    )
    await snapshots.upsert(session, account.id, [original], 1)
    assert await snapshots.load(session, account.id) == [original]
    assert (
        await snapshots.get(session, account.id, "a1") == original
        and await snapshots.get(session, account.id, "zz") is None
    )


async def test_upsert_updates_existing_rows_and_leaves_others(session, fakes):
    account = await make_account(session)
    await snapshots.upsert(session, account.id, [entry(1, progress=1), entry(2, progress=1)], 1)
    await snapshots.upsert(session, account.id, [entry(1, progress=9, status=Status.COMPLETED)], 1)
    by_id = {e.media_id: e for e in await snapshots.load(session, account.id)}
    assert (by_id["a1"].progress, by_id["a1"].status) == (9, Status.COMPLETED) and by_id["a2"].progress == 1
    assert await snapshots.count(session, account.id) == 2


async def test_a_batch_with_the_same_title_twice_keeps_the_last(session, fakes):
    account = await make_account(session)
    await snapshots.upsert(session, account.id, [entry(1, progress=1), entry(1, progress=2)], 1)
    assert [e.progress for e in await snapshots.load(session, account.id)] == [2]


async def test_upserting_nothing_is_fine(session, fakes):
    account = await make_account(session)
    await snapshots.upsert(session, account.id, [], 1)
    assert await snapshots.count(session, account.id) == 0


async def test_snapshots_are_kept_per_account(session, fakes):
    a, b = await make_account(session, remote="a"), await make_account(session, remote="b")
    await snapshots.upsert(session, a.id, [entry(1, progress=1)], 1)
    await snapshots.upsert(session, b.id, [entry(1, progress=7)], 1)
    assert [e.progress for e in await snapshots.load(session, a.id)] == [1]
    await snapshots.remove(session, a.id, ["a1"])
    assert await snapshots.count(session, a.id) == 0 and await snapshots.count(session, b.id) == 1


async def test_replace_all_reports_what_changed_behind_our_back(session, fakes):
    account = await make_account(session)
    await snapshots.upsert(session, account.id, [entry(1, progress=1), entry(2, progress=2), entry(3, progress=3)], 1)
    # On the provider: 1 is untouched, 2 was edited on their site, 3 was removed, 4 is new.
    result = await snapshots.replace_all(
        session, account.id, [entry(1, progress=1), entry(2, progress=20), entry(4)], 1
    )
    assert (result.added, result.removed, result.drifted) == (1, 1, 1)
    assert {e.media_id: e.progress for e in await snapshots.load(session, account.id)} == {"a1": 1, "a2": 20, "a4": 0}
    again = await snapshots.replace_all(session, account.id, [entry(1, progress=1), entry(2, progress=20), entry(4)], 1)
    assert (again.added, again.removed, again.drifted) == (0, 0, 0)  # the new state is the baseline


async def test_replace_all_on_an_empty_snapshot_just_adds(session, fakes):
    account = await make_account(session)
    result = await snapshots.replace_all(session, account.id, [entry(1), entry(2)], 1)
    assert (result.added, result.removed, result.drifted) == (2, 0, 0)


def test_the_hash_covers_only_synced_fields_at_the_providers_precision():
    base = entry(1, progress=3, score=84)
    assert snapshots.synced_hash(base, 10) == snapshots.synced_hash(entry(1, progress=3, score=80), 10)  # 84 ~ 80
    assert snapshots.synced_hash(base, 1) != snapshots.synced_hash(entry(1, progress=3, score=80), 1)
    assert snapshots.synced_hash(base, 1) == snapshots.synced_hash(entry(1, progress=3, score=84, title="Renamed"), 1)
    for changed in (
        entry(1, progress=4, score=84),
        entry(1, progress=3, score=84, rewatching=True),
        entry(1, status=Status.DROPPED, progress=3, score=84),
        entry(1, progress=3, score=84, finished_at=date(2026, 1, 1)),
    ):
        assert snapshots.synced_hash(base, 1) != snapshots.synced_hash(changed, 1)


def test_newest_update():
    e1, e2 = (
        entry(1, updated_at=datetime(2026, 1, 1, tzinfo=UTC)),
        entry(2, updated_at=datetime(2026, 3, 1, tzinfo=UTC)),
    )
    assert snapshots.newest_update([e1, entry(3), e2]) == e2.updated_at
    assert snapshots.newest_update([entry(3)]) is None and snapshots.newest_update([]) is None


async def test_deleting_the_account_deletes_its_snapshot(session, fakes):
    account = await make_account(session)
    await snapshots.upsert(session, account.id, [entry(1)], 1)
    await session.delete(account)
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(ListSnapshot)) == 0


async def test_purge_stale_drops_snapshots_nobody_has_read_in_a_long_time(session, fakes):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    old = await make_account(session, remote="old")
    fresh = await make_account(session, remote="fresh")
    never = await make_account(session, remote="never")
    old.last_full_read_at, old.sync_cursor = now - timedelta(days=61), now - timedelta(days=61)
    fresh.last_full_read_at, fresh.sync_cursor = now - timedelta(days=59), now - timedelta(days=59)
    for account in (old, fresh):
        await snapshots.upsert(session, account.id, [entry(1)], 1)

    assert await snapshots.purge_stale(session, now=now) == 1

    await session.refresh(old)
    await session.refresh(fresh)
    assert await snapshots.count(session, old.id) == 0
    assert old.last_full_read_at is None and old.sync_cursor is None  # the next sync starts with a full read
    assert await snapshots.count(session, fresh.id) == 1
    assert fresh.last_full_read_at is not None and fresh.sync_cursor is not None
    await session.refresh(never)
    assert never.last_full_read_at is None


async def test_a_purged_account_gets_a_full_read_at_its_next_sync(session, fakes):
    from anisync import sync

    account = await make_account(session)
    account.last_full_read_at = datetime.now(UTC) - timedelta(days=90)
    await snapshots.purge_stale(session)
    await session.refresh(account)
    assert sync.full_read_due(account)
