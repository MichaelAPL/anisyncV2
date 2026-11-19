"""Admin: site banner, maintenance mode (`/api/admin/site`), provider kill switch (`/api/admin/providers`)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from anisync import moderation, permissions, switches
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.models import LinkedAccount, ProviderSetting, ProviderStat, User
from anisync.providers import UnknownProvider, all_providers, get_provider

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_any_permission)])


def _clean(text: str | None) -> str | None:
    text = " ".join((text or "").split())
    return text or None


# ---- banner and maintenance ----------------------------------------------------------------------------------


class AdminSiteOut(BaseModel):
    maintenance: bool
    maintenance_message: str | None
    banner_text: str | None
    banner_level: str
    scheduled_syncs_paused: bool
    updated_at: datetime
    updated_by_name: str | None


class SiteIn(BaseModel):
    maintenance: bool
    maintenance_message: str | None = Field(default=None, max_length=500)
    banner_text: str | None = Field(default=None, max_length=500)
    banner_level: Literal["info", "warning", "error"] = "info"

    @field_validator("maintenance_message", "banner_text")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value and any(ch in value for ch in "<>"):
            raise ValueError("Plain text only: no HTML")
        return _clean(value)


async def _site_out(session: Session) -> AdminSiteOut:
    row = await switches.site(session)
    name = await session.scalar(select(User.display_name).where(User.id == row.updated_by)) if row.updated_by else None
    return AdminSiteOut(
        maintenance=row.maintenance,
        maintenance_message=row.maintenance_message,
        banner_text=row.banner_text,
        banner_level=row.banner_level,
        scheduled_syncs_paused=row.scheduled_syncs_paused,
        updated_at=row.updated_at,
        updated_by_name=name,
    )


@router.get("/site")
async def get_site(
    session: Session, admin: Annotated[User, Depends(require_permission(permissions.SITE_MANAGE))]
) -> AdminSiteOut:
    return await _site_out(session)


@router.put("/site")
async def update_site(
    body: SiteIn, session: Session, admin: Annotated[User, Depends(require_permission(permissions.SITE_MANAGE))]
) -> AdminSiteOut:
    row = await switches.site(session)
    new = {
        "maintenance": body.maintenance,
        "maintenance_message": body.maintenance_message,
        "banner_text": body.banner_text,
        "banner_level": body.banner_level,
    }
    changed = sorted(key for key, value in new.items() if getattr(row, key) != value)
    for key, value in new.items():
        setattr(row, key, value)
    row.updated_at, row.updated_by = datetime.now(UTC), admin.id
    if changed:
        await moderation.record(session, admin, "site.update", changed=changed, maintenance=body.maintenance)
    await session.commit()
    return await _site_out(session)


class PausedIn(BaseModel):
    paused: bool


@router.put("/syncs/scheduled")
async def pause_scheduled_syncs(
    body: PausedIn, session: Session, admin: Annotated[User, Depends(require_permission(permissions.SYNCS_CONTROL))]
) -> AdminSiteOut:
    """Pause or resume the hourly scheduled syncs for everyone (edits made in AniSync still go out)."""
    row = await switches.site(session)
    if row.scheduled_syncs_paused != body.paused:
        row.scheduled_syncs_paused = body.paused
        row.updated_at, row.updated_by = datetime.now(UTC), admin.id
        await moderation.record(session, admin, "syncs.pause" if body.paused else "syncs.resume")
        await session.commit()
    return await _site_out(session)


# ---- providers -----------------------------------------------------------------------------------------------


class RequestsOut(BaseModel):
    requests: int
    rate_limited: int
    errors: int
    avg_ms: int


class ProviderHealthOut(BaseModel):
    id: str
    name: str
    paused: bool
    paused_reason: str | None
    changed_at: datetime | None
    accounts: int
    today: RequestsOut
    last_7d: RequestsOut


class PauseIn(BaseModel):
    paused: bool
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value and any(ch in value for ch in "<>"):
            raise ValueError("Plain text only: no HTML")
        return _clean(value)


def _requests(rows: list[ProviderStat]) -> RequestsOut:
    total = sum(r.requests for r in rows)
    return RequestsOut(
        requests=total,
        rate_limited=sum(r.rate_limited for r in rows),
        errors=sum(r.errors for r in rows),
        avg_ms=round(sum(r.total_ms for r in rows) / total) if total else 0,
    )


async def _health(session: Session, only: str | None = None) -> list[ProviderHealthOut]:
    today = datetime.now(UTC).date()
    stats = list(await session.scalars(select(ProviderStat).where(ProviderStat.day >= today - timedelta(days=6))))
    settings = {s.provider: s for s in await session.scalars(select(ProviderSetting))}
    accounts = dict(
        (await session.execute(select(LinkedAccount.provider, func.count()).group_by(LinkedAccount.provider))).all()
    )
    out = []
    for p in all_providers():
        if only and p.id != only:
            continue
        mine = [s for s in stats if s.provider == p.id]
        setting = settings.get(p.id)
        out.append(
            ProviderHealthOut(
                id=p.id,
                name=p.name,
                paused=bool(setting and setting.paused),
                paused_reason=setting.paused_reason if setting and setting.paused else None,
                changed_at=setting.changed_at if setting else None,
                accounts=accounts.get(p.id, 0),
                today=_requests([s for s in mine if s.day == today]),
                last_7d=_requests(mine),
            )
        )
    return out


@router.get("/providers")
async def provider_health(
    session: Session, admin: Annotated[User, Depends(require_permission(permissions.STATS_READ))]
) -> list[ProviderHealthOut]:
    return await _health(session)


@router.put("/providers/{provider_id}")
async def set_provider_paused(
    provider_id: str,
    body: PauseIn,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.PROVIDERS_MANAGE))],
) -> ProviderHealthOut:
    """Pause a provider (syncs and edit pushes skip it, users are told) or resume it."""
    try:
        get_provider(provider_id)
    except UnknownProvider:
        raise HTTPException(404, "Unknown provider") from None
    if body.paused and not body.reason:
        raise HTTPException(422, "Say why: users will see the reason")
    setting = await session.get(ProviderSetting, provider_id)
    if setting is None:
        setting = ProviderSetting(provider=provider_id, paused=False)
        session.add(setting)
    if setting.paused != body.paused:
        await moderation.record(
            session,
            admin,
            "provider.pause" if body.paused else "provider.resume",
            note=body.reason,
            provider=provider_id,
        )
    setting.paused = body.paused
    setting.paused_reason = body.reason if body.paused else None
    setting.changed_at, setting.changed_by = datetime.now(UTC), admin.id
    await session.commit()
    [health] = await _health(session, only=provider_id)
    return health
