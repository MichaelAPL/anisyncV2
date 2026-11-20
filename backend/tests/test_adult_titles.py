"""Adult titles: the flag each provider gives, leaving them out of a mirror on request, and blurring their covers."""

import json

import httpx
import pytest
import respx

from anisync import snapshots, sync
from anisync.engine import plan_sync
from anisync.providers import anilist, kitsu, mal
from anisync.providers.base import ListEntry, Status
from tests.fakes import FakeClient
from tests.test_ownership import browser
from tests.test_ownership import make_user as make_web_user
from tests.test_sync import make_user, put, remote
from tests.test_sync_snapshots import title

usesfakes = pytest.mark.usefixtures("fakes", "client")


# ---- what each provider says ------------------------------------------------------------------------------------


@respx.mock
async def test_mal_marks_only_explicit_titles_as_adult_and_asks_for_the_field():
    def node(i, nsfw, *genres):
        node = {"id": i, "title": f"T{i}", "nsfw": nsfw, "genres": [{"id": 1, "name": g} for g in genres]}
        return {"node": node, "list_status": {"status": "completed"}}

    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    node(1, "white", "Comedy"),
                    node(2, "gray", "Ecchi"),  # borderline, not adult
                    node(3, "black"),
                    node(4, "gray", "Hentai"),  # MAL gives its hentai titles `gray`, with the Hentai genre
                    node(5, "white", "Hentai"),
                ]
            },
        )
    )
    entries = await mal.MalClient("tok").fetch_list()
    assert {e.media_id: e.adult for e in entries} == {"1": False, "2": False, "3": True, "4": True, "5": True}
    params = route.calls.last.request.url.params
    assert "nsfw" in params["fields"] and "genres" in params["fields"]
    assert params["nsfw"] == "true"  # without nsfw=true MAL leaves adult titles out of the list altogether


def test_anilist_reads_is_adult_in_both_queries():
    assert "isAdult" in anilist.LIST_QUERY
    entry = anilist.AniListClient._entry(
        {"id": 1, "mediaId": 5, "status": "COMPLETED", "media": {"isAdult": True, "title": {"userPreferred": "x"}}}
    )
    assert entry.adult is True
    plain = anilist.AniListClient._entry(
        {"id": 1, "mediaId": 5, "status": "COMPLETED", "media": {"title": {"userPreferred": "x"}}}
    )
    assert plain.adult is False


@respx.mock
async def test_kitsu_reads_the_nsfw_attribute_of_the_anime():
    def item(i, nsfw):
        return {
            "id": f"e{i}",
            "attributes": {"status": "completed", "progress": 1},
            "relationships": {"anime": {"data": {"id": str(i)}}},
        }, {"type": "anime", "id": str(i), "attributes": {"canonicalTitle": f"T{i}", "nsfw": nsfw}, "relationships": {}}

    a, a_inc = item(1, False)
    b, b_inc = item(2, True)
    route = respx.get(f"{kitsu.API}/library-entries").mock(
        return_value=httpx.Response(200, json={"data": [a, b], "included": [a_inc, b_inc]})
    )
    entries = await kitsu.KitsuClient("tok", remote_user_id="1").fetch_list()
    assert {e.media_id: e.adult for e in entries} == {"1": False, "2": True}
    assert "nsfw" in route.calls.last.request.url.params["fields[anime]"]


async def test_the_flag_survives_the_snapshot(session, fakes):
    user = await make_user(session)
    await snapshots.upsert(session, user.master.id, [title(1, adult=True), title(2)], 1)
    loaded = {e.media_id: e.adult for e in await snapshots.load(session, user.master.id)}
    assert loaded == {"a1": True, "a2": False}
    assert json.loads(json.dumps(title(1, adult=True).to_dict()))["adult"] is True


# ---- leaving adult titles out of a mirror -----------------------------------------------------------------------


def test_the_planner_skips_adult_titles_only_when_asked_and_never_deletes_their_twins():
    master = [
        ListEntry(media_id="m1", status=Status.COMPLETED, adult=True),
        ListEntry(media_id="m2", status=Status.COMPLETED),
    ]
    id_map = {"m1": "k1", "m2": "k2"}
    off = plan_sync(master, [], id_map, 1)
    assert sorted(c.media_id for c in off.changes) == ["k1", "k2"] and off.adult_skipped == 0
    on = plan_sync(master, [], id_map, 1, skip_adult=True)
    assert [c.media_id for c in on.changes] == ["k2"] and on.adult_skipped == 1
    # The mirror already has the adult title (and one that isn't on the master): pruning must keep the adult one.
    mirror = [
        ListEntry(media_id="k1", status=Status.DROPPED, adult=True),
        ListEntry(media_id="k9", status=Status.COMPLETED),
    ]
    pruned = plan_sync(master, mirror, id_map, 1, prune_missing=True, skip_adult=True)
    assert sorted((c.op, c.media_id) for c in pruned.changes) == [("create", "k2"), ("delete", "k9")]


def test_an_adult_title_without_a_match_is_not_listed_as_unmatched_when_adult_titles_are_skipped():
    master = [ListEntry(media_id="m1", status=Status.COMPLETED, title="X", adult=True)]
    assert plan_sync(master, [], {"m1": None}, 1, skip_adult=True).unmapped == []
    assert len(plan_sync(master, [], {"m1": None}, 1).unmapped) == 1


async def test_a_sync_leaves_adult_titles_out_of_a_mirror_that_asked_and_counts_them(session, fakes):
    user = await make_user(session)
    put("fa", title(1, adult=True), title(2), title(3, adult=True))
    user.mirrors[0].skip_adult = True
    await session.flush()
    result = (await sync.run_sync(session, user.id, "manual"))[str(user.mirrors[0].id)]
    assert result["create"] == 1 and result["adult_skipped"] == 2
    assert sorted(remote("fb")) == ["b2"]
    assert not result.get("errors")
    # Turning it off copies them on the next sync.
    user.mirrors[0].skip_adult = False
    await session.flush()
    again = (await sync.run_sync(session, user.id, "manual"))[str(user.mirrors[0].id)]
    assert again["create"] == 2 and "adult_skipped" not in again
    assert sorted(remote("fb")) == ["b1", "b2", "b3"]


async def test_other_mirrors_still_get_adult_titles(session, fakes):
    from tests.fakes import register_fake

    register_fake("fc", score_step=1)
    try:
        user = await make_user(session, mirrors=("fb", "fc"))
        put("fa", title(1, adult=True))
        FakeClient.REMOTE.pop(("fc", "fc-user"), None)
        skipping = next(m for m in user.mirrors if m.provider == "fb")
        skipping.skip_adult = True
        await session.flush()
        results = await sync.run_sync(session, user.id, "manual")
        by_provider = {r["provider"]: r for r in results.values()}
        assert by_provider["fb"]["adult_skipped"] == 1 and "adult_skipped" not in by_provider["fc"]
    finally:
        from anisync.providers.registry import unregister

        unregister("fc")


async def test_an_edit_to_an_adult_title_is_not_pushed_to_a_mirror_that_skips_them(session, fakes):
    user = await make_user(session)
    mirror = user.mirrors[0]
    mirror.skip_adult = True
    await session.flush()
    async with sync.IdMapper(session) as mapper:
        await mapper.remember({"fa": "a1", "fb": "b1"})
        await mapper.remember({"fa": "a2", "fb": "b2"})
    await sync.push_entry(session, mirror.id, "fa", "a1", title(1, adult=True))
    assert remote("fb") == {}
    await sync.push_entry(session, mirror.id, "fa", "a2", title(2))
    assert sorted(remote("fb")) == ["b2"]


# ---- the API ----------------------------------------------------------------------------------------------------


@usesfakes
async def test_the_setting_and_the_cover_choice_are_saved_per_account_and_per_user(session):
    user, accounts, token = await make_web_user(session, "ad1")
    async with browser(token) as c:
        me = (await c.get("/api/me")).json()
        assert me["show_adult_covers"] is False and all(a["skip_adult"] is False for a in me["accounts"])
        patched = (await c.patch(f"/api/accounts/{accounts['fb'].id}", json={"skip_adult": True})).json()
        assert {a["provider"]: a["skip_adult"] for a in patched["accounts"]} == {"fa": False, "fb": True}
        shown = (await c.patch("/api/me", json={"show_adult_covers": True})).json()
        assert shown["show_adult_covers"] is True and shown["display_name"] == user.display_name


@usesfakes
async def test_nobody_can_change_someone_elses_account_setting(session):
    _, mine, token = await make_web_user(session, "ad2")
    _, theirs, _ = await make_web_user(session, "ad3")
    async with browser(token) as c:
        assert (await c.patch(f"/api/accounts/{theirs['fb'].id}", json={"skip_adult": True})).status_code in (403, 404)
    await session.refresh(theirs["fb"])
    assert theirs["fb"].skip_adult is False and mine


@usesfakes
async def test_the_list_says_which_titles_are_adult_and_the_summary_how_many_were_left_out(session):
    user, accounts, token = await make_web_user(session, "ad4")
    FakeClient.REMOTE[("fa", "fa-ad4")] = {e.media_id: e for e in (title(1, adult=True), title(2))}
    accounts["fb"].skip_adult = True
    await session.flush()
    async with browser(token) as c:
        listed = {e["media_id"]: e["adult"] for e in (await c.get("/api/list")).json()}
        assert listed == {"a1": True, "a2": False}
        preview = (await c.get("/api/sync/preview")).json()
    assert next(iter(preview.values()))["adult_skipped"] == 1
