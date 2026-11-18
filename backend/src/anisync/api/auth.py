"""Sign-in and account linking.

There are no AniSync passwords: you sign in with MAL or AniList, and the first
account you link creates your AniSync user (and becomes your master list).
Kitsu has no OAuth redirect flow, so it can only be linked once signed in.
"""

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from anisync.api.deps import CurrentUser, OptionalUser, Session
from anisync.config import get_settings
from anisync.models import LinkedAccount, Service, User
from anisync.services import CLIENTS, anilist, kitsu, mal
from anisync.services.base import TokenSet

router = APIRouter(prefix="/auth", tags=["auth"])
OAUTH_SERVICES = {Service.MAL, Service.ANILIST}


def _redirect_uri(service: Service) -> str:
    return f"{get_settings().public_url}/auth/{service.value}/callback"


def _frontend(path: str, **params: str) -> RedirectResponse:
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{get_settings().public_url}{path}{query}")


@router.get("/{service}/login")
async def login(service: Service, request: Request) -> RedirectResponse:
    if service not in OAUTH_SERVICES:
        raise HTTPException(400, f"{service} does not support OAuth sign-in")
    state = secrets.token_urlsafe(24)
    oauth = {"service": service.value, "state": state}
    if service == Service.MAL:
        oauth["verifier"] = secrets.token_urlsafe(64)[:128]
        url = mal.authorize_url(state, oauth["verifier"], _redirect_uri(service))
    else:
        url = anilist.authorize_url(state, _redirect_uri(service))
    request.session["oauth"] = oauth
    return RedirectResponse(url)


@router.get("/{service}/callback")
async def callback(
    service: Service,
    request: Request,
    session: Session,
    user: OptionalUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    oauth = request.session.pop("oauth", None)
    if error or not code:
        return _frontend("/", error=error or "missing_code")
    if not oauth or oauth["service"] != service.value or not secrets.compare_digest(oauth["state"], state or ""):
        return _frontend("/", error="invalid_state")

    try:
        if service == Service.MAL:
            tokens = await mal.exchange_code(code, oauth["verifier"], _redirect_uri(service))
        else:
            tokens = await anilist.exchange_code(code, _redirect_uri(service))
    except httpx.HTTPError:
        return _frontend("/", error="token_exchange_failed")

    try:
        user = await _link_account(session, user, service, tokens)
    except PermissionError:
        return _frontend("/dashboard", error="account_already_linked")
    request.session["user_id"] = user.id
    return _frontend("/dashboard")


class KitsuLogin(BaseModel):
    username: str  # Kitsu accepts the account email here
    password: str


@router.post("/kitsu")
async def link_kitsu(body: KitsuLogin, session: Session, user: CurrentUser) -> dict:
    try:
        tokens = await kitsu.password_login(body.username, body.password)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, "Kitsu rejected those credentials") from exc
    try:
        await _link_account(session, user, Service.KITSU, tokens)
    except PermissionError as exc:
        raise HTTPException(409, "That Kitsu account is linked to another AniSync user") from exc
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


async def _link_account(session: Session, user: User | None, service: Service, tokens: TokenSet) -> User:
    remote = await CLIENTS[service](tokens.access_token).whoami()
    account = await session.scalar(
        select(LinkedAccount).where(LinkedAccount.service == service, LinkedAccount.remote_user_id == remote.id)
    )

    if account and user and account.user_id != user.id:
        raise PermissionError
    if user is None:
        if account:
            user = await session.get(User, account.user_id)
        else:
            user = User(display_name=remote.username)
            session.add(user)
            await session.flush()
    if account is None:
        account = LinkedAccount(user_id=user.id, service=service, remote_user_id=remote.id)
        session.add(account)

    account.remote_username = remote.username
    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.expires_at = tokens.expires_at
    await session.flush()
    if user.master_account_id is None:
        user.master_account_id = account.id
    await session.commit()
    await session.refresh(user, ["accounts"])
    return user
