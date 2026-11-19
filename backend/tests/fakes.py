"""A made-up provider ("acme") used to prove that new providers need no changes outside `providers/`."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from anisync.providers.base import (
    AuthKind,
    ListEntry,
    Provider,
    ProviderClient,
    ProviderColors,
    ReauthRequired,
    RemoteUser,
)
from anisync.providers.registry import register, unregister


class AcmeClient(ProviderClient):
    provider_id = "acme"
    # What acme "knows": {(provider, id): {provider: id, ...}} for the titles it can translate.
    TABLE: ClassVar[dict[tuple[str, str], dict[str, str]]] = {}
    calls: ClassVar[list[tuple[str, list[str]]]] = []

    async def whoami(self) -> RemoteUser:
        return RemoteUser(id="1", username="acme_user")

    async def fetch_list(self) -> list[ListEntry]:
        return []

    async def save_entry(self, entry: ListEntry) -> None:
        pass

    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        pass

    async def lookup_external_ids(self, provider_id: str, ids: list[str]) -> list[dict[str, str]]:
        AcmeClient.calls.append((provider_id, list(ids)))
        return [dict(m) for i in ids if (m := AcmeClient.TABLE.get((provider_id, i)))]


def register_acme() -> Provider:
    AcmeClient.TABLE = {}
    AcmeClient.calls = []
    return register(
        Provider(
            id="acme",
            name="Acme List",
            monogram="AC",
            colors=ProviderColors(bg="#EEEEEE", fg="#111111", dark_bg="#222222", dark_fg="#EEEEEE"),
            auth_kind=AuthKind.API_KEY,
            score_step=1,
            rate_limit=(10, 1),
            client=AcmeClient,
            url_pattern=re.compile(r"^https?://acme\.example/anime/(\d+)"),
            lookup_from=frozenset({"mal", "acme"}),
        )
    )


def unregister_acme() -> None:
    unregister("acme")


# ---- in-memory providers for testing sync orchestration ---------------------------------------------------


class FakeClient(ProviderClient):
    """A provider whose 'remote' list lives in memory. Subclassed per provider id by `register_fake`."""

    REMOTE: ClassVar[dict[tuple[str, str], dict[str, ListEntry]]] = {}
    AVATARS: ClassVar[dict[str, str | None]] = {}  # provider id -> the picture its whoami reports
    FAIL_ON: ClassVar[set[str]] = set()  # media ids whose save raises
    REJECT_TOKENS: ClassVar[bool] = False  # simulate a revoked token
    REQUESTS: ClassVar[list[tuple[str, str]]] = []  # (provider id, "full" | "since" | "save" | "delete")
    _clock: ClassVar[int] = 0

    @classmethod
    def stamp(cls) -> datetime:
        """A strictly increasing 'server time', one second per call."""
        cls._clock += 1
        return datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=cls._clock)

    def _store(self) -> dict[str, ListEntry]:
        return FakeClient.REMOTE.setdefault((self.provider_id, self.remote_user_id or "1"), {})

    async def whoami(self) -> RemoteUser:
        if FakeClient.REJECT_TOKENS:
            raise ReauthRequired("token rejected")
        return RemoteUser(
            id=self.remote_user_id or "1",
            username=f"{self.provider_id}_user",
            avatar_url=FakeClient.AVATARS.get(self.provider_id),
        )

    async def fetch_list(self, since: datetime | None = None) -> list[ListEntry]:
        FakeClient.REQUESTS.append((self.provider_id, "full" if since is None else "since"))
        if FakeClient.REJECT_TOKENS:
            raise ReauthRequired("revoked")
        entries = list(self._store().values())
        if since is not None:
            entries = [e for e in entries if e.updated_at and e.updated_at > since]
        return entries

    async def save_entry(self, entry: ListEntry) -> None:
        if entry.media_id in FakeClient.FAIL_ON:
            raise RuntimeError(f"cannot save {entry.media_id}")
        FakeClient.REQUESTS.append((self.provider_id, "save"))
        self._store()[entry.media_id] = replace(
            entry, remote_entry_id=f"entry-{entry.media_id}", updated_at=FakeClient.stamp()
        )

    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        FakeClient.REQUESTS.append((self.provider_id, "delete"))
        self._store().pop(media_id, None)


def register_fake(provider_id: str, score_step: int = 1, refresh=None, incremental: bool = False) -> Provider:
    client = type(f"Fake{provider_id}Client", (FakeClient,), {"provider_id": provider_id})
    return register(
        Provider(
            id=provider_id,
            name=provider_id.upper(),
            monogram=provider_id[:2].upper(),
            colors=ProviderColors(bg="#EEEEEE", fg="#111111", dark_bg="#222222", dark_fg="#EEEEEE"),
            auth_kind=AuthKind.OAUTH_CODE,
            score_step=score_step,
            rate_limit=(1000, 1),
            client=client,
            incremental_reads=incremental,
            image_hosts=("img.example.test",),
            refresh=refresh,
        )
    )


def reset_fakes() -> None:
    FakeClient.REMOTE = {}
    FakeClient.FAIL_ON = set()
    FakeClient.AVATARS = {}
    FakeClient.REJECT_TOKENS = False
    FakeClient.REQUESTS = []


def unregister_fakes() -> None:
    unregister("fa")
    unregister("fb")
