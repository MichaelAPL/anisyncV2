import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from anisync.services import anilist, kitsu, mal
from anisync.services.base import ListEntry, ReauthRequired, Status


@respx.mock
async def test_mal_fetch_and_save():
    respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "node": {"id": 1, "title": "Cowboy Bebop", "num_episodes": 26},
                        "list_status": {
                            "status": "plan_to_watch",
                            "score": 9,
                            "num_episodes_watched": 0,
                            "is_rewatching": False,
                            "start_date": "2021-04",
                            "updated_at": "2022-01-01T00:00:00+00:00",
                        },
                    }
                ],
                "paging": {},
            },
        )
    )
    route = respx.patch(f"{mal.API}/anime/1/my_list_status").mock(return_value=httpx.Response(200, json={}))
    client = mal.MalClient("tok")
    [e] = await client.fetch_list()
    assert (e.status, e.score, e.started_at, e.mal_id) == (Status.PLANNING, 90, None, 1)

    await client.save_entry(ListEntry(media_id=1, status=Status.WATCHING, progress=3, score=87))
    form = parse_qs(route.calls.last.request.content.decode())
    assert form["status"] == ["watching"] and form["score"] == ["9"] and form["num_watched_episodes"] == ["3"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_anilist_fetch_dedupes_and_maps_repeating():
    e = {
        "id": 7,
        "mediaId": 1,
        "status": "REPEATING",
        "progress": 4,
        "updatedAt": 1700000000,
        "score": 85,
        "startedAt": {"year": 2020, "month": 5, "day": None},
        "completedAt": {"year": 2020, "month": 6, "day": 1},
        "media": {"idMal": 1, "episodes": 26, "title": {"userPreferred": "Bebop"}, "coverImage": None},
    }
    respx.post(anilist.GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"MediaListCollection": {"lists": [{"entries": [e]}, {"entries": [e]}]}}}
        )
    )
    [entry] = await anilist.AniListClient("tok", "42").fetch_list()
    assert entry.status == Status.WATCHING and entry.rewatching
    assert entry.started_at is None and entry.finished_at.isoformat() == "2020-06-01"
    assert entry.mal_id == 1 and entry.remote_entry_id == "7"


@respx.mock
async def test_anilist_save_sends_repeating():
    route = respx.post(anilist.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    await anilist.AniListClient("tok", "42").save_entry(
        ListEntry(media_id=1, status=Status.WATCHING, rewatching=True, score=None)
    )
    variables = json.loads(route.calls.last.request.content)["variables"]
    assert variables["status"] == "REPEATING" and variables["scoreRaw"] == 0 and "startedAt" not in variables


@respx.mock
async def test_kitsu_fetch_reads_mappings_and_creates_entry():
    respx.get(f"{kitsu.API}/library-entries", params={"filter[kind]": "anime"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "900",
                        "attributes": {
                            "status": "completed",
                            "progress": 26,
                            "ratingTwenty": 17,
                            "reconsuming": False,
                            "startedAt": "2020-01-02T00:00:00.000Z",
                            "finishedAt": None,
                            "updatedAt": "2022-01-01T00:00:00.000Z",
                        },
                        "relationships": {"anime": {"data": {"type": "anime", "id": "1"}}},
                    }
                ],
                "included": [
                    {
                        "type": "anime",
                        "id": "1",
                        "attributes": {"canonicalTitle": "Cowboy Bebop", "episodeCount": 26},
                        "relationships": {"mappings": {"data": [{"type": "mappings", "id": "m1"}]}},
                    },
                    {
                        "type": "mappings",
                        "id": "m1",
                        "attributes": {"externalSite": "myanimelist/anime", "externalId": "1"},
                    },
                ],
                "links": {},
            },
        )
    )
    client = kitsu.KitsuClient("tok", "55")
    [e] = await client.fetch_list()
    assert (e.status, e.score, e.mal_id, e.remote_entry_id) == (Status.COMPLETED, 85, 1, "900")
    assert e.started_at.isoformat() == "2020-01-02"

    respx.get(f"{kitsu.API}/library-entries", params={"filter[animeId]": "2"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    post = respx.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(201, json={}))
    await client.save_entry(ListEntry(media_id=2, status=Status.PLANNING, score=62))
    body = json.loads(post.calls.last.request.content)["data"]
    assert body["attributes"]["ratingTwenty"] == 12 and body["attributes"]["status"] == "planned"
    assert body["relationships"]["user"]["data"]["id"] == "55"


@respx.mock
async def test_unauthorized_raises_reauth():
    respx.get(f"{mal.API}/users/@me/animelist").mock(return_value=httpx.Response(401))
    with pytest.raises(ReauthRequired):
        await mal.MalClient("bad").fetch_list()
