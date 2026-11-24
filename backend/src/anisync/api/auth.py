"""Sign-in and account linking, driven by each provider's `auth_kind`.

There are no AniSyncHub passwords: you sign in with a provider that supports a redirect (OAuth) flow, and the first
account you link creates your AniSyncHub user (and becomes your master list). Providers that only take credentials
(`AuthKind.PASSWORD`) can be linked once you are signed in. While the sign-up allowlist is on (the default in
production), only listed provider accounts may create a user; see `SIGNUP_ALLOWLIST` in config.py.
"""

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select

from anisync import moderation, ratelimit, sessions
from anisync.api.deps import CurrentUser, OptionalUser, Session
from anisync.api.schemas import OkOut
from anisync.config import get_settings
from anisync.models import LinkedAccount, User
from anisync.providers import UnknownProvider, get_provider
from anisync.providers.base import AuthKind, Provider, TokenSet

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

# The OAuth state and PKCE verifier live in a short-lived signed cookie between the redirect and the callback.
OAUTH_COOKIE = "anisync_oauth"
OAUTH_MAX_AGE = 600

SIGN_IN_LIMIT = ratelimit.per_ip("oauth", limit=30, window=timedelta(minutes=15))
PASSWORD_IP_LIMIT = ratelimit.per_ip("password-link", limit=5, window=timedelta(minutes=15))
PASSWORD_USER_LIMIT = ratelimit.per_user("password-link", limit=5, window=timedelta(minutes=15))


class SignupClosed(Exception):  # noqa: N818 (a refusal, not an error)
    """The account isn't linked to anyone and isn't on the sign-up allowlist, so it may not create a user."""


def _oauth_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="anisync-oauth")


def _secure_cookies() -> bool:
    return get_settings().public_url.startswith("https://")


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


@router.get("/{provider_id}/login", response_class=RedirectResponse, status_code=307, dependencies=[SIGN_IN_LIMIT])
async def login(provider_id: str) -> RedirectResponse:
    provider = _provider(provider_id)
    if not provider.can_sign_in or provider.authorize_url is None:
        raise HTTPException(400, f"{provider.name} does not support OAuth sign-in")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)[:128] if provider.auth_kind == AuthKind.OAUTH_PKCE_PLAIN else None
    response = RedirectResponse(provider.authorize_url(state, verifier, _redirect_uri(provider)))
    response.set_cookie(
        OAUTH_COOKIE,
        _oauth_serializer().dumps({"provider": provider.id, "state": state, "verifier": verifier}),
        max_age=OAUTH_MAX_AGE,
        httponly=True,
        samesite="lax",  # must accompany the top-level redirect back from the provider
        secure=_secure_cookies(),
        path="/auth",
    )
    return response


@router.get("/{provider_id}/callback", response_class=RedirectResponse, status_code=307, dependencies=[SIGN_IN_LIMIT])
async def callback(
    provider_id: str,
    request: Request,
    session: Session,
    user: OptionalUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    provider = _provider(provider_id)
    response = _frontend("/")
    response.delete_cookie(OAUTH_COOKIE, path="/auth")  # single use, whatever happens next
    try:
        oauth = _oauth_serializer().loads(request.cookies.get(OAUTH_COOKIE, ""), max_age=OAUTH_MAX_AGE)
    except BadSignature:
        oauth = None
    if error or not code:
        return _failed(response, error or "missing_code")
    if not oauth or oauth["provider"] != provider.id or not secrets.compare_digest(oauth["state"], state or ""):
        return _failed(response, "invalid_state")
    if provider.exchange_code is None:
        raise HTTPException(400, f"{provider.name} does not support OAuth sign-in")

    try:
        tokens = await provider.exchange_code(code, oauth["verifier"], _redirect_uri(provider))
    except httpx.HTTPError:
        return _failed(response, "token_exchange_failed")

    try:
        user = await _link_account(session, user, provider, tokens)
    except PermissionError:
        return _failed(response, "account_already_linked", path="/dashboard")
    except SignupClosed:
        response.headers["location"] = f"{get_settings().public_url}/not-open"
        return response
    if user.is_banned:
        return _failed(response, "account_banned")
    await moderation.bootstrap_admins(session)
    token = await sessions.start(
        session, user.id, request.headers.get("user-agent"), replacing=request.cookies.get(sessions.COOKIE)
    )
    response.headers["location"] = f"{get_settings().public_url}/dashboard"
    sessions.set_cookie(response, token)
    return response


def _failed(response: RedirectResponse, error: str, path: str = "/") -> RedirectResponse:
    response.headers["location"] = f"{get_settings().public_url}{path}?{urlencode({'error': error})}"
    return response


class PasswordLogin(BaseModel):
    username: str  # some providers accept the account email here
    password: str


@router.post("/{provider_id}/password", dependencies=[PASSWORD_IP_LIMIT, PASSWORD_USER_LIMIT])
async def link_with_password(provider_id: str, body: PasswordLogin, session: Session, user: CurrentUser) -> OkOut:
    provider = _provider(provider_id)
    if provider.auth_kind != AuthKind.PASSWORD or provider.password_login is None:
        raise HTTPException(400, f"{provider.name} is not linked with a username and password")
    try:
        tokens = await provider.password_login(body.username, body.password)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 401):
            raise HTTPException(400, f"{provider.name} rejected those credentials") from exc
        raise HTTPException(
            502, f"{provider.name} didn't accept the login request ({exc.response.status_code}). Try again later"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Couldn't reach {provider.name}. Try again later") from exc
    try:
        await _link_account(session, user, provider, tokens)
    except PermissionError as exc:
        raise HTTPException(409, f"That {provider.name} account is linked to another AniSyncHub user") from exc
    except SignupClosed as exc:
        raise HTTPException(403, "AniSyncHub isn't open to new accounts yet") from exc
    return OkOut()


@router.post("/logout")
async def logout(request: Request, response: Response, session: Session) -> OkOut:
    if token := request.cookies.get(sessions.COOKIE):
        await sessions.end(session, token)
    sessions.clear_cookie(response)
    return OkOut()


@router.post("/logout-all")
async def logout_everywhere(response: Response, session: Session, user: CurrentUser) -> OkOut:
    """Sign out every device, this one included."""
    await sessions.end_all(session, user.id)
    sessions.clear_cookie(response)
    return OkOut()


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
            settings = get_settings()
            if settings.signup_allowlist_enabled and (provider.id, remote.id) not in settings.signup_allowlist_ids:
                log.info("Sign-up refused for %s:%s (not on SIGNUP_ALLOWLIST)", provider.id, remote.id)
                raise SignupClosed
            user = User(display_name=remote.username[:40])
            if avatar := provider.safe_image_url(remote.avatar_url):
                user.avatar_url, user.avatar_provider = avatar, provider.id  # a starting point; never changes by itself
            session.add(user)
            await session.flush()
    if account is None:
        account = LinkedAccount(user_id=user.id, provider=provider.id, remote_user_id=remote.id)
        session.add(account)

    account.remote_username = remote.username
    account.avatar_url = provider.safe_image_url(remote.avatar_url)
    account.hides_adult = remote.hides_adult
    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.expires_at = tokens.expires_at
    await session.flush()
    if user.master_account_id is None:
        user.master_account_id = account.id
    await session.commit()
    await session.refresh(user, ["accounts"])
    return user
