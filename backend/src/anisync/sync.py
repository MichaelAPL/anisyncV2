"""Orchestration: bring list snapshots up to date, plan against them, apply the differences.

A sync works on snapshots (see `snapshots.py`), so a run where nothing changed costs one small request: the master's
incremental read. The mirrors are not asked at all between full reads, because we know what we last wrote there.
A full read of an account happens when it has none yet, at least weekly (that is what notices removals and edits made
directly on a provider's site), when the provider can't read incrementally (master only), or when asked for.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import snapshots
from anisync.engine import Plan, plan_sync
from anisync.logs import redact
from anisync.mapping import IdMapper
from anisync.models import LinkedAccount, SyncRun, User
from anisync.providers import get_provider
from anisync.providers.base import ListEntry, ProviderClient, ReauthRequired

log = logging.getLogger(__name__)

FULL_READ_EVERY = timedelta(days=7)
SYNC_SLOTS = 24  # users are spread over the day: a user is synced in the hour that equals their id modulo this


async def client_for(account: LinkedAccount, session: AsyncSession) -> ProviderClient:
    provider = get_provider(account.provider)
    if account.expires_at and account.expires_at < datetime.now(UTC) + timedelta(minutes=5):
        # Providers without a refresh hook (or an account without a refresh token) need the user to re-link.
        if provider.refresh is None or not account.refresh_token:
            raise ReauthRequired(f"{provider.name} token for {account.remote_username} expired; re-link needed")
        tokens = await provider.refresh(account.refresh_token)
        account.access_token = tokens.access_token
        account.refresh_token = tokens.refresh_token or account.refresh_token
        account.expires_at = tokens.expires_at
        await session.commit()
    return provider.client(account.access_token, account.remote_user_id)


# ---- reading -----------------------------------------------------------------------------------------------


@dataclass
class ReadResult:
    mode: Literal["full", "incremental", "snapshot"]  # how the snapshot was brought up to date
    received: list[ListEntry] = field(default_factory=list)  # what the provider sent (nothing for "snapshot")
    removed: int = 0  # entries gone from the provider (full reads)
    drifted: int = 0  # entries edited directly on the provider's site since we last read or wrote them (full reads)


def full_read_due(account: LinkedAccount, now: datetime | None = None) -> bool:
    return (
        account.last_full_read_at is None or (now or datetime.now(UTC)) - account.last_full_read_at >= FULL_READ_EVERY
    )


async def refresh_snapshot(
    session: AsyncSession, account: LinkedAccount, client: ProviderClient, *, is_master: bool, full: bool = False
) -> ReadResult:
    """Bring the account's snapshot up to date with the provider, as cheaply as the provider allows."""
    provider = get_provider(account.provider)
    now = datetime.now(UTC)
    incremental = is_master and provider.incremental_reads and account.sync_cursor is not None
    if full or full_read_due(account, now) or (is_master and not incremental):
        entries = await client.fetch_list()
        replaced = await snapshots.replace_all(session, account.id, entries, provider.score_step)
        account.last_full_read_at = now
        account.sync_cursor = snapshots.newest_update(entries) or account.sync_cursor
        await session.commit()
        return ReadResult("full", entries, replaced.removed, replaced.drifted)
    if is_master:
        entries = await client.fetch_list(since=account.sync_cursor)
        await snapshots.upsert(session, account.id, entries, provider.score_step)
        account.sync_cursor = max(snapshots.newest_update(entries) or account.sync_cursor, account.sync_cursor)
        await session.commit()
        return ReadResult("incremental", entries)
    return ReadResult("snapshot")  # a mirror between full reads: what we last wrote is what is there


# ---- planning and applying ---------------------------------------------------------------------------------


async def plan_for_mirror(
    session: AsyncSession,
    master: LinkedAccount,
    master_entries: list[ListEntry],
    mirror: LinkedAccount,
    mirror_entries: list[ListEntry],
    learn_from: list[ListEntry],
) -> Plan:
    async with IdMapper(session) as mapper:
        await mapper.learn_from(learn_from)
        id_map = await mapper.resolve(master.provider, mirror.provider, [e.media_id for e in master_entries])
    await session.commit()
    return plan_sync(
        master_entries, mirror_entries, id_map, get_provider(mirror.provider).score_step, mirror.prune_missing
    )


async def apply_plan(session: AsyncSession, mirror: LinkedAccount, client: ProviderClient, plan: Plan) -> list[str]:
    """Write the plan to the provider, then record what succeeded in the mirror's snapshot.

    A change that fails is left out of the snapshot, so the next sync plans it again."""
    errors, written, deleted = [], [], []
    for change in plan.changes:
        try:
            if change.op == "delete":
                await client.delete_entry(change.media_id, change.current.remote_entry_id if change.current else None)
                deleted.append(change.media_id)
            else:
                await client.save_entry(change.desired)
                written.append(change.desired)
        except ReauthRequired:
            raise
        except Exception as exc:  # keep going; one bad entry shouldn't block the rest
            log.warning("sync change failed: %s %s: %s", change.op, change.media_id, exc)
            errors.append(f"{change.op} {change.media_id}: {redact(str(exc))[:300]}")  # shown to users and admins
    step = get_provider(mirror.provider).score_step
    await snapshots.upsert(session, mirror.id, written, step)
    await snapshots.remove(session, mirror.id, deleted)
    await session.commit()
    return errors


async def run_sync(
    session: AsyncSession, user_id: int, trigger: str, dry_run: bool = False, full: bool = False
) -> dict:
    """Sync the user's master list to every mirror. `dry_run` only reads (it may refresh snapshots)."""
    user = await session.get(User, user_id)
    if user is None or user.master is None:
        return {"skipped": "no master list selected"}
    if user.is_banned:
        return {"skipped": "banned"}
    run = None
    if not dry_run:
        run = SyncRun(user_id=user.id, trigger=trigger, summary={})
        session.add(run)
        await session.commit()

    master = user.master
    summary: dict = {}
    master_read = await refresh_snapshot(session, master, await client_for(master, session), is_master=True, full=full)
    master_entries = await snapshots.load(session, master.id)
    log.info("sync user %s: master read %s (%s received)", user.id, master_read.mode, len(master_read.received))
    for mirror in user.mirrors:
        try:
            client = await client_for(mirror, session)
            read = await refresh_snapshot(session, mirror, client, is_master=False, full=full)
            mirror_entries = await snapshots.load(session, mirror.id)
            plan = await plan_for_mirror(
                session, master, master_entries, mirror, mirror_entries, master_read.received + read.received
            )
            result: dict = plan.counts() | {"unmapped_titles": [e.title for e in plan.unmapped][:50], "read": read.mode}
            if read.mode == "full":
                result["drifted"] = read.drifted
            if dry_run:
                result["changes"] = [
                    {"op": c.op, "media_id": c.media_id, "fields": c.fields, "title": (c.desired or c.current).title}
                    for c in plan.changes
                ]
            else:
                result["errors"] = await apply_plan(session, mirror, client, plan)
                mirror.last_synced_at = datetime.now(UTC)
        except ReauthRequired as exc:
            result = {"error": str(exc), "reauth_required": True}
        summary[str(mirror.id)] = {"provider": mirror.provider, "username": mirror.remote_username} | result

    if run is not None:
        run.summary = summary
        run.status = "error" if any("error" in s for s in summary.values()) else "ok"
        run.finished_at = datetime.now(UTC)
        await session.commit()
    return summary


async def push_entry(
    session: AsyncSession, account_id: int, master_provider: str, master_media_id: str, entry: ListEntry | None
) -> None:
    """Propagate one master-list edit to one mirror. entry=None means it was removed from the master."""
    account = await session.get(LinkedAccount, account_id)
    if account is None or not account.sync_enabled:
        return
    owner = await session.get(User, account.user_id)
    if owner is None or owner.is_banned:
        return
    async with IdMapper(session) as mapper:
        if entry is not None:
            await mapper.learn_from([entry])
        target_id = (await mapper.resolve(master_provider, account.provider, [master_media_id]))[master_media_id]
    await session.commit()
    if target_id is None:
        log.info("no %s ID for %s title %s; skipping push", account.provider, master_provider, master_media_id)
        return
    client = await client_for(account, session)
    step = get_provider(account.provider).score_step
    if entry is None:
        if account.prune_missing:
            await client.delete_entry(target_id)
            await snapshots.remove(session, account.id, [target_id])
    else:
        written = replace(entry, media_id=target_id, remote_entry_id=None)
        await client.save_entry(written)
        await snapshots.upsert(session, account.id, [written], step)
    await session.commit()


# ---- scheduling --------------------------------------------------------------------------------------------


def sync_hour(user_id: int) -> int:
    """The hour of the day (UTC) in which this user's scheduled sync runs."""
    return user_id % SYNC_SLOTS


def next_sync_at(user_id: int, now: datetime | None = None) -> datetime:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while candidate.hour != sync_hour(user_id):
        candidate += timedelta(hours=1)
    return candidate


async def users_in_slot(session: AsyncSession, hour: int) -> list[int]:
    """Users with a master list whose scheduled sync is due in this hour of the day."""
    ids = await session.scalars(
        select(User.id).where(
            User.master_account_id.is_not(None), User.banned_at.is_(None), User.id % SYNC_SLOTS == hour % SYNC_SLOTS
        )
    )
    return list(ids)


async def purge_runs(session: AsyncSession, keep_days: int = 30, keep_per_user: int = 50) -> int:
    """Delete sync runs older than `keep_days`, and any beyond the newest `keep_per_user` of a user."""
    ranked = select(
        SyncRun.id,
        SyncRun.started_at,
        func.row_number().over(partition_by=SyncRun.user_id, order_by=SyncRun.started_at.desc()).label("rank"),
    ).subquery()
    doomed = select(ranked.c.id).where(
        or_(ranked.c.rank > keep_per_user, ranked.c.started_at < datetime.now(UTC) - timedelta(days=keep_days))
    )
    result = await session.execute(delete(SyncRun).where(SyncRun.id.in_(doomed)))
    await session.commit()
    return result.rowcount or 0


async def users_with_master(session: AsyncSession) -> list[int]:
    return list(
        await session.scalars(select(User.id).where(User.master_account_id.is_not(None), User.banned_at.is_(None)))
    )
