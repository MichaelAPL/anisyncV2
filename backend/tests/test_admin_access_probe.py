"""A plain user must not reach any admin endpoint, whatever headers, paths or bodies they send."""

import re

import pytest
from sqlalchemy import select

from anisync import sessions
from anisync.models import AdminAction, User
from tests.test_api_contract import API_ROUTES
from tests.test_ownership import browser, make_user

pytestmark = pytest.mark.usefixtures("fakes", "client")

ADMIN_ROUTES = [(m, r.path) for r in API_ROUTES if r.path.startswith("/api/admin") for m in r.methods if m != "HEAD"]

# Headers someone might try to make the server think the request is privileged, internal or proxied.
SPOOF_HEADERS = {
    "X-Forwarded-For": "127.0.0.1",
    "X-Real-IP": "127.0.0.1",
    "X-Forwarded-Host": "localhost",
    "X-Original-URL": "/api/me",
    "X-Rewrite-URL": "/api/me",
    "X-HTTP-Method-Override": "GET",
    "X-User-Id": "1",
    "X-Role": "admin",
    "X-Admin": "true",
    "Authorization": "Bearer admin",
}


def fill(path: str, user_id: int) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: str(user_id) if m.group(0) == "{user_id}" else "1", path)


def test_the_probe_walks_every_admin_route():
    assert len(ADMIN_ROUTES) >= 30


@pytest.mark.parametrize("spoof", [False, True])
async def test_every_admin_route_refuses_a_plain_user(client, session, spoof):
    target, _, _ = await make_user(session, "t")
    _, _, token = await make_user(session, "plain")
    headers = SPOOF_HEADERS if spoof else {}
    for method, path in ADMIN_ROUTES:
        url = fill(path, target.id)
        assert (await client.request(method, url, headers=headers, json={})).status_code == 401, url
        async with browser(token) as c:
            assert (await c.request(method, url, headers=headers, json={})).status_code == 403, (method, url)
    assert await session.scalar(select(AdminAction.id).limit(1)) is None  # nothing was done


async def test_forged_or_tampered_session_cookies_are_signed_out(session):
    admin, _, token = await make_user(session, "adm")
    admin.role = "admin"
    await session.flush()
    for cookie in (str(admin.id), sessions.hash_token(token), token[:-1] + ("A" if token[-1] != "A" else "B"), ""):
        async with browser(cookie) as c:
            assert (await c.get("/api/admin/stats/overview")).status_code == 401, cookie


@pytest.mark.parametrize(
    "path",
    [
        "/API/admin/stats/overview",
        "/api/Admin/stats/overview",
        "/api//admin/stats/overview",
        "/api/admin/stats/overview/",
        "/api/admin/./stats/overview",
        "/api/x/../admin/stats/overview",
        "/api/admin%2Fstats/overview",
        "/api/admin/stats/overview;.js",
        "/api/admin/stats/overview?role=admin",
    ],
)
async def test_path_variants_never_reach_an_admin_route_as_a_plain_user(session, path):
    _, _, token = await make_user(session, "plain")
    async with browser(token) as c:
        r = await c.get(path)
    assert r.status_code in (403, 404, 405, 307), r.status_code
    assert "users_total" not in r.text


async def test_a_plain_user_cannot_raise_their_own_role(session):
    user, accounts, token = await make_user(session, "plain")
    async with browser(token) as c:
        await c.patch("/api/me", json={"role": "admin", "permissions": ["roles.manage"], "display_name": "x"})
        await c.patch(f"/api/accounts/{next(iter(accounts.values())).id}", json={"user_id": 1, "role": "admin"})
        await c.put(f"/api/admin/users/{user.id}/role", json={"role": "admin"})
        me = (await c.get("/api/me")).json()
    await session.refresh(user)
    assert user.role == "user" and me["permissions"] == []
    assert (await session.get(User, user.id)).role == "user"
