"""Translate title IDs between providers by going through canonical media.

Every title is one `Media` row with a `MediaExternalId` per provider that knows it, so any provider can be mapped
to any other through it ("provider -> media -> provider"). IDs come from, in order of preference:

1. What's already in the database: the offline dataset import (see `seed.py`), earlier syncs, manual links.
2. Hints on fetched list entries (`ListEntry.external_ids`): free, learned with `learn_from`.
3. Remote lookups: a provider that can translate IDs (`ProviderClient.lookup_external_ids`, declared in
   `Provider.lookup_from`) is asked in batches. No code here knows which providers those are.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType

import httpx
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anisync.models import Media, MediaExternalId
from anisync.providers import get_provider, is_registered
from anisync.providers.base import ListEntry, ProviderClient

log = logging.getLogger(__name__)

CHUNK = 1000


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def existing_pairs(session: AsyncSession, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """{(provider, title id): media id} for the pairs that are already stored."""
    out: dict[tuple[str, str], int] = {}
    for chunk in _chunks(list(set(pairs)), CHUNK):
        rows = await session.execute(
            select(MediaExternalId.provider, MediaExternalId.external_id, MediaExternalId.media_id).where(
                tuple_(MediaExternalId.provider, MediaExternalId.external_id).in_(chunk)
            )
        )
        out.update({(p, e): m for p, e, m in rows})
    return out


@dataclass
class KnownTitle:
    """One title as known by several providers at once, with whatever display data came along."""

    ids: Mapping[str, str]  # provider id -> title id
    title: str = ""
    episodes: int | None = None
    cover_url: str | None = None
    source: str = field(default="learned")


class IdMapper:
    """Use as `async with IdMapper(session) as mapper:` so the HTTP client used for lookups gets closed."""

    def __init__(self, session: AsyncSession, http: httpx.AsyncClient | None = None):
        self.session = session
        self._http = http
        self._owns_http = http is None
        self._clients: dict[str, ProviderClient] = {}

    async def __aenter__(self) -> "IdMapper":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self, provider_id: str) -> ProviderClient:
        """A client without a user token: lookups only use public endpoints."""
        if provider_id not in self._clients:
            http = self._http or httpx.AsyncClient(timeout=30)
            self._http = http
            self._clients[provider_id] = get_provider(provider_id).client(access_token=None, http=http)
        return self._clients[provider_id]

    # ---- learning -------------------------------------------------------------------------------------

    async def learn_from(self, entries: Iterable[ListEntry]) -> None:
        """Remember cross-provider IDs that list entries carry. Cheap when everything is already known."""
        await self.remember_many(
            KnownTitle(e.external_ids, e.title, e.total_episodes, e.image_url)
            for e in entries
            if len(e.external_ids) >= 2
        )

    async def remember(self, ids: Mapping[str, str], **meta) -> None:
        await self.remember_many([KnownTitle(dict(ids), **meta)])

    async def remember_many(self, titles: Iterable[KnownTitle]) -> None:
        # Only providers we know; a title needs two IDs to say anything about how providers relate.
        items = []
        for t in titles:
            ids = {p: str(i) for p, i in t.ids.items() if i and is_registered(p)}
            if len(ids) >= 2:
                items.append(KnownTitle(ids, t.title, t.episodes, t.cover_url, t.source))
        if not items:
            return
        known = await existing_pairs(self.session, (pair for t in items for pair in t.ids.items()))
        for t in items:
            media_ids = {known[pair] for pair in t.ids.items() if pair in known}
            if len(media_ids) == 1 and all(pair in known for pair in t.ids.items()):
                continue  # steady state: nothing new to learn
            try:
                async with self.session.begin_nested():
                    await self._store(t, known)
            except IntegrityError:
                # Another worker stored the same IDs at the same time; theirs is as good as ours.
                log.info("concurrent insert while learning %s; skipping", dict(t.ids))
                for pair in t.ids.items():
                    known.pop(pair, None)

    async def _store(self, t: KnownTitle, known: dict[tuple[str, str], int]) -> None:
        media_ids = sorted({known[pair] for pair in t.ids.items() if pair in known})
        if not media_ids:
            media = Media(title=t.title, episodes=t.episodes, cover_url=t.cover_url)
            self.session.add(media)
            await self.session.flush()
            media_id = media.id
        else:
            media_id = media_ids[0]
            if len(media_ids) > 1:
                if not await self._merge(media_ids):
                    log.warning("not merging media %s: they disagree on a provider's ID (%s)", media_ids, dict(t.ids))
                    return
                for pair, m in list(known.items()):
                    if m in media_ids[1:]:
                        known[pair] = media_id
            media = await self.session.get(Media, media_id)
            if media is not None:
                media.title = media.title or t.title
                media.episodes = media.episodes or t.episodes
                media.cover_url = media.cover_url or t.cover_url
        owned = {
            p: e
            for p, e in await self.session.execute(
                select(MediaExternalId.provider, MediaExternalId.external_id).where(
                    MediaExternalId.media_id == media_id
                )
            )
        }
        for provider, external_id in t.ids.items():
            if provider in owned:
                if owned[provider] != external_id:
                    log.warning(
                        "media %s already has %s ID %s; ignoring %s", media_id, provider, owned[provider], external_id
                    )
                continue
            self.session.add(
                MediaExternalId(media_id=media_id, provider=provider, external_id=external_id, source=t.source)
            )
            known[(provider, external_id)] = media_id
        await self.session.flush()

    async def _merge(self, media_ids: list[int]) -> bool:
        """Merge media rows that turned out to be the same title into the first one. False = they conflict."""
        rows = (
            await self.session.scalars(select(MediaExternalId).where(MediaExternalId.media_id.in_(media_ids)))
        ).all()
        per_provider: dict[str, set[str]] = {}
        for r in rows:
            per_provider.setdefault(r.provider, set()).add(r.external_id)
        if any(len(ids) > 1 for ids in per_provider.values()):
            return False
        keep = media_ids[0]
        for r in rows:
            r.media_id = keep
        await self.session.flush()
        for other in media_ids[1:]:
            gone = await self.session.get(Media, other)
            if gone is not None:
                await self.session.delete(gone)
        await self.session.flush()
        return True

    # ---- resolving ------------------------------------------------------------------------------------

    async def resolve(self, source: str, target: str, ids: Sequence[str]) -> dict[str, str | None]:
        """Map title IDs on provider `source` to IDs on `target` (None = no known equivalent)."""
        ids = list(dict.fromkeys(ids))
        if source == target:
            return {i: i for i in ids}
        result: dict[str, str | None] = dict.fromkeys(ids)
        for step in (None, self._remote_lookup, self._hop):
            if step:
                await step(source, target, [i for i in ids if result[i] is None])
            result |= await self._cached(source, target, [i for i in ids if result[i] is None])
            if all(result[i] is not None for i in ids):
                break
        return result

    async def _cached(self, source: str, target: str, ids: Sequence[str]) -> dict[str, str]:
        src, tgt = aliased(MediaExternalId), aliased(MediaExternalId)
        out: dict[str, str] = {}
        for chunk in _chunks(list(ids), CHUNK):
            rows = await self.session.execute(
                select(src.external_id, tgt.external_id)
                .select_from(src)
                .join(tgt, tgt.media_id == src.media_id)
                .where(src.provider == source, tgt.provider == target, src.external_id.in_(chunk))
            )
            out.update(dict(rows.all()))
        return out

    async def _remote_lookup(self, source: str, target: str, ids: Sequence[str]) -> None:
        """Ask the target provider, then the source provider, to translate the IDs we couldn't resolve."""
        remaining = list(ids)
        for provider_id in dict.fromkeys((target, source)):
            if not remaining:
                return
            if source in get_provider(provider_id).lookup_from:
                await self._ask(provider_id, source, remaining)
                remaining = [i for i in remaining if i not in await self._cached(source, target, remaining)]

    async def _hop(self, source: str, target: str, ids: Sequence[str]) -> None:
        """For titles we know under a third provider, ask a provider that can translate that provider's IDs."""
        if not ids:
            return
        other = aliased(MediaExternalId)
        src = aliased(MediaExternalId)
        by_provider: dict[str, set[str]] = {}
        for chunk in _chunks(list(ids), CHUNK):
            rows = await self.session.execute(
                select(other.provider, other.external_id)
                .select_from(src)
                .join(other, other.media_id == src.media_id)
                .where(src.provider == source, src.external_id.in_(chunk), other.provider.not_in([source, target]))
            )
            for provider_id, external_id in rows:
                if is_registered(provider_id) and provider_id in get_provider(provider_id).lookup_from:
                    by_provider.setdefault(provider_id, set()).add(external_id)
        for provider_id, external_ids in by_provider.items():
            await self._ask(provider_id, provider_id, sorted(external_ids))

    async def _ask(self, provider_id: str, from_provider: str, ids: Sequence[str]) -> None:
        found = await self._client(provider_id).lookup_external_ids(from_provider, list(ids))
        await self.remember_many(KnownTitle(mapping) for mapping in found)
