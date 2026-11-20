"""Cross-reference conflicts are remembered once, reviewed by an admin, and exported as a report."""

import logging

import pytest
from sqlalchemy import select

from anisync import moderation, permissions
from anisync.mapping import IdMapper
from anisync.models import AdminAction, IdConflict, MediaExternalId
from tests.test_ownership import browser
from tests.test_ownership import make_user as make_browser_user

pytestmark = pytest.mark.usefixtures("fakes", "client")


@pytest.fixture
async def admin(session):
    user, _, token = await make_browser_user(session, "root")
    user.role = "admin"
    await session.flush()
    return user, token


async def call(token, method, url, **kwargs):
    async with browser(token) as c:
        return await c.request(method, url, **kwargs)


async def make_conflicts(session):
    """One of each kind: a title whose provider id differs from the dataset's, and two titles claimed to be one."""
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "a1", "fb": "b1"}, title="Alpha")
        await mapper.remember({"fa": "a2", "fb": "b2"}, title="Beta")
        await mapper.remember({"fa": "a1", "fb": "b9"})  # alpha again, with another fb id: already_has
        await mapper.remember({"fa": "a1", "fb": "b2"})  # alpha's fa id with beta's fb id: disagree


async def conflicts(session) -> dict[str, IdConflict]:
    return {c.kind: c for c in (await session.scalars(select(IdConflict))).all()}


# ---- recording --------------------------------------------------------------------------------------------------


async def test_both_kinds_are_recorded_with_what_was_claimed_and_which_titles_are_involved(session):
    await make_conflicts(session)
    found = await conflicts(session)
    assert set(found) == {"already_has", "disagree"}
    assert found["already_has"].ids == {"fa": "a1", "fb": "b9"} and len(found["already_has"].media_ids) == 1
    assert found["disagree"].ids == {"fa": "a1", "fb": "b2"} and len(found["disagree"].media_ids) == 2
    assert all(c.status == "open" and c.seen == 1 for c in found.values())


async def test_the_datasets_answer_is_what_stays(session):
    await make_conflicts(session)
    async with IdMapper(session) as mapper:
        assert await mapper.resolve("fa", "fb", ["a1", "a2"]) == {"a1": "b1", "a2": "b2"}  # nothing was rewritten
    pairs = {(e.provider, e.external_id) for e in (await session.scalars(select(MediaExternalId))).all()}
    assert ("fb", "b9") not in pairs


async def test_a_consistent_hint_is_not_a_conflict(session):
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "a1", "fb": "b1"})
        await mapper.remember({"fa": "a1", "fb": "b1"})
    assert (await session.scalars(select(IdConflict))).all() == []


async def test_seeing_the_same_conflict_again_counts_it_and_logs_it_only_the_first_time(session, caplog):
    caplog.set_level(logging.DEBUG, logger="anisync.mapping")
    async with IdMapper(session) as mapper:
        await mapper.remember({"fa": "a1", "fb": "b1"})
        for _ in range(3):
            await mapper.remember({"fa": "a1", "fb": "b9"})
    [row] = (await session.scalars(select(IdConflict))).all()
    assert row.seen == 3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "id conflict" in r.message]
    assert len(warnings) == 1


# ---- reviewing --------------------------------------------------------------------------------------------------


async def test_the_list_shows_titles_links_counts_and_filters_by_status(session, admin):
    _, token = admin
    await make_conflicts(session)
    body = (await call(token, "GET", "/api/admin/titles/conflicts")).json()
    assert body["counts"] == {"open": 2, "kept": 0} and len(body["items"]) == 2
    already = next(i for i in body["items"] if i["kind"] == "already_has")
    assert already["titles"] == ["Alpha"] and already["ids"] == {"fa": "a1", "fb": "b9"}
    assert already["links"] == {}  # the fake providers have no title pages
    first = body["items"][0]["id"]
    kept = await call(
        token, "PUT", f"/api/admin/titles/conflicts/{first}", json={"status": "kept", "note": " dataset is right "}
    )
    assert kept.status_code == 200 and kept.json()["status"] == "kept" and kept.json()["note"] == "dataset is right"
    assert [i["status"] for i in (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"]] == ["open"]
    kept_only = (await call(token, "GET", "/api/admin/titles/conflicts", params={"status": "kept"})).json()
    assert [i["id"] for i in kept_only["items"]] == [first] and kept_only["counts"] == {"open": 1, "kept": 1}
    assert len((await call(token, "GET", "/api/admin/titles/conflicts", params={"status": "all"})).json()["items"]) == 2


async def test_a_decision_can_be_reopened_and_is_written_to_the_audit_log(session, admin):
    user, token = admin
    await make_conflicts(session)
    cid = (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"][0]["id"]
    await call(token, "PUT", f"/api/admin/titles/conflicts/{cid}", json={"status": "kept"})
    reopened = await call(token, "PUT", f"/api/admin/titles/conflicts/{cid}", json={"status": "open"})
    assert reopened.json()["status"] == "open" and reopened.json()["resolved_at"] is None
    actions = [a.action for a in (await session.scalars(select(AdminAction).order_by(AdminAction.id))).all()]
    assert actions == ["id_conflict.keep", "id_conflict.reopen"]


async def test_a_new_sighting_of_a_kept_conflict_keeps_the_decision(session, admin):
    _, token = admin
    await make_conflicts(session)
    cid = (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"][0]["id"]
    await call(token, "PUT", f"/api/admin/titles/conflicts/{cid}", json={"status": "kept"})
    await make_conflicts(session)  # the same hints again at the next full read
    row = await session.get(IdConflict, cid)
    await session.refresh(row)
    assert row.status == "kept" and row.seen == 2


async def test_unknown_conflicts_and_bad_decisions_are_refused(session, admin):
    _, token = admin
    assert (await call(token, "PUT", "/api/admin/titles/conflicts/999999", json={"status": "kept"})).status_code == 404
    await make_conflicts(session)
    cid = (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"][0]["id"]
    assert (await call(token, "PUT", f"/api/admin/titles/conflicts/{cid}", json={"status": "fixed"})).status_code == 422


async def test_only_admins_with_the_link_permission_can_see_or_decide(session, admin):
    _, token = admin
    await make_conflicts(session)
    cid = (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"][0]["id"]
    plain, _, plain_token = await make_browser_user(session, "plain")
    reader, _, reader_token = await make_browser_user(session, "reader")
    permissions.ROLE_PERMISSIONS["reader"] = frozenset({permissions.STATS_READ})
    reader.role = "reader"
    await session.flush()
    try:
        for who in (plain_token, reader_token):
            assert (await call(who, "GET", "/api/admin/titles/conflicts")).status_code in (401, 403)
            assert (
                await call(who, "PUT", f"/api/admin/titles/conflicts/{cid}", json={"status": "kept"})
            ).status_code in (401, 403)
            assert (await call(who, "GET", "/api/admin/titles/conflicts/report.csv")).status_code in (401, 403)
    finally:
        permissions.ROLE_PERMISSIONS.pop("reader", None)
    async with browser("") as anon:
        assert (await anon.get("/api/admin/titles/conflicts")).status_code == 401


# ---- the report -------------------------------------------------------------------------------------------------


async def test_the_report_is_a_csv_with_the_claim_the_titles_and_the_note(session, admin):
    _, token = admin
    await make_conflicts(session)
    cid = next(
        i["id"]
        for i in (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"]
        if i["kind"] == "disagree"
    )
    await call(
        token,
        "PUT",
        f"/api/admin/titles/conflicts/{cid}",
        json={"status": "kept", "note": "kitsu has the wrong anilist id"},
    )
    response = await call(token, "GET", "/api/admin/titles/conflicts/report.csv", params={"status": "all"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("kind,status,seen") and len(lines) == 3
    disagree = next(line for line in lines if line.startswith("disagree"))
    assert "fa=a1, fb=b2" in disagree and "kitsu has the wrong anilist id" in disagree and "Alpha | Beta" in disagree


async def test_the_report_cannot_be_used_to_smuggle_a_spreadsheet_formula(session, admin):
    _, token = admin
    await make_conflicts(session)
    cid = (await call(token, "GET", "/api/admin/titles/conflicts")).json()["items"][0]["id"]
    await call(
        token,
        "PUT",
        f"/api/admin/titles/conflicts/{cid}",
        json={"status": "kept", "note": '=HYPERLINK("http://evil","x")'},
    )
    text = (await call(token, "GET", "/api/admin/titles/conflicts/report.csv", params={"status": "all"})).text
    assert ",'=HYPERLINK" in text or "'=HYPERLINK" in text
    assert ",=HYPERLINK" not in text


async def test_downloading_the_report_is_recorded(session, admin):
    _, token = admin
    await make_conflicts(session)
    await call(token, "GET", "/api/admin/titles/conflicts/report.csv")
    assert "id_conflict.report" in [a.action for a in (await session.scalars(select(AdminAction))).all()]
    assert moderation  # (the audit helper is the one the endpoints use)
