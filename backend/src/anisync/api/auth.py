"""Sign-in and account linking, driven by each provider's `auth_kind`.

There are no AniSync passwords: you sign in with a provider that supports a redirect (OAuth) flow, and the first
account you link creates your AniSync user (and becomes your master list). Providers that only take credentials
(`AuthKind.PASSWORD`) can be linked once you are signed in.
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
from anisync.models import LinkedAccount, User
from anisync.providers import UnknownProvider, get_provider
from anisync.providers.base import AuthKind, Provider, TokenSet

router = APIRouter(prefix="/auth", tags=["auth"])


def _provider(provider_id: str) -> Provider:
    try:
        return get_provider(provider_id)
    except UnknownProvider:
        raise HTTPException(404, "Unknown provider") from None


def _redirect_uri(provider: Provider) -> str:
    return f"{get_settings().public_url}/auth/{provider.id}/callback"


def _frontend(path: str, **params: str) -> RedirectResponse:
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{get_settings().public_url}{path}{query}")


@router.get("/{provider_id}/login")
async def login(provider_id: str, request: Request) -> RedirectResponse:
    provider = _provider(provider_id)
    if not provider.can_sign_in or provider.authorize_url is None:
        raise HTTPException(400, f"{provider.name} does not support OAuth sign-in")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)[:128] if provider.auth_kind == AuthKind.OAUTH_PKCE_PLAIN else None
    request.session["oauth"] = {"provider": provider.id, "state": state, "verifier": verifier}
    return RedirectResponse(provider.authorize_url(state, verifier, _redirect_uri(provider)))


@router.get("/{provider_id}/callback")
async def callback(
    provider_id: str,
    request: Request,
    session: Session,
    user: OptionalUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    provider = _provider(provider_id)
    oauth = request.session.pop("oauth", None)
    if error or not code:
        return _frontend("/", error=error or "missing_code")
    if not oauth or oauth["provider"] != provider.id or not secrets.compare_digest(oauth["state"], state or ""):
        return _frontend("/", error="invalid_state")
    if provider.exchange_code is None:
        raise HTTPException(400, f"{provider.name} does not support OAuth sign-in")

    try:
        tokens = await provider.exchange_code(code, oauth["verifier"], _redirect_uri(provider))
    except httpx.HTTPError:
        return _frontend("/", error="token_exchange_failed")

    try:
        user = await _link_account(session, user, provider, tokens)
    except PermissionError:
        return _frontend("/dashboard", error="account_already_linked")
    request.session["user_id"] = user.id
    return _frontend("/dashboard")


class PasswordLogin(BaseModel):
    username: str  # some providers accept the account email here
    password: str


@router.post("/{provider_id}/password")
async def link_with_password(provider_id: str, body: PasswordLogin, session: Session, user: CurrentUser) -> dict:
    provider = _provider(provider_id)
    if provider.auth_kind != AuthKind.PASSWORD or provider.password_login is None:
        raise HTTPException(400, f"{provider.name} is not linked with a username and password")
    try:
        tokens = await provider.password_login(body.username, body.password)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, f"{provider.name} rejected those credentials") from exc
    try:
        await _link_account(session, user, provider, tokens)
    except PermissionError as exc:
        raise HTTPException(409, f"That {provider.name} account is linked to another AniSync user") from exc
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


async def _link_account(session: Session, user: User | None, provider: Provider, tokens: TokenSet) -> User:
    remote = await provider.client(tokens.access_token).whoami()
    account = await session.scalar(
        select(LinkedAccount).where(LinkedAccount.provider == provider.id, LinkedAccount.remote_user_id == remote.id)
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
        account = LinkedAccount(user_id=user.id, provider=provider.id, remote_user_id=remote.id)
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
