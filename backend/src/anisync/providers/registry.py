"""Registry of list providers.

A provider registers itself when its module is imported (see `providers/__init__.py`). Everything outside the
`providers` package looks providers up here by their string ID and never names a specific provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anisync.providers.base import Provider


class UnknownProvider(KeyError):
    """No provider is registered under that ID."""


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    if provider.id in _REGISTRY:
        raise ValueError(f"provider {provider.id!r} is already registered")
    _REGISTRY[provider.id] = provider
    return provider


def unregister(provider_id: str) -> None:
    """Remove a provider (used by tests that register a throwaway one)."""
    _REGISTRY.pop(provider_id, None)


def get_provider(provider_id: str) -> Provider:
    try:
        return _REGISTRY[provider_id]
    except KeyError:
        raise UnknownProvider(provider_id) from None


def is_registered(provider_id: str) -> bool:
    return provider_id in _REGISTRY


def all_providers() -> list[Provider]:
    return list(_REGISTRY.values())
