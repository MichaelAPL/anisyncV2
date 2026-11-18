from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from anisync.models import LinkedAccount, ListSnapshot, Media, MediaExternalId, User


async def make_account(session, provider="mal", remote_id="42") -> LinkedAccount:
    user = User(display_name="kaito")
    session.add(user)
    await session.flush()
    account = LinkedAccount(user_id=user.id, provider=provider, remote_user_id=remote_id, remote_username="kaito")
    account.access_token = "secret-access"
    account.refresh_token = "secret-refresh"
    session.add(account)
    await session.flush()
    return account


async def test_tokens_are_encrypted_at_rest(session):
    account = await make_account(session)
    stored = (await session.execute(text("select access_token, refresh_token from linked_accounts"))).one()
    assert "secret" not in stored.access_token and "secret" not in stored.refresh_token
    await session.refresh(account)
    assert account.access_token == "secret-access" and account.refresh_token == "secret-refresh"


async def test_provider_is_stored_as_a_plain_string(session, acme):
    account = await make_account(session, provider="acme")
    assert (await session.scalar(text("select provider from linked_accounts"))) == "acme"
    assert account.provider == "acme"


async def test_one_account_per_provider_identity(session):
    await make_account(session, "mal", "42")
    with pytest.raises(IntegrityError):
        await make_account(session, "mal", "42")


async def test_same_remote_id_on_different_providers_is_fine(session):
    await make_account(session, "mal", "42")
    await make_account(session, "anilist", "42")


async def test_external_ids_are_unique_per_provider_and_per_media(session):
    media_a, media_b = Media(title="A"), Media(title="B")
    session.add_all([media_a, media_b])
    await session.flush()
    session.add(MediaExternalId(media_id=media_a.id, provider="mal", external_id="1"))
    await session.flush()
    for media_id, external_id in [(media_b.id, "1"), (media_a.id, "2")]:  # same ID twice / second ID for one provider
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(MediaExternalId(media_id=media_id, provider="mal", external_id=external_id))
                await session.flush()


async def test_external_id_provider_is_validated(session):
    from anisync.providers import UnknownProvider

    with pytest.raises(UnknownProvider):
        MediaExternalId(media_id=1, provider="nope", external_id="1")


async def test_deleting_media_removes_its_external_ids(session):
    media = Media(title="A")
    session.add(media)
    await session.flush()
    session.add(MediaExternalId(media_id=media.id, provider="mal", external_id="1"))
    await session.flush()
    await session.delete(media)
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(MediaExternalId)) == 0


async def test_snapshots_belong_to_the_account_and_survive_unmapped_titles(session):
    account = await make_account(session)
    account_id = account.id
    media = Media(title="Cowboy Bebop")
    session.add(media)
    await session.flush()
    session.add_all(
        [
            ListSnapshot(
                account_id=account.id,
                external_id="1",
                media_id=media.id,
                status="watching",
                progress=3,
                score=80,
                remote_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                last_written_hash="abc",
                title="Cowboy Bebop",
                external_ids={"mal": "1"},
            ),
            ListSnapshot(account_id=account.id, external_id="999", status="planning", progress=0),  # not mapped yet
        ]
    )
    await session.flush()
    count = select(func.count()).select_from(ListSnapshot)
    assert await session.scalar(count) == 2

    await session.delete(media)  # keeps the snapshot, just unmaps it
    await session.flush()
    session.expire_all()
    assert await session.scalar(count) == 2
    assert (await session.get(ListSnapshot, (account_id, "1"))).media_id is None

    await session.delete(account)  # deleting the account deletes its snapshots
    await session.flush()
    assert await session.scalar(count) == 0
