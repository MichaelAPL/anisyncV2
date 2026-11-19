from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import sessions
from anisync.db import get_session
from anisync.models import User

Session = Annotated[AsyncSession, Depends(get_session)]


async def optional_user(request: Request, session: Session) -> User | None:
    token = request.cookies.get(sessions.COOKIE)
    user = await sessions.user_for(session, token) if token else None
    return None if user is None or user.is_banned else user  # a banned user is treated as signed out


async def require_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(401, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(require_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]


def require_permission(*needed: str):
    """A dependency that passes the signed-in user if their role grants *all* of `needed`, else 403.

    Use as `admin: Annotated[User, Depends(require_permission("users.ban"))]`. Banned users never get this far
    (`require_user` treats them as signed out). Tests find the declared permissions in `.permissions`."""

    async def check(user: CurrentUser) -> User:
        if not set(needed) <= user.permissions:
            raise HTTPException(403, "Not allowed")
        return user

    check.permissions = frozenset(needed)  # type: ignore[attr-defined]
    return check


async def require_any_permission(user: CurrentUser) -> User:
    """Safety net on the whole admin router: whatever a route forgets to declare, a plain user never gets in."""
    if not user.permissions:
        raise HTTPException(403, "Not allowed")
    return user
