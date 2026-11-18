"""Rate limits kept in Postgres, so they hold across processes and restarts.

Each attempt stores one row under a key like `password-link:ip:<hash>`. Callers are refused with 429 and a
`Retry-After` once they've made `limit` attempts inside `window`. IPs are stored only as a salted hash and old rows
are purged by the housekeeping job. Behind a proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>`
so `request.client` is the real client address.
"""

import hashlib
import math
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.api.deps import CurrentUser, Session
from anisync.config import get_settings
from anisync.models import RateLimitHit


def client_id(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(f"{get_settings().session_secret}:{host}".encode()).hexdigest()[:24]


async def check(db: AsyncSession, key: str, limit: int, window: timedelta) -> None:
    """Record an attempt under `key`, or raise 429 if `limit` attempts were already made within `window`."""
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))  # serialize attempts per key
    now = datetime.now(UTC)
    recent = (
        await db.scalars(
            select(RateLimitHit.created_at)
            .where(RateLimitHit.key == key, RateLimitHit.created_at > now - window)
            .order_by(RateLimitHit.created_at)
        )
    ).all()
    if len(recent) >= limit:
        # Free again once the oldest of the last `limit` attempts leaves the window.
        wait = max(1, math.ceil((recent[-limit] + window - now).total_seconds()))
        await db.rollback()
        raise HTTPException(429, "Too many attempts. Try again later.", headers={"Retry-After": str(wait)})
    db.add(RateLimitHit(key=key, created_at=now))
    await db.commit()  # keep the attempt even if the request later fails


def per_ip(action: str, limit: int, window: timedelta):
    async def dependency(request: Request, db: Session) -> None:
        await check(db, f"{action}:ip:{client_id(request)}", limit, window)

    return Depends(dependency)


def per_user(action: str, limit: int, window: timedelta):
    async def dependency(user: CurrentUser, db: Session) -> None:
        await check(db, f"{action}:user:{user.id}", limit, window)

    return Depends(dependency)


async def purge_old(db: AsyncSession, older_than: timedelta = timedelta(days=1)) -> int:
    result = await db.execute(delete(RateLimitHit).where(RateLimitHit.created_at < datetime.now(UTC) - older_than))
    await db.commit()
    return result.rowcount or 0
