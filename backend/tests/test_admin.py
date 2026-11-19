"""Roles, permissions, bans and the admin audit trail (Phase 4.0 and 4.1)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from anisync import moderation, permissions, sessions, sync
from anisync.api.deps import require_permission
from anisync.config import get_settings
from anisync.models import AdminAction, User, UserSession
from tests.fakes import FakeClient
from tests.test_api_contract import API_ROUTES, dependency_calls
from tests.test_auth_and_api import sign_in_with_mal
from tests.test_ownership import browser, make_user, titles

# make_user links the fake providers "fa" and "fb"; `client` points the app at the rolled-back test session.
pytestmark = pytest.mark.usefixtures("fakes", "client")


async def make_admin(session, tag="admin", role="admin"):
    user, accounts, token = await make_user(session, tag)
    user.role = role
    await session.flush()
    return user, token


@pytest.fixture
def moderator_role():
    """A role that may only ban: proves permissions, not role names, decide."""
    permissions.ROLE_PERMISSIONS["mod"] = frozenset({permissions.USERS_BAN})
    yield "mod"
    del permissions.ROLE_PERMISSIONS["mod"]


# ---- permissions ----------------------------------------------------------------------------------------


def test_roles_map_to_permissions():
    assert permissions.permissions_for("user") == frozenset()
    assert permissions.permissions_for("admin") == permissions.ALL_PERMISSIONS
    assert permissions.permissions_for("no-such-role") == frozenset()  # unknown roles grant nothing


def test_a_user_can_only_hold_a_known_role():
    with pytest.raises(ValueError, match="unknown role"):
        User(display_name="x", role="superuser")


def test_every_admin_route_declares_a_known_permission():
    admin_routes = [r for r in API_ROUTES if r.path.startswith("/api/admin")]
    assert admin_routes, "the walk found no admin routes"
    for route in admin_routes:
        declared = [p for call in dependency_calls(route.dependant) for p in getattr(call, "permissions", ())]
        assert declared, f"{route.path} has no require_permission"
        assert set(declared) <= permissions.ALL_PERMISSIONS


async def test_admin_routes_refuse_signed_out_and_plain_users(client, session):
    target, _, _ = await make_user(session, "t")
    plain, _, token = await make_user(session, "plain")
    calls = [
        ("POST", f"/api/admin/users/{target.id}/ban", {"reason": "spam"}),
        ("POST", f"/api/admin/users/{target.id}/unban", {}),
        ("PUT", f"/api/admin/users/{target.id}/role", {"role": "admin"}),
    ]
    for method, url, body in calls:
        assert (await client.request(method, url, json=body)).status_code == 401
        async with browser(token) as c:
            assert (await c.request(method, url, json=body)).status_code == 403, url
    await session.refresh(target)
    assert not target.is_banned and target.role == "user"


async def test_a_role_only_gets_what_its_permissions_allow(session, moderator_role):
    boss, _ = await make_admin(session, "boss")
    mod, token = await make_admin(session, "mod", role=moderator_role)
    victim, _, _ = await make_user(session, "v")
    async with browser(token) as c:
        assert (await c.post(f"/api/admin/users/{victim.id}/ban", json={"reason": "spam"})).status_code == 200
        assert (await c.put(f"/api/admin/users/{victim.id}/role", json={"role": "admin"})).status_code == 403
        assert (await c.get("/api/me")).json()["permissions"] == ["users.ban"]
    assert boss.id != mod.id


async def test_require_permission_needs_all_of_them(session):
    user, _, _ = await make_user(session, "x")
    user.role = "admin"
    assert await require_permission("users.ban", "roles.manage")(user) is user
    user.role = "user"
    with pytest.raises(Exception, match="Not allowed"):
        await require_permission("users.ban")(user)


async def test_me_tells_the_ui_the_role_and_permissions(session):
    admin, token = await make_admin(session)
    plain, _, plain_token = await make_user(session, "plain")
    async with browser(token) as c:
        me = (await c.get("/api/me")).json()
    assert me["role"] == "admin" and set(me["permissions"]) == permissions.ALL_PERMISSIONS
    async with browser(plain_token) as c:
        me = (await c.get("/api/me")).json()
    assert me["role"] == "user" and me["permissions"] == []


# ---- bans -----------------------------------------------------------------------------------------------


async def test_banning_signs_the_user_out_everywhere_and_is_audited(session, fakes):
    admin, token = await make_admin(session)
    victim, _, victim_token = await make_user(session, "v")
    await sessions.start(session, victim.id, "second device")
    async with browser(token) as c:
        response = await c.post(f"/api/admin/users/{victim.id}/ban", json={"reason": "scraping the API"})
    assert response.status_code == 200
    body = response.json()
    assert body["banned_at"] and body["ban_reason"] == "scraping the API"

    assert not (await session.scalars(select(UserSession).where(UserSession.user_id == victim.id))).all()
    async with browser(victim_token) as c:
        assert (await c.get("/api/me")).status_code == 401
    [action] = (await session.scalars(select(AdminAction).where(AdminAction.action == "user.ban"))).all()
    assert (action.actor_id, action.target_user_id, action.note) == (admin.id, victim.id, "scraping the API")


async def test_a_banned_user_with_a_leftover_session_is_still_refused(session):
    victim, _, token = await make_user(session, "v")
    victim.banned_at = datetime.now(UTC)
    await session.flush()
    async with browser(token) as c:
        assert (await c.get("/api/me")).status_code == 401


async def test_a_banned_user_is_skipped_by_every_sync_path(session, fakes):
    admin, _ = await make_admin(session)
    victim, accounts, _ = await make_user(session, "v")
    titles("v", "1")
    await moderation.ban(session, admin, victim.id, "abuse")

    assert victim.id not in await sync.users_in_slot(session, victim.id % sync.SYNC_SLOTS)
    assert victim.id not in await sync.users_with_master(session)
    assert await sync.run_sync(session, victim.id, "manual") == {"skipped": "banned"}
    await sync.push_entry(session, accounts["fb"].id, "fa", "1", None)
    assert FakeClient.REQUESTS == []


async def test_a_banned_user_cannot_sign_in_again_and_gets_no_new_session(client, session):
    response = await sign_in_with_mal(client, remote_id=555, name="spammer")
    assert response.headers["location"].endswith("/dashboard")
    user = await session.scalar(select(User).where(User.display_name == "spammer"))
    admin, _ = await make_admin(session)
    await moderation.ban(session, admin, user.id, "spam")

    client.cookies.clear()
    again = await sign_in_with_mal(client, remote_id=555, name="spammer")
    assert "error=account_banned" in again.headers["location"]
    assert (await client.get("/api/me")).status_code == 401
    assert await session.scalar(select(User).where(User.display_name == "spammer")) is user  # no second user created


async def test_unban_restores_access(session):
    admin, _ = await make_admin(session)
    victim, _, _ = await make_user(session, "v")
    await moderation.ban(session, admin, victim.id, "oops")
    await moderation.unban(session, admin, victim.id, "it was a mistake")
    assert not victim.is_banned and victim.ban_reason is None and victim.banned_by is None
    token = await sessions.start(session, victim.id, "again")
    async with browser(token) as c:
        assert (await c.get("/api/me")).status_code == 200
    actions = (await session.scalars(select(AdminAction.action).order_by(AdminAction.id))).all()
    assert actions == ["user.ban", "user.unban"]


@pytest.mark.parametrize("scenario", ["self", "missing", "twice", "unbanned", "manager"])
async def test_ban_guards(session, scenario):
    admin, _ = await make_admin(session)
    other, _, _ = await make_user(session, "o")
    other_admin, _ = await make_admin(session, "other-admin")
    cases = {
        "self": (moderation.ban(session, admin, admin.id, "x"), 400),
        "missing": (moderation.ban(session, admin, 99999, "x"), 404),
        "unbanned": (moderation.unban(session, admin, other.id), 409),
        "manager": (moderation.ban(session, admin, other_admin.id, "x"), 409),
    }
    if scenario == "twice":
        await moderation.ban(session, admin, other.id, "first")
        cases["twice"] = (moderation.ban(session, admin, other.id, "again"), 409)
    coroutine, status = cases[scenario]
    for name, (unused, _) in cases.items():
        if name != scenario:
            unused.close()
    with pytest.raises(moderation.ModerationError) as info:
        await coroutine
    assert info.value.status == status


async def test_the_ban_reason_is_required(session):
    admin, token = await make_admin(session)
    victim, _, _ = await make_user(session, "v")
    async with browser(token) as c:
        assert (await c.post(f"/api/admin/users/{victim.id}/ban", json={"reason": ""})).status_code == 422


# ---- role changes ---------------------------------------------------------------------------------------


async def test_granting_and_revoking_a_role_is_audited(session):
    admin, token = await make_admin(session)
    user, _, _ = await make_user(session, "u")
    async with browser(token) as c:
        assert (await c.put(f"/api/admin/users/{user.id}/role", json={"role": "admin"})).json()["role"] == "admin"
        assert (await c.put(f"/api/admin/users/{user.id}/role", json={"role": "user"})).json()["role"] == "user"
        assert (await c.put(f"/api/admin/users/{user.id}/role", json={"role": "wizard"})).status_code == 422
    rows = (await session.scalars(select(AdminAction).where(AdminAction.action == "user.role"))).all()
    assert [r.details for r in rows] == [{"previous": "user", "new": "admin"}, {"previous": "admin", "new": "user"}]


async def test_the_last_role_manager_cannot_demote_themselves(session):
    admin, token = await make_admin(session)
    async with browser(token) as c:
        response = await c.put(f"/api/admin/users/{admin.id}/role", json={"role": "user"})
    assert response.status_code == 409
    await session.refresh(admin)
    assert admin.role == "admin"
    second, _ = await make_admin(session, "second")
    await moderation.set_role(session, admin, admin.id, "user")  # fine once another admin exists
    assert admin.role == "user" and second.role == "admin"


async def test_a_banned_user_cannot_be_given_a_role(session):
    admin, _ = await make_admin(session)
    victim, _, _ = await make_user(session, "v")
    await moderation.ban(session, admin, victim.id, "x")
    with pytest.raises(moderation.ModerationError):
        await moderation.set_role(session, admin, victim.id, "admin")


# ---- bootstrap ------------------------------------------------------------------------------------------


async def test_admin_user_ids_promote_existing_users_and_never_demote(session, monkeypatch):
    first, _, _ = await make_user(session, "first")
    second, _, _ = await make_user(session, "second")
    banned, _, _ = await make_user(session, "banned")
    banned.banned_at = datetime.now(UTC)
    second.role = "admin"
    await session.flush()
    monkeypatch.setattr(get_settings(), "admin_user_ids", f"{first.id}, {banned.id}, junk, 999999")

    assert await moderation.bootstrap_admins(session) == 1
    assert (first.role, second.role, banned.role) == ("admin", "admin", "user")  # second untouched, banned skipped
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")
    assert await moderation.bootstrap_admins(session) == 0
    assert first.role == "admin"
