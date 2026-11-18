"""Background jobs (Procrastinate: Postgres-backed queue, the Sidekiq equivalent).

Run a standalone worker with:
    uv run procrastinate --app=anisync.jobs.app worker
or let the API process run one in-process (RUN_WORKER_IN_PROCESS=true, the default).
"""

import logging

import httpx
from procrastinate import App, PsycopgConnector, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued

from anisync import sync
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


@app.periodic(cron=settings.daily_sync_cron, periodic_id="daily-sync")
@app.task(queue="sync", queueing_lock="daily-sync")
async def daily_sync(timestamp: int) -> None:
    """Fan out one sync_user job per user that has a master list."""
    async with SessionLocal() as session:
        user_ids = await sync.users_with_master(session)
    for user_id in user_ids:
        await enqueue_user_sync(user_id, "daily")


@app.task(queue="sync", retry=TRANSIENT)
async def sync_user(user_id: int, trigger: str) -> dict:
    async with SessionLocal() as session:
        return await sync.run_sync(session, user_id, trigger)


@app.task(queue="push", retry=TRANSIENT)
async def push_entry(account_id: int, master_provider: str, master_media_id: str, entry: dict | None) -> None:
    async with SessionLocal() as session:
        await sync.push_entry(
            session, account_id, master_provider, master_media_id, ListEntry.from_dict(entry) if entry else None
        )


async def enqueue_user_sync(user_id: int, trigger: str) -> bool:
    """Queue a full sync unless one is already waiting for this user."""
    try:
        await sync_user.configure(queueing_lock=f"sync-user-{user_id}").defer_async(user_id=user_id, trigger=trigger)
        return True
    except AlreadyEnqueued:
        return False
