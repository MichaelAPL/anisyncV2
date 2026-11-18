"""Server-side browser sessions.

The cookie holds a random token (never a user ID); the database holds only its SHA-256, so a leaked table can't be
replayed. Sessions can be listed and revoked individually or all at once, expire after 30 days of inactivity, and a
fresh one is issued on every sign-in (the old one is revoked), which rules out session fixation.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.config import get_settings
from anisync.models import User, UserSession

COOKIE = "anisync_session"
LIFETIME = timedelta(days=30)
TOUCH_AFTER = timedelta(minutes=5)  # don't write on every request


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def start(db: AsyncSession, user_id: int, user_agent: str | None, replacing: str | None = None) -> str:
    """Create a session for the user and return its token. `replacing` (the current cookie) is revoked."""
    if replacing:
        await end(db, replacing)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user_id,
            user_agent=(user_agent or "")[:255],
            created_at=now,
            last_seen_at=now,
            expires_at=now + LIFETIME,
        )
    )
    await db.commit()
    return token


async def find(db: AsyncSession, token: str) -> UserSession | None:
    row = await db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    now = datetime.now(UTC)
    if row is None or row.expires_at <= now:
        return None
    if now - row.last_seen_at > TOUCH_AFTER:
        row.last_seen_at = now
        row.expires_at = now + LIFETIME
        await db.commit()
    return row


async def user_for(db: AsyncSession, token: str) -> User | None:
    row = await find(db, token)
    return await db.get(User, row.user_id) if row else None


async def end(db: AsyncSession, token: str) -> None:
    await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
    await db.commit()


async def end_all(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await db.commit()
    return result.rowcount or 0


async def purge_expired(db: AsyncSession) -> int:
    result = await db.execute(delete(UserSession).where(UserSession.expires_at <= datetime.now(UTC)))
    await db.commit()
    return result.rowcount or 0


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().public_url.startswith("https://"),
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
