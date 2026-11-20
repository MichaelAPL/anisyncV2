"""Sign-in, account linking and the edit-push flow, driven by the provider registry (HTTP to providers is mocked)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select, text

from anisync import jobs
from anisync.models import LinkedAccount
from anisync.providers import kitsu, mal


def query(response) -> dict[str, list[str]]:
    return parse_qs(urlparse(response.headers["location"]).query)


async def sign_in_with_mal(client, remote_id=77, name="kaito"):
    """Runs the OAuth redirect flow against a mocked provider. Returns the callback response."""
    login = await client.get("/auth/mal/login")
    state = query(login)["state"][0]
    with respx.mock:
        respx.post(mal.TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "mal-access", "refresh_token": "mal-refresh", "expires_in": 2_678_400}
            )
        )
        respx.get(f"{mal.API}/users/@me").mock(return_value=httpx.Response(200, json={"id": remote_id, "name": name}))
        return await client.get(f"/auth/mal/callback?code=abc&state={state}")


async def test_login_redirects_follow_the_provider_auth_kind(client):
    pkce = await client.get("/auth/mal/login")
    assert pkce.status_code == 307 and urlparse(pkce.headers["location"]).netloc == "myanimelist.net"
    q = query(pkce)
    assert q["client_id"] == ["mal-test-id"] and q["code_challenge_method"] == ["plain"]
    assert q["code_challenge"][0] and q["redirect_uri"] == ["http://localhost:5173/auth/mal/callback"]

    plain = await client.get("/auth/anilist/login")
    q = query(plain)
    assert q["client_id"] == ["anilist-test-id"] and "code_challenge" not in q


async def test_login_rejects_unknown_and_credential_only_providers(client):
    assert (await client.get("/auth/nope/login")).status_code == 404
    assert (await client.get("/auth/kitsu/login")).status_code == 400
    assert (await client.get("/auth/nope/callback?code=x&state=y")).status_code == 404
    # Signed-out callers get 401 first, so they learn nothing about which providers exist.
    assert (await client.post("/auth/nope/password", json={"username": "a", "password": "b"})).status_code == 401


async def test_callback_rejects_a_wrong_state(client):
    await client.get("/auth/mal/login")
    response = await client.get("/auth/mal/callback?code=abc&state=forged")
    assert response.status_code == 307 and "error=invalid_state" in response.headers["location"]
    assert (await client.get("/api/me")).status_code == 401


async def test_sign_in_creates_the_user_and_stores_tokens_encrypted(client, session):
    response = await sign_in_with_mal(client)
    assert response.headers["location"] == "http://localhost:5173/dashboard"

    me = (await client.get("/api/me")).json()
    assert me["display_name"] == "kaito"
    [account] = me["accounts"]
    assert account["provider"] == "mal" and account["is_master"] and account["remote_username"] == "kaito"
    assert "token" not in str(me).lower()

    raw = await session.scalar(text("select access_token from linked_accounts"))
    assert "mal-access" not in raw
    stored = (await session.scalars(select(LinkedAccount))).one()
    assert stored.access_token == "mal-access" and stored.refresh_token == "mal-refresh"


async def test_signing_in_again_reuses_the_user(client, session):
    await sign_in_with_mal(client)
    await client.post("/auth/logout")
    assert (await client.get("/api/me")).status_code == 401
    await sign_in_with_mal(client)
    assert len((await client.get("/api/me")).json()["accounts"]) == 1


async def test_password_providers_link_after_signing_in(client):
    body = {"username": "kaito@example.com", "password": "hunter2"}
    assert (await client.post("/auth/kitsu/password", json=body)).status_code == 401  # not signed in

    await sign_in_with_mal(client)
    assert (await client.post("/auth/mal/password", json=body)).status_code == 400  # wrong kind of provider
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "k-access", "refresh_token": "k-refresh"})
        )
        respx.get(f"{kitsu.API}/users").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "55", "attributes": {"name": "kaito"}}]})
        )
        assert (await client.post("/auth/kitsu/password", json=body)).json() == {"ok": True}

    accounts = (await client.get("/api/me")).json()["accounts"]
    assert [(a["provider"], a["is_master"]) for a in accounts] == [("mal", True), ("kitsu", False)]


async def test_linking_kitsu_records_whether_it_hides_adult_titles(client):
    await sign_in_with_mal(client)
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "k", "refresh_token": "r"})
        )
        respx.get(f"{kitsu.API}/users").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "55", "attributes": {"name": "kaito", "sfwFilter": True}}]}
            )
        )
        await client.post("/auth/kitsu/password", json={"username": "kaito@example.com", "password": "pw"})
    flags = {a["provider"]: a["hides_adult"] for a in (await client.get("/api/me")).json()["accounts"]}
    assert flags == {"mal": False, "kitsu": True}


async def test_rejected_credentials_are_reported_without_leaking_details(client):
    await sign_in_with_mal(client)
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        response = await client.post("/auth/kitsu/password", json={"username": "a", "password": "wrong"})
    assert response.status_code == 400 and response.json() == {"detail": "Kitsu rejected those credentials"}


async def test_a_blocked_or_unreachable_provider_is_not_reported_as_wrong_credentials(client):
    await sign_in_with_mal(client)
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(return_value=httpx.Response(403, text="Just a moment..."))
        blocked = await client.post("/auth/kitsu/password", json={"username": "a", "password": "b"})
        respx.post(kitsu.TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
        down = await client.post("/auth/kitsu/password", json={"username": "a", "password": "b"})
    assert blocked.status_code == 502 and "wrong" not in blocked.text and "rejected" not in blocked.text
    assert down.status_code == 502 and "reach Kitsu" in down.json()["detail"]


async def test_editing_the_master_list_queues_one_push_per_mirror(client):
    await sign_in_with_mal(client)
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "k", "refresh_token": "r"})
        )
        respx.get(f"{kitsu.API}/users").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "55", "attributes": {"name": "kaito"}}]})
        )
        await client.post("/auth/kitsu/password", json={"username": "a", "password": "b"})

    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector), respx.mock:
        patch = respx.patch(f"{mal.API}/anime/1/my_list_status").mock(return_value=httpx.Response(200, json={}))
        response = await client.put("/api/list/1", json={"status": "watching", "progress": 5, "score": 80})
    assert response.json() == {"ok": True, "queued_mirrors": 1}
    assert patch.called  # the master was written first

    [job] = connector.jobs.values()
    assert job["task_name"] == "anisync.jobs.push_entry"
    assert job["args"]["master_provider"] == "mal" and job["args"]["master_media_id"] == "1"
    assert job["args"]["entry"]["external_ids"] == {"mal": "1"}


async def test_the_api_only_exposes_your_own_accounts(client):
    await sign_in_with_mal(client)
    mine = (await client.get("/api/me")).json()["accounts"][0]["id"]
    assert (await client.patch(f"/api/accounts/{mine + 1000}", json={"sync_enabled": False})).status_code == 404
    assert (await client.put("/api/me/master", json={"account_id": mine + 1000})).status_code == 404


@pytest.mark.parametrize("path", ["/api/me", "/api/list", "/api/sync/runs", "/api/sync/preview"])
async def test_signed_out_requests_are_rejected(client, path):
    assert (await client.get(path)).status_code == 401
