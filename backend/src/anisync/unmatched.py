"""The queue of titles that couldn't be translated to a mirror's provider, for admins to link by hand.

Filled by every applied sync (`record`), cleaned by the daily housekeeping (`purge`). A title that has since been
mapped (by the dataset, learning, or an admin) no longer counts as unmatched, whether or not its row is still there.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, exists, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anisync.models import MediaExternalId, UnmatchedTitle, UnmatchedTitleUser
from anisync.providers.base import ListEntry

KEEP = timedelta(days=30)
CHUNK = 1000


def _chunks[T](items: Sequence[T]) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


async def record(session: AsyncSession, user_id: int, source: str, target: str, entries: Sequence[ListEntry]) -> None:
    """Make the user's set of unmatched titles for this source → target pair exactly `entries`."""
    now = datetime.now(UTC)
    ids = sorted({e.media_id for e in entries})
    mine = and_(
        UnmatchedTitleUser.user_id == user_id, UnmatchedTitleUser.source == source, UnmatchedTitleUser.target == target
    )
    stale = delete(UnmatchedTitleUser).where(mine)
    if ids:
        stale = stale.where(UnmatchedTitleUser.source_id.not_in(ids))
    await session.execute(stale)
    titles = {e.media_id: e.title for e in entries}
    for chunk in _chunks(ids):
        stmt = pg_insert(UnmatchedTitle).values(
            [
                {
                    "source": source,
                    "source_id": i,
                    "target": target,
                    "title": titles[i][:255],
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                for i in chunk
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["source", "source_id", "target"],
                set_={"title": stmt.excluded.title, "last_seen_at": now},
            )
        )
        await session.execute(
            pg_insert(UnmatchedTitleUser)
            .values([{"source": source, "source_id": i, "target": target, "user_id": user_id} for i in chunk])
            .on_conflict_do_nothing()
        )


def resolved():
    """SQL condition: the title now has an ID on the target provider (it is mapped, so no longer unmatched)."""
    src, tgt = aliased(MediaExternalId), aliased(MediaExternalId)
    return exists().where(
        src.provider == UnmatchedTitle.source,
        src.external_id == UnmatchedTitle.source_id,
        tgt.media_id == src.media_id,
        tgt.provider == UnmatchedTitle.target,
    )


async def purge(session: AsyncSession) -> int:
    """Forget titles that were resolved or haven't been seen for a month."""
    result = await session.execute(
        delete(UnmatchedTitle).where(or_(UnmatchedTitle.last_seen_at < datetime.now(UTC) - KEEP, resolved()))
    )
    await session.commit()
    return result.rowcount or 0


async def forget(session: AsyncSession, source: str, source_id: str, target: str) -> None:
    await session.execute(
        delete(UnmatchedTitle).where(
            UnmatchedTitle.source == source, UnmatchedTitle.source_id == source_id, UnmatchedTitle.target == target
        )
    )
