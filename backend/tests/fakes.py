"""A made-up provider ("acme") used to prove that new providers need no changes outside `providers/`."""

import re
from typing import ClassVar

from anisync.providers.base import AuthKind, ListEntry, Provider, ProviderClient, ProviderColors, RemoteUser
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
