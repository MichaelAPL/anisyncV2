"""Service-agnostic list model and the interface every service client implements.

Every service stores list data slightly differently. Clients translate to/from this
normalized form so the sync engine only ever deals with one shape:

- status: MAL `plan_to_watch`, AniList `PLANNING`, Kitsu `planned` -> Status.PLANNING, etc.
- score: always 0-100 (None = unscored). MAL is 0-10 (step 10), Kitsu ratingTwenty
  is 2-20 (step 5), AniList scoreRaw is 0-100 (step 1).
- rewatching: MAL `is_rewatching`, AniList status REPEATING, Kitsu `reconsuming`.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar

import httpx
from aiolimiter import AsyncLimiter

from anisync.models import Service


class Status(StrEnum):
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLANNING = "planning"


@dataclass(frozen=True)
class ListEntry:
    media_id: int  # the anime's ID *on the service this entry belongs to*
    status: Status
    progress: int = 0
    score: int | None = None  # 0-100
    rewatching: bool = False
    started_at: date | None = None
    finished_at: date | None = None
    updated_at: datetime | None = None
    # Display data (not synced).
    title: str = ""
    total_episodes: int | None = None
    image_url: str | None = None
    # Cross-service ID hints exposed by the service (AniList idMal, Kitsu mappings).
    mal_id: int | None = None
    anilist_id: int | None = None
    kitsu_id: int | None = None
    # The service's own list-entry ID, when it has one (AniList, Kitsu).
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


class RateLimited(Exception):
    def __init__(self, service: Service, retry_after: float | None):
        super().__init__(f"{service} rate limited (retry after {retry_after}s)")
        self.retry_after = retry_after


class ReauthRequired(Exception):
    """The stored token is expired/revoked and cannot be refreshed; the user must re-link."""


def quantize_score(score: int | None, step: int) -> int | None:
    """Round a 0-100 score to the precision a service can store (0 means unscored)."""
    if not score:
        return None
    return max(step, min(100, round(score / step) * step))


class ServiceClient(abc.ABC):
    service: ClassVar[Service]
    score_step: ClassVar[int]
    # (max requests, per seconds). Enforced per process/event loop; switch to a shared
    # (Postgres/Redis) limiter if you run several worker processes.
    rate_limit: ClassVar[tuple[float, float]]
    _limiters: ClassVar[dict[tuple[Service, int], AsyncLimiter]] = {}

    @property
    def limiter(self) -> AsyncLimiter:
        key = (self.service, id(asyncio.get_running_loop()))
        if key not in ServiceClient._limiters:
            ServiceClient._limiters[key] = AsyncLimiter(*self.rate_limit)
        return ServiceClient._limiters[key]

    def __init__(
        self, access_token: str | None, remote_user_id: str | None = None, http: httpx.AsyncClient | None = None
    ):
        self.access_token = access_token
        self.remote_user_id = remote_user_id
        self.http = http or httpx.AsyncClient(timeout=30)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        async with self.limiter:
            resp = await self.http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimited(self.service, float(retry_after) if retry_after else None)
        if resp.status_code == 401:
            raise ReauthRequired(f"{self.service} rejected the access token")
        resp.raise_for_status()
        return resp

    @abc.abstractmethod
    async def whoami(self) -> RemoteUser:
        """Return the account that owns the access token."""

    @abc.abstractmethod
    async def fetch_list(self) -> list[ListEntry]:
        """Return the user's full anime list."""

    @abc.abstractmethod
    async def save_entry(self, entry: ListEntry) -> None:
        """Create or update the list entry for entry.media_id."""

    @abc.abstractmethod
    async def delete_entry(self, media_id: int, remote_entry_id: str | None = None) -> None:
        """Remove media_id from the user's list."""
