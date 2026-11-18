"""Incremental reads: only what changed after a cursor, newest first, stopping early."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from anisync.providers import anilist, kitsu, mal

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def mal_item(media_id: int, updated: datetime) -> dict:
    return {
        "node": {"id": media_id, "title": f"T{media_id}", "num_episodes": 12},
        "list_status": {"status": "watching", "score": 8, "num_episodes_watched": 3, "updated_at": updated.isoformat()},
    }


def al_item(media_id: int, updated: datetime) -> dict:
    return {
        "id": media_id * 10,
        "mediaId": media_id,
        "status": "CURRENT",
        "progress": 3,
        "updatedAt": int(updated.timestamp()),
        "score": 80,
        "startedAt": {"year": None, "month": None, "day": None},
        "completedAt": {"year": None, "month": None, "day": None},
        "media": {"idMal": media_id, "episodes": 12, "title": {"userPreferred": f"T{media_id}"}, "coverImage": None},
    }


def al_page(items: list[dict], has_next: bool = False) -> httpx.Response:
    return httpx.Response(200, json={"data": {"Page": {"pageInfo": {"hasNextPage": has_next}, "mediaList": items}}})


# ---- MAL ---------------------------------------------------------------------------------------------------


@respx.mock
async def test_mal_returns_only_entries_newer_than_the_cursor_in_one_request():
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [mal_item(3, at(30)), mal_item(2, at(20)), mal_item(1, at(10))],
                "paging": {"next": "https://next"},
            },
        )
    )
    entries = await mal.MalClient("tok").fetch_list(since=at(20))
    assert [e.media_id for e in entries] == ["3"]  # 20 is not *after* the cursor; nothing past it is read
    assert route.call_count == 1
    params = route.calls.last.request.url.params
    assert params["sort"] == "list_updated_at" and params["limit"] == "100"


@respx.mock
async def test_mal_with_nothing_new_costs_one_request():
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(
            200, json={"data": [mal_item(1, at(10)), mal_item(2, at(5))], "paging": {"next": "https://next"}}
        )
    )
    assert await mal.MalClient("tok").fetch_list(since=at(10)) == []
    assert route.call_count == 1


@respx.mock
async def test_mal_keeps_paging_while_everything_is_newer():
    page2 = "https://api.myanimelist.net/v2/users/@me/animelist?offset=100"
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        side_effect=[
            httpx.Response(200, json={"data": [mal_item(5, at(50)), mal_item(4, at(40))], "paging": {"next": page2}}),
            httpx.Response(200, json={"data": [mal_item(3, at(30)), mal_item(1, at(5))], "paging": {"next": page2}}),
        ]
    )
    entries = await mal.MalClient("tok").fetch_list(since=at(10))
    assert [e.media_id for e in entries] == ["5", "4", "3"] and route.call_count == 2


@respx.mock
async def test_mal_full_reads_are_unchanged():
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(200, json={"data": [mal_item(1, at(1))], "paging": {}})
    )
    await mal.MalClient("tok").fetch_list()
    params = route.calls.last.request.url.params
    assert "sort" not in params and params["limit"] == "1000"


# ---- AniList -----------------------------------------------------------------------------------------------


@respx.mock
async def test_anilist_returns_only_newer_entries_and_stops_at_the_first_older_one():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        return_value=al_page([al_item(3, at(30)), al_item(2, at(20)), al_item(1, at(10))], has_next=True)
    )
    entries = await anilist.AniListClient("tok", "42").fetch_list(since=at(20))
    assert [e.media_id for e in entries] == ["3"] and route.call_count == 1
    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {"userId": 42, "page": 1} and "UPDATED_TIME_DESC" in body["query"]


@respx.mock
async def test_anilist_with_nothing_new_costs_one_request():
    route = respx.post(anilist.GRAPHQL_URL).mock(return_value=al_page([al_item(1, at(10))], has_next=True))
    assert await anilist.AniListClient("tok", "42").fetch_list(since=at(10)) == []
    assert route.call_count == 1


@respx.mock
async def test_anilist_entries_sharing_the_cursor_timestamp_are_not_read_again():
    """A bulk import gives thousands of entries one timestamp; none of them may be re-read on every sync."""
    same = [al_item(i, at(10)) for i in range(1, 51)]
    route = respx.post(anilist.GRAPHQL_URL).mock(return_value=al_page(same, has_next=True))
    assert await anilist.AniListClient("tok", "42").fetch_list(since=at(10)) == []
    assert route.call_count == 1


@respx.mock
async def test_anilist_pages_while_everything_is_newer():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        side_effect=[
            al_page([al_item(4, at(40)), al_item(3, at(30))], has_next=True),
            al_page([al_item(2, at(20)), al_item(1, at(5))], has_next=True),
            al_page([al_item(0, at(1))]),  # never requested
        ]
    )
    entries = await anilist.AniListClient("tok", "42").fetch_list(since=at(10))
    assert [e.media_id for e in entries] == ["4", "3", "2"] and route.call_count == 2
    assert [json.loads(c.request.content)["variables"]["page"] for c in route.calls] == [1, 2]


@respx.mock
async def test_anilist_recent_entries_parse_like_full_reads():
    respx.post(anilist.GRAPHQL_URL).mock(return_value=al_page([al_item(7, at(30))]))
    [entry] = await anilist.AniListClient("tok", "42").fetch_list(since=at(0))
    assert entry.external_ids == {"anilist": "7", "mal": "7"} and entry.remote_entry_id == "70"
    assert entry.updated_at == at(30) and entry.title == "T7"


@respx.mock
async def test_anilist_stops_when_the_last_page_is_reached():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        return_value=al_page([al_item(2, at(20)), al_item(1, at(15))], has_next=False)
    )
    entries = await anilist.AniListClient("tok", "42").fetch_list(since=at(0))
    assert len(entries) == 2 and route.call_count == 1


# ---- descriptors -------------------------------------------------------------------------------------------


def test_providers_declare_whether_they_read_incrementally():
    from anisync.providers import get_provider

    assert get_provider("mal").incremental_reads and get_provider("anilist").incremental_reads
    assert not get_provider("kitsu").incremental_reads


@respx.mock
async def test_kitsu_ignores_the_cursor_and_reads_everything():
    from tests.helpers import load_fixture

    respx.get(f"{kitsu.API}/library-entries").mock(
        return_value=httpx.Response(200, json=load_fixture("kitsu/library_entries.json"))
    )
    assert len(await kitsu.KitsuClient("tok", "55").fetch_list(since=at(0))) == 1


@respx.mock
async def test_anilist_incremental_reads_skip_entries_the_full_read_never_returns():
    """Hidden from the status lists and in no custom list: the grouped collection omits them, so we must too."""
    hidden = {**al_item(5, at(50)), "hiddenFromStatusLists": True, "customLists": None}
    hidden_but_custom = {
        **al_item(4, at(40)),
        "hiddenFromStatusLists": True,
        "customLists": {"Favourites": True, "Old": False},
    }
    custom_names = {**al_item(3, at(30)), "hiddenFromStatusLists": True, "customLists": ["Favourites"]}
    only_off = {**al_item(2, at(20)), "hiddenFromStatusLists": True, "customLists": {"Old": False}}
    visible = {**al_item(1, at(10)), "hiddenFromStatusLists": False, "customLists": None}
    respx.post(anilist.GRAPHQL_URL).mock(
        return_value=al_page([hidden, hidden_but_custom, custom_names, only_off, visible])
    )
    entries = await anilist.AniListClient("tok", "42").fetch_list(since=at(0))
    assert [e.media_id for e in entries] == ["4", "3", "1"]


@respx.mock
async def test_the_cursor_still_advances_past_skipped_entries():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        return_value=al_page(
            [{**al_item(9, at(90)), "hiddenFromStatusLists": True, "customLists": None}, al_item(1, at(10))]
        )
    )
    assert [e.media_id for e in await anilist.AniListClient("tok", "42").fetch_list(since=at(5))] == ["1"]
    assert "hiddenFromStatusLists" in json.loads(route.calls.last.request.content)["query"]
