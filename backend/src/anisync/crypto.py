"""Encryption of provider tokens at rest, with key rotation.

`TOKEN_ENCRYPTION_KEYS` is a comma-separated list: the first key encrypts, every key can decrypt. To rotate:
generate a key (`anisync generate-key`), put it in front (`new,old`), deploy, run `anisync rotate-keys` (re-encrypts
every stored token with the new key), then drop the old key. A single `TOKEN_ENCRYPTION_KEY` still works for dev.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from anisync.config import get_settings

__all__ = ["InvalidToken", "decrypt", "encrypt", "needs_rotation", "rotate"]


def _keys() -> list[str]:
    settings = get_settings()
    keys = [k.strip() for k in settings.token_encryption_keys.split(",") if k.strip()]
    if not keys and settings.token_encryption_key:
        keys = [settings.token_encryption_key]
    if not keys:
        raise RuntimeError("TOKEN_ENCRYPTION_KEYS is not set (see backend/.env.example; `anisync generate-key`)")
    return keys


@lru_cache
def _fernet() -> MultiFernet:
    try:
        return MultiFernet([Fernet(k.encode()) for k in _keys()])
    except ValueError:  # never echo the key itself
        raise RuntimeError("TOKEN_ENCRYPTION_KEYS contains an invalid key (each must be a Fernet key)") from None


def encrypt(value: str | None) -> str | None:
    return None if value is None else _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    return None if value is None else _fernet().decrypt(value.encode()).decode()


def needs_rotation(value: str) -> bool:
    """True unless `value` is already encrypted with the primary (first) key. Raises InvalidToken for unknown keys."""
    try:
        Fernet(_keys()[0].encode()).decrypt(value.encode())
        return False
    except InvalidToken:
        _fernet().decrypt(value.encode())  # raises InvalidToken if no configured key can read it
        return True


def rotate(value: str) -> str:
    """Re-encrypt `value` with the primary key."""
    return _fernet().rotate(value.encode()).decode()
