"""Background jobs (Procrastinate: Postgres-backed queue, the Sidekiq equivalent).

Run a standalone worker with:
    uv run procrastinate --app=anisync.jobs.app worker
or let the API process run one in-process (RUN_WORKER_IN_PROCESS=true, the default).
"""

import logging
from datetime import UTC, datetime

import httpx
from procrastinate import App, PsycopgConnector, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued

from anisync import metrics, ratelimit, seed, sessions, snapshots, switches, sync, unmatched
from anisync.config import get_settings
from anisync.db import SessionLocal
from anisync.providers.base import ListEntry, RateLimited

log = logging.getLogger(__name__)
settings = get_settings()

app = App(connector=PsycopgConnector(conninfo=settings.database_url))

TRANSIENT = RetryStrategy(
    max_attempts=5,
    exponential_wait=30,
    retry_exceptions={RateLimited, httpx.TransportError, httpx.HTTPStatusError},
)


@app.periodic(cron=settings.sync_tick_cron, periodic_id="sync-tick")
@app.task(queue="sync", queueing_lock="sync-tick")
async def sync_tick(timestamp: int) -> None:
    """Hourly: queue a sync for the users whose hour it is (each user once a day, spread over 24 hours)."""
    hour = datetime.fromtimestamp(timestamp, UTC).hour
    async with SessionLocal() as session:
        if not await switches.background_work_allowed(session):
            log.info("sync tick skipped: scheduled syncs are paused or the site is in maintenance")
            return
        user_ids = await sync.users_in_slot(session, hour)
    for user_id in user_ids:
        await enqueue_user_sync(user_id, "scheduled")


@app.periodic(cron="*/5 * * * *", periodic_id="flush-metrics")
@app.task(queue="maintenance", queueing_lock="flush-metrics")
async def flush_metrics(timestamp: int) -> None:
    """Write the provider request counters collected in memory to the database (also done at shutdown)."""
    async with SessionLocal() as session:
        await metrics.flush(session)


@app.periodic(cron="30 3 * * *", periodic_id="housekeeping")
@app.task(queue="maintenance", queueing_lock="housekeeping")
async def housekeeping(timestamp: int) -> None:
    """Delete expired sessions, old rate-limit attempts and old sync history."""
    async with SessionLocal() as session:
        expired = await sessions.purge_expired(session)
        attempts = await ratelimit.purge_old(session)
        runs = await sync.purge_runs(session)
        titles = await unmatched.purge(session)
        stopped = await sync.close_interrupted_runs(session)
        expired_snapshots = await snapshots.purge_stale(session)
    if stopped:
        log.warning("housekeeping: closed %s sync run(s) that stopped halfway", stopped)
    log.info(
        "housekeeping: removed %s sessions, %s rate-limit rows, %s sync runs, %s unmatched titles, "
        "%s stale list snapshots",
        expired,
        attempts,
        runs,
        titles,
        expired_snapshots,
    )


@app.periodic(cron=settings.id_dataset_cron, periodic_id="seed-ids")
@app.task(queue="maintenance", queueing_lock="seed-ids", retry=TRANSIENT)
async def seed_ids(timestamp: int) -> None:
    """Import the offline ID dataset if there is a new version (cheap otherwise: only its first line is read)."""
    async with SessionLocal() as session:
        await seed.run_import(session)


@app.task(queue="maintenance", queueing_lock="seed-ids-now", retry=TRANSIENT)
async def import_dataset(force: bool = False) -> None:
    """The same import, started by an admin (`force` re-imports a version that was already imported)."""
    async with SessionLocal() as session:
        await seed.run_import(session, force=force)


@app.task(queue="sync", retry=TRANSIENT)
async def sync_user(user_id: int, trigger: str, full: bool = False) -> dict:
    async with SessionLocal() as session:
        if not await switches.background_work_allowed(session):
            return {"skipped": "paused"}  # the next scheduled sync picks the user up again
        return await sync.run_sync(session, user_id, trigger, full=full)


@app.task(queue="push", retry=TRANSIENT)
async def push_entry(account_id: int, master_provider: str, master_media_id: str, entry: dict | None) -> None:
    async with SessionLocal() as session:
        if not await switches.background_work_allowed(session):
            return  # not lost: the mirror's snapshot lacks the change, so the next sync writes it
        await sync.push_entry(
            session, account_id, master_provider, master_media_id, ListEntry.from_dict(entry) if entry else None
        )


async def enqueue_user_sync(user_id: int, trigger: str, full: bool = False) -> bool:
    """Queue a sync unless one is already waiting for this user. `full` also re-reads every account in full."""
    kwargs = {"user_id": user_id, "trigger": trigger} | ({"full": True} if full else {})
    try:
        await sync_user.configure(queueing_lock=f"sync-user-{user_id}").defer_async(**kwargs)
        return True
    except AlreadyEnqueued:
        return False
