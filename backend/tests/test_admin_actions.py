"""What admins (and users themselves) can do to an account: full re-check, preview, revoke, unlink, delete (4.10)."""

from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import func, select

from anisync import jobs, moderation, permissions, sessions, sync
from anisync.models import (
    AdminAction,
    LinkedAccount,
    ListSnapshot,
    SyncRun,
    SyncRunEvent,
    User,
    UserSession,
)
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user, titles

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


@pytest.fixture
async def admin(session):
    user, _, token = await make_user(session, "root")
    user.role = "admin"
    await session.flush()
    return user, token


@pytest.fixture
async def second_admin(session):
    """Deleting or banning role managers is refused, so tests that need an admin target use a role without that."""
    permissions.ROLE_PERMISSIONS["helper"] = frozenset({permissions.USERS_READ})
    yield "helper"
    del permissions.ROLE_PERMISSIONS["helper"]


@pytest.fixture
def queue():
    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector):
        yield connector


async def call(token, method, url, **kwargs):
    async with browser(token) as c:
        return await c.request(method, url, **kwargs)


async def actions(session, name):
    return (await session.scalars(select(AdminAction).where(AdminAction.action == name).order_by(AdminAction.id))).all()


async def rows(session, model, **where):
    query = select(func.count()).select_from(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return await session.scalar(query)


# ---- full re-check and preview -----------------------------------------------------------------------------------


async def test_a_full_check_is_queued_with_the_full_flag(session, admin, queue):
    root, token = admin
    user, _, _ = await make_user(session, "u")
    assert (await call(token, "POST", f"/api/admin/users/{user.id}/full-check")).json() == {"queued": True}
    assert (await call(token, "POST", f"/api/admin/users/{user.id}/full-check")).json() == {"queued": False}
    [job] = queue.jobs.values()
    assert job["args"] == {"user_id": user.id, "trigger": "manual", "full": True}
    assert len(await actions(session, "user.full_check")) == 2 and root.id
    assert (await call(token, "POST", "/api/admin/users/999999/full-check")).status_code == 404


async def test_the_queued_full_check_really_reads_everything(session, admin, queue, monkeypatch):
    class Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(jobs, "SessionLocal", lambda: Context())
    user, _, _ = await make_user(session, "u")
    titles("u", "1")
    await jobs.sync_user(user.id, "manual")
    for account in user.accounts:  # as if both had just been read in full
        account.last_full_read_at = NOW
    user.master.sync_cursor = NOW
    FakeClient.REQUESTS.clear()
    await jobs.sync_user(user.id, "manual")
    assert ("fa", "full") not in FakeClient.REQUESTS  # an ordinary sync reads the master incrementally
    FakeClient.REQUESTS.clear()
    await jobs.sync_user(user.id, "manual", full=True)
    assert ("fa", "full") in FakeClient.REQUESTS and ("fb", "full") in FakeClient.REQUESTS


async def test_a_preview_shows_the_changes_and_writes_nothing(session, admin):
    _, token = admin
    user, _, _ = await make_user(session, "u")
    titles("u", "1", "2")
    response = await call(token, "POST", f"/api/admin/users/{user.id}/preview")
    assert response.status_code == 200
    [mirror] = response.json().values()
    assert mirror["provider"] == "fb" and mirror["create"] == 2 and len(mirror["changes"]) == 2
    assert ("fb", "save") not in FakeClient.REQUESTS
    assert await rows(session, SyncRun, user_id=user.id) == 0  # a preview isn't a run


async def test_a_preview_needs_a_master_and_a_live_connection(session, admin):
    _, token = admin
    nomaster, _, _ = await make_user(session, "nomaster")
    nomaster.master_account_id = None
    broken, _, _ = await make_user(session, "broken")
    await session.flush()
    FakeClient.REJECT_TOKENS = True
    assert (await call(token, "POST", f"/api/admin/users/{nomaster.id}/preview")).status_code == 409
    assert (await call(token, "POST", f"/api/admin/users/{broken.id}/preview")).status_code == 409
    assert (await call(token, "POST", "/api/admin/users/999999/preview")).status_code == 404


# ---- revoking tokens and unlinking -------------------------------------------------------------------------------


async def test_revoking_tokens_makes_the_user_link_the_account_again(session, admin):
    root, token = admin
    user, accounts, user_token = await make_user(session, "u")
    titles("u", "1")
    assert (await call(user_token, "GET", "/api/list")).status_code == 200
    response = await call(token, "POST", f"/api/admin/users/{user.id}/accounts/{accounts['fa'].id}/revoke-tokens")
    assert response.status_code == 200

    await session.refresh(accounts["fa"])
    assert accounts["fa"].access_token == "revoked" and accounts["fa"].refresh_token is None
    me = (await call(user_token, "GET", "/api/me")).json()
    flags = {a["provider"]: (a["needs_relink"], a["expires_soon"]) for a in me["accounts"]}
    assert flags["fa"] == (True, False)
    refused = await call(user_token, "POST", "/api/list/refresh")
    assert refused.status_code == 409 and "re-link" in refused.json()["detail"]
    [action] = await actions(session, "account.revoke_tokens")
    assert (action.target_user_id, action.details["provider"], action.actor_id) == (user.id, "fa", root.id)
    assert "revoked" not in str(action.details)


async def test_an_account_must_belong_to_the_user_you_name(session, admin):
    _, token = admin
    one, _, _ = await make_user(session, "one")
    two, two_accounts, _ = await make_user(session, "two")
    url = f"/api/admin/users/{one.id}/accounts/{two_accounts['fa'].id}"
    assert (await call(token, "POST", url + "/revoke-tokens")).status_code == 404
    assert (await call(token, "DELETE", url)).status_code == 404
    await session.refresh(two_accounts["fa"])
    assert two_accounts["fa"].access_token != "revoked" and await rows(session, LinkedAccount, user_id=two.id) == 2


async def test_unlinking_removes_the_account_and_its_copy_and_moves_the_master(session, admin):
    root, token = admin
    user, accounts, _ = await make_user(session, "u")
    session.add_all(
        [ListSnapshot(account_id=accounts["fa"].id, external_id=str(n), status="watching") for n in (1, 2)]
        + [ListSnapshot(account_id=accounts["fb"].id, external_id="9", status="watching")]
    )
    await session.flush()
    base = f"/api/admin/users/{user.id}/accounts"
    assert (await call(token, "DELETE", f"{base}/{accounts['fa'].id}")).status_code == 200  # the master
    await session.refresh(user)
    assert user.master_account_id == accounts["fb"].id
    assert await rows(session, ListSnapshot, account_id=accounts["fa"].id) == 0
    assert await rows(session, ListSnapshot, account_id=accounts["fb"].id) == 1  # the other account is untouched
    assert (await call(token, "DELETE", f"{base}/{accounts['fb'].id}")).status_code == 200  # the last one
    await session.refresh(user)
    assert user.master_account_id is None and await rows(session, LinkedAccount, user_id=user.id) == 0
    details = [a.details for a in await actions(session, "account.unlink")]
    assert details[0]["was_master"] is True and details[0]["provider"] == "fa" and root.id


async def test_a_banned_users_accounts_stay_linked_so_they_cannot_sign_up_again(session, admin):
    root, token = admin
    user, accounts, _ = await make_user(session, "u")
    await moderation.ban(session, root, user.id, "spam")
    response = await call(token, "DELETE", f"/api/admin/users/{user.id}/accounts/{accounts['fa'].id}")
    assert response.status_code == 409
    assert await rows(session, LinkedAccount, user_id=user.id) == 2 and await actions(session, "account.unlink") == []


# ---- deleting a user ---------------------------------------------------------------------------------------------


async def populated(session, tag):
    user, accounts, token = await make_user(session, tag)
    run = SyncRun(user_id=user.id, trigger="manual", summary={}, started_at=NOW)
    session.add(run)
    await session.flush()
    session.add_all(
        [
            SyncRunEvent(run_id=run.id, account_id=accounts["fb"].id, level="error", kind="change_failed", message="x"),
            ListSnapshot(account_id=accounts["fa"].id, external_id="1", status="watching"),
            ListSnapshot(account_id=accounts["fb"].id, external_id="2", status="watching"),
        ]
    )
    await session.flush()
    return user, accounts, token


async def test_deleting_a_user_removes_everything_about_them_and_nothing_else(session, admin):
    root, token = admin
    victim, victim_accounts, victim_token = await populated(session, "victim")
    bystander, bystander_accounts, bystander_token = await populated(session, "bystander")
    await sessions.start(session, victim.id, "second device")
    victim_id, account_ids = victim.id, [a.id for a in victim_accounts.values()]
    root_id, bystander_id = root.id, bystander.id
    await moderation.record(session, root, "user.sync", victim)  # an earlier audit row about the victim

    response = await call(token, "DELETE", f"/api/admin/users/{victim_id}?confirm={victim_id}")
    assert response.status_code == 200
    session.expire_all()
    assert await session.get(User, victim_id) is None
    assert await rows(session, UserSession, user_id=victim_id) == 0
    assert await rows(session, SyncRun, user_id=victim_id) == 0
    assert (
        await session.scalar(select(func.count()).select_from(LinkedAccount).where(LinkedAccount.id.in_(account_ids)))
    ) == 0
    assert (
        await session.scalar(
            select(func.count()).select_from(ListSnapshot).where(ListSnapshot.account_id.in_(account_ids))
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count()).select_from(SyncRunEvent).where(SyncRunEvent.account_id.in_(account_ids))
        )
        == 0
    )
    # the other user is untouched
    assert await rows(session, LinkedAccount, user_id=bystander_id) == 2
    assert await rows(session, SyncRun, user_id=bystander_id) == 1
    assert (await call(bystander_token, "GET", "/api/me")).status_code == 200 and bystander_accounts
    # the audit trail survives, without a link to the deleted user, and says who was deleted
    [deleted] = await actions(session, "user.delete")
    assert (deleted.actor_id, deleted.target_user_id) == (root_id, None)
    assert deleted.details["user_id"] == victim_id and deleted.details["display_name"] == "user-victim"
    earlier = (await actions(session, "user.sync"))[0]
    assert earlier.target_user_id is None
    assert (await call(victim_token, "GET", "/api/me")).status_code == 401


@pytest.mark.parametrize("case", ["wrong-confirm", "missing-confirm", "self", "banned", "role-manager", "unknown"])
async def test_delete_guards(session, admin, case):
    root, token = admin
    target, _, _ = await make_user(session, "target")
    manager, _, _ = await make_user(session, "manager")
    manager.role = "admin"
    await session.flush()
    if case == "banned":
        await moderation.ban(session, root, target.id, "x")
    tid = {"self": root.id, "role-manager": manager.id, "unknown": 99999999}.get(case, target.id)
    url = {"wrong-confirm": f"?confirm={tid + 1}", "missing-confirm": ""}.get(case, f"?confirm={tid}")
    response = await call(token, "DELETE", f"/api/admin/users/{tid}{url}")
    assert response.status_code == {"wrong-confirm": 422, "missing-confirm": 422, "self": 400, "unknown": 404}.get(
        case, 409
    )
    assert await actions(session, "user.delete") == []
    if case != "unknown":
        assert await session.get(User, tid) is not None


async def test_deleted_users_leave_queued_jobs_harmless(session, admin):
    root, token = admin
    victim, accounts, _ = await make_user(session, "victim")
    victim_id, account_id = victim.id, accounts["fb"].id
    await call(token, "DELETE", f"/api/admin/users/{victim_id}?confirm={victim_id}")
    assert await sync.run_sync(session, victim_id, "scheduled") == {"skipped": "no master list selected"}
    await sync.push_entry(session, account_id, "fa", "a1", None)  # no error, no request
    assert FakeClient.REQUESTS == []


# ---- permissions ---------------------------------------------------------------------------------------------------

ACTIONS = [
    ("POST", "/full-check", permissions.SYNCS_RETRY),
    ("POST", "/preview", permissions.SYNCS_RETRY),
    ("POST", "/accounts/ACCOUNT/revoke-tokens", permissions.USERS_MANAGE_TOKENS),
    ("DELETE", "/accounts/ACCOUNT", permissions.USERS_MANAGE_TOKENS),
    ("DELETE", "?confirm=ID", permissions.USERS_DELETE),
]


@pytest.mark.parametrize(("method", "suffix", "needed"), ACTIONS)
async def test_each_action_needs_exactly_its_permission(session, client, method, suffix, needed):
    permissions.ROLE_PERMISSIONS["partial"] = frozenset(permissions.ALL_PERMISSIONS - {needed})
    permissions.ROLE_PERMISSIONS["only"] = frozenset({needed})
    try:
        target, accounts, _ = await make_user(session, "target")
        everything_but, _, token_without = await make_user(session, "without")
        only, _, token_only = await make_user(session, "only")
        everything_but.role, only.role = "partial", "only"
        await session.flush()
        url = f"/api/admin/users/{target.id}" + suffix.replace("ACCOUNT", str(accounts["fb"].id)).replace(
            "ID", str(target.id)
        )
        if suffix.startswith("?"):
            url = f"/api/admin/users/{target.id}{suffix.replace('ID', str(target.id))}"
        assert (await client.request(method, url)).status_code == 401
        assert (await call(token_without, method, url)).status_code == 403
        assert (await call(token_only, method, url)).status_code != 403
    finally:
        del permissions.ROLE_PERMISSIONS["partial"], permissions.ROLE_PERMISSIONS["only"]


# ---- the user deleting their own account ---------------------------------------------------------------------------


async def test_a_user_can_delete_their_own_account(session):
    user, accounts, token = await populated(session, "me")
    other, _, _ = await populated(session, "other")
    user_id, other_id = user.id, other.id
    async with browser(token) as c:
        assert (await c.post("/api/me/delete", json={"confirm": "nope"})).status_code == 422
        assert await session.get(User, user_id) is not None
        response = await c.post("/api/me/delete", json={"confirm": "  Delete My Account "})
        assert response.status_code == 200
        assert (
            "anisync_session" in response.headers["set-cookie"]
            and 'anisync_session=""' in response.headers["set-cookie"]
        )
    session.expire_all()
    assert await session.get(User, user_id) is None
    assert (
        await rows(session, LinkedAccount, user_id=user_id) == 0
        and await rows(session, LinkedAccount, user_id=other_id) == 2
    )
    assert (await call(token, "GET", "/api/me")).status_code == 401


async def test_the_last_role_manager_cannot_delete_their_own_account(session, admin):
    root, token = admin
    response = await call(token, "POST", "/api/me/delete", json={"confirm": "delete my account"})
    assert response.status_code == 409 and await session.get(User, root.id) is not None
    second, _, _ = await make_user(session, "second")
    second.role = "admin"
    await session.flush()
    assert (await call(token, "POST", "/api/me/delete", json={"confirm": "delete my account"})).status_code == 200


# ---- warning users before their connection ends ----------------------------------------------------------------


async def test_me_flags_connections_that_end_soon_but_not_renewable_ones(session):
    user, accounts, token = await make_user(session, "u")
    accounts["fa"].expires_at = NOW + timedelta(days=10)  # no refresh token: ends in 10 days
    accounts["fb"].refresh_token = "renews"  # fb can renew (it has a refresh hook)
    accounts["fb"].expires_at = NOW - timedelta(days=1)
    await session.flush()
    flags = {
        a["provider"]: (a["needs_relink"], a["expires_soon"])
        for a in (await call(token, "GET", "/api/me")).json()["accounts"]
    }
    assert flags == {"fa": (False, True), "fb": (False, False)}
    accounts["fa"].expires_at = NOW + timedelta(days=60)
    accounts["fb"].refresh_token = None
    await session.flush()
    flags = {
        a["provider"]: (a["needs_relink"], a["expires_soon"])
        for a in (await call(token, "GET", "/api/me")).json()["accounts"]
    }
    assert flags == {"fa": (False, False), "fb": (True, False)}


async def test_a_refresh_token_does_not_help_when_the_provider_cannot_refresh(session):
    """fa has no refresh hook: even a stored refresh token can't renew its connection."""
    user, accounts, token = await make_user(session, "u")
    accounts["fa"].refresh_token = "useless"
    accounts["fa"].expires_at = NOW - timedelta(days=1)
    await session.flush()
    flags = {a["provider"]: a["needs_relink"] for a in (await call(token, "GET", "/api/me")).json()["accounts"]}
    assert flags["fa"] is True


# ---- the list of connections that end ----------------------------------------------------------------------------


async def test_admins_see_which_connections_end_soon(session, admin):
    _, token = admin
    soon, soon_accounts, _ = await make_user(session, "soon")
    gone, gone_accounts, _ = await make_user(session, "gone")
    far, far_accounts, _ = await make_user(session, "far")
    renewable, renewable_accounts, _ = await make_user(session, "renewable")
    banned, banned_accounts, _ = await make_user(session, "banned")
    soon_accounts["fa"].expires_at = NOW + timedelta(days=5)
    gone_accounts["fa"].expires_at = NOW - timedelta(days=2)
    for accounts in (soon_accounts, gone_accounts, far_accounts, renewable_accounts, banned_accounts):
        accounts["fb"].expires_at = NOW + timedelta(days=200)
    far_accounts["fa"].expires_at = NOW + timedelta(days=200)
    renewable_accounts["fa"].expires_at = NOW + timedelta(days=200)
    renewable_accounts["fb"].refresh_token = "renews"
    renewable_accounts["fb"].expires_at = NOW + timedelta(days=1)  # ends soon, but we renew it ourselves
    banned_accounts["fa"].expires_at = NOW + timedelta(days=1)
    banned.banned_at = NOW
    await session.flush()

    found = (await call(token, "GET", "/api/admin/accounts/expiring?days=30&limit=500")).json()
    mine = {a["user_id"]: a for a in found if a["user_id"] in {soon.id, gone.id, far.id, renewable.id, banned.id}}
    assert {u: (a["state"], a["provider"], a["is_master"]) for u, a in mine.items()} == {
        soon.id: ("expiring", "fa", True),
        gone.id: ("expired", "fa", True),
    }
    order = [a["user_id"] for a in found if a["user_id"] in mine]
    assert order == [gone.id, soon.id]  # soonest first
    wider = (await call(token, "GET", "/api/admin/accounts/expiring?days=365&limit=500")).json()
    assert far.id in {a["user_id"] for a in wider}
    assert (await call(token, "GET", "/api/admin/accounts/expiring?days=0")).status_code == 422
