"""List snapshots: what we last read from, or wrote to, each linked account.

Syncing works on snapshots instead of asking the providers every time. The master's snapshot is kept current with
cheap incremental reads (only entries changed since a cursor), a mirror's snapshot is whatever we last wrote or read
there, and `plan_sync` compares the two. A change that failed to apply stays missing from the mirror's snapshot, so
the next run retries it without any special bookkeeping. Edits made directly on a provider's site and removals only
show up in a full read, which therefore happens at least weekly.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import LinkedAccount, ListSnapshot
from anisync.providers.base import ListEntry, Status, quantize_score

CHUNK = 1000
SNAPSHOT_TTL = timedelta(days=60)  # a snapshot is a sync cache, not storage: one nobody has read this long is dropped


def synced_hash(entry: ListEntry, score_step: int) -> str:
    """Hash of the fields that sync, with the score at the provider's precision (so 84 and 80 match on step 10)."""
    parts = (
        entry.status.value,
        entry.progress,
        quantize_score(entry.score, score_step) or 0,
        int(entry.rewatching),
        entry.started_at.isoformat() if entry.started_at else "",
        entry.finished_at.isoformat() if entry.finished_at else "",
    )
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def row_to_entry(row: ListSnapshot) -> ListEntry:
    return ListEntry(
        media_id=row.external_id,
        status=Status(row.status),
        progress=row.progress,
        score=row.score,
        rewatching=row.rewatching,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.remote_updated_at,
        title=row.title,
        total_episodes=row.total_episodes,
        image_url=row.image_url,
        adult=row.adult,
        external_ids=dict(row.external_ids or {}),
        remote_entry_id=row.remote_entry_id,
    )


def _values(account_id: int, entry: ListEntry, score_step: int) -> dict:
    return {
        "account_id": account_id,
        "external_id": entry.media_id,
        "remote_entry_id": entry.remote_entry_id,
        "status": entry.status.value,
        "progress": entry.progress,
        "score": entry.score,
        "rewatching": entry.rewatching,
        "started_at": entry.started_at,
        "finished_at": entry.finished_at,
        "remote_updated_at": entry.updated_at,
        "last_written_hash": synced_hash(entry, score_step),
        "title": entry.title,
        "total_episodes": entry.total_episodes,
        "image_url": entry.image_url,
        "adult": entry.adult,
        "external_ids": entry.external_ids,
    }


async def load(session: AsyncSession, account_id: int) -> list[ListEntry]:
    rows = await session.scalars(select(ListSnapshot).where(ListSnapshot.account_id == account_id))
    return [row_to_entry(r) for r in rows]


async def get(session: AsyncSession, account_id: int, external_id: str) -> ListEntry | None:
    row = await session.get(ListSnapshot, (account_id, external_id))
    return row_to_entry(row) if row else None


async def count(session: AsyncSession, account_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(ListSnapshot).where(ListSnapshot.account_id == account_id)
    )


async def upsert(session: AsyncSession, account_id: int, entries: Iterable[ListEntry], score_step: int) -> None:
    latest = {e.media_id: _values(account_id, e, score_step) for e in entries}  # one row per title per statement
    rows = list(latest.values())
    for start in range(0, len(rows), CHUNK):
        stmt = pg_insert(ListSnapshot).values(rows[start : start + CHUNK])
        keep = {"account_id", "external_id"}
        update = {c: stmt.excluded[c] for c in rows[0] if c not in keep} | {"updated_at": func.now()}
        await session.execute(
            stmt.on_conflict_do_update(index_elements=[ListSnapshot.account_id, ListSnapshot.external_id], set_=update)
        )


async def remote_entry_id(session: AsyncSession, account_id: int, external_id: str) -> str | None:
    """The provider's own list-entry id for a title we have seen on the account (None: not known to be on it)."""
    return await session.scalar(
        select(ListSnapshot.remote_entry_id).where(
            ListSnapshot.account_id == account_id, ListSnapshot.external_id == external_id
        )
    )


async def remove_adult(session: AsyncSession, account_id: int) -> int:
    """Forget the adult titles stored for the account (used when its owner chooses to ignore them)."""
    result = await session.execute(
        delete(ListSnapshot).where(ListSnapshot.account_id == account_id, ListSnapshot.adult.is_(True))
    )
    return result.rowcount or 0


async def purge_stale(session: AsyncSession, ttl: timedelta = SNAPSHOT_TTL, now: datetime | None = None) -> int:
    """Delete the snapshots of accounts whose list we haven't read in full for `ttl`; returns the accounts affected.

    Syncing accounts are read in full at least weekly, so only accounts that stopped syncing reach this (sync off,
    re-link needed, a user who went quiet). Clearing `last_full_read_at` and `sync_cursor` makes the next sync start
    from a full read, so nothing outdated is ever planned from, or written over newer data."""
    cutoff = (now or datetime.now(UTC)) - ttl
    stale = select(LinkedAccount.id).where(LinkedAccount.last_full_read_at < cutoff)
    await session.execute(delete(ListSnapshot).where(ListSnapshot.account_id.in_(stale)))
    result = await session.execute(
        update(LinkedAccount)
        .where(LinkedAccount.last_full_read_at < cutoff)
        .values(last_full_read_at=None, sync_cursor=None)
    )
    await session.commit()
    return result.rowcount or 0


async def remove(session: AsyncSession, account_id: int, external_ids: Sequence[str]) -> None:
    for start in range(0, len(external_ids), CHUNK):
        await session.execute(
            delete(ListSnapshot).where(
                ListSnapshot.account_id == account_id, ListSnapshot.external_id.in_(external_ids[start : start + CHUNK])
            )
        )


@dataclass
class Replaced:
    added: int = 0
    removed: int = 0  # in the snapshot but no longer on the provider
    drifted: int = 0  # changed on the provider since we last read or wrote it (edited on their site)


async def replace_all(
    session: AsyncSession, account_id: int, entries: Sequence[ListEntry], score_step: int
) -> Replaced:
    """Make the snapshot equal to a full read of the provider, reporting what changed behind our back."""
    known = dict(
        (
            await session.execute(
                select(ListSnapshot.external_id, ListSnapshot.last_written_hash).where(
                    ListSnapshot.account_id == account_id
                )
            )
        ).all()
    )
    result = Replaced()
    fresh = {e.media_id: e for e in entries}
    for external_id, entry in fresh.items():
        if external_id not in known:
            result.added += 1
        elif known[external_id] is not None and known[external_id] != synced_hash(entry, score_step):
            result.drifted += 1
    gone = [i for i in known if i not in fresh]
    result.removed = len(gone)
    await remove(session, account_id, gone)
    await upsert(session, account_id, fresh.values(), score_step)
    return result


def newest_update(entries: Iterable[ListEntry]):
    stamps = [e.updated_at for e in entries if e.updated_at]
    return max(stamps) if stamps else None
