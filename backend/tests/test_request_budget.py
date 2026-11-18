"""Phase 3's goal, measured: how many provider requests a sync costs, with the real MAL, AniList and Kitsu clients.

HTTP is mocked with respx and every request is counted. Master = MAL, mirrors = AniList and Kitsu.
"""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from aiolimiter import AsyncLimiter

from anisync import sync
from anisync.models import LinkedAccount, User
from anisync.providers import anilist, kitsu, mal
from anisync.providers.base import ProviderClient

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    """The providers' request pacing is real in production; here only the request *counts* matter."""
    monkeypatch.setattr(ProviderClient, "limiter", property(lambda self: AsyncLimiter(10_000, 1)))


class Providers:
    """Mocked MAL, AniList and Kitsu holding the same three titles on MAL and only title 1 on the mirrors."""

    def __init__(self, router: respx.MockRouter):
        self.router = router
        self.mal_updates = {1: T0 + timedelta(minutes=3), 2: T0 + timedelta(minutes=2), 3: T0 + timedelta(minutes=1)}
        self.mal_progress = {1: 5, 2: 1, 3: 1}
        router.get(f"{mal.API}/users/@me/animelist").mock(side_effect=self.mal_list)
        router.post(anilist.GRAPHQL_URL).mock(side_effect=self.anilist)
        router.get(f"{kitsu.API}/library-entries").mock(side_effect=self.kitsu_entries)
        router.get(f"{kitsu.API}/mappings").mock(side_effect=self.kitsu_mapping)
        router.post(f"{kitsu.API}/library-entries").mock(return_value=httpx.Response(201, json={}))
        router.patch(url__regex=rf"{mal.API}/anime/\d+/my_list_status").mock(return_value=httpx.Response(200, json={}))

    def mal_list(self, request: httpx.Request) -> httpx.Response:
        items = [
            {
                "node": {"id": i, "title": f"Title {i}", "num_episodes": 12},
                "list_status": {
                    "status": "watching",
                    "score": 8,
                    "num_episodes_watched": self.mal_progress[i],
                    "updated_at": self.mal_updates[i].isoformat(),
                },
            }
            for i in sorted(self.mal_updates, key=self.mal_updates.get, reverse=True)
        ]
        return httpx.Response(200, json={"data": items, "paging": {}})

    def anilist(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "MediaListCollection" in query:  # the AniList mirror already has title 1 (AniList 21, MAL 1)
            entry = {
                "id": 7,
                "mediaId": 21,
                "status": "CURRENT",
                "progress": 5,
                "updatedAt": int(T0.timestamp()),
                "score": 80,
                "startedAt": {"year": None, "month": None, "day": None},
                "completedAt": {"year": None, "month": None, "day": None},
                "media": {"idMal": 1, "episodes": 12, "title": {"userPreferred": "Title 1"}, "coverImage": None},
            }
            return httpx.Response(200, json={"data": {"MediaListCollection": {"lists": [{"entries": [entry]}]}}})
        if "idMal_in" in query:
            return httpx.Response(200, json={"data": {"Page": {"media": [{"id": 20 + i, "idMal": i} for i in (2, 3)]}}})
        return httpx.Response(200, json={"data": {"SaveMediaListEntry": {"id": 1}}})

    def kitsu_entries(self, request: httpx.Request) -> httpx.Response:
        if "filter[animeId]" in request.url.params:  # "is this title already on the list?"
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "900",
                        "attributes": {
                            "status": "current",
                            "progress": 5,
                            "ratingTwenty": 16,
                            "reconsuming": False,
                            "startedAt": None,
                            "finishedAt": None,
                            "updatedAt": "2026-11-01T12:00:00.000Z",
                        },
                        "relationships": {"anime": {"data": {"type": "anime", "id": "101"}}},
                    }
                ],
                "included": [
                    {
                        "type": "anime",
                        "id": "101",
                        "attributes": {"canonicalTitle": "Title 1", "episodeCount": 12},
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

    def kitsu_mapping(self, request: httpx.Request) -> httpx.Response:
        external_id = request.url.params["filter[externalId]"]
        item = {"relationships": {"item": {"data": {"type": "anime", "id": str(100 + int(external_id))}}}}
        return httpx.Response(200, json={"data": [item]})

    _offset = 0  # respx's call list can't be cleared reliably, so remember where this phase of the test began

    def requests(self) -> list[httpx.Request]:
        return [call.request for call in list(self.router.calls)[self._offset :]]

    def calls(self) -> Counter:
        return Counter(request.url.host for request in self.requests())

    def reset(self) -> None:
        self._offset = len(self.router.calls)


@pytest.fixture
def providers():
    with respx.mock(assert_all_called=False) as router:
        yield Providers(router)


async def make_user(session) -> User:
    user = User(display_name="kaito")
    session.add(user)
    accounts = {}
    for provider, remote_id in (("mal", "m1"), ("anilist", "77"), ("kitsu", "55")):
        account = LinkedAccount(
            user=user,
            provider=provider,
            remote_user_id=remote_id,
            remote_username=provider,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        account.access_token = f"{provider}-token"
        session.add(account)
        accounts[provider] = account
    await session.flush()
    user.master_account_id = accounts["mal"].id
    await session.flush()
    return user


async def test_a_sync_where_nothing_changed_costs_one_request_in_total(session, providers):
    user = await make_user(session)
    first = await sync.run_sync(session, user.id, "manual")
    assert [r["create"] for r in first.values()] == [2, 2]  # titles 2 and 3 were missing on both mirrors
    first_run = providers.calls()
    providers.reset()

    nightly = await sync.run_sync(session, user.id, "scheduled")
    calls = providers.calls()
    assert all(r["create"] == r["update"] == r["delete"] == 0 for r in nightly.values())
    assert calls == {"api.myanimelist.net": 1}, calls  # the master's incremental read, nothing else
    assert sum(calls.values()) <= 2 * 3  # the roadmap's bound: at most two requests per linked account
    request = providers.requests()[0]
    assert request.url.params["sort"] == "list_updated_at" and request.url.params["limit"] == "100"
    assert (
        first_run["api.myanimelist.net"] == 1 and first_run["graphql.anilist.co"] >= 3 and first_run["kitsu.app"] >= 3
    )


async def test_one_edit_costs_the_read_plus_one_write_per_mirror(session, providers):
    user = await make_user(session)
    await sync.run_sync(session, user.id, "manual")
    providers.reset()
    providers.mal_progress[2] = 9
    providers.mal_updates[2] = T0 + timedelta(hours=1)

    [anilist_result, kitsu_result] = (await sync.run_sync(session, user.id, "scheduled")).values()
    assert (anilist_result["update"], kitsu_result["update"]) == (1, 1)
    calls = providers.calls()
    assert calls["api.myanimelist.net"] == 1 and calls["graphql.anilist.co"] == 1  # the AniList write
    assert calls["kitsu.app"] == 2  # the write, after finding the entry that already exists


async def test_unmappable_titles_are_not_looked_up_again_every_night(session, providers):
    user = await make_user(session)
    providers.router.post(anilist.GRAPHQL_URL).mock(  # AniList doesn't know titles 2 and 3, and neither does Kitsu
        side_effect=lambda request: httpx.Response(
            200,
            json=json.loads(providers.anilist(request).content)
            if "idMal_in" not in json.loads(request.content)["query"]
            else {"data": {"Page": {"media": []}}},
        )
    )
    providers.router.get(f"{kitsu.API}/mappings").mock(return_value=httpx.Response(200, json={"data": []}))
    first = await sync.run_sync(session, user.id, "manual")
    assert [r["unmapped"] for r in first.values()] == [2, 2]
    providers.reset()
    providers.mal_updates[1] = T0 + timedelta(hours=1)  # something changed, so the plan is made again
    await sync.run_sync(session, user.id, "scheduled")
    calls = providers.calls()
    assert calls == {"api.myanimelist.net": 1}, calls  # the failed lookups are not repeated


async def test_the_first_sync_reads_each_list_once(session, providers):
    user = await make_user(session)
    await sync.run_sync(session, user.id, "preview", dry_run=True)
    calls = providers.calls()
    providers.reset()
    await sync.run_sync(session, user.id, "manual")
    after_preview = providers.calls()
    assert calls["api.myanimelist.net"] == 1 and after_preview["api.myanimelist.net"] == 1  # incremental read only
    reads = [r for r in providers.requests() if r.url.host == "kitsu.app" and r.method == "GET"]
    assert not any("filter[kind]" in r.url.params for r in reads)  # the preview already read Kitsu's list
