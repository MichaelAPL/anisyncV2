"""Translate anime IDs between services, backed by the `anime_ids` cache table.

Sources, in order of preference:
1. Hints already present on fetched list entries (AniList idMal, Kitsu mappings) - free.
2. The `anime_ids` table (everything learned before, across all users).
3. Remote lookups: AniList batch queries (50 IDs/request) and Kitsu /mappings.
"""

from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import AnimeIds, Service
from anisync.services.anilist import AniListClient
from anisync.services.base import ListEntry
from anisync.services.kitsu import KitsuClient

FIELD = {Service.MAL: "mal_id", Service.ANILIST: "anilist_id", Service.KITSU: "kitsu_id"}
KITSU_SITE = {Service.MAL: "myanimelist/anime", Service.ANILIST: "anilist/anime"}


def _chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class IdMapper:
    def __init__(self, session: AsyncSession):
        self.session = session
        # Public lookups need no user token.
        self.anilist = AniListClient(access_token=None)
        self.kitsu = KitsuClient(access_token=None)

    async def remember(
        self, mal_id: int | None = None, anilist_id: int | None = None, kitsu_id: int | None = None
    ) -> None:
        known = {k: v for k, v in (("mal_id", mal_id), ("anilist_id", anilist_id), ("kitsu_id", kitsu_id)) if v}
        if len(known) < 2:
            return
        rows = (
            await self.session.scalars(
                select(AnimeIds).where(or_(*(getattr(AnimeIds, k) == v for k, v in known.items())))
            )
        ).all()
        row = rows[0] if rows else AnimeIds()
        for other in rows[1:]:  # merge rows that turned out to be the same anime
            for k in FIELD.values():
                known.setdefault(k, getattr(other, k))
            await self.session.delete(other)
        await self.session.flush()
        for k, v in known.items():
            if v and getattr(row, k) != v:
                setattr(row, k, v)
        self.session.add(row)
        await self.session.flush()

    async def learn_from(self, entries: Iterable[ListEntry]) -> None:
        for e in entries:
            await self.remember(e.mal_id, e.anilist_id, e.kitsu_id)

    async def _lookup(self, source: Service, ids: list[int]) -> dict[int, AnimeIds]:
        col = getattr(AnimeIds, FIELD[source])
        rows = (await self.session.scalars(select(AnimeIds).where(col.in_(ids)))).all()
        return {getattr(r, FIELD[source]): r for r in rows}

    async def resolve(self, source: Service, target: Service, ids: list[int]) -> dict[int, int | None]:
        """Map `ids` on `source` to IDs on `target` (None = no known equivalent)."""
        if source == target:
            return {i: i for i in ids}
        rows = await self._lookup(source, ids)
        result = {i: getattr(rows[i], FIELD[target]) if i in rows else None for i in ids}
        missing = [i for i, v in result.items() if v is None]
        if missing:
            await self._fetch_remote(source, target, missing, rows)
            rows = await self._lookup(source, missing)
            result |= {i: getattr(rows[i], FIELD[target]) for i in missing if i in rows}
        return result

    async def _fetch_remote(self, source: Service, target: Service, ids: list[int], rows: dict[int, AnimeIds]) -> None:
        if {source, target} == {Service.MAL, Service.ANILIST}:
            for chunk in _chunks(ids, 50):
                if source == Service.MAL:
                    found = await self.anilist.media_by_mal_ids(chunk)
                    for mal_id, al_id in found.items():
                        await self.remember(mal_id=mal_id, anilist_id=al_id)
                else:
                    found_mal = await self.anilist.media_by_ids(chunk)
                    for al_id, mal_id in found_mal.items():
                        await self.remember(mal_id=mal_id, anilist_id=al_id)
        elif target == Service.KITSU:
            for i in ids:
                kitsu_id = await self.kitsu.kitsu_id_for(KITSU_SITE[source], i)
                await self.remember(**{FIELD[source]: i, "kitsu_id": kitsu_id})
        else:  # Kitsu -> MAL/AniList: hop through whatever the cache knows, else give up.
            for i in ids:
                row = rows.get(i)
                if row and row.mal_id and target == Service.ANILIST:
                    found = await self.anilist.media_by_mal_ids([row.mal_id])
                    await self.remember(mal_id=row.mal_id, anilist_id=found.get(row.mal_id), kitsu_id=i)
                elif row and row.anilist_id and target == Service.MAL:
                    found_mal = await self.anilist.media_by_ids([row.anilist_id])
                    await self.remember(mal_id=found_mal.get(row.anilist_id), anilist_id=row.anilist_id, kitsu_id=i)
