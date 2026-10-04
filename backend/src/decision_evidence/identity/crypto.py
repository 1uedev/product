from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from decision_evidence.config import get_settings


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if key is None:
        raise RuntimeError("SECRET_KEY is not configured")
    return Fernet(key.get_secret_value().encode())


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(blob: bytes) -> str | None:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken:
        return None
