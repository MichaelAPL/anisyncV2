"""Admin view of the offline ID dataset (Phase 4.12)."""

from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select

from anisync import jobs, permissions
from anisync.models import AdminAction, DatasetImport, Media, MediaExternalId
from tests.test_ownership import browser, make_user

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


async def call(token, method, url, **kwargs):
    async with browser(token) as c:
        return await c.request(method, url, **kwargs)


def coverage(body, provider):
    return next(
        (c for c in body["coverage"] if c["provider"] == provider), {"ids": 0, "seed": 0, "learned": 0, "manual": 0}
    )


async def test_the_status_shows_imports_newest_first_and_coverage_by_source(session, admin):
    _, token = admin
    before = (await call(token, "GET", "/api/admin/dataset")).json()
    session.add_all(
        [
            DatasetImport(
                dataset="d", version="v1", status="ok", stats={"entries": 5}, started_at=NOW - timedelta(days=7)
            ),
            DatasetImport(dataset="d", version="v2", status="error", error="boom", stats={}, started_at=NOW),
        ]
    )
    media = [Media(title=f"t{i}") for i in range(3)]
    session.add_all(media)
    await session.flush()
    for i, (provider, source) in enumerate([("fa", "seed"), ("fa", "seed"), ("fa", "manual"), ("fb", "learned")]):
        session.add(MediaExternalId(media_id=media[i % 3].id, provider=provider, external_id=f"x{i}", source=source))
    await session.flush()

    body = (await call(token, "GET", "/api/admin/dataset")).json()
    assert [i["version"] for i in body["imports"][:2]] == ["v2", "v1"]
    assert (body["imports"][0]["status"], body["imports"][0]["error"]) == ("error", "boom")
    assert body["imports"][1]["stats"] == {"entries": 5}
    assert body["media"] - before["media"] == 3
    fa, fa_before = coverage(body, "fa"), coverage(before, "fa")
    assert {k: fa[k] - fa_before[k] for k in ("ids", "seed", "learned", "manual")} == {
        "ids": 3,
        "seed": 2,
        "learned": 0,
        "manual": 1,
    }
    fb, fb_before = coverage(body, "fb"), coverage(before, "fb")
    assert (fb["ids"] - fb_before["ids"], fb["learned"] - fb_before["learned"]) == (1, 1)
    assert body["source_url"].startswith("https://")


async def test_an_import_can_be_started_and_is_audited(session, admin, queue):
    root, token = admin
    response = await call(token, "POST", "/api/admin/dataset/import", json={"force": True})
    assert response.json() == {"queued": True}
    [job] = queue.jobs.values()
    assert job["task_name"] == "anisync.jobs.import_dataset" and job["args"] == {"force": True}
    assert (await call(token, "POST", "/api/admin/dataset/import", json={})).json() == {
        "queued": False
    }  # already waiting
    rows = (await session.scalars(select(AdminAction).where(AdminAction.action == "dataset.import"))).all()
    assert [r.details for r in rows] == [{"force": True, "queued": True}, {"force": False, "queued": False}] and root.id


async def test_a_second_import_is_refused_while_one_is_running(session, admin, queue):
    _, token = admin
    session.add(DatasetImport(dataset="d", status="running", stats={}))
    await session.flush()
    assert (await call(token, "POST", "/api/admin/dataset/import", json={})).status_code == 409
    assert not queue.jobs


async def test_reading_needs_stats_read_and_importing_needs_providers_manage(session, client):
    permissions.ROLE_PERMISSIONS["viewer"] = frozenset({permissions.STATS_READ})
    permissions.ROLE_PERMISSIONS["other"] = frozenset({permissions.USERS_READ})
    try:
        viewer, _, viewer_token = await make_user(session, "viewer")
        other, _, other_token = await make_user(session, "other")
        viewer.role, other.role = "viewer", "other"
        await session.flush()
        assert (await client.get("/api/admin/dataset")).status_code == 401
        assert (await call(other_token, "GET", "/api/admin/dataset")).status_code == 403
        assert (await call(viewer_token, "GET", "/api/admin/dataset")).status_code == 200
        assert (await call(viewer_token, "POST", "/api/admin/dataset/import", json={})).status_code == 403
    finally:
        del permissions.ROLE_PERMISSIONS["viewer"], permissions.ROLE_PERMISSIONS["other"]
