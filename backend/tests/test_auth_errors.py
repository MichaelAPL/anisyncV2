"""The failure paths of sign-in: none of them may end with a session or change another user's account."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from itsdangerous import URLSafeTimedSerializer
from pydantic import ValidationError
from sqlalchemy import func, select

from anisync import sessions
from anisync.config import Settings, get_settings
from anisync.main import app
from anisync.models import LinkedAccount, User, UserSession
from anisync.providers import anilist, kitsu, mal
from tests.test_auth_and_api import sign_in_with_mal


def location(response) -> tuple[str, dict]:
    parts = urlparse(response.headers["location"])
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


def has_session_cookie(response) -> bool:
    return any(
        c.startswith(f"{sessions.COOKIE}=") and "Max-Age=0" not in c for c in response.headers.get_list("set-cookie")
    )


async def start_login(client, provider="mal") -> str:
    login = await client.get(f"/auth/{provider}/login")
    return parse_qs(urlparse(login.headers["location"]).query)["state"][0]


async def test_a_provider_error_ends_without_a_session(client, session):
    await start_login(client)
    response = await client.get("/auth/mal/callback?error=access_denied")
    assert location(response) == ("/", {"error": "access_denied"}) and not has_session_cookie(response)
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_a_missing_code_ends_without_a_session(client):
    await start_login(client)
    response = await client.get("/auth/mal/callback?state=whatever")
    assert location(response)[1] == {"error": "missing_code"} and not has_session_cookie(response)


async def test_a_failed_token_exchange_ends_without_a_session_or_an_account(client, session):
    state = await start_login(client)
    with respx.mock:
        respx.post(mal.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        response = await client.get(f"/auth/mal/callback?code=used-code&state={state}")
    assert location(response)[1] == {"error": "token_exchange_failed"} and not has_session_cookie(response)
    assert await session.scalar(select(func.count()).select_from(LinkedAccount)) == 0


async def test_a_cookie_signed_with_another_secret_is_rejected(client):
    forged = URLSafeTimedSerializer("attacker-secret", salt="anisync-oauth").dumps(
        {"provider": "mal", "state": "chosen-by-attacker", "verifier": "v"}
    )
    client.cookies.set("anisync_oauth", forged, path="/auth")
    response = await client.get("/auth/mal/callback?code=abc&state=chosen-by-attacker")
    assert location(response)[1] == {"error": "invalid_state"} and not has_session_cookie(response)


async def test_a_state_started_for_one_provider_cannot_complete_another(client):
    state = await start_login(client, "anilist")
    response = await client.get(f"/auth/mal/callback?code=abc&state={state}")
    assert location(response)[1] == {"error": "invalid_state"} and not has_session_cookie(response)


async def test_the_oauth_cookie_is_scoped_and_expires(client):
    login = await client.get("/auth/mal/login")
    cookie = login.headers["set-cookie"]
    assert "anisync_oauth=" in cookie and "HttpOnly" in cookie and "Path=/auth" in cookie
    assert "Max-Age=600" in cookie and "SameSite=lax" in cookie


async def test_callbacks_without_a_started_login_are_rejected(client):
    response = await client.get("/auth/mal/callback?code=abc&state=abc")
    assert location(response)[1] == {"error": "invalid_state"} and not has_session_cookie(response)


async def test_linking_an_account_that_belongs_to_someone_else_changes_nothing(client, session):
    await sign_in_with_mal(client, remote_id=77, name="alice")  # alice owns MAL account 77
    owner = (await session.scalars(select(LinkedAccount.user_id))).one()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=client.headers,
    ) as bob:
        # Bob signs in with AniList...
        state = await start_login(bob, "anilist")
        with respx.mock:
            respx.post(anilist.TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"access_token": "al-tok", "expires_in": 3600})
            )
            respx.post(anilist.GRAPHQL_URL).mock(
                return_value=httpx.Response(200, json={"data": {"Viewer": {"id": 5, "name": "bob"}}})
            )
            await bob.get(f"/auth/anilist/callback?code=abc&state={state}")
        assert (await bob.get("/api/me")).json()["display_name"] == "bob"

        # ... then tries to link Alice's MAL account to himself.
        state = await start_login(bob, "mal")
        with respx.mock:
            respx.post(mal.TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"access_token": "t", "refresh_token": "r", "expires_in": 3600})
            )
            respx.get(f"{mal.API}/users/@me").mock(return_value=httpx.Response(200, json={"id": 77, "name": "alice"}))
            response = await bob.get(f"/auth/mal/callback?code=abc&state={state}")
        assert location(response) == ("/dashboard", {"error": "account_already_linked"})
        assert [a["provider"] for a in (await bob.get("/api/me")).json()["accounts"]] == ["anilist"]

    mal_account = (await session.scalars(select(LinkedAccount).where(LinkedAccount.provider == "mal"))).one()
    assert mal_account.user_id == owner
    assert mal_account.access_token != "t"  # Bob's token did not replace Alice's


# Closed access: while the sign-up allowlist is on, only listed provider accounts may create an AniSyncHub user.


@pytest.fixture
def signup_gate(monkeypatch):
    """Turns the sign-up allowlist on with the given entries (the settings are re-read for the test)."""

    def turn_on(allowlist: str) -> None:
        monkeypatch.setenv("SIGNUP_ALLOWLIST_ENABLED", "true")
        monkeypatch.setenv("SIGNUP_ALLOWLIST", allowlist)
        get_settings.cache_clear()

    yield turn_on
    monkeypatch.delenv("SIGNUP_ALLOWLIST_ENABLED", raising=False)
    monkeypatch.delenv("SIGNUP_ALLOWLIST", raising=False)
    get_settings.cache_clear()


async def row_counts(session) -> tuple[int, int, int]:
    return tuple(
        [await session.scalar(select(func.count()).select_from(m)) for m in (User, LinkedAccount, UserSession)]
    )


async def test_an_account_not_on_the_allowlist_cannot_create_a_user(client, session, signup_gate, caplog):
    signup_gate("mal:1001")
    before = await row_counts(session)
    with caplog.at_level("INFO", logger="anisync.api.auth"):
        response = await sign_in_with_mal(client, remote_id=2002, name="stranger")
    assert location(response) == ("/not-open", {}) and not has_session_cookie(response)
    assert await row_counts(session) == before
    assert "mal:2002" in caplog.text  # the owner copies the id from here into the allowlist
    assert (await client.get("/api/me")).status_code == 401


async def test_an_account_on_the_allowlist_creates_a_user_and_signs_in(client, signup_gate):
    signup_gate("anilist:5, mal:1001")
    response = await sign_in_with_mal(client, remote_id=1001, name="owner")
    assert location(response) == ("/dashboard", {}) and has_session_cookie(response)
    assert (await client.get("/api/me")).json()["display_name"] == "owner"


async def test_the_allowlist_matches_account_ids_never_usernames(client, session, signup_gate):
    signup_gate("mal:1001")
    response = await sign_in_with_mal(client, remote_id=3003, name="1001")
    assert location(response) == ("/not-open", {}) and not has_session_cookie(response)
    assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_signed_in_users_link_further_accounts_whatever_the_allowlist_says(client, signup_gate):
    signup_gate("mal:1001")
    await sign_in_with_mal(client, remote_id=1001)
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "k-access", "refresh_token": "k-refresh"})
        )
        respx.get(f"{kitsu.API}/users").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "55", "attributes": {"name": "kaito"}}]})
        )
        response = await client.post("/auth/kitsu/password", json={"username": "kaito", "password": "pw"})
    assert response.json() == {"ok": True}
    assert [a["provider"] for a in (await client.get("/api/me")).json()["accounts"]] == ["mal", "kitsu"]


async def test_an_account_linked_before_the_gate_still_signs_in(client, signup_gate):
    await sign_in_with_mal(client, remote_id=2002)  # gate off: open sign-in
    await client.post("/auth/logout")
    signup_gate("mal:1001")
    response = await sign_in_with_mal(client, remote_id=2002)
    assert location(response) == ("/dashboard", {}) and has_session_cookie(response)


async def test_development_keeps_sign_in_open(client, session):
    assert not get_settings().signup_allowlist_enabled
    response = await sign_in_with_mal(client, remote_id=2002)
    assert location(response) == ("/dashboard", {})
    assert await session.scalar(select(func.count()).select_from(User)) == 1


def test_production_turns_the_allowlist_on_unless_told_otherwise():
    production = {"public_url": "https://anisync.example", "session_secret": "x" * 40, "token_encryption_keys": "k"}
    assert Settings(**production, _env_file=None).signup_allowlist_enabled is True
    assert Settings(**production, signup_allowlist_enabled=False, _env_file=None).signup_allowlist_enabled is False
    assert Settings(public_url="http://localhost:5173", _env_file=None).signup_allowlist_enabled is False


def test_the_allowlist_is_parsed_once_into_provider_and_account_id():
    settings = Settings(signup_allowlist=" mal:1001 ,anilist:5,, ", _env_file=None)
    assert settings.signup_allowlist_ids == {("mal", "1001"), ("anilist", "5")}


@pytest.mark.parametrize("entry", ["foo:1", "mal", "mal:"])
def test_an_allowlist_entry_for_an_unknown_provider_or_without_an_id_refuses_to_load(entry):
    with pytest.raises(ValidationError, match="SIGNUP_ALLOWLIST"):
        Settings(signup_allowlist=entry, _env_file=None)
