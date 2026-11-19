"""Provider-agnostic list model, the provider descriptor and the interface every provider client implements.

Every list provider stores data slightly differently. Clients translate to/from this normalized form so the
sync engine only ever deals with one shape:

- status: e.g. "plan to watch" / "planning" / "planned" in the different providers -> Status.PLANNING.
- score: always 0-100 (None = unscored). Each provider has its own precision (`Provider.score_step`).
- rewatching: a separate flag, however the provider models it.
- IDs: `ListEntry.media_id` is the provider's own ID (a string, since some providers use non-numeric IDs);
  `external_ids` maps provider IDs to the IDs the provider reports for the same title on other providers.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from aiolimiter import AsyncLimiter
from pydantic_settings import BaseSettings, SettingsConfigDict

from anisync import metrics
from anisync.http import client as http_client
from anisync.providers.registry import get_provider


class Status(StrEnum):
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLANNING = "planning"


@dataclass(frozen=True)
class ListEntry:
    media_id: str  # the title's ID *on the provider this entry belongs to*
    status: Status
    progress: int = 0  # episodes watched
    score: int | None = None  # 0-100
    rewatching: bool = False
    started_at: date | None = None
    finished_at: date | None = None
    updated_at: datetime | None = None
    # Display data (not synced).
    title: str = ""
    total_episodes: int | None = None
    image_url: str | None = None
    # {provider id: that provider's ID for this title}. Includes the entry's own provider, plus whatever the
    # provider tells us about other providers (e.g. a cross-reference field).
    external_ids: dict[str, str] = field(default_factory=dict)
    # The provider's own list-entry ID, when it has one.
    remote_entry_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        for key in ("started_at", "finished_at", "updated_at"):
            d[key] = d[key].isoformat() if d[key] else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ListEntry:
        d = dict(d)
        d["status"] = Status(d["status"])
        for key in ("started_at", "finished_at"):
            d[key] = date.fromisoformat(d[key]) if d.get(key) else None
        d["updated_at"] = datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else None
        return cls(**d)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None


@dataclass
class RemoteUser:
    id: str
    username: str
    avatar_url: str | None = None  # the provider's profile picture, if the user has one


def error_detail(resp: httpx.Response) -> str | None:
    """The reason a provider gave for refusing a request, from the usual error shapes (JSON:API `errors`, GraphQL
    `errors`, `message`, OAuth `error_description`). Short and single-line; None when the body says nothing useful."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    parts: list[str] = []
    errors = body.get("errors")
    if isinstance(errors, list):
        for e in errors[:3]:
            if isinstance(e, dict):
                parts.append(str(e.get("detail") or e.get("message") or e.get("title") or "").strip())
    for key in ("message", "error_description", "error"):
        if not parts and isinstance(body.get(key), str):
            parts.append(body[key].strip())
    text = "; ".join(p for p in parts if p)
    return " ".join(text.split())[:200] or None


class ChangeSkipped(Exception):
    """A change that can't be made, and that isn't a failure of ours: the provider hides the entry from us. Reported as
    a skipped title with the reason, not as an error."""


class RateLimited(Exception):
    def __init__(self, provider_id: str, retry_after: float | None):
        super().__init__(f"{provider_id} rate limited (retry after {retry_after}s)")
        self.provider_id = provider_id
        self.retry_after = retry_after


class ReauthRequired(Exception):
    """The stored token is expired/revoked and cannot be refreshed; the user must re-link."""


def quantize_score(score: int | None, step: int) -> int | None:
    """Round a 0-100 score to the precision a provider can store (0 means unscored)."""
    if not score:
        return None
    return max(step, min(100, round(score / step) * step))


# ---- provider descriptor -----------------------------------------------------------------------------------


class AuthKind(StrEnum):
    OAUTH_CODE = "oauth_code"  # redirect flow, authorization code
    OAUTH_PKCE_PLAIN = "oauth_pkce_plain"  # redirect flow, PKCE with the `plain` challenge method
    PASSWORD = "password"  # noqa: S105 (an enum value)  # the user types their credentials once; we exchange them for tokens
    API_KEY = "api_key"  # the user pastes a key (no provider uses this yet)

    @property
    def is_redirect(self) -> bool:
        return self in (AuthKind.OAUTH_CODE, AuthKind.OAUTH_PKCE_PLAIN)


@dataclass(frozen=True)
class ProviderColors:
    """Monogram chip colors (light and dark theme)."""

    bg: str
    fg: str
    dark_bg: str
    dark_fg: str


# (state, code_verifier or None, redirect_uri) -> URL to send the browser to
AuthorizeUrl = Callable[[str, str | None, str], str]
# (code, code_verifier or None, redirect_uri) -> tokens
ExchangeCode = Callable[[str, str | None, str], Awaitable[TokenSet]]
Refresh = Callable[[str], Awaitable[TokenSet]]
PasswordLogin = Callable[[str, str], Awaitable[TokenSet]]


@dataclass(frozen=True)
class Provider:
    id: str  # stable lowercase string stored in the database
    name: str
    monogram: str
    colors: ProviderColors
    auth_kind: AuthKind
    score_step: int
    rate_limit: tuple[float, float]  # (max requests, per seconds)
    client: type[ProviderClient]
    media_types: tuple[str, ...] = ("anime",)
    # Extracts this provider's title ID from a URL on its website (used to import offline ID datasets).
    url_pattern: re.Pattern[str] | None = None
    # Provider IDs whose title IDs this provider's client can translate (`ProviderClient.lookup_external_ids`).
    lookup_from: frozenset[str] = frozenset()
    # `fetch_list(since=...)` returns only entries changed after `since` (newest first, stopping early), which makes
    # a sync with nothing new cost one small request instead of a full list read.
    incremental_reads: bool = False
    # Domains (and their subdomains) the provider serves user-facing images from. Only https URLs on these are stored
    # and shown, so a stored URL can't point anywhere else.
    image_hosts: tuple[str, ...] = ()
    # Auth hooks; which ones are set follows from `auth_kind`. No `refresh` means tokens can't be renewed.
    authorize_url: AuthorizeUrl | None = None
    exchange_code: ExchangeCode | None = None
    refresh: Refresh | None = None
    password_login: PasswordLogin | None = None

    @property
    def can_sign_in(self) -> bool:
        """Users can sign in to AniSync with this provider (redirect flows only)."""
        return self.auth_kind.is_redirect

    def safe_image_url(self, url: str | None) -> str | None:
        """The URL if it is https and on one of this provider's image domains, otherwise None."""
        if not url or len(url) > 500:
            return None
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host == d or host.endswith(f".{d}") for d in self.image_hosts):
            return None
        return url

    def title_id_from_url(self, url: str) -> str | None:
        match = self.url_pattern.match(url) if self.url_pattern else None
        return match.group(1) if match else None


class ProviderCredentials(BaseSettings):
    """`<PROVIDER>_CLIENT_ID` / `<PROVIDER>_CLIENT_SECRET` from the environment or `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    client_id: str = ""
    client_secret: str = ""


@lru_cache
def credentials(provider_id: str) -> ProviderCredentials:
    return ProviderCredentials(_env_prefix=f"{provider_id.upper()}_")  # type: ignore[call-arg]


# ---- client interface --------------------------------------------------------------------------------------


class ProviderClient(abc.ABC):
    provider_id: ClassVar[str]
    _limiters: ClassVar[dict[tuple[str, int], AsyncLimiter]] = {}

    @property
    def provider(self) -> Provider:
        return get_provider(self.provider_id)

    @property
    def score_step(self) -> int:
        return self.provider.score_step

    @property
    def limiter(self) -> AsyncLimiter:
        # Limits are enforced per process and event loop; switch to a shared (Postgres) limiter before running
        # several worker processes.
        key = (self.provider_id, id(asyncio.get_running_loop()))
        if key not in ProviderClient._limiters:
            ProviderClient._limiters[key] = AsyncLimiter(*self.provider.rate_limit)
        return ProviderClient._limiters[key]

    def __init__(
        self, access_token: str | None, remote_user_id: str | None = None, http: httpx.AsyncClient | None = None
    ):
        self.access_token = access_token
        self.remote_user_id = remote_user_id
        self.http = http or http_client()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        started = time.monotonic()
        try:
            async with self.limiter:
                resp = await self.http.request(method, url, headers=headers, **kwargs)
        except Exception:
            metrics.record(self.provider_id, None, time.monotonic() - started)
            raise
        metrics.record(self.provider_id, resp.status_code, time.monotonic() - started)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimited(self.provider_id, float(retry_after) if retry_after else None)
        if resp.status_code == 401:
            raise ReauthRequired(f"{self.provider_id} rejected the access token")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = error_detail(resp)
            if detail is None:
                raise
            # Keep the exception type, add the provider's own explanation ("why was it refused") to the message.
            first_line = str(exc).splitlines()[0]
            raise httpx.HTTPStatusError(f"{first_line}: {detail}", request=exc.request, response=exc.response) from exc
        return resp

    @abc.abstractmethod
    async def whoami(self) -> RemoteUser:
        """Return the account that owns the access token."""

    @abc.abstractmethod
    async def fetch_list(self, since: datetime | None = None) -> list[ListEntry]:
        """Return the user's anime list. With `since` (only for providers with `incremental_reads`): just the
        entries whose provider-side update time is strictly after it. Strictly, because bulk imports give thousands of
        entries one identical timestamp, and re-reading them all on every sync would defeat the point."""

    @abc.abstractmethod
    async def save_entry(self, entry: ListEntry) -> None:
        """Create or update the list entry for entry.media_id."""

    @abc.abstractmethod
    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        """Remove media_id from the user's list."""

    async def lookup_external_ids(self, provider_id: str, ids: list[str]) -> list[dict[str, str]]:
        """Optional. Translate title IDs of `provider_id` (one of `Provider.lookup_from`) using this provider.

        Returns one `{provider id: title id}` mapping per title found, always including the ID that was asked about
        plus every other provider's ID this provider knows for it. Works without a user token.
        """
        raise NotImplementedError(f"{self.provider_id} cannot look up IDs from {provider_id}")
