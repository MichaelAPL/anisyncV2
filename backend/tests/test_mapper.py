"""The ID resolver: provider -> canonical media -> provider."""

import httpx
import pytest
import respx
from sqlalchemy import event, func, select

from anisync.mapping import IdMapper
from anisync.models import Media, MediaExternalId
from anisync.providers import anilist
from anisync.providers.base import ListEntry, Status


def entry(media_id, **ids):
    return ListEntry(media_id=str(media_id), status=Status.WATCHING, external_ids={k: str(v) for k, v in ids.items()})


async def media_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(Media))


@pytest.fixture
def no_network():
    """Any HTTP request fails: used to prove a resolution came from the database."""
    with respx.mock(assert_all_called=False):
        yield


async def test_resolves_from_what_was_learned(session, no_network):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "1", "anilist": "21", "kitsu": "1"}, title="Cowboy Bebop")
        assert await mapper.resolve("mal", "anilist", ["1"]) == {"1": "21"}
        assert await mapper.resolve("anilist", "kitsu", ["21"]) == {"21": "1"}
        assert await mapper.resolve("kitsu", "mal", ["1"]) == {"1": "1"}
    assert await media_count(session) == 1


async def test_same_provider_is_the_identity(session, no_network):
    async with IdMapper(session) as mapper:
        assert await mapper.resolve("mal", "mal", ["5", "6"]) == {"5": "5", "6": "6"}


async def test_learn_from_list_entries(session, no_network):
    async with IdMapper(session) as mapper:
        await mapper.learn_from([entry(21, anilist=21, mal=1), entry(5, anilist=5), entry(9, kitsu=9, mal=3)])
        assert await mapper.resolve("anilist", "mal", ["21"]) == {"21": "1"}
        assert await mapper.resolve("mal", "kitsu", ["3"]) == {"3": "9"}
    assert await media_count(session) == 2  # the entry with a single ID says nothing about other providers


async def test_unregistered_providers_are_ignored(session, no_network):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "1", "nope": "2"})
    assert await media_count(session) == 0


async def test_remote_lookup_through_a_provider_that_declares_it(session, acme):
    acme.TABLE = {("mal", "1"): {"mal": "1", "acme": "A1"}}
    async with IdMapper(session) as mapper:
        assert await mapper.resolve("mal", "acme", ["1", "2"]) == {"1": "A1", "2": None}
        assert acme.calls == [("mal", ["1", "2"])]
        # Learned: the second time nothing is known for "1" but it's answered from the database.
        acme.calls.clear()
        assert (await mapper.resolve("mal", "acme", ["1"]))["1"] == "A1"
        assert await mapper.resolve("acme", "mal", ["A1"]) == {"A1": "1"}
    assert acme.calls == []


async def test_hop_through_a_third_provider(session):
    """kitsu 5 is only known to be anilist 50; anilist can translate its own IDs, so we learn mal 7 that way."""
    with respx.mock:
        route = respx.post(anilist.GRAPHQL_URL).mock(
            return_value=httpx.Response(200, json={"data": {"Page": {"media": [{"id": 50, "idMal": 7}]}}})
        )
        async with IdMapper(session) as mapper:
            await mapper.remember({"kitsu": "5", "anilist": "50"})
            assert await mapper.resolve("kitsu", "mal", ["5"]) == {"5": "7"}
    assert route.call_count == 1
    assert await media_count(session) == 1


async def test_two_discoveries_of_the_same_title_are_merged(session, acme, no_network):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "1", "anilist": "10"})
        await mapper.remember({"kitsu": "5", "acme": "A5"})
        assert await media_count(session) == 2
        await mapper.remember({"mal": "1", "kitsu": "5"})
        assert await media_count(session) == 1
        assert await mapper.resolve("anilist", "acme", ["10"]) == {"10": "A5"}


async def test_titles_that_disagree_are_not_merged(session, no_network):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "1", "anilist": "10"})
        await mapper.remember({"mal": "2", "anilist": "20"})
        await mapper.remember({"mal": "1", "anilist": "20"})  # claims 1 and 2 are one title: they conflict
        assert await media_count(session) == 2
        assert await mapper.resolve("mal", "anilist", ["1", "2"]) == {"1": "10", "2": "20"}


async def test_learning_known_titles_costs_one_query(session, no_network):
    entries = [entry(i, anilist=i, mal=1000 + i) for i in range(1, 60)]
    queries: list[str] = []
    conn = session.bind.sync_connection
    event.listen(conn, "before_cursor_execute", lambda *a: queries.append(a[2]))
    async with IdMapper(session) as mapper:
        await mapper.learn_from(entries)
        first = len(queries)
        queries.clear()
        await mapper.learn_from(entries)
    assert first > 59 and len(queries) == 1


async def test_display_data_is_kept_on_the_media(session, no_network):
    async with IdMapper(session) as mapper:
        await mapper.learn_from(
            [
                ListEntry(
                    media_id="21",
                    status=Status.WATCHING,
                    external_ids={"anilist": "21", "mal": "1"},
                    title="Cowboy Bebop",
                    total_episodes=26,
                    image_url="https://img.example/1.jpg",
                )
            ]
        )
    media = (await session.scalars(select(Media))).one()
    assert (media.title, media.episodes, media.cover_url) == ("Cowboy Bebop", 26, "https://img.example/1.jpg")
    ids = (await session.scalars(select(MediaExternalId).order_by(MediaExternalId.provider))).all()
    assert [(i.provider, i.external_id, i.source) for i in ids] == [
        ("anilist", "21", "learned"),
        ("mal", "1", "learned"),
    ]


# ---- the negative cache -------------------------------------------------------------------------------------


async def test_untranslatable_ids_are_not_asked_about_again_for_a_week(session, acme):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from anisync.models import LookupMiss

    async with IdMapper(session) as mapper:
        assert await mapper.resolve("mal", "acme", ["404"]) == {"404": None}
        assert acme.calls == [("mal", ["404"])]
        assert await mapper.resolve("mal", "acme", ["404"]) == {"404": None}
        assert len(acme.calls) == 1  # remembered as a miss

        await session.execute(update(LookupMiss).values(checked_at=datetime.now(UTC) - timedelta(days=8)))
        assert await mapper.resolve("mal", "acme", ["404"]) == {"404": None}
        assert len(acme.calls) == 2  # asked again after the week


async def test_a_title_learned_later_resolves_despite_an_earlier_miss(session, acme):
    async with IdMapper(session) as mapper:
        assert await mapper.resolve("mal", "acme", ["7"]) == {"7": None}
        await mapper.remember({"mal": "7", "acme": "A7"})  # e.g. a dataset import or a list entry taught us
        assert await mapper.resolve("mal", "acme", ["7"]) == {"7": "A7"}
    assert len(acme.calls) == 1


async def test_misses_are_per_direction(session, acme):
    async with IdMapper(session) as mapper:
        await mapper.resolve("mal", "acme", ["5"])
        acme.TABLE = {("mal", "5"): {"mal": "5", "acme": "A5"}}
        assert await mapper.resolve("acme", "mal", ["A5"]) == {"A5": None}  # the other direction was never asked
        assert await mapper.resolve("mal", "acme", ["5"]) == {"5": None}  # this one is still remembered as a miss
