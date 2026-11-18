"""Checks for the test infrastructure itself: DB isolation and the API client."""

from sqlalchemy import func, select

from anisync.models import User


async def test_commits_are_rolled_back_after_each_test_part_1(session):
    session.add(User(display_name="isolated"))
    await session.commit()  # only releases a savepoint
    assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_commits_are_rolled_back_after_each_test_part_2(session):
    assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_api_client_uses_the_test_session(client, session):
    assert (await client.get("/api/health")).json() == {"ok": True}
    assert (await client.get("/api/me")).status_code == 401
