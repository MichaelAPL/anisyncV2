"""Sync orchestration (sync.py) against in-memory providers: planning, applying, refreshing tokens, pushing edits."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anisync import sync
from anisync.models import LinkedAccount, SyncRun, User
from anisync.providers.base import ListEntry, ReauthRequired, Status
from tests.fakes import FakeClient

FUTURE = datetime.now(UTC) + timedelta(days=30)


async def make_user(session, master="fa", mirrors=("fb",), prune=False, tag="") -> User:
    user = User(display_name="kaito")
    session.add(user)
    accounts = {}
    for provider in (master, *mirrors):
        account = LinkedAccount(
            user=user,
            provider=provider,
            remote_user_id=f"{provider}-user{tag}",
            remote_username=provider,
            expires_at=FUTURE,
        )
        account.access_token = f"{provider}-token"
        account.refresh_token = f"{provider}-refresh"
        account.prune_missing = prune and provider != master
        session.add(account)
        accounts[provider] = account
    await session.flush()
    user.master_account_id = accounts[master].id
    await session.flush()
    return user


def put(provider: str, *entries: ListEntry) -> None:
    store = FakeClient.REMOTE.setdefault((provider, f"{provider}-user"), {})
    store.update({e.media_id: e for e in entries})


def remote(provider: str) -> dict[str, ListEntry]:
    return FakeClient.REMOTE.get((provider, f"{provider}-user"), {})


def title(n: int, provider: str = "fa", **kw) -> ListEntry:
    other = "fb" if provider == "fa" else "fa"
    return ListEntry(
        media_id=f"{provider[1]}{n}",
        status=kw.pop("status", Status.WATCHING),
        title=f"Title {n}",
        external_ids={"fa": f"a{n}", "fb": f"b{n}"} if other else {},
        **kw,
    )


async def test_preview_reports_changes_and_writes_nothing(session, fakes):
    user = await make_user(session)
    put("fa", title(1, progress=5), title(2, status=Status.COMPLETED))
    summary = await sync.run_sync(session, user.id, "preview", dry_run=True)
    [result] = summary.values()
    assert (result["create"], result["update"], result["delete"], result["unmapped"]) == (2, 0, 0, 0)
    assert {c["op"] for c in result["changes"]} == {"create"} and result["provider"] == "fb"
    assert remote("fb") == {}
    assert (await session.scalars(select(SyncRun))).all() == []  # a preview leaves no history


async def test_sync_writes_the_mirror_and_records_the_run(session, fakes):
    user = await make_user(session)
    put("fa", title(1, progress=5, score=87), title(2, status=Status.COMPLETED))
    summary = await sync.run_sync(session, user.id, "manual")
    [result] = summary.values()
    assert result["create"] == 2 and result["errors"] == []
    assert sorted(remote("fb")) == ["b1", "b2"]
    assert remote("fb")["b1"].progress == 5 and remote("fb")["b1"].score == 87
    run = (await session.scalars(select(SyncRun))).one()
    assert (run.trigger, run.status) == ("manual", "ok") and run.finished_at is not None
    mirror = user.mirrors[0]
    assert mirror.last_synced_at is not None


async def test_a_second_sync_finds_nothing_to_do(session, fakes):
    user = await make_user(session)
    put("fa", title(1, progress=5, score=87))
    await sync.run_sync(session, user.id, "manual")
    summary = await sync.run_sync(session, user.id, "daily")
    [result] = summary.values()
    assert (result["create"], result["update"], result["delete"]) == (0, 0, 0)  # score 87 vs the mirror's step of 10


async def test_changes_on_the_master_reach_the_mirror(session, fakes):
    user = await make_user(session)
    put("fa", title(1, progress=1))
    await sync.run_sync(session, user.id, "daily")
    put("fa", title(1, progress=9, status=Status.COMPLETED))
    [result] = (await sync.run_sync(session, user.id, "daily")).values()
    assert result["update"] == 1
    assert (remote("fb")["b1"].progress, remote("fb")["b1"].status) == (9, Status.COMPLETED)


async def test_titles_without_a_known_equivalent_are_reported_not_synced(session, fakes):
    user = await make_user(session)
    put(
        "fa",
        title(1),
        ListEntry(media_id="a99", status=Status.PLANNING, title="Obscure OVA", external_ids={"fa": "a99"}),
    )
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert result["unmapped"] == 1 and result["unmapped_titles"] == ["Obscure OVA"] and result["create"] == 1
    assert sorted(remote("fb")) == ["b1"]


async def test_mirror_only_titles_are_removed_only_when_pruning(session, fakes):
    for prune, expected in ((False, ["b1", "b7"]), (True, ["b1"])):
        FakeClient.REMOTE.clear()
        user = await make_user(session, prune=prune)
        put("fa", title(1))
        put("fb", title(7, "fb"))
        await sync.run_sync(session, user.id, "manual")
        assert sorted(remote("fb")) == expected, f"prune={prune}"
        await session.delete(user)
        await session.flush()


async def test_one_failing_entry_does_not_stop_the_others(session, fakes):
    user = await make_user(session)
    put("fa", title(1), title(2), title(3))
    FakeClient.FAIL_ON = {"b2"}
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert sorted(remote("fb")) == ["b1", "b3"]
    assert len(result["errors"]) == 1 and "b2" in result["errors"][0]


async def test_a_revoked_master_token_surfaces_to_the_caller(session, fakes):
    user = await make_user(session)
    FakeClient.REJECT_TOKENS = True
    with pytest.raises(ReauthRequired):
        await sync.run_sync(session, user.id, "manual")


async def test_a_user_without_a_master_is_skipped(session, fakes):
    user = await make_user(session)
    user.master_account_id = None
    await session.flush()
    assert await sync.run_sync(session, user.id, "daily") == {"skipped": "no master list selected"}


async def test_users_with_a_master_are_found_for_the_daily_fan_out(session, fakes):
    with_master = await make_user(session)
    without = await make_user(session, tag="-2")
    without.master_account_id = None
    await session.flush()
    ids = await sync.users_with_master(session)
    assert with_master.id in ids and without.id not in ids


# ---- tokens ------------------------------------------------------------------------------------------------


async def test_expiring_tokens_are_refreshed_through_the_providers_hook(session, fakes):
    user = await make_user(session)
    mirror = user.mirrors[0]  # "fb" can refresh
    mirror.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    client = await sync.client_for(mirror, session)
    assert fakes.refresh_calls == ["fb-refresh"]
    assert client.access_token == "fresh-access" and mirror.access_token == "fresh-access"
    assert mirror.refresh_token == "fresh-refresh" and mirror.expires_at > datetime.now(UTC)


async def test_providers_that_cannot_refresh_ask_the_user_to_relink(session, fakes):
    user = await make_user(session)
    user.master.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # "fa" has no refresh hook
    with pytest.raises(ReauthRequired, match="re-link"):
        await sync.client_for(user.master, session)
    assert fakes.refresh_calls == []


async def test_an_account_needing_relink_is_reported_per_mirror(session, fakes):
    user = await make_user(session)
    put("fa", title(1))
    user.mirrors[0].refresh_token = None
    user.mirrors[0].expires_at = datetime.now(UTC) - timedelta(minutes=1)
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert result["reauth_required"] is True
    run = (await session.scalars(select(SyncRun))).one()
    assert run.status == "error"


# ---- pushing single edits ----------------------------------------------------------------------------------


async def test_push_entry_writes_one_title_to_one_mirror(session, fakes):
    user = await make_user(session)
    mirror = user.mirrors[0]
    entry = title(1, progress=4, score=85)
    await sync.push_entry(session, mirror.id, "fa", "a1", entry)
    assert remote("fb")["b1"].progress == 4
    assert remote("fb")["b1"].score == 85 and remote("fb")["b1"].remote_entry_id == "entry-b1"


async def test_push_entry_skips_titles_it_cannot_map(session, fakes):
    user = await make_user(session)
    entry = ListEntry(media_id="a50", status=Status.WATCHING, external_ids={"fa": "a50"})
    await sync.push_entry(session, user.mirrors[0].id, "fa", "a50", entry)
    assert remote("fb") == {}


async def test_push_removal_deletes_only_when_the_mirror_prunes(session, fakes):
    for prune, expected in ((False, ["b1"]), (True, [])):
        FakeClient.REMOTE.clear()
        user = await make_user(session, prune=prune)
        put("fb", title(1, "fb"))
        async with sync.IdMapper(session) as mapper:
            await mapper.remember({"fa": "a1", "fb": "b1"})
        await sync.push_entry(session, user.mirrors[0].id, "fa", "a1", None)
        assert sorted(remote("fb")) == expected, f"prune={prune}"
        await session.delete(user)
        await session.flush()


async def test_push_entry_ignores_disabled_mirrors(session, fakes):
    user = await make_user(session)
    mirror = user.mirrors[0]
    mirror.sync_enabled = False
    await sync.push_entry(session, mirror.id, "fa", "a1", title(1))
    assert remote("fb") == {}
