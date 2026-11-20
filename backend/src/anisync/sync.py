"""Orchestration: bring list snapshots up to date, plan against them, apply the differences.

A sync works on snapshots (see `snapshots.py`), so a run where nothing changed costs one small request: the master's
incremental read. The mirrors are not asked at all between full reads, because we know what we last wrote there.
A full read of an account happens when it has none yet, at least weekly (that is what notices removals and edits made
directly on a provider's site), when the provider can't read incrementally (master only), or when asked for.
"""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import snapshots, switches, unmatched
from anisync.engine import Plan, plan_sync
from anisync.logs import redact
from anisync.mapping import IdMapper
from anisync.models import LinkedAccount, SyncRun, SyncRunEvent, User
from anisync.providers import get_provider
from anisync.providers.base import ChangeSkipped, ListEntry, ProviderClient, RateLimited, ReauthRequired

log = logging.getLogger(__name__)

FULL_READ_EVERY = timedelta(days=7)
MAX_EVENTS_PER_MIRROR = 50  # a run that fails everywhere keeps its log readable and small; the summary has the count
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


def _without_adult(entries: list[ListEntry], drop: bool) -> list[ListEntry]:
    """The owner chose to ignore adult titles on this list: they are dropped before anything is stored."""
    return [e for e in entries if not e.adult] if drop else entries


def full_read_due(account: LinkedAccount, now: datetime | None = None) -> bool:
    return (
        account.last_full_read_at is None or (now or datetime.now(UTC)) - account.last_full_read_at >= FULL_READ_EVERY
    )


async def refresh_snapshot(
    session: AsyncSession,
    account: LinkedAccount,
    client: ProviderClient,
    *,
    is_master: bool,
    full: bool = False,
    drop_adult: bool = False,
) -> ReadResult:
    """Bring the account's snapshot up to date with the provider, as cheaply as the provider allows."""
    provider = get_provider(account.provider)
    now = datetime.now(UTC)
    incremental = is_master and provider.incremental_reads and account.sync_cursor is not None
    if full or full_read_due(account, now) or (is_master and not incremental):
        entries = _without_adult(await client.fetch_list(), drop_adult)
        replaced = await snapshots.replace_all(session, account.id, entries, provider.score_step)
        account.last_full_read_at = now
        account.sync_cursor = snapshots.newest_update(entries) or account.sync_cursor
        await session.commit()
        return ReadResult("full", entries, replaced.removed, replaced.drifted)
    if is_master:
        entries = _without_adult(await client.fetch_list(since=account.sync_cursor), drop_adult)
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
    ignore_adult: bool = False,
) -> Plan:
    async with IdMapper(session) as mapper:
        await mapper.learn_from(learn_from)
        id_map = await mapper.resolve(master.provider, mirror.provider, [e.media_id for e in master_entries])
    await session.commit()
    # A master that hides adult titles from us looks shorter than it is: never treat that as "removed from master".
    prune = mirror.prune_missing and not master.hides_adult
    return plan_sync(
        master_entries,
        mirror_entries,
        id_map,
        get_provider(mirror.provider).score_step,
        prune,
        mirror.skip_adult,
        protect_adult=ignore_adult,
    )


@dataclass
class Event:
    level: str
    kind: str
    message: str
    media_ref: str | None = None


BATCH = 25  # changes written between saves: progress is visible and an interrupted sync keeps what it did
RATE_LIMIT_RETRIES = 3
MAX_WAIT = 60.0  # never sleep longer than this for one "retry after"


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)  # a function of its own so tests can skip the wait


async def _write(client: ProviderClient, change) -> str | None:
    """One change, waiting out a provider's "slow down" (up to a few times) instead of failing every change after it."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            if change.op == "delete":
                await client.delete_entry(change.media_id, change.current.remote_entry_id if change.current else None)
                return None
            return await client.save_entry(change.desired)  # the provider's id for the entry, when it told us
        except RateLimited as exc:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            await _pause(min(exc.retry_after or 5.0, MAX_WAIT) + 1)


async def apply_plan(
    session: AsyncSession,
    mirror: LinkedAccount,
    client: ProviderClient,
    plan: Plan,
    events: list[Event] | None = None,
    run: SyncRun | None = None,
    skipped: list[str] | None = None,
) -> list[str]:
    """Write the plan to the provider, then record what succeeded in the mirror's snapshot.

    Results are saved every `BATCH` changes, so a long sync shows progress (`run.planned`/`run.processed`) and an
    interrupted one keeps what it did. A change that fails is left out of the snapshot, so the next sync plans it
    again. Failures are also appended to `events` (structured, for the admin sync log) when it is given. Changes the
    provider hides from us (`ChangeSkipped`) are not failures: their reasons are collected in `skipped`."""
    step = get_provider(mirror.provider).score_step
    errors: list[str] = []
    written: list[ListEntry] = []
    deleted: list[str] = []

    async def save() -> None:
        await snapshots.upsert(session, mirror.id, written, step)
        await snapshots.remove(session, mirror.id, deleted)
        written.clear()
        deleted.clear()
        await session.commit()

    if run is not None:
        run.planned += len(plan.changes)
        await session.commit()
    for done, change in enumerate(plan.changes, start=1):
        try:
            learned = await _write(client, change)
            if change.op == "delete":
                deleted.append(change.media_id)
            else:
                written.append(replace(change.desired, remote_entry_id=learned) if learned else change.desired)
        except ReauthRequired:
            await save()
            raise
        except ChangeSkipped as exc:
            mirror.hides_adult = True  # the provider just showed us it hides titles; the notice follows from this
            if skipped is not None:
                skipped.append(str(exc))
            if events is not None:
                events.append(Event("info", "skipped", f"{change.op} {change.media_id}: {exc}"[:500], change.media_id))
        except Exception as exc:  # keep going; one bad entry shouldn't block the rest
            log.warning("sync change failed: %s %s: %s", change.op, change.media_id, exc)
            message = f"{change.op} {change.media_id}: {redact(str(exc))[:300]}"  # shown to users and admins
            errors.append(message)
            if events is not None:
                rate_limited = isinstance(exc, RateLimited)
                events.append(
                    Event(
                        "warning" if rate_limited else "error",
                        "rate_limited" if rate_limited else "change_failed",
                        message,
                        change.media_id,
                    )
                )
        if run is not None:
            run.processed += 1
        if done % BATCH == 0:
            await save()
    await save()
    return errors


RUN_STALE_AFTER = timedelta(minutes=15)  # an unfinished run older than this is treated as dead, not as running
_RUN_LOCK = 7001  # advisory-lock namespace: one sync start per user at a time


async def running_run(session: AsyncSession, user_id: int) -> SyncRun | None:
    """The user's sync that is in progress right now, if any."""
    return await session.scalar(
        select(SyncRun)
        .where(
            SyncRun.user_id == user_id,
            SyncRun.finished_at.is_(None),
            SyncRun.started_at > datetime.now(UTC) - RUN_STALE_AFTER,
        )
        .order_by(SyncRun.id.desc())
        .limit(1)
    )


async def close_interrupted_runs(session: AsyncSession, everything: bool = False) -> int:
    """Mark runs that stopped halfway (the process died or was restarted) as `interrupted` instead of leaving them
    "running". `everything` also closes young ones: right after a start no run of this process can be in progress."""
    stmt = update(SyncRun).where(SyncRun.finished_at.is_(None))
    if not everything:
        stmt = stmt.where(SyncRun.started_at <= datetime.now(UTC) - RUN_STALE_AFTER)
    result = await session.execute(stmt.values(status="interrupted", finished_at=datetime.now(UTC)))
    await session.commit()
    return result.rowcount or 0


async def start_run(session: AsyncSession, user_id: int, trigger: str) -> SyncRun | None:
    """Open a run for the user, or None when one is already in progress. Checked and created under a per-user lock so
    two workers starting at the same moment can't both win (the lock is held until the run row is committed)."""
    await session.execute(select(func.pg_advisory_xact_lock(_RUN_LOCK, user_id)))
    if await running_run(session, user_id) is not None:
        return None  # the lock is released when this transaction ends
    run = SyncRun(user_id=user_id, trigger=trigger, summary={})
    session.add(run)
    await session.commit()
    return run


async def run_sync(
    session: AsyncSession, user_id: int, trigger: str, dry_run: bool = False, full: bool = False
) -> dict:
    """Sync the user's master list to every mirror. `dry_run` only reads (it may refresh snapshots)."""
    user = await session.get(User, user_id)
    if user is None or user.master is None:
        return {"skipped": "no master list selected"}
    if user.is_banned:
        return {"skipped": "banned"}
    paused = await switches.paused_providers(session)
    if user.master.provider in paused:
        return {"skipped": f"{get_provider(user.master.provider).name} is paused"}
    run = None
    if not dry_run:
        run = await start_run(session, user.id, trigger)
        if run is None:
            return {"skipped": "a sync is already running"}
    run_id = run.id if run is not None else None  # read now: a rollback expires `run`, and reading it then is I/O
    try:
        return await _sync_mirrors(session, user, run, paused, dry_run, full)
    except BaseException:
        if run_id is not None:  # never leave a run "running" behind: the dashboard and admins read it as in progress
            await session.rollback()
            failed = await session.get(SyncRun, run_id)
            if failed is not None:
                failed.status, failed.finished_at = "error", datetime.now(UTC)
                failed.summary = failed.summary or {"error": "the sync stopped unexpectedly"}
                await session.commit()
        raise


async def check_adult_filter(account: LinkedAccount, client: ProviderClient) -> None:
    """Re-read whether the account hides adult titles (the user may have changed the setting). One request, and only
    for providers that have such a setting."""
    if get_provider(account.provider).adult_help is not None:
        account.hides_adult = (await client.whoami()).hides_adult


async def _sync_mirrors(
    session: AsyncSession, user: User, run: SyncRun | None, paused: dict, dry_run: bool, full: bool
) -> dict:
    master = user.master
    summary: dict = {}
    master_client = await client_for(master, session)
    ignore_adult = user.ignore_adult_titles
    master_read = await refresh_snapshot(
        session, master, master_client, is_master=True, full=full, drop_adult=ignore_adult
    )
    if master_read.mode == "full":  # weekly; in between, the user can say "I changed it" (POST .../recheck-adult)
        await check_adult_filter(master, master_client)
    master_entries = await snapshots.load(session, master.id)
    log.info("sync user %s: master read %s (%s received)", user.id, master_read.mode, len(master_read.received))
    for mirror in user.mirrors:
        events: list[Event] = []
        if mirror.provider in paused:  # not an error: the mirror is left alone and catches up once it is resumed
            summary[str(mirror.id)] = {
                "provider": mirror.provider,
                "username": mirror.remote_username,
                "paused": True,
                "reason": paused[mirror.provider],
            }
            continue
        try:
            client = await client_for(mirror, session)
            read = await refresh_snapshot(session, mirror, client, is_master=False, full=full)
            if read.mode == "full":
                await check_adult_filter(mirror, client)
            mirror_entries = await snapshots.load(session, mirror.id)
            plan = await plan_for_mirror(
                session,
                master,
                master_entries,
                mirror,
                mirror_entries,
                master_read.received + read.received,
                ignore_adult,
            )
            result: dict = plan.counts() | {"unmapped_titles": [e.title for e in plan.unmapped][:50], "read": read.mode}
            if plan.adult_skipped:
                result["adult_skipped"] = plan.adult_skipped
            if read.mode == "full":
                result["drifted"] = read.drifted
                if read.drifted:
                    events.append(
                        Event("info", "drifted", f"{read.drifted} entries were edited directly on {mirror.provider}")
                    )
            if plan.unmapped:
                events.append(
                    Event("info", "unmapped", f"{len(plan.unmapped)} titles have no {mirror.provider} equivalent")
                )
            if dry_run:
                result["changes"] = [
                    {"op": c.op, "media_id": c.media_id, "fields": c.fields, "title": (c.desired or c.current).title}
                    for c in plan.changes
                ]
            else:
                await unmatched.record(session, user.id, master.provider, mirror.provider, plan.unmapped)
                skipped: list[str] = []
                result["errors"] = await apply_plan(session, mirror, client, plan, events, run, skipped)
                if skipped:
                    result["skipped"] = len(skipped)
                    result["skipped_reason"] = skipped[0]
                mirror.last_synced_at = datetime.now(UTC)
        except ReauthRequired as exc:
            result = {"error": str(exc), "reauth_required": True}
            events.append(Event("error", "reauth", redact(str(exc))[:500]))
        if run is not None:
            session.add_all(
                SyncRunEvent(
                    run_id=run.id,
                    account_id=mirror.id,
                    provider=mirror.provider,
                    level=e.level,
                    kind=e.kind,
                    message=e.message,
                    media_ref=e.media_ref,
                )
                for e in events[:MAX_EVENTS_PER_MIRROR]
            )
            if len(events) > MAX_EVENTS_PER_MIRROR:
                result["events_dropped"] = len(events) - MAX_EVENTS_PER_MIRROR
        summary[str(mirror.id)] = {"provider": mirror.provider, "username": mirror.remote_username} | result

    if run is not None:
        run.summary = summary
        run.status = "error" if any("error" in s for s in summary.values()) else "ok"
        run.changes = sum(s.get("create", 0) + s.get("update", 0) + s.get("delete", 0) for s in summary.values())
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
    if account.provider in await switches.paused_providers(session):
        log.info("%s is paused; not pushing %s (the next sync catches up)", account.provider, master_media_id)
        return
    if entry is not None and entry.adult and account.skip_adult:
        return  # this mirror was asked to leave adult titles out
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
        master = owner.master
        if account.prune_missing and not (master is not None and master.hides_adult):
            await client.delete_entry(target_id)
            await snapshots.remove(session, account.id, [target_id])
    else:
        known = await snapshots.remote_entry_id(session, account.id, target_id)  # lets the provider skip a lookup
        written = replace(entry, media_id=target_id, remote_entry_id=known)
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


def next_batch_at(now: datetime | None = None) -> datetime:
    """The start of the next hourly batch of scheduled syncs (UTC)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


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
