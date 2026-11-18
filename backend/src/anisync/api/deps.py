from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import sessions
from anisync.db import get_session
from anisync.models import User

Session = Annotated[AsyncSession, Depends(get_session)]


async def optional_user(request: Request, session: Session) -> User | None:
    token = request.cookies.get(sessions.COOKIE)
    return await sessions.user_for(session, token) if token else None


async def require_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(401, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(require_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]
