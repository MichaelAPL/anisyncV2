"""The unmatched titles queue and manual linking (Phase 4.8)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from anisync import moderation, permissions, sync, unmatched
from anisync.mapping import IdMapper
from anisync.models import (
    AdminAction,
    MediaExternalId,
    UnmatchedTitle,
    UnmatchedTitleUser,
)
from anisync.providers.base import ListEntry, Status
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user
from tests.test_ownership import make_user as make_browser_user
from tests.test_sync import make_user as make_sync_user
from tests.test_sync import put, remote, title

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


def odd(n: int, name: str | None = None) -> ListEntry:
    """A title on the master (fa) that no mirror (fb) id is known for."""
    return ListEntry(
        media_id=f"o{n}", status=Status.PLANNING, title=name or f"Obscure {n}", external_ids={"fa": f"o{n}"}
    )


@pytest.fixture
async def admin(session):
    user, _, token = await make_browser_user(session, "root")
    user.role = "admin"
    await session.flush()
    return user, token


async def call(token, method, url, **kwargs):
    async with browser(token) as c:
        return await c.request(method, url, **kwargs)


async def queue(token, **params):
    return (await call(token, "GET", "/api/admin/titles/unmatched", params={"limit": 200, **params})).json()


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


# ---- collecting ------------------------------------------------------------------------------------------------


async def test_a_sync_records_the_titles_it_could_not_translate(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", title(1), odd(1, "Weird OVA"), odd(2, "Weird ONA"))
    await sync.run_sync(session, user.id, "manual")
    found = {t["source_id"]: t for t in await queue(token)}
    assert {k: (v["source"], v["target"], v["title"], v["users"]) for k, v in found.items()} == {
        "o1": ("fa", "fb", "Weird OVA", 1),
        "o2": ("fa", "fb", "Weird ONA", 1),
    }


async def test_a_preview_records_nothing(session, admin):
    user = await make_sync_user(session)
    put("fa", odd(1))
    await sync.run_sync(session, user.id, "manual", dry_run=True)
    assert await count(session, UnmatchedTitle) == 0


async def test_the_queue_counts_how_many_users_have_each_title_and_ranks_by_it(session, admin):
    _, token = admin
    one = await make_sync_user(session)
    two = await make_sync_user(session, tag="2")
    FakeClient.REMOTE[("fa", "fa-user2")] = {}
    put("fa", odd(1, "Shared"), odd(2, "Only mine"))
    FakeClient.REMOTE[("fa", "fa-user2")] = {"o1": odd(1, "Shared")}
    await sync.run_sync(session, one.id, "manual")
    await sync.run_sync(session, two.id, "manual")
    found = await queue(token)
    assert [(t["source_id"], t["users"]) for t in found] == [("o1", 2), ("o2", 1)]  # most affected first


async def test_a_user_who_fixes_their_list_drops_out_of_the_count(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", odd(1), odd(2))
    await sync.run_sync(session, user.id, "manual")
    assert {t["source_id"] for t in await queue(token)} == {"o1", "o2"}
    FakeClient.REMOTE[("fa", "fa-user")] = {"o1": odd(1)}  # the other one was removed on the master
    await sync.run_sync(session, user.id, "manual", full=True)
    assert {t["source_id"] for t in await queue(token)} == {"o1"}


async def test_record_replaces_the_users_set_and_keeps_others(session):
    one = await make_sync_user(session)
    two = await make_sync_user(session, tag="2")
    await unmatched.record(session, one.id, "fa", "fb", [odd(1), odd(2)])
    await unmatched.record(session, two.id, "fa", "fb", [odd(1)])
    await unmatched.record(session, one.id, "fa", "fb", [odd(2), odd(3)])
    links = {(r.source_id, r.user_id) for r in (await session.scalars(select(UnmatchedTitleUser))).all()}
    assert links == {("o1", two.id), ("o2", one.id), ("o3", one.id)}
    await unmatched.record(session, one.id, "fa", "fb", [])
    assert {(r.source_id, r.user_id) for r in (await session.scalars(select(UnmatchedTitleUser))).all()} == {
        ("o1", two.id)
    }


# ---- listing ---------------------------------------------------------------------------------------------------


async def test_the_queue_can_be_filtered_searched_and_paged(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    await unmatched.record(session, user.id, "fa", "fb", [odd(1, "Alpha 100%"), odd(2, "Beta"), odd(3, "Gamma")])
    await unmatched.record(
        session, user.id, "fb", "fa", [ListEntry(media_id="z1", status=Status.PLANNING, title="Zed")]
    )
    assert {t["title"] for t in await queue(token, source="fb")} == {"Zed"}
    assert {t["title"] for t in await queue(token, target="fb")} == {"Alpha 100%", "Beta", "Gamma"}
    assert {t["title"] for t in await queue(token, q="alp")} == {"Alpha 100%"}
    assert {t["title"] for t in await queue(token, q="%")} == {"Alpha 100%"}  # a wildcard matches only itself
    assert {t["title"] for t in await queue(token, q="_")} == set()
    first = await queue(token, target="fb", limit=2)
    rest = await queue(token, target="fb", limit=2, offset=2)
    assert len(first) == 2 and len(rest) == 1 and not {t["source_id"] for t in first} & {t["source_id"] for t in rest}
    assert (await call(token, "GET", "/api/admin/titles/unmatched?limit=0")).status_code == 422
    assert (await call(token, "GET", "/api/admin/titles/unmatched?offset=-1")).status_code == 422


async def test_titles_that_have_been_mapped_since_are_not_listed_and_get_purged(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    await unmatched.record(session, user.id, "fa", "fb", [odd(1), odd(2), odd(3)])
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "o1", "fb": "learned-1"}, title="Obscure 1")  # someone learned it meanwhile
    assert {t["source_id"] for t in await queue(token)} == {"o2", "o3"}
    old = await session.get(UnmatchedTitle, ("fa", "o3", "fb"))
    old.last_seen_at = NOW - timedelta(days=40)
    await session.flush()
    assert await unmatched.purge(session) == 2  # the resolved one and the stale one
    assert {t.source_id for t in (await session.scalars(select(UnmatchedTitle))).all()} == {"o2"}


async def test_deleting_a_user_removes_their_part_of_the_queue(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    await unmatched.record(session, user.id, "fa", "fb", [odd(1)])
    from anisync import deletion

    await deletion.delete_user(session, user)
    assert await count(session, UnmatchedTitleUser) == 0 and await queue(token) == []


# ---- linking ---------------------------------------------------------------------------------------------------


async def test_linking_a_title_fixes_it_for_every_user_at_their_next_sync(session, admin):
    root, token = admin
    one = await make_sync_user(session)
    two = await make_sync_user(session, tag="2")
    put("fa", odd(1, "Weird OVA"))
    FakeClient.REMOTE[("fa", "fa-user2")] = {"o1": odd(1, "Weird OVA")}
    await sync.run_sync(session, one.id, "manual")
    await sync.run_sync(session, two.id, "manual")
    assert remote("fb") == {}

    body = {"source": "fa", "source_id": "o1", "target": "fb", "target_id": "b777"}
    assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 200
    assert await queue(token) == [] and await count(session, UnmatchedTitle) == 0  # and the row itself is gone
    link = await session.scalar(
        select(MediaExternalId).where(MediaExternalId.provider == "fb", MediaExternalId.external_id == "b777")
    )
    assert link.source == "manual"

    await sync.run_sync(session, one.id, "manual")
    await sync.run_sync(session, two.id, "manual")
    assert list(remote("fb")) == ["b777"] and list(FakeClient.REMOTE[("fb", "fb-user2")]) == ["b777"]
    [action] = (await session.scalars(select(AdminAction).where(AdminAction.action == "title.link"))).all()
    assert action.actor_id == root.id
    assert action.details == {
        "source_provider": "fa",
        "source_id": "o1",
        "target_provider": "fb",
        "target_id": "b777",
    }


async def test_a_link_can_name_a_title_that_was_never_in_the_queue(session, admin):
    _, token = admin
    body = {"source": "fa", "source_id": "new1", "target": "fb", "target_id": "b1"}
    assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 200
    async with IdMapper(session) as mapper:
        assert (await mapper.resolve("fa", "fb", ["new1"])) == {"new1": "b1"}


async def test_linking_is_idempotent(session, admin):
    _, token = admin
    body = {"source": "fa", "source_id": "o1", "target": "fb", "target_id": "b1"}
    assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 200
    assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 200
    assert await count(session, MediaExternalId) >= 2


async def test_conflicting_links_are_refused_and_nothing_changes(session, admin):
    _, token = admin
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "a1", "fb": "b1"}, title="One")
        await mapper.remember({"fa": "a2", "fb": "b2"}, title="Two")
    link = "/api/admin/titles/link"
    # fb b1 belongs to a1; a2 is a different title
    taken = await call(token, "POST", link, json={"source": "fa", "source_id": "a2", "target": "fb", "target_id": "b1"})
    assert taken.status_code == 409 and "different title" in taken.json()["detail"]
    # a1 already has fb id b1; it can't also be b9
    second = await call(
        token, "POST", link, json={"source": "fa", "source_id": "a1", "target": "fb", "target_id": "b9"}
    )
    assert second.status_code == 409 and "b1" in second.json()["detail"]
    async with IdMapper(session) as mapper:
        assert await mapper.resolve("fa", "fb", ["a1", "a2"]) == {"a1": "b1", "a2": "b2"}
    assert (await session.scalars(select(AdminAction).where(AdminAction.action == "title.link"))).all() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "nope"},
        {"target": "nope"},
        {"target": "fa"},  # the same provider on both sides
        {"target_id": ""},
        {"target_id": "has space"},
        {"source_id": "../x"},
        {"target_id": "x" * 65},
    ],
)
async def test_bad_links_are_refused(admin, bad):
    _, token = admin
    body = {"source": "fa", "source_id": "o1", "target": "fb", "target_id": "b1"} | bad
    assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 422


async def test_manual_links_survive_a_later_import_of_the_dataset(session, admin):
    _, token = admin
    await call(
        token,
        "POST",
        "/api/admin/titles/link",
        json={"source": "fa", "source_id": "o1", "target": "fb", "target_id": "b1"},
    )
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "o1", "fb": "other"}, title="Dataset says otherwise", source="seed")
    link = await session.scalar(
        select(MediaExternalId).where(
            MediaExternalId.provider == "fb",
            MediaExternalId.media_id
            == (
                await session.scalar(
                    select(MediaExternalId.media_id).where(
                        MediaExternalId.provider == "fa", MediaExternalId.external_id == "o1"
                    )
                )
            ),
        )
    )
    assert (link.external_id, link.source) == ("b1", "manual")


async def test_the_queue_and_linking_need_titles_link(session, client):
    permissions.ROLE_PERMISSIONS["reader"] = frozenset({permissions.USERS_READ})
    try:
        reader, _, token = await make_user(session, "reader")
        reader.role = "reader"
        await session.flush()
        body = {"source": "fa", "source_id": "o1", "target": "fb", "target_id": "b1"}
        assert (await client.get("/api/admin/titles/unmatched")).status_code == 401
        assert (await call(token, "GET", "/api/admin/titles/unmatched")).status_code == 403
        assert (await call(token, "POST", "/api/admin/titles/link", json=body)).status_code == 403
    finally:
        del permissions.ROLE_PERMISSIONS["reader"]
    assert moderation and sync
