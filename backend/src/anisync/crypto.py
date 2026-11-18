from functools import lru_cache

from cryptography.fernet import Fernet

from anisync.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set (see backend/.env.example)")
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    return None if value is None else _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    return None if value is None else _fernet().decrypt(value.encode()).decode()
