"""Provider kill switch, paused scheduled syncs, maintenance mode and the site banner (Phase 4.6, 4.7, 4.9)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select

from anisync import jobs, permissions, sessions, switches, sync
from anisync.models import AdminAction, ProviderStat, SyncRun
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user
from tests.test_sync import make_user as make_sync_user
from tests.test_sync import put, remote, title

pytestmark = pytest.mark.usefixtures("fakes", "client")
NOW = datetime.now(UTC)


@pytest.fixture
async def admin(session):
    user, _, token = await make_user(session, "root")
    user.role = "admin"
    await session.flush()
    return user, token


@pytest.fixture
def queue():
    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector):
        yield connector


@pytest.fixture
def job_session(session, monkeypatch):
    """Background jobs open their own database session; point them at the test's rolled-back one."""

    class Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(jobs, "SessionLocal", lambda: Context())


async def call(token, method, url, **kwargs):
    async with browser(token) as c:
        return await c.request(method, url, **kwargs)


async def actions(session, name):
    return (await session.scalars(select(AdminAction).where(AdminAction.action == name).order_by(AdminAction.id))).all()


# ---- provider kill switch ------------------------------------------------------------------------------------


async def test_a_paused_mirror_is_left_alone_and_catches_up_after_it_is_resumed(session, admin):
    root, token = admin
    user = await make_sync_user(session)
    put("fa", title(1), title(2))
    response = await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "Kitsu is down"})
    assert response.status_code == 200 and response.json()["paused"] is True

    [result] = (await sync.run_sync(session, user.id, "manual")).values()
    assert result["paused"] is True and result["reason"] == "Kitsu is down" and "error" not in result
    assert remote("fb") == {} and ("fb", "save") not in FakeClient.REQUESTS
    run = (await session.scalars(select(SyncRun))).one()
    assert run.status == "ok"  # skipping a paused provider is not a failure

    await call(token, "PUT", "/api/admin/providers/fb", json={"paused": False})
    await sync.run_sync(session, user.id, "manual")
    assert sorted(remote("fb")) == ["b1", "b2"]


async def test_a_paused_master_stops_the_sync_before_anything_is_read(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", title(1))
    await call(token, "PUT", "/api/admin/providers/fa", json={"paused": True, "reason": "AniList outage"})
    assert await sync.run_sync(session, user.id, "manual") == {"skipped": "FA is paused"}
    assert FakeClient.REQUESTS == [] and not (await session.scalars(select(SyncRun))).all()


async def test_edit_pushes_skip_a_paused_mirror_and_the_next_sync_writes_them(session, admin):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", title(1))
    await sync.run_sync(session, user.id, "manual")
    FakeClient.REQUESTS.clear()
    mirror = user.mirrors[0]
    await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "down"})
    changed = replace(title(1), progress=9)
    put("fa", changed)
    await sync.push_entry(session, mirror.id, "fa", "a1", changed)
    assert FakeClient.REQUESTS == [] and remote("fb")["b1"].progress != 9
    await call(token, "PUT", "/api/admin/providers/fb", json={"paused": False})
    await sync.run_sync(session, user.id, "manual", full=True)
    assert remote("fb")["b1"].progress == 9


async def test_editing_the_list_is_refused_while_its_provider_is_paused_but_reading_still_works(session, admin, queue):
    _, admin_token = admin
    user, _, token = await make_user(session, "reader")
    FakeClient.REMOTE[("fa", "fa-reader")] = {}
    put_title = {"status": "watching", "progress": 1}
    assert (await call(token, "GET", "/api/list")).status_code == 200  # first read fills the snapshot
    await call(admin_token, "PUT", "/api/admin/providers/fa", json={"paused": True, "reason": "outage"})
    assert (await call(token, "GET", "/api/list")).status_code == 200  # served from our copy
    refused = await call(token, "PUT", "/api/list/x1", json=put_title)
    assert refused.status_code == 503 and "paused" in refused.json()["detail"]
    assert (await call(token, "POST", "/api/list/refresh")).status_code == 503
    assert user.id


async def test_pausing_needs_a_reason_a_real_provider_and_the_permission(session, admin):
    _, token = admin
    plain, _, plain_token = await make_user(session, "plain")
    assert (await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True})).status_code == 422
    assert (
        await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": " "})
    ).status_code == 422
    assert (await call(token, "PUT", "/api/admin/providers/nope", json={"paused": False})).status_code == 404
    assert (
        await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "<b>x"})
    ).status_code == 422
    assert (
        await call(plain_token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "x"})
    ).status_code == 403
    assert await switches.paused_providers(session) == {}
    assert plain.id


async def test_pausing_and_resuming_are_audited_once_per_change(session, admin):
    root, token = admin
    pause = {"paused": True, "reason": "  Kitsu   is down "}
    await call(token, "PUT", "/api/admin/providers/fb", json=pause)
    await call(token, "PUT", "/api/admin/providers/fb", json=pause)  # no change: not logged again
    await call(token, "PUT", "/api/admin/providers/fb", json={"paused": False, "reason": "ignored"})
    assert [(a.note, a.details["provider"]) for a in await actions(session, "provider.pause")] == [
        ("Kitsu is down", "fb")
    ]
    assert len(await actions(session, "provider.resume")) == 1
    body = (await call(token, "GET", "/api/admin/providers")).json()
    fb = next(p for p in body if p["id"] == "fb")
    assert fb["paused"] is False and fb["paused_reason"] is None and root.id


async def test_provider_health_adds_up_the_request_counters(session, admin):
    _, token = admin
    today = NOW.date()
    session.add_all(
        [
            ProviderStat(day=today, provider="fa", requests=10, rate_limited=1, errors=2, total_ms=2000),
            ProviderStat(
                day=today - timedelta(days=3), provider="fa", requests=30, rate_limited=0, errors=0, total_ms=3000
            ),
            ProviderStat(
                day=today - timedelta(days=9), provider="fa", requests=500, rate_limited=5, errors=5, total_ms=1
            ),
        ]
    )
    await session.flush()
    fa = next(p for p in (await call(token, "GET", "/api/admin/providers")).json() if p["id"] == "fa")
    assert fa["today"] == {"requests": 10, "rate_limited": 1, "errors": 2, "avg_ms": 200}
    assert fa["last_7d"] == {"requests": 40, "rate_limited": 1, "errors": 2, "avg_ms": 125}
    assert fa["accounts"] >= 1


async def test_the_provider_list_tells_the_ui_who_is_paused(session, admin, client):
    _, token = admin
    await call(token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "maintenance on their side"})
    body = (await client.get("/api/providers")).json()  # no sign-in needed: the landing page shows it
    by_id = {p["id"]: p for p in body}
    assert by_id["fb"]["paused"] is True and by_id["fb"]["paused_reason"] == "maintenance on their side"
    assert by_id["fa"]["paused"] is False
    assert {"mal", "anilist", "kitsu"} <= set(by_id)
    mal = by_id["mal"]
    assert (mal["monogram"], mal["auth_kind"], mal["can_sign_in"]) == ("MAL", "oauth_pkce_plain", True)
    assert by_id["kitsu"]["can_sign_in"] is False and set(mal["colors"]) == {"bg", "fg", "dark_bg", "dark_fg"}


# ---- scheduled syncs and background jobs --------------------------------------------------------------------


async def test_the_hourly_tick_obeys_the_pause_switch(session, admin, queue, job_session):
    _, token = admin
    user = await make_sync_user(session)
    stamp = int(NOW.replace(hour=user.id % 24, minute=0, second=0, microsecond=0).timestamp())
    await jobs.sync_tick(stamp)
    assert [j["args"]["user_id"] for j in queue.jobs.values()] == [user.id]
    queue.jobs.clear()

    assert (await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": True})).json()[
        "scheduled_syncs_paused"
    ]
    await jobs.sync_tick(stamp)
    assert not queue.jobs
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": False})
    await jobs.sync_tick(stamp)
    assert len(queue.jobs) == 1


async def test_queued_work_is_skipped_while_paused_and_nothing_is_lost(session, admin, queue, job_session):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", title(1))
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": True})
    assert await jobs.sync_user(user.id, "scheduled") == {"skipped": "paused"}
    await jobs.push_entry(user.mirrors[0].id, "fa", "a1", None)
    assert FakeClient.REQUESTS == [] and not (await session.scalars(select(SyncRun))).all()
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": False})
    await jobs.sync_user(user.id, "scheduled")
    assert sorted(remote("fb")) == ["b1"]


async def test_pausing_syncs_needs_syncs_control_and_is_audited(session, admin):
    root, token = admin
    permissions.ROLE_PERMISSIONS["retrier"] = frozenset({permissions.SYNCS_RETRY})
    try:
        other, _, other_token = await make_user(session, "retrier")
        other.role = "retrier"
        await session.flush()
        assert (await call(other_token, "PUT", "/api/admin/syncs/scheduled", json={"paused": True})).status_code == 403
    finally:
        del permissions.ROLE_PERMISSIONS["retrier"]
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": True})
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": True})
    await call(token, "PUT", "/api/admin/syncs/scheduled", json={"paused": False})
    assert len(await actions(session, "syncs.pause")) == 1 and len(await actions(session, "syncs.resume")) == 1
    assert root.id


# ---- job controls ----------------------------------------------------------------------------------------------


async def test_failed_jobs_can_be_listed_retried_and_cancelled(session, admin, queue):
    root, token = admin
    await jobs.enqueue_user_sync(11, "scheduled")
    await jobs.enqueue_user_sync(12, "manual")
    [first, second] = sorted(queue.jobs)
    queue.jobs[first]["status"] = "failed"
    queue.jobs[second]["status"] = "doing"

    failed = (await call(token, "GET", "/api/admin/syncs/jobs?status=failed")).json()
    assert [(j["id"], j["task"], j["user_id"], j["trigger"]) for j in failed] == [(first, "sync_user", 11, "scheduled")]
    assert [j["id"] for j in (await call(token, "GET", "/api/admin/syncs/jobs?status=doing")).json()] == [second]
    assert (await call(token, "GET", "/api/admin/syncs/jobs?status=weird")).status_code == 422

    assert (await call(token, "POST", f"/api/admin/syncs/jobs/{first}/retry")).json() == {"queued": True}
    assert (await call(token, "POST", f"/api/admin/syncs/jobs/{first}/retry")).json() == {
        "queued": False
    }  # one waiting
    todo = (await call(token, "GET", "/api/admin/syncs/jobs?status=todo")).json()
    assert [(j["user_id"], j["trigger"]) for j in todo] == [(11, "scheduled")]
    assert (await call(token, "POST", f"/api/admin/syncs/jobs/{second}/retry")).status_code == 409  # still running
    assert (await call(token, "POST", "/api/admin/syncs/jobs/999999/retry")).status_code == 404

    assert (await call(token, "POST", f"/api/admin/syncs/jobs/{todo[0]['id']}/cancel")).status_code == 200
    assert (await call(token, "POST", "/api/admin/syncs/jobs/999999/cancel")).status_code == 404
    assert [a.details["job_id"] for a in await actions(session, "job.retry")] == [first, first]
    assert len(await actions(session, "job.cancel")) == 1 and root.id


async def test_job_lists_never_show_list_data(session, admin, queue):
    _, token = admin
    await jobs.push_entry.configure(queueing_lock="x").defer_async(
        account_id=5, master_provider="fa", master_media_id="a1", entry={"title": "SECRET TITLE", "status": "watching"}
    )
    [job_id] = queue.jobs
    queue.jobs[job_id]["status"] = "failed"
    response = await call(token, "GET", "/api/admin/syncs/jobs?status=failed")
    assert response.json()[0]["account_id"] == 5 and "SECRET TITLE" not in response.text


# ---- site banner and maintenance mode --------------------------------------------------------------------------


async def test_the_banner_is_public_and_changes_are_audited(session, admin, client):
    root, token = admin
    fresh = (await client.get("/api/site")).json()
    assert (fresh["maintenance"], fresh["banner_text"], fresh["banner_level"]) == (False, None, "info")
    update = {"maintenance": False, "banner_text": "  Kitsu   syncing is   slow today ", "banner_level": "warning"}
    body = (await call(token, "PUT", "/api/admin/site", json=update)).json()
    assert body["banner_text"] == "Kitsu syncing is slow today" and body["updated_by_name"] == "user-root"
    seen = (await client.get("/api/site")).json()
    assert (seen["banner_text"], seen["banner_level"]) == ("Kitsu syncing is slow today", "warning")
    await call(token, "PUT", "/api/admin/site", json=update)  # unchanged: nothing new in the audit log
    [action] = await actions(session, "site.update")
    assert action.details["changed"] == ["banner_level", "banner_text"]
    cleared = (await call(token, "PUT", "/api/admin/site", json={"maintenance": False, "banner_text": "  "})).json()
    assert cleared["banner_text"] is None and root.id


@pytest.mark.parametrize(
    "bad",
    [
        {"banner_text": "<script>alert(1)</script>"},
        {"maintenance_message": "<b>soon</b>"},
        {"banner_text": "x" * 501},
        {"banner_level": "fatal"},
    ],
)
async def test_the_banner_is_plain_text_and_validated(admin, bad):
    assert (await call(admin[1], "PUT", "/api/admin/site", json={"maintenance": False, **bad})).status_code == 422


async def test_only_site_managers_can_change_the_site(session, client):
    plain, _, plain_token = await make_user(session, "plain")
    assert (await client.put("/api/admin/site", json={"maintenance": True})).status_code == 401
    assert (await call(plain_token, "PUT", "/api/admin/site", json={"maintenance": True})).status_code == 403
    assert (await call(plain_token, "GET", "/api/admin/site")).status_code == 403
    assert (await client.get("/api/site")).json()["maintenance"] is False and plain.id


async def test_maintenance_mode_refuses_changes_but_not_reading_admins_or_sign_out(session, admin, queue):
    root, admin_token = admin
    user, accounts, token = await make_user(session, "u")
    await call(
        admin_token, "PUT", "/api/admin/site", json={"maintenance": True, "maintenance_message": "Back at 14:00"}
    )

    refused = await call(token, "PUT", "/api/me/master", json={"account_id": accounts["fb"].id})
    assert refused.status_code == 503 and refused.json()["detail"] == "Back at 14:00"
    assert refused.headers["retry-after"] == "300"
    assert (await call(token, "POST", "/api/sync")).status_code == 503
    assert (await call(token, "PATCH", "/api/me", json={"display_name": "new"})).status_code == 503
    assert (await call(token, "GET", "/api/me")).status_code == 200  # reading is fine
    assert (await call(token, "GET", "/api/site")).json()["maintenance_message"] == "Back at 14:00"
    assert (
        await call(admin_token, "PUT", "/api/admin/providers/fb", json={"paused": True, "reason": "x"})
    ).status_code == 200
    assert (await call(token, "POST", "/auth/logout")).status_code == 200

    await call(admin_token, "PUT", "/api/admin/site", json={"maintenance": False})
    assert (await call(token, "PATCH", "/api/me", json={"display_name": "new"})).status_code == 401  # logged out above
    other_token = await sessions.start(session, user.id, "again")
    assert (await call(other_token, "PATCH", "/api/me", json={"display_name": "new"})).status_code == 200
    assert root.id


async def test_a_default_maintenance_message_is_used_when_none_is_given(session, admin):
    _, admin_token = admin
    user, _, token = await make_user(session, "u")
    await call(admin_token, "PUT", "/api/admin/site", json={"maintenance": True})
    response = await call(token, "PATCH", "/api/me", json={"display_name": "x"})
    assert response.status_code == 503 and "maintenance" in response.json()["detail"].lower() and user.id


async def test_maintenance_also_pauses_the_background_work(session, admin, queue, job_session):
    _, token = admin
    user = await make_sync_user(session)
    put("fa", title(1))
    await call(token, "PUT", "/api/admin/site", json={"maintenance": True})
    assert await jobs.sync_user(user.id, "scheduled") == {"skipped": "paused"}
    await jobs.sync_tick(int(NOW.replace(hour=user.id % 24, minute=0, second=0, microsecond=0).timestamp()))
    assert not queue.jobs and FakeClient.REQUESTS == []
