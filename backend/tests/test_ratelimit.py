"""Rate limits kept in Postgres."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy import func, select

from anisync import ratelimit
from anisync.main import app
from anisync.models import RateLimitHit
from anisync.providers import kitsu
from tests.test_auth_and_api import sign_in_with_mal

WINDOW = timedelta(minutes=15)


async def hits(session, prefix="") -> int:
    return await session.scalar(
        select(func.count()).select_from(RateLimitHit).where(RateLimitHit.key.like(f"{prefix}%"))
    )


async def attempt(session, key="k", limit=3):
    await ratelimit.check(session, key, limit, WINDOW)


async def test_attempts_up_to_the_limit_are_allowed_then_refused(session):
    for _ in range(3):
        await attempt(session)
    with pytest.raises(HTTPException) as exc:
        await attempt(session)
    assert exc.value.status_code == 429
    assert 1 <= int(exc.value.headers["Retry-After"]) <= WINDOW.total_seconds()


async def test_a_refused_attempt_is_not_recorded(session):
    for _ in range(3):
        await attempt(session)
    with pytest.raises(HTTPException):
        await attempt(session)
    assert await hits(session) == 3


async def test_keys_are_independent(session):
    for _ in range(3):
        await attempt(session, "a")
    await attempt(session, "b")


async def test_attempts_leave_the_window(session):
    old = datetime.now(UTC) - WINDOW - timedelta(seconds=1)
    session.add_all([RateLimitHit(key="k", created_at=old) for _ in range(3)])
    await session.flush()
    await attempt(session)  # the old ones no longer count


async def test_retry_after_is_when_the_oldest_counted_attempt_expires(session):
    now = datetime.now(UTC)
    session.add_all([RateLimitHit(key="k", created_at=now - timedelta(minutes=m)) for m in (14, 5, 1)])
    await session.flush()
    with pytest.raises(HTTPException) as exc:
        await attempt(session)
    assert 50 <= int(exc.value.headers["Retry-After"]) <= 70  # the 14-minute-old attempt expires in ~1 minute


async def test_purge_removes_only_old_rows(session):
    now = datetime.now(UTC)
    session.add_all([RateLimitHit(key="k", created_at=now - timedelta(days=2)), RateLimitHit(key="k", created_at=now)])
    await session.flush()
    assert await ratelimit.purge_old(session) == 1 and await hits(session) == 1


# ---- over HTTP ----------------------------------------------------------------------------------------------


async def test_password_linking_is_limited_and_failed_attempts_count(client, session):
    await sign_in_with_mal(client)
    body = {"username": "a@example.com", "password": "wrong"}
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        statuses = [(await client.post("/auth/kitsu/password", json=body)).status_code for _ in range(6)]
        last = await client.post("/auth/kitsu/password", json=body)
    assert statuses == [400] * 5 + [429]
    assert last.status_code == 429 and int(last.headers["retry-after"]) > 0
    assert "kitsu" not in last.text.lower()


async def test_the_limit_also_applies_per_user_from_other_addresses(client, session):
    await sign_in_with_mal(client)
    body = {"username": "a@example.com", "password": "wrong"}
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 4000))
    elsewhere = httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=client.headers, cookies=client.cookies
    )
    with respx.mock:
        respx.post(kitsu.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        for _ in range(5):
            await client.post("/auth/kitsu/password", json=body)
        assert (await elsewhere.post("/auth/kitsu/password", json=body)).status_code == 429
    await elsewhere.aclose()


async def test_sign_in_routes_are_limited_per_address(client):
    statuses = [(await client.get("/auth/mal/login")).status_code for _ in range(31)]
    assert statuses[:30] == [307] * 30 and statuses[30] == 429


async def test_addresses_are_stored_hashed(client, session):
    await client.get("/auth/mal/login")
    key = (await session.scalars(select(RateLimitHit.key))).one()
    assert key.startswith("oauth:ip:") and "127.0.0.1" not in key and len(key) < 128


def _request_from(host: str):
    from starlette.requests import Request

    return Request({"type": "http", "client": (host, 1234), "headers": []})


def test_ipv6_addresses_in_one_64_share_a_bucket_and_ipv4_is_unchanged():
    same = ratelimit.client_id(_request_from("2001:db8:1:2::1"))
    assert same == ratelimit.client_id(_request_from("2001:db8:1:2:ffff:ffff:ffff:ffff"))
    assert same != ratelimit.client_id(_request_from("2001:db8:1:3::1"))
    assert ratelimit.client_id(_request_from("203.0.113.7")) != ratelimit.client_id(_request_from("203.0.113.8"))
