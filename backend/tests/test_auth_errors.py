"""The failure paths of sign-in: none of them may end with a session or change another user's account."""

from urllib.parse import parse_qs, urlparse

import httpx
import respx
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import func, select

from anisync import sessions
from anisync.main import app
from anisync.models import LinkedAccount, UserSession
from anisync.providers import anilist, mal
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
