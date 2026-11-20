import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from anisync.providers import anilist, kitsu, mal
from anisync.providers.base import ListEntry, ReauthRequired, Status
from tests.helpers import load_fixture


@respx.mock
async def test_mal_fetch_and_save():
    respx.get(f"{mal.API}/users/@me/animelist").mock(
        return_value=httpx.Response(200, json=load_fixture("mal/animelist.json"))
    )
    route = respx.patch(f"{mal.API}/anime/1/my_list_status").mock(return_value=httpx.Response(200, json={}))
    client = mal.MalClient("tok")
    [e] = await client.fetch_list()
    assert (e.status, e.score, e.started_at, e.media_id) == (Status.PLANNING, 90, None, "1")
    assert e.external_ids == {"mal": "1"}

    await client.save_entry(ListEntry(media_id="1", status=Status.WATCHING, progress=3, score=87))
    form = parse_qs(route.calls.last.request.content.decode())
    assert form["status"] == ["watching"] and form["score"] == ["9"] and form["num_watched_episodes"] == ["3"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_anilist_fetch_dedupes_and_maps_repeating():
    respx.post(anilist.GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("anilist/list_collection.json"))
    )
    [entry] = await anilist.AniListClient("tok", "42").fetch_list()
    assert entry.status == Status.WATCHING and entry.rewatching
    assert entry.started_at is None and entry.finished_at.isoformat() == "2020-06-01"
    assert entry.external_ids == {"anilist": "1", "mal": "1"} and entry.remote_entry_id == "7"


@respx.mock
async def test_anilist_save_sends_repeating():
    route = respx.post(anilist.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
    await anilist.AniListClient("tok", "42").save_entry(
        ListEntry(media_id="1", status=Status.WATCHING, rewatching=True, score=None)
    )
    variables = json.loads(route.calls.last.request.content)["variables"]
    assert variables["status"] == "REPEATING" and variables["scoreRaw"] == 0 and "startedAt" not in variables


@respx.mock
async def test_kitsu_fetch_reads_mappings_and_creates_entry():
    respx.get(f"{kitsu.API}/library-entries", params={"filter[kind]": "anime"}).mock(
        return_value=httpx.Response(200, json=load_fixture("kitsu/library_entries.json"))
    )
    client = kitsu.KitsuClient("tok", "55")
    [e] = await client.fetch_list()
    assert (e.status, e.score, e.remote_entry_id) == (Status.COMPLETED, 85, "900")
    assert e.media_id == "1" and e.external_ids == {"kitsu": "1", "mal": "1"}
    assert e.started_at.isoformat() == "2020-01-02"

    respx.get(f"{kitsu.API}/library-entries", params={"filter[animeId]": "2"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    post = respx.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(201, json={}))
    await client.save_entry(ListEntry(media_id="2", status=Status.PLANNING, score=62))
    body = json.loads(post.calls.last.request.content)["data"]
    assert body["attributes"]["ratingTwenty"] == 12 and body["attributes"]["status"] == "planned"
    assert body["relationships"]["user"]["data"]["id"] == "55"


@respx.mock
async def test_unauthorized_raises_reauth():
    respx.get(f"{mal.API}/users/@me/animelist").mock(return_value=httpx.Response(401))
    with pytest.raises(ReauthRequired):
        await mal.MalClient("bad").fetch_list()


@respx.mock
async def test_anilist_lookup_external_ids_in_both_directions():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"Page": {"media": [{"id": 21, "idMal": 1}, {"id": 99, "idMal": None}]}}}
        )
    )
    client = anilist.AniListClient(None)
    assert await client.lookup_external_ids("mal", ["1"]) == [
        {"anilist": "21", "mal": "1"},
        {"anilist": "99"},
    ]
    assert json.loads(route.calls.last.request.content)["variables"] == {"ids": [1]}
    assert "idMal_in" in json.loads(route.calls.last.request.content)["query"]
    await client.lookup_external_ids("anilist", ["21"])
    assert "id_in" in json.loads(route.calls.last.request.content)["query"]
    with pytest.raises(NotImplementedError):
        await client.lookup_external_ids("kitsu", ["1"])


@respx.mock
async def test_anilist_lookup_batches_of_50():
    route = respx.post(anilist.GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json={"data": {"Page": {"media": []}}})
    )
    await anilist.AniListClient(None).lookup_external_ids("mal", [str(i) for i in range(120)])
    assert [len(json.loads(c.request.content)["variables"]["ids"]) for c in route.calls] == [50, 50, 20]


@respx.mock
async def test_kitsu_lookup_external_ids_uses_mappings():
    route = respx.get(f"{kitsu.API}/mappings").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"relationships": {"item": {"data": {"type": "anime", "id": "11"}}}}]}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    found = await kitsu.KitsuClient(None).lookup_external_ids("mal", ["1", "2"])
    assert found == [{"mal": "1", "kitsu": "11"}]
    assert route.calls[0].request.url.params["filter[externalSite]"] == "myanimelist/anime"
    with pytest.raises(NotImplementedError):
        await kitsu.KitsuClient(None).lookup_external_ids("kitsu", ["1"])


@respx.mock
async def test_every_provider_request_names_the_client_instead_of_the_library_default():
    # Kitsu's token endpoint answers 403 to the default python-httpx agent (bot protection), so we always send our own.
    route = respx.post(kitsu.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 100})
    )
    await kitsu.password_login("a@example.com", "pw")
    assert route.calls.last.request.headers["User-Agent"] == "AniSync/0.1"
    api_route = respx.get(f"{kitsu.API}/users").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "1", "attributes": {"name": "k"}}]})
    )
    await kitsu.KitsuClient("tok").whoami()
    assert api_route.calls.last.request.headers["User-Agent"] == "AniSync/0.1"


@respx.mock
async def test_a_refused_request_says_why_in_the_providers_own_words():
    body = {"errors": [{"title": "Invalid", "detail": "startedAt - must not be in the future", "status": "422"}]}
    respx.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(422, json=body))
    client = kitsu.KitsuClient("tok", remote_user_id="1")
    respx.get(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client.save_entry(ListEntry(media_id="47470", status=Status.WATCHING, progress=1))
    message = str(caught.value)
    assert "422" in message and "startedAt - must not be in the future" in message
    assert "\n" not in message  # one line: it is stored in the sync log and shown to users
    assert caught.value.response.status_code == 422  # still a normal status error for callers that check it


@respx.mock
async def test_a_refusal_without_a_readable_body_keeps_the_plain_error():
    respx.get(f"{mal.API}/users/@me/animelist").mock(return_value=httpx.Response(500, text="<html>oops</html>"))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await mal.MalClient("tok").fetch_list()
    assert "500" in str(caught.value) and "oops" not in str(caught.value)


def _kitsu_entry(progress: int = 13) -> ListEntry:
    return ListEntry(media_id="47470", status=Status.COMPLETED, progress=progress)


@respx.mock
async def test_kitsu_adult_titles_it_hides_are_skipped_not_failed():
    from anisync.providers.base import ChangeSkipped

    respx.get(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(200, json={"data": []}))
    taken = {"errors": [{"title": "Invalid", "detail": "animeId - has already been taken", "status": "422"}]}
    respx.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(422, json=taken))
    with pytest.raises(ChangeSkipped, match="SFW filter"):
        await kitsu.KitsuClient("tok", remote_user_id="1").save_entry(_kitsu_entry())


@respx.mock
async def test_kitsu_caps_progress_at_its_own_episode_count():
    respx.get(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(200, json={"data": []}))
    refused = {"errors": [{"title": "Invalid", "detail": "progress - cannot exceed length of media", "status": "422"}]}
    post = respx.post(f"{kitsu.API}/library-entries").mock(
        side_effect=[httpx.Response(422, json=refused), httpx.Response(201, json={})]
    )
    respx.get(f"{kitsu.API}/anime/47470").mock(
        return_value=httpx.Response(200, json={"data": {"attributes": {"episodeCount": 1}}})
    )
    await kitsu.KitsuClient("tok", remote_user_id="1").save_entry(_kitsu_entry(progress=13))
    sent = [json.loads(c.request.content)["data"]["attributes"]["progress"] for c in post.calls]
    assert sent == [13, 1]  # tried as is, then with Kitsu's count


@respx.mock
async def test_kitsu_progress_errors_stay_errors_when_the_episode_count_is_unknown():
    respx.get(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(200, json={"data": []}))
    refused = {"errors": [{"title": "Invalid", "detail": "progress - cannot exceed length of media", "status": "422"}]}
    respx.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(422, json=refused))
    respx.get(f"{kitsu.API}/anime/47470").mock(return_value=httpx.Response(404, json={"errors": []}))
    with pytest.raises(httpx.HTTPStatusError, match="cannot exceed"):
        await kitsu.KitsuClient("tok", remote_user_id="1").save_entry(_kitsu_entry())


@respx.mock
async def test_reads_are_asked_again_after_a_network_hiccup_but_writes_are_not(no_real_waiting):
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.ConnectError("reset"), httpx.Response(200, json={"data": []})]
    )
    assert await mal.MalClient("tok").fetch_list() == []
    assert route.call_count == 3 and no_real_waiting == [2.0, 6.0]

    patch = respx.patch(f"{mal.API}/anime/1/my_list_status").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        await mal.MalClient("tok").save_entry(ListEntry(media_id="1", status=Status.WATCHING, progress=1))
    assert patch.call_count == 1  # a write that may have gone through is never repeated blindly


@respx.mock
async def test_a_read_that_keeps_failing_gives_up_after_the_retries_and_provider_refusals_are_not_retried(
    no_real_waiting,
):
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        await mal.MalClient("tok").fetch_list()
    assert route.call_count == 3
    refused = respx.get(f"{mal.API}/users/@me").mock(return_value=httpx.Response(500, text="oops"))
    with pytest.raises(httpx.HTTPStatusError):
        await mal.MalClient("tok").whoami()
    assert refused.call_count == 1  # an answer, even a bad one, isn't a hiccup


@respx.mock
async def test_errors_that_are_not_network_hiccups_are_not_retried(no_real_waiting):
    route = respx.get(f"{mal.API}/users/@me/animelist").mock(side_effect=httpx.UnsupportedProtocol("nope"))
    with pytest.raises(httpx.UnsupportedProtocol):
        await mal.MalClient("tok").fetch_list()
    assert route.call_count == 1 and no_real_waiting == []
