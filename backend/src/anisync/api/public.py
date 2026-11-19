"""Public, read-only facts the UI needs before anyone is signed in: the provider list and the site notice."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from anisync import switches
from anisync.api.deps import Session
from anisync.providers import all_providers

router = APIRouter(prefix="/api", tags=["public"])


class ColorsOut(BaseModel):
    bg: str
    fg: str
    dark_bg: str
    dark_fg: str


class ProviderOut(BaseModel):
    id: str
    name: str
    monogram: str
    colors: ColorsOut
    auth_kind: str  # oauth_code | oauth_pkce_plain | password | api_key: tells the UI which sign-in form to show
    can_sign_in: bool
    paused: bool
    paused_reason: str | None


class SiteOut(BaseModel):
    maintenance: bool
    maintenance_message: str | None
    banner_text: str | None  # plain text: render it as text, never as HTML
    banner_level: str
    updated_at: datetime


@router.get("/providers")
async def providers(session: Session) -> list[ProviderOut]:
    paused = await switches.paused_providers(session)
    return [
        ProviderOut(
            id=p.id,
            name=p.name,
            monogram=p.monogram,
            colors=ColorsOut(bg=p.colors.bg, fg=p.colors.fg, dark_bg=p.colors.dark_bg, dark_fg=p.colors.dark_fg),
            auth_kind=p.auth_kind.value,
            can_sign_in=p.can_sign_in,
            paused=p.id in paused,
            paused_reason=paused.get(p.id),
        )
        for p in all_providers()
    ]


@router.get("/site")
async def site(session: Session) -> SiteOut:
    row = await switches.site(session)
    return SiteOut(
        maintenance=row.maintenance,
        maintenance_message=row.maintenance_message,
        banner_text=row.banner_text,
        banner_level=row.banner_level,
        updated_at=row.updated_at,
    )


async def maintenance_guard(request: Request, session: Session) -> None:
    """While maintenance mode is on, anything that changes data is refused with 503 (admins and sign-out excepted)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    path = request.url.path
    if path.startswith("/api/admin") or path == "/auth/logout":
        return
    row = await switches.site(session)
    if row.maintenance:
        raise HTTPException(
            503,
            row.maintenance_message or "AniSync is down for maintenance",
            headers={"Retry-After": "300"},
        )
