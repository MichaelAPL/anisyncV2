"""CSRF: state-changing requests from other origins are refused."""

import pytest

from anisync import sessions
from anisync.security import origin_of
from tests.test_auth_and_api import sign_in_with_mal

OURS = "http://localhost:5173"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:5173/dashboard?x=1", "http://localhost:5173"),
        ("HTTPS://Example.COM/a", "https://example.com"),
        ("null", "null"),
        ("", None),
        (None, None),
        ("not a url", None),
    ],
)
def test_origin_of(url, expected):
    assert origin_of(url) == expected


async def test_requests_from_our_own_origin_work(client):
    await sign_in_with_mal(client)
    assert (await client.post("/auth/logout", headers={"Origin": OURS})).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:5174", "http://localhost"])
async def test_other_origins_are_refused_for_every_unsafe_method(client, origin):
    await sign_in_with_mal(client)
    h = {"Origin": origin}
    for method, path in [
        ("POST", "/auth/logout"),
        ("PUT", "/api/me/master"),
        ("PATCH", "/api/accounts/1"),
        ("DELETE", "/api/sessions/1"),
    ]:
        response = await client.request(method, path, headers=h)
        assert response.status_code == 403, (method, path, origin)
        assert response.json() == {"detail": "Cross-site request blocked"}
    assert (await client.get("/api/me", headers=h)).status_code == 200  # reads are not state-changing


async def test_the_referer_is_used_when_there_is_no_origin(client):
    await sign_in_with_mal(client)
    client.headers.pop("Origin")
    assert (await client.post("/auth/logout", headers={"Referer": "https://evil.example/page"})).status_code == 403
    assert (await client.post("/auth/logout", headers={"Referer": f"{OURS}/settings"})).status_code == 200


async def test_a_session_cookie_without_any_origin_information_is_refused(client):
    await sign_in_with_mal(client)
    client.headers.pop("Origin")
    assert sessions.COOKIE in client.cookies
    assert (await client.post("/auth/logout")).status_code == 403


async def test_requests_without_a_session_and_without_origin_are_not_blocked(client):
    client.headers.pop("Origin")
    assert (await client.post("/auth/logout")).status_code == 200  # nothing to forge


async def test_a_refusal_still_carries_a_request_id(client):
    response = await client.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and len(response.headers["x-request-id"]) == 12
