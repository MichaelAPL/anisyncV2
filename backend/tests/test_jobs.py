from procrastinate.testing import InMemoryConnector

from anisync import jobs


async def test_only_one_pending_sync_per_user():
    connector = InMemoryConnector()
    with jobs.app.replace_connector(connector):
        assert await jobs.enqueue_user_sync(7, "manual") is True
        assert await jobs.enqueue_user_sync(7, "manual") is False  # already waiting
        assert await jobs.enqueue_user_sync(8, "manual") is True  # other users are independent
    assert sorted(j["args"]["user_id"] for j in connector.jobs.values()) == [7, 8]


def test_scheduled_and_queued_tasks_are_registered():
    names = set(jobs.app.tasks)
    assert {
        "anisync.jobs.sync_tick",
        "anisync.jobs.seed_ids",
        "anisync.jobs.sync_user",
        "anisync.jobs.push_entry",
    } <= names
