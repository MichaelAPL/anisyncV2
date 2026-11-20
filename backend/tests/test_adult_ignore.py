"""The master-level switch: adult titles on the master list are not stored, shown or copied anywhere."""

import pytest
from sqlalchemy import select

from anisync import snapshots, sync
from anisync.engine import plan_sync
from anisync.models import ListSnapshot
from anisync.providers.base import ListEntry, Status
from tests.fakes import FakeClient
from tests.test_ownership import browser
from tests.test_ownership import make_user as make_web_user
from tests.test_sync import make_user, put, remote
from tests.test_sync_snapshots import title

usesfakes = pytest.mark.usefixtures("fakes", "client")


async def stored(session, account_id) -> set[str]:
    return set(
        (await session.scalars(select(ListSnapshot.external_id).where(ListSnapshot.account_id == account_id))).all()
    )


# ---- reading ----------------------------------------------------------------------------------------------------


async def test_adult_titles_are_dropped_before_anything_is_stored_when_the_owner_ignores_them(session, fakes):
    user = await make_user(session)
    put("fa", title(1, adult=True), title(2))
    user.ignore_adult_titles = True
    await session.flush()
    read = await sync.refresh_snapshot(
        session, user.master, FakeClient_for(user.master), is_master=True, drop_adult=True
    )
    assert [e.media_id for e in read.received] == ["a2"]
    assert await stored(session, user.master.id) == {"a2"}


def FakeClient_for(account):  # noqa: N802 (reads like the class it builds)
    from anisync.providers import get_provider

    return get_provider(account.provider).client("t", account.remote_user_id)


async def test_without_the_switch_adult_titles_are_stored_as_before(session, fakes):
    user = await make_user(session)
    put("fa", title(1, adult=True), title(2))
    await sync.refresh_snapshot(session, user.master, FakeClient_for(user.master), is_master=True)
    assert await stored(session, user.master.id) == {"a1", "a2"}


async def test_an_incremental_read_drops_them_too(session, fakes):
    user = await make_user(session)
    put("fa", title(2))
    await sync.refresh_snapshot(session, user.master, FakeClient_for(user.master), is_master=True, drop_adult=True)
    put("fa", title(3, adult=True), title(4))  # new on the master since
    read = await sync.refresh_snapshot(
        session, user.master, FakeClient_for(user.master), is_master=True, drop_adult=True
    )
    assert read.mode == "incremental"
    assert await stored(session, user.master.id) == {"a2", "a4"}


# ---- syncing ----------------------------------------------------------------------------------------------------


async def test_a_sync_copies_no_adult_title_to_any_mirror_when_the_master_ignores_them(session, fakes):
    user = await make_user(session)
    put("fa", title(1, adult=True), title(2))
    user.ignore_adult_titles = True
    await session.flush()
    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert result["create"] == 1 and sorted(remote("fb")) == ["b2"]
    user.ignore_adult_titles = False
    await session.flush()
    again = await sync.run_sync(session, user.id, "manual", full=True)
    assert sorted(remote("fb")) == ["b1", "b2"] and next(iter(again.values()))["create"] == 1


def test_adult_titles_already_on_a_mirror_are_never_deleted_because_the_master_ignores_them():
    master = [ListEntry(media_id="m2", status=Status.COMPLETED)]  # the adult m1 was dropped on read
    mirror = [
        ListEntry(media_id="k1", status=Status.COMPLETED, adult=True),  # the adult title's twin
        ListEntry(media_id="k9", status=Status.COMPLETED),  # a plain title that is on the mirror only
    ]
    id_map = {"m2": "k2"}
    without = plan_sync(master, mirror, id_map, 1, prune_missing=True)
    assert sorted(c.media_id for c in without.changes if c.op == "delete") == ["k1", "k9"]  # why the guard exists
    guarded = plan_sync(master, mirror, id_map, 1, prune_missing=True, protect_adult=True)
    assert [c.media_id for c in guarded.changes if c.op == "delete"] == ["k9"]


async def test_a_pruning_mirror_keeps_its_adult_titles_in_a_real_sync(session, fakes):
    user = await make_user(session, prune=True)
    put("fa", title(2))
    put("fb", title(1, "fb", adult=True), title(9, "fb"))
    user.ignore_adult_titles = True
    await session.flush()
    await sync.run_sync(session, user.id, "manual")
    assert sorted(remote("fb")) == ["b1", "b2"]  # b9 was removed, the adult b1 stayed


# ---- the switch -------------------------------------------------------------------------------------------------


@usesfakes
async def test_turning_it_on_forgets_stored_adult_titles_at_once_and_off_brings_them_back(session):
    user, accounts, token = await make_web_user(session, "ig1")
    FakeClient.REMOTE[("fa", "fa-ig1")] = {e.media_id: e for e in (title(1, adult=True), title(2))}
    async with browser(token) as c:
        assert {e["media_id"] for e in (await c.get("/api/list")).json()} == {"a1", "a2"}
        on = await c.patch("/api/me", json={"ignore_adult_titles": True})
        assert on.status_code == 200 and on.json()["ignore_adult_titles"] is True
        assert {e["media_id"] for e in (await c.get("/api/list")).json()} == {"a2"}  # gone from My list at once
        off = await c.patch("/api/me", json={"ignore_adult_titles": False})
        assert off.json()["ignore_adult_titles"] is False
        assert {e["media_id"] for e in (await c.get("/api/list")).json()} == {"a1", "a2"}  # read again in full


@usesfakes
async def test_a_list_read_while_the_switch_is_on_never_stores_adult_titles(session):
    user, accounts, token = await make_web_user(session, "ig2")
    FakeClient.REMOTE[("fa", "fa-ig2")] = {e.media_id: e for e in (title(1, adult=True), title(2))}
    async with browser(token) as c:
        await c.patch("/api/me", json={"ignore_adult_titles": True})
        assert {e["media_id"] for e in (await c.get("/api/list")).json()} == {"a2"}
    assert await stored(session, accounts["fa"].id) == {"a2"}


@usesfakes
async def test_refreshing_the_list_does_not_bring_in_adult_titles_added_on_the_provider(session):
    user, accounts, token = await make_web_user(session, "ig5")
    FakeClient.REMOTE[("fa", "fa-ig5")] = {e.media_id: e for e in (title(2),)}
    async with browser(token) as c:
        await c.patch("/api/me", json={"ignore_adult_titles": True})
        await c.get("/api/list")
        FakeClient.REMOTE[("fa", "fa-ig5")] |= {
            e.media_id: e for e in (title(3, adult=True), title(4))
        }  # added on the site
        refreshed = (await c.post("/api/list/refresh")).json()
        assert refreshed == {"mode": "incremental", "changed": 1}  # only the plain one counts
        assert {e["media_id"] for e in (await c.get("/api/list")).json()} == {"a2", "a4"}
    assert await stored(session, accounts["fa"].id) == {"a2", "a4"}


@usesfakes
async def test_choosing_another_master_clears_its_adult_titles_when_the_switch_is_on(session):
    user, accounts, token = await make_web_user(session, "ig3")
    await snapshots.upsert(session, accounts["fb"].id, [title(1, "fb", adult=True), title(2, "fb")], 1)
    mirror_id = accounts["fb"].id
    async with browser(token) as c:
        await c.patch("/api/me", json={"ignore_adult_titles": True})
        await c.put("/api/me/master", json={"account_id": mirror_id})
    assert await stored(session, mirror_id) == {"b2"}


@usesfakes
async def test_the_switch_is_off_by_default_and_needs_a_signed_in_user(session, client):
    _, _, token = await make_web_user(session, "ig4")
    async with browser(token) as c:
        assert (await c.get("/api/me")).json()["ignore_adult_titles"] is False
    assert (await client.patch("/api/me", json={"ignore_adult_titles": True})).status_code == 401
