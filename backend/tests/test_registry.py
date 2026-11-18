import re
from pathlib import Path

import pytest

from anisync.models import LinkedAccount
from anisync.providers import UnknownProvider, all_providers, get_provider, is_registered
from anisync.providers.base import AuthKind, credentials
from anisync.providers.registry import register

SRC = Path(__file__).resolve().parents[1] / "src" / "anisync"


def test_builtin_providers():
    assert {p.id for p in all_providers()} >= {"mal", "anilist", "kitsu"}
    assert get_provider("mal").auth_kind == AuthKind.OAUTH_PKCE_PLAIN
    assert get_provider("anilist").auth_kind == AuthKind.OAUTH_CODE
    assert get_provider("kitsu").auth_kind == AuthKind.PASSWORD


@pytest.mark.parametrize("provider", all_providers(), ids=lambda p: p.id)
def test_descriptors_are_complete(provider):
    assert provider.client.provider_id == provider.id
    assert provider.score_step >= 1 and provider.rate_limit[0] > 0
    assert provider.name and provider.monogram and provider.colors.bg.startswith("#")
    assert provider.url_pattern is not None
    if provider.auth_kind.is_redirect:
        assert provider.authorize_url and provider.exchange_code and provider.can_sign_in
    if provider.auth_kind == AuthKind.PASSWORD:
        assert provider.password_login and not provider.can_sign_in
    assert provider.lookup_from <= {p.id for p in all_providers()}


def test_refresh_hooks():
    assert get_provider("mal").refresh and get_provider("kitsu").refresh
    assert get_provider("anilist").refresh is None  # tokens last a year and can't be renewed


def test_title_id_from_url():
    assert get_provider("mal").title_id_from_url("https://myanimelist.net/anime/1/Cowboy_Bebop") == "1"
    assert get_provider("anilist").title_id_from_url("https://anilist.co/anime/21") == "21"
    assert get_provider("kitsu").title_id_from_url("https://kitsu.app/anime/47450") == "47450"
    assert get_provider("mal").title_id_from_url("https://anilist.co/anime/21") is None
    assert get_provider("mal").title_id_from_url("https://myanimelist.net/manga/2") is None


def test_unknown_provider():
    assert not is_registered("nope")
    with pytest.raises(UnknownProvider):
        get_provider("nope")


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError):
        register(get_provider("mal"))


def test_credentials_come_from_the_environment():
    assert credentials("mal").client_id == "mal-test-id"
    assert credentials("anilist").client_secret == "anilist-test-secret"
    assert credentials("kitsu").client_id == ""


def test_account_provider_is_validated_against_the_registry(acme):
    LinkedAccount(provider="mal")
    LinkedAccount(provider="acme")  # a new provider needs no model change
    with pytest.raises(UnknownProvider):
        LinkedAccount(provider="nope")


def test_nothing_outside_the_providers_package_names_a_provider():
    """Adding or removing a provider must only touch `providers/`."""
    names = re.compile(r"\b(mal|anilist|kitsu|myanimelist)\b", re.IGNORECASE)
    offenders = {
        f"{path.relative_to(SRC)}:{n}: {line.strip()}"
        for path in SRC.rglob("*.py")
        if "providers" not in path.relative_to(SRC).parts[:1]
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if names.search(line)
    }
    assert not offenders, "\n".join(sorted(offenders))
