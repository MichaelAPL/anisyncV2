"""Admin endpoints (`/api/admin/*`). Each route declares the permission it needs; the router as a whole also
refuses users without any permission."""

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from anisync import moderation, permissions, stats
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.models import LinkedAccount, User

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


# ---- statistics ---------------------------------------------------------------------------------------------


class ProviderAccountsOut(BaseModel):
    provider: str
    accounts: int
    masters: int  # users who chose this provider as their master
    needs_relink: int
    expiring_soon: int


class OverviewOut(BaseModel):
    users_total: int
    users_with_master: int
    new_today: int
    new_7d: int
    active_7d: int
    active_30d: int
    banned: int
    providers: list[ProviderAccountsOut]


class DayOut(BaseModel):
    day: date
    new_users: int
    runs_ok: int
    runs_error: int
    changes: int


class ProviderRequestsOut(BaseModel):
    day: date
    provider: str
    requests: int
    rate_limited: int
    errors: int
    avg_ms: int


class DailyOut(BaseModel):
    days: list[DayOut]
    provider_requests: list[ProviderRequestsOut]


@router.get("/stats/overview")
async def stats_overview(
    session: Session, admin: Annotated[User, Depends(require_permission(permissions.STATS_READ))]
) -> OverviewOut:
    return OverviewOut.model_validate(asdict(await stats.overview(session)))


@router.get("/stats/daily")
async def stats_daily(
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.STATS_READ))],
    days: Annotated[int, Query(ge=1, le=60)] = 14,
) -> DailyOut:
    series, requests = await stats.daily(session, days)
    return DailyOut(
        days=[DayOut.model_validate(asdict(d)) for d in series],
        provider_requests=[ProviderRequestsOut.model_validate(asdict(r)) for r in requests],
    )


# ---- connections that end ----------------------------------------------------------------------------------


class ExpiringAccountOut(BaseModel):
    account_id: int
    user_id: int
    display_name: str
    provider: str
    remote_username: str
    is_master: bool
    expires_at: datetime
    state: str  # "expired" (the user has to link it again now) | "expiring"


@router.get("/accounts/expiring")
async def expiring_accounts(
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_READ))],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ExpiringAccountOut]:
    """Connections that can't be renewed and end within `days`, soonest first."""
    now = datetime.now(UTC)
    found = await session.execute(
        select(LinkedAccount, User)
        .join(User, User.id == LinkedAccount.user_id)
        .where(
            LinkedAccount.expires_at.is_not(None),
            LinkedAccount.expires_at < now + timedelta(days=days),
            User.banned_at.is_(None),
        )
        .order_by(LinkedAccount.expires_at)
    )
    out = [
        ExpiringAccountOut(
            account_id=a.id,
            user_id=u.id,
            display_name=u.display_name,
            provider=a.provider,
            remote_username=a.remote_username,
            is_master=u.master_account_id == a.id,
            expires_at=a.expires_at,
            state="expired" if a.expires_at < now else "expiring",
        )
        for a, u in found
        if a.cannot_renew
    ]
    return out[:limit]
