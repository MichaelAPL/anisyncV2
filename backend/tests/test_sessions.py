"""Server-side sessions: lifecycle, revocation, rotation and cookie flags."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from anisync import sessions
from anisync.models import User, UserSession
from tests.test_auth_and_api import sign_in_with_mal


async def make_user(session, name="kaito") -> User:
    user = User(display_name=name)
    session.add(user)
    await session.flush()
    return user


async def count_sessions(session) -> int:
    return await session.scalar(select(func.count()).select_from(UserSession))


async def test_the_token_is_random_and_only_its_hash_is_stored(session):
    user = await make_user(session)
    token = await sessions.start(session, user.id, "Mozilla/5.0")
    other = await sessions.start(session, user.id, "Mozilla/5.0")
    assert token != other and len(token) >= 40
    stored = (await session.scalars(select(UserSession.token_hash))).all()
    assert token not in stored and sessions.hash_token(token) in stored
    assert str(user.id) not in token  # nothing derived from the user


async def test_a_session_finds_its_user_until_it_expires_or_is_revoked(session):
    user = await make_user(session)
    token = await sessions.start(session, user.id, None)
    assert (await sessions.user_for(session, token)).id == user.id
    assert await sessions.user_for(session, "not-a-token") is None

    row = (await session.scalars(select(UserSession))).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    assert await sessions.user_for(session, token) is None

    fresh = await sessions.start(session, user.id, None)
    await sessions.end(session, fresh)
    assert await sessions.user_for(session, fresh) is None


async def test_activity_extends_the_session_but_not_on_every_request(session):
    user = await make_user(session)
    token = await sessions.start(session, user.id, None)
    row = (await session.scalars(select(UserSession))).one()
    before = row.expires_at
    await sessions.find(session, token)  # just created: no write
    assert row.expires_at == before

    row.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)
    row.expires_at = datetime.now(UTC) + timedelta(days=1)
    await sessions.find(session, token)
    assert row.expires_at > datetime.now(UTC) + timedelta(days=29)


async def test_starting_a_session_revokes_the_one_it_replaces(session):
    user = await make_user(session)
    old = await sessions.start(session, user.id, None)
    new = await sessions.start(session, user.id, None, replacing=old)
    assert await sessions.user_for(session, old) is None
    assert (await sessions.user_for(session, new)).id == user.id and await count_sessions(session) == 1


async def test_end_all_signs_out_every_device_of_that_user_only(session):
    a, b = await make_user(session, "a"), await make_user(session, "b")
    tokens_a = [await sessions.start(session, a.id, None) for _ in range(3)]
    token_b = await sessions.start(session, b.id, None)
    assert await sessions.end_all(session, a.id) == 3
    for token in tokens_a:
        assert await sessions.user_for(session, token) is None
    assert (await sessions.user_for(session, token_b)).id == b.id


async def test_expired_sessions_are_purged(session):
    user = await make_user(session)
    await sessions.start(session, user.id, None)
    old = await sessions.start(session, user.id, None)
    await session.execute(
        text("update sessions set expires_at = now() - interval '1 day' where token_hash = :h"),
        {"h": sessions.hash_token(old)},
    )
    assert await sessions.purge_expired(session) == 1 and await count_sessions(session) == 1


async def test_user_agent_is_truncated(session):
    user = await make_user(session)
    await sessions.start(session, user.id, "x" * 1000)
    assert len((await session.scalars(select(UserSession.user_agent))).one()) == 255


# ---- over HTTP ----------------------------------------------------------------------------------------------


async def test_signing_in_sets_a_hardened_cookie_that_is_not_the_user_id(client, session):
    response = await sign_in_with_mal(client)
    cookie = response.headers.get_list("set-cookie")
    [session_cookie] = [c for c in cookie if c.startswith(f"{sessions.COOKIE}=")]
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie and "Path=/" in session_cookie
    assert "Max-Age=2592000" in session_cookie
    token = session_cookie.split(";")[0].split("=", 1)[1]
    assert sessions.hash_token(token) in (await session.scalars(select(UserSession.token_hash))).all()
    assert "oauth" in "".join(cookie) and "Max-Age=0" in "".join(c for c in cookie if "anisync_oauth" in c)


async def test_every_sign_in_issues_a_new_session_and_revokes_the_old(client, session):
    await sign_in_with_mal(client)
    first = client.cookies[sessions.COOKIE]
    await sign_in_with_mal(client)
    second = client.cookies[sessions.COOKIE]
    assert first != second and await count_sessions(session) == 1
    assert (await client.get("/api/me")).status_code == 200


async def test_a_stolen_old_token_stops_working_after_the_next_sign_in(client, session, monkeypatch):
    await sign_in_with_mal(client)
    old = client.cookies[sessions.COOKIE]
    await sign_in_with_mal(client)
    client.cookies.clear()
    client.cookies.set(sessions.COOKIE, old)
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_revokes_the_session_server_side(client, session):
    await sign_in_with_mal(client)
    token = client.cookies[sessions.COOKIE]
    assert (await client.post("/auth/logout")).json() == {"ok": True}
    assert await count_sessions(session) == 0
    client.cookies.set(sessions.COOKIE, token)  # replaying the old cookie gets nothing
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_all_signs_out_other_devices_too(client, session):
    await sign_in_with_mal(client)
    laptop = client.cookies[sessions.COOKIE]
    user_id = (await client.get("/api/me")).json()["id"]
    await sessions.start(session, user_id, "Phone")
    await sessions.start(session, user_id, "Tablet")
    assert await count_sessions(session) == 3
    assert (await client.post("/auth/logout-all")).json() == {"ok": True}
    assert await count_sessions(session) == 0
    client.cookies.set(sessions.COOKIE, laptop)
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_all_needs_a_session(client):
    assert (await client.post("/auth/logout-all")).status_code == 401


async def test_the_session_list_marks_this_device_and_hides_tokens(client, session):
    await sign_in_with_mal(client)
    user_id = (await client.get("/api/me")).json()["id"]
    await sessions.start(session, user_id, "Phone")
    rows = (await client.get("/api/sessions")).json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    assert {"id", "created_at", "last_seen_at", "user_agent", "current"} == set(rows[0])


async def test_a_session_can_be_revoked_by_its_owner_only(client, session):
    await sign_in_with_mal(client)
    me = (await client.get("/api/me")).json()["id"]
    await sessions.start(session, me, "Phone")
    other = await make_user(session, "someone else")
    await sessions.start(session, other.id, "Their laptop")
    rows = (await client.get("/api/sessions")).json()
    phone = next(r for r in rows if not r["current"])
    theirs = (await session.scalars(select(UserSession).where(UserSession.user_id == other.id))).one()

    assert (await client.delete(f"/api/sessions/{theirs.id}")).status_code == 404
    assert await count_sessions(session) == 3
    assert (await client.delete(f"/api/sessions/{phone['id']}")).json() == {"ok": True}
    assert await count_sessions(session) == 2
