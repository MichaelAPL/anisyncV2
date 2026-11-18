"""Token encryption and key rotation."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from anisync import crypto
from anisync.cli import main, rotate_all_tokens
from anisync.config import get_settings
from anisync.models import LinkedAccount, User


@pytest.fixture
def keys(monkeypatch):
    """Configure TOKEN_ENCRYPTION_KEYS for one test."""

    def configure(*key_list: str, single: str = "") -> None:
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEYS", ",".join(key_list))
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", single)
        get_settings.cache_clear()
        crypto._fernet.cache_clear()

    yield configure
    monkeypatch.undo()
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


def key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip_and_none(keys):
    keys(key())
    assert crypto.decrypt(crypto.encrypt("secret")) == "secret"
    assert crypto.encrypt(None) is None and crypto.decrypt(None) is None
    assert "secret" not in crypto.encrypt("secret")


def test_a_single_key_setting_still_works(keys):
    k = key()
    keys(single=k)
    assert Fernet(k.encode()).decrypt(crypto.encrypt("x").encode()) == b"x"


def test_the_first_key_encrypts_and_every_key_decrypts(keys):
    new, old = key(), key()
    keys(new, old)
    from_old = Fernet(old.encode()).encrypt(b"legacy").decode()
    assert crypto.decrypt(from_old) == "legacy"
    assert Fernet(new.encode()).decrypt(crypto.encrypt("fresh").encode()) == b"fresh"


def test_rotation_moves_a_token_to_the_primary_key(keys):
    new, old = key(), key()
    keys(new, old)
    token = Fernet(old.encode()).encrypt(b"legacy").decode()
    assert crypto.needs_rotation(token)
    rotated = crypto.rotate(token)
    assert not crypto.needs_rotation(rotated)
    assert Fernet(new.encode()).decrypt(rotated.encode()) == b"legacy"
    with pytest.raises(InvalidToken):
        Fernet(old.encode()).decrypt(rotated.encode())  # the old key is no longer needed


def test_unreadable_tokens_are_reported_not_guessed(keys):
    keys(key())
    with pytest.raises(InvalidToken):
        crypto.needs_rotation(Fernet(key().encode()).encrypt(b"x").decode())


def test_bad_configuration_fails_loudly_without_echoing_keys(keys):
    keys()
    with pytest.raises(RuntimeError, match="not set"):
        crypto.encrypt("x")
    keys("definitely-not-a-key")
    with pytest.raises(RuntimeError) as exc:
        crypto.encrypt("x")
    assert "definitely-not-a-key" not in str(exc.value) and "definitely-not-a-key" not in repr(exc.value.__cause__)


async def add_account(session, provider, remote_id, access, refresh):
    user = User(display_name=remote_id)
    session.add(user)
    account = LinkedAccount(user=user, provider=provider, remote_user_id=remote_id, remote_username=remote_id)
    account._access_token, account._refresh_token = access, refresh
    session.add(account)
    await session.flush()
    return account


async def test_rotate_all_tokens(session, keys):
    new, old = key(), key()
    keys(new, old)
    enc = lambda k, v: Fernet(k.encode()).encrypt(v.encode()).decode()  # noqa: E731
    legacy = await add_account(session, "mal", "1", enc(old, "a1"), enc(old, "r1"))
    current = await add_account(session, "anilist", "2", enc(new, "a2"), None)  # AniList has no refresh token

    first = await rotate_all_tokens(session)
    assert (first.tokens, first.rotated, first.failed) == (3, 2, 0)  # the current one was left alone
    keys(new)  # now the old key can be dropped
    await session.refresh(legacy)
    assert (legacy.access_token, legacy.refresh_token) == ("a1", "r1")
    await session.refresh(current)
    assert current.access_token == "a2"

    second = await rotate_all_tokens(session)
    assert (second.rotated, second.failed) == (0, 0)  # idempotent


async def test_rotation_counts_tokens_it_cannot_read(session, keys):
    keys(key())
    await add_account(session, "mal", "1", Fernet(key().encode()).encrypt(b"x").decode(), None)
    result = await rotate_all_tokens(session)
    assert (result.tokens, result.rotated, result.failed) == (1, 0, 1)


def test_generate_key_prints_a_usable_key(capsys):
    assert main(["generate-key"]) == 0
    Fernet(capsys.readouterr().out.strip().encode())  # raises if it isn't a valid key
