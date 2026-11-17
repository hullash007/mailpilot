"""Token encryption at rest (Fernet / AES-128-CBC + HMAC).

Read-only Gmail tokens still expose the whole mailbox, so credentials are
encrypted before they touch the database. The key comes from
TOKEN_ENCRYPTION_KEY; without it, storing or reading tokens fails closed.
Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import json

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


class TokenEncryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenEncryptionError(
            "TOKEN_ENCRYPTION_KEY is not set; refusing to store or read OAuth tokens."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenEncryptionError(f"TOKEN_ENCRYPTION_KEY is invalid: {exc}")


def encrypt_tokens(payload: dict) -> str:
    return _fernet().encrypt(json.dumps(payload).encode()).decode()


def decrypt_tokens(blob: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken:
        raise TokenEncryptionError("Stored OAuth tokens cannot be decrypted with this key.")
