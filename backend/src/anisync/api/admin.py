"""Admin endpoints (`/api/admin/*`). Each route declares the permission it needs; the router as a whole also
refuses users without any permission."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from anisync import moderation, permissions
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_any_permission)])


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    role: str
    created_at: datetime
    banned_at: datetime | None
    ban_reason: str | None


class BanIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class UnbanIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RoleIn(BaseModel):
    role: str = Field(max_length=32)


async def _run(action) -> User:
    try:
        return await action
    except moderation.ModerationError as exc:
        raise HTTPException(exc.status, exc.message) from None


@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    body: BanIn,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_BAN))],
) -> AdminUserOut:
    return AdminUserOut.model_validate(await _run(moderation.ban(session, admin, user_id, body.reason)))


@router.post("/users/{user_id}/unban")
async def unban_user(
    user_id: int,
    body: UnbanIn,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_BAN))],
) -> AdminUserOut:
    return AdminUserOut.model_validate(await _run(moderation.unban(session, admin, user_id, body.note)))


@router.put("/users/{user_id}/role")
async def set_user_role(
    user_id: int,
    body: RoleIn,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.ROLES_MANAGE))],
) -> AdminUserOut:
    return AdminUserOut.model_validate(await _run(moderation.set_role(session, admin, user_id, body.role)))
