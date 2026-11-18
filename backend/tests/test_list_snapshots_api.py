"""The list endpoints serve from snapshots: provider requests happen on the first read, on refresh, and on writes."""

from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector

from anisync import jobs
from tests.fakes import FakeClient
from tests.test_ownership import browser, make_user
from tests.test_sync_snapshots import requests, title


def put(*entries) -> None:
    FakeClient.REMOTE.setdefault(("fa", "fa-al"), {}).update({e.media_id: e for e in entries})


@pytest.fixture
async def alice(client, session, fakes):
    user, accounts, token = await make_user(session, "al")
    put(title(1, progress=3), title(2, progress=4))
    async with browser(token) as c:
        yield c, user, accounts


async def test_the_list_is_read_from_the_provider_once_and_then_served_from_the_snapshot(alice):
    c, *_ = alice
    first = (await c.get("/api/list")).json()
    assert requests() == [("fa", "full")]
    assert [e["title"] for e in first] == ["Title 1", "Title 2"] and first[0]["progress"] == 3
    for _ in range(3):
        assert (await c.get("/api/list")).json() == first
    assert requests() == []  # no more provider requests, however often it is opened


async def test_refresh_picks_up_edits_made_on_the_masters_own_site_with_one_request(alice):
    c, *_ = alice
    await c.get("/api/list")
    requests()
    put(title(2, progress=40))  # edited on the provider's website
    assert (await c.post("/api/list/refresh")).json() == {"mode": "incremental", "changed": 1}
    assert requests() == [("fa", "since")]
    assert {e["media_id"]: e["progress"] for e in (await c.get("/api/list")).json()} == {"a1": 3, "a2": 40}
    assert requests() == []


async def test_refreshing_with_nothing_new_changes_nothing(alice):
    c, *_ = alice
    await c.get("/api/list")
    requests()
    assert (await c.post("/api/list/refresh")).json() == {"mode": "incremental", "changed": 0}
    assert requests() == [("fa", "since")]


async def test_a_first_refresh_reads_the_whole_list(alice):
    c, *_ = alice
    assert (await c.post("/api/list/refresh")).json() == {"mode": "full", "changed": 2}
    assert requests() == [("fa", "full")]


async def test_refresh_is_rate_limited(alice):
    c, *_ = alice
    statuses = [(await c.post("/api/list/refresh")).status_code for _ in range(7)]
    assert statuses == [200] * 6 + [429]


async def test_editing_keeps_the_snapshot_current_without_reading_the_provider(alice):
    c, *_ = alice
    await c.get("/api/list")
    requests()
    with jobs.app.replace_connector(InMemoryConnector()):
        assert (
            await c.put("/api/list/a1", json={"status": "completed", "progress": 12, "score": 90})
        ).status_code == 200
    assert requests() == [("fa", "save")]
    entry = next(e for e in (await c.get("/api/list")).json() if e["media_id"] == "a1")
    assert (entry["status"], entry["progress"], entry["score"]) == ("completed", 12, 90)
    assert entry["title"] == "Title 1" and entry["external_ids"] == {"fa": "a1", "fb": "b1"}  # display data kept
    assert requests() == []


async def test_removing_a_title_removes_it_from_the_snapshot(alice):
    c, *_ = alice
    await c.get("/api/list")
    requests()
    with jobs.app.replace_connector(InMemoryConnector()):
        assert (await c.delete("/api/list/a2")).json() == {"ok": True}
    assert [e["media_id"] for e in (await c.get("/api/list")).json()] == ["a1"]
    assert requests() == [("fa", "delete")]


async def test_me_says_when_the_next_scheduled_sync_runs(alice):
    c, user, accounts = alice
    me = (await c.get("/api/me")).json()
    when = datetime.fromisoformat(me["next_sync_at"])
    assert when.hour == user.id % 24 and when.minute == 0
    assert datetime.now(UTC) < when <= datetime.now(UTC) + timedelta(hours=24, minutes=1)
    await c.delete(f"/api/accounts/{accounts['fa'].id}")  # unlinking the master leaves no master...
    assert (await c.get("/api/me")).json()["next_sync_at"] is None  # ...so nothing is scheduled
