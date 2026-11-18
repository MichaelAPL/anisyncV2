"""Importing the offline ID dataset (no network: the dataset is served from a fixture)."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from anisync.mapping import IdMapper, KnownTitle
from anisync.models import DatasetImport, Media, MediaExternalId
from anisync.seed import SeedStats, import_titles, parse_entry, run_import
from tests.helpers import FIXTURES

URL = "https://dataset.example/anime-offline-database.jsonl"
SAMPLE = (FIXTURES / "manami" / "sample.jsonl").read_text()


def sample_titles() -> list[KnownTitle]:
    objects = [json.loads(line) for line in SAMPLE.splitlines()[1:] if line.strip()]
    return [t for o in objects if (t := parse_entry(o))]


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def pairs(session) -> dict[tuple[str, str], tuple[int, str]]:
    rows = (await session.scalars(select(MediaExternalId))).all()
    return {(r.provider, r.external_id): (r.media_id, r.source) for r in rows}


# ---- parsing -----------------------------------------------------------------------------------------------


def test_parse_reads_every_registered_provider_from_the_source_urls():
    first = json.loads(SAMPLE.splitlines()[1])
    title = parse_entry(first)
    assert title.ids == {"anilist": "142051", "kitsu": "47450", "mal": "51478"}
    assert (title.title, title.episodes, title.source) == ("!NVADE SHOW!", 1, "seed")


def test_parse_ignores_sites_we_have_no_provider_for():
    assert parse_entry({"sources": ["https://anidb.net/anime/1", "https://myanimelist.net/anime/5"]}) is None


def test_parse_drops_a_provider_with_two_different_ids():
    assert parse_entry({"sources": ["https://myanimelist.net/anime/1", "https://myanimelist.net/anime/2"]}) is None
    t = parse_entry(
        {
            "sources": [
                "https://myanimelist.net/anime/1",
                "https://myanimelist.net/anime/2",
                "https://anilist.co/anime/3",
                "https://kitsu.app/anime/4",
            ]
        }
    )
    assert t.ids == {"anilist": "3", "kitsu": "4"}


def test_parse_handles_missing_and_zero_episodes_and_long_titles():
    sources = ["https://myanimelist.net/anime/1", "https://anilist.co/anime/2"]
    assert parse_entry({"sources": sources, "episodes": 0}).episodes is None
    assert parse_entry({"sources": sources}).title == ""
    assert len(parse_entry({"sources": sources, "title": "x" * 900}).title) == 500


def test_parse_uses_providers_registered_at_runtime(acme):
    t = parse_entry({"sources": ["https://acme.example/anime/77", "https://myanimelist.net/anime/1"]})
    assert t.ids == {"acme": "77", "mal": "1"}


# ---- bulk import -------------------------------------------------------------------------------------------


async def test_import_creates_media_and_links(session):
    stats = await import_titles(session, sample_titles())
    assert (stats.usable, stats.created, stats.extended, stats.conflicts) == (4, 4, 0, 0)
    assert stats.ids_added == 3 + 3 + 2 + 2  # AniDB and the like are not providers
    assert await count(session, Media) == 4
    stored = await pairs(session)
    assert stored[("mal", "51478")][0] == stored[("kitsu", "47450")][0] == stored[("anilist", "142051")][0]
    assert {source for _, source in stored.values()} == {"seed"}
    media = {m.title: m for m in (await session.scalars(select(Media))).all()}
    assert media["!NVADE SHOW!"].episodes == 1 and media["Sample Series"].episodes is None


async def test_importing_twice_changes_nothing(session):
    await import_titles(session, sample_titles())
    before = await pairs(session)
    stats = await import_titles(session, sample_titles())
    assert (stats.created, stats.extended, stats.unchanged, stats.ids_added) == (0, 0, 4, 0)
    assert await pairs(session) == before and await count(session, Media) == 4


async def test_many_new_titles_in_one_batch_keep_their_own_ids(session):
    titles = [KnownTitle({"mal": str(i), "anilist": str(1000 + i)}, title=f"T{i}", source="seed") for i in range(1, 80)]
    stats = await import_titles(session, titles)
    assert stats.created == 79
    stored = await pairs(session)
    for i in range(1, 80):
        assert stored[("mal", str(i))][0] == stored[("anilist", str(1000 + i))][0]
    assert len({m for m, _ in stored.values()}) == 79
    titles_by_media = {m.id: m.title for m in (await session.scalars(select(Media))).all()}
    assert all(titles_by_media[stored[("mal", str(i))][0]] == f"T{i}" for i in range(1, 80))


async def test_ids_in_one_batch_that_share_a_title_are_combined(session):
    stats = await import_titles(
        session,
        [KnownTitle({"mal": "1", "anilist": "2"}, "A"), KnownTitle({"anilist": "2", "kitsu": "3"}, "A")],
    )
    assert (stats.created, stats.extended, stats.ids_added) == (1, 0, 3)
    assert await count(session, Media) == 1 and len(await pairs(session)) == 3


async def test_import_adds_ids_to_titles_we_already_know_and_keeps_old_links(session):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "51478", "anilist": "142051"}, title="Learned title")
    stats = await import_titles(session, sample_titles())
    assert (stats.created, stats.extended) == (3, 1)
    stored = await pairs(session)
    assert stored[("kitsu", "47450")] == (stored[("mal", "51478")][0], "seed")
    assert stored[("mal", "51478")][1] == "learned"  # untouched
    assert await count(session, Media) == 4
    media = (await session.scalars(select(Media).where(Media.id == stored[("mal", "51478")][0]))).one()
    assert media.title == "Learned title"  # existing metadata wins


async def test_import_fills_missing_metadata_on_known_titles(session):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "51478", "anilist": "142051"})  # no title or episodes known
    await import_titles(session, sample_titles())
    session.expire_all()
    media = (await session.scalars(select(Media).where(Media.title == "!NVADE SHOW!"))).one()
    assert media.episodes == 1


async def test_a_manual_link_wins_over_the_dataset(session):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "51478", "kitsu": "99999"}, source="manual")
    stats = await import_titles(session, sample_titles())
    stored = await pairs(session)
    assert stored[("kitsu", "99999")] == (stored[("mal", "51478")][0], "manual")
    assert ("kitsu", "47450") not in stored  # the dataset's idea of the Kitsu ID was ignored
    assert stats.conflicts == 1


async def test_titles_that_span_two_known_media_are_left_alone(session):
    async with IdMapper(session) as mapper:
        await mapper.remember({"mal": "51478", "kitsu": "1"})
        await mapper.remember({"anilist": "142051", "kitsu": "2"})
    media_before = await count(session, Media)
    stats = await import_titles(session, sample_titles()[:1])
    assert stats.conflicts == 1 and await count(session, Media) == media_before == 2


async def test_resolving_after_an_import_needs_no_remote_lookups(session):
    await import_titles(session, sample_titles())
    with respx.mock:  # any request would fail
        async with IdMapper(session) as mapper:
            assert await mapper.resolve("mal", "kitsu", ["51478", "20707"]) == {"51478": "47450", "20707": "8925"}
            assert await mapper.resolve("anilist", "mal", ["102416"]) == {"102416": "20707"}


# ---- the streaming import job ------------------------------------------------------------------------------


async def test_run_import_streams_and_records_the_run(session):
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE))
        run = await run_import(session, url=URL, batch_size=2)  # several batches
    assert (run.status, run.version) == ("ok", "2026-07-04")
    assert run.stats["entries"] == 6 and run.stats["usable"] == 4 and run.stats["created"] == 4
    assert run.finished_at is not None and await count(session, Media) == 4


async def test_run_import_skips_a_version_it_already_has(session):
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE))
        await run_import(session, url=URL)
        second = await run_import(session, url=URL)
        forced = await run_import(session, url=URL, force=True)
    assert second.status == "unchanged" and second.stats == {}
    assert forced.status == "ok" and forced.stats["unchanged"] == 4 and forced.stats["created"] == 0
    assert await count(session, DatasetImport) == 3 and await count(session, Media) == 4


async def test_a_failed_download_is_recorded_and_reraised(session):
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await run_import(session, url=URL)
    run = (await session.scalars(select(DatasetImport))).one()
    assert run.status == "error" and "503" in run.error and run.finished_at is not None


async def test_a_failure_midway_keeps_the_batches_already_imported(session):
    broken = SAMPLE.splitlines()
    broken.insert(4, "{not json")
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, text="\n".join(broken)))
        with pytest.raises(json.JSONDecodeError):
            await run_import(session, url=URL, batch_size=1)
    run = (await session.scalars(select(DatasetImport))).one()
    assert run.status == "error" and run.stats["entries"] == 3
    assert await count(session, Media) == 3  # the three good entries before the bad line were kept


def test_stats_add_up():
    total = SeedStats(created=1, ids_added=2)
    total.add(SeedStats(created=3, conflicts=1))
    assert (total.created, total.ids_added, total.conflicts) == (4, 2, 1)


def test_the_sample_matches_the_real_release_shape():
    meta = json.loads(SAMPLE.splitlines()[0])
    assert meta["lastUpdate"] == "2026-07-04" and "ODbL" in meta["license"]["name"]
    assert Path(FIXTURES / "manami" / "README.md").exists()
