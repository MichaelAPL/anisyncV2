"""Seed the canonical media tables from an offline ID dataset, so most titles never need a remote lookup.

Dataset: anime-offline-database, https://github.com/cedya77/anime-offline-database, the maintained continuation of
manami-project/anime-offline-database (https://github.com/manami-project/anime-offline-database, archived after release
2026-27). Both are released under the ODbL 1.0 with a Database Contents License 1.0: credit both projects wherever the
site is described (README, footer). Set `ID_DATASET_URL` to use another source with the same format.

Format: the `.jsonl` release asset. One JSON object per line; the first is metadata (`lastUpdate`, `license`), the
rest are anime with `sources`, the URLs of the title on many sites. Which URLs we understand is decided by each
registered provider (`Provider.url_pattern`), so nothing here names a provider. Titles with fewer than two known IDs
say nothing about how providers relate and are skipped; after registering a new provider, re-run the import (with
`force`) to backfill its IDs.

Existing links are never changed or removed, so manual links and anything learned earlier win over the dataset.

Run once with `uv run python -m anisync.seed [--force]`; the worker also checks weekly (see `jobs.py`).
"""

import asyncio
import json
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, exists, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.config import get_settings
from anisync.mapping import KnownTitle, existing_pairs
from anisync.models import DatasetImport, Media, MediaExternalId
from anisync.providers import all_providers

log = logging.getLogger(__name__)

DATASET = "anime-offline-database"
SOURCE = "seed"
BATCH_SIZE = 2000
INSERT_CHUNK = 3000


@dataclass
class SeedStats:
    entries: int = 0  # anime described by the dataset
    usable: int = 0  # ... with at least two IDs we understand
    created: int = 0  # new media
    extended: int = 0  # existing media that gained provider IDs
    unchanged: int = 0
    conflicts: int = 0  # titles that disagree with what we already store (ours is kept)
    ids_added: int = 0

    def add(self, other: "SeedStats") -> None:
        for name, value in asdict(other).items():
            setattr(self, name, getattr(self, name) + value)


def parse_entry(obj: dict[str, Any]) -> KnownTitle | None:
    """One dataset line -> the title's IDs on every registered provider (None if fewer than two)."""
    ids: dict[str, str] = {}
    ambiguous: set[str] = set()
    for url in obj.get("sources") or []:
        for provider in all_providers():
            title_id = provider.title_id_from_url(url)
            if title_id is None:
                continue
            if ids.get(provider.id, title_id) != title_id:
                ambiguous.add(provider.id)  # two different IDs for one provider: don't guess
            ids[provider.id] = title_id
            break
    for provider_id in ambiguous:
        del ids[provider_id]
    if len(ids) < 2:
        return None
    return KnownTitle(ids, title=(obj.get("title") or "")[:500], episodes=obj.get("episodes") or None, source=SOURCE)


async def import_titles(session: AsyncSession, titles: Iterable[KnownTitle]) -> SeedStats:
    """Add titles in bulk: new media for unknown titles, new provider IDs for known ones."""
    batch = list(titles)
    stats = SeedStats(usable=len(batch))
    if not batch:
        return stats

    pair_media: dict[tuple[str, str], int] = await existing_pairs(session, (p for t in batch for p in t.ids.items()))
    existing = set(pair_media.values())
    occupancy: dict[int, dict[str, str]] = {m: {} for m in existing}  # media -> {provider: id}
    for media_id, provider, external_id in await session.execute(
        select(MediaExternalId.media_id, MediaExternalId.provider, MediaExternalId.external_id).where(
            MediaExternalId.media_id.in_(existing)
        )
    ):
        occupancy[media_id][provider] = external_id
    meta = {m.id: m for m in (await session.scalars(select(Media).where(Media.id.in_(existing)))).all()}

    new_media: list[dict[str, Any]] = []  # new media get negative placeholder keys until they have real IDs
    links: list[tuple[int, str, str]] = []
    fills: dict[int, dict[str, Any]] = {}
    for t in batch:
        keys = {pair_media[pair] for pair in t.ids.items() if pair in pair_media}
        if len(keys) > 1:  # the IDs belong to different titles in our data: leave it alone
            stats.conflicts += 1
            continue
        if keys:
            key = keys.pop()
        else:
            key = -(len(new_media) + 1)
            new_media.append({"title": t.title, "episodes": t.episodes})
            occupancy[key] = {}
            stats.created += 1
        added, conflict = 0, False
        for provider, external_id in t.ids.items():
            have = occupancy[key].get(provider)
            if have is None:
                occupancy[key][provider] = external_id
                pair_media[(provider, external_id)] = key
                links.append((key, provider, external_id))
                added += 1
            elif have != external_id:
                conflict = True
        if key > 0:
            current = meta.get(key)
            if current is not None and (not current.title and t.title or current.episodes is None and t.episodes):
                fills[key] = {"id": key} | {
                    k: v for k, v in (("title", t.title), ("episodes", t.episodes)) if v and not getattr(current, k)
                }
            stats.extended += bool(added)
            stats.unchanged += not added and not conflict
        stats.conflicts += conflict

    real_ids: dict[int, int] = {}
    if new_media:
        result = await session.execute(insert(Media).returning(Media.id, sort_by_parameter_order=True), new_media)
        real_ids = {-(i + 1): row[0] for i, row in enumerate(result)}
    rows = [
        {"media_id": real_ids.get(key, key), "provider": provider, "external_id": external_id, "source": SOURCE}
        for key, provider, external_id in links
    ]
    for start in range(0, len(rows), INSERT_CHUNK):
        inserted = await session.execute(  # RETURNING: `rowcount` isn't reliable for multi-row inserts
            pg_insert(MediaExternalId)
            .values(rows[start : start + INSERT_CHUNK])
            .on_conflict_do_nothing()
            .returning(MediaExternalId.external_id)
        )
        stats.ids_added += len(inserted.all())
    if fills:
        await session.execute(update(Media), list(fills.values()))
    if real_ids:  # media whose every ID lost a race with another writer would be left empty
        await session.execute(
            delete(Media).where(Media.id.in_(real_ids.values()), ~exists().where(MediaExternalId.media_id == Media.id))
        )
    return stats


async def _already_imported(session: AsyncSession, version: str | None) -> bool:
    if version is None:
        return False
    return bool(
        await session.scalar(
            select(
                exists().where(
                    DatasetImport.dataset == DATASET, DatasetImport.version == version, DatasetImport.status == "ok"
                )
            )
        )
    )


async def run_import(
    session: AsyncSession,
    *,
    url: str | None = None,
    http: httpx.AsyncClient | None = None,
    force: bool = False,
    batch_size: int = BATCH_SIZE,
) -> DatasetImport:
    """Stream the dataset and import it in batches (committing each). Skips it if this version was already imported."""
    url = url or get_settings().id_dataset_url
    owns_http = http is None
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True)
    run = DatasetImport(dataset=DATASET, status="running")
    session.add(run)
    await session.commit()
    run_id = run.id
    stats = SeedStats()
    try:
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            lines = response.aiter_lines()
            meta = json.loads(await anext(lines))
            run.version = str(meta.get("lastUpdate") or "") or None
            if not force and await _already_imported(session, run.version):
                run.status = "unchanged"
            else:
                batch: list[KnownTitle] = []
                async for line in lines:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if "sources" not in obj:
                        continue
                    stats.entries += 1
                    if title := parse_entry(obj):
                        batch.append(title)
                    if len(batch) >= batch_size:
                        stats.add(await import_titles(session, batch))
                        await session.commit()
                        batch = []
                stats.add(await import_titles(session, batch))
                run.status = "ok"
                run.stats = asdict(stats)
        run.finished_at = datetime.now(UTC)
        await session.commit()
    except BaseException as exc:
        await session.rollback()
        failed = await session.get(DatasetImport, run_id)
        failed.status = "error"
        failed.error = f"{type(exc).__name__}: {exc}"[:2000]
        failed.stats = asdict(stats)
        failed.finished_at = datetime.now(UTC)
        await session.commit()
        raise
    finally:
        if owns_http:
            await http.aclose()
    log.info("dataset import %s: %s %s", run.version, run.status, run.stats)
    return run


async def _main(argv: Sequence[str]) -> None:
    from anisync.db import SessionLocal

    async with SessionLocal() as session:
        run = await run_import(session, force="--force" in argv)
    print(f"{DATASET} {run.version}: {run.status} {run.stats}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it would log the signed download URL
    asyncio.run(_main(sys.argv[1:]))
