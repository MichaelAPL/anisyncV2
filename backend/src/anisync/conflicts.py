"""Cross-reference conflicts: a list read says "these provider IDs are one title" while the ID dataset disagrees.

The mapper never merges such titles and never overwrites what it has (the dataset wins), so nothing is written wrongly;
this module only remembers each conflict once, counts how often it comes up, and keeps an admin's decision, so the log
isn't flooded and the conflicts can be reviewed and reported to whoever owns the wrong data.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import IdConflict, Media


@dataclass(frozen=True)
class Conflict:
    kind: (
        str  # "already_has": the media row has another ID for a provider; "disagree": the IDs belong to different media
    )
    ids: Mapping[str, str]  # what the list read claimed
    media_ids: Sequence[int]
    detail: str

    @property
    def fingerprint(self) -> str:
        ids = "|".join(f"{p}:{i}" for p, i in sorted(self.ids.items()))
        return f"{self.kind}|{ids}|{','.join(str(m) for m in sorted(self.media_ids))}"[:400]


async def record(session: AsyncSession, conflict: Conflict) -> bool:
    """Remember the conflict (or count one more sighting). True when it is new."""
    now = datetime.now(UTC)
    stmt = pg_insert(IdConflict).values(
        fingerprint=conflict.fingerprint,
        kind=conflict.kind,
        ids=dict(conflict.ids),
        media_ids=sorted(conflict.media_ids),
        detail=conflict.detail[:500],
        seen=1,
        first_seen_at=now,
        last_seen_at=now,
        status="open",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[IdConflict.fingerprint], set_={"seen": IdConflict.seen + 1, "last_seen_at": now}
    ).returning(IdConflict.seen)
    return (await session.scalar(stmt)) == 1


async def titles(session: AsyncSession, rows: Sequence[IdConflict]) -> dict[int, str]:
    """{media id: title} for every media row the conflicts mention."""
    wanted = {m for r in rows for m in r.media_ids}
    if not wanted:
        return {}
    found = await session.execute(select(Media.id, Media.title).where(Media.id.in_(wanted)))
    return {i: t for i, t in found}


async def counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(IdConflict.status, func.count()).group_by(IdConflict.status))
    return dict(rows.all())
