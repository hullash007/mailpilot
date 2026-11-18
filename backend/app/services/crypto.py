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


class TokenCipher:
    """Explicit Fernet service. Does not choose or bind an account."""
    def __init__(self, key: bytes):
        if type(key) is not bytes or not key:
            raise TokenEncryptionError("An explicit token encryption key is required.")
        try:
            self._cipher = Fernet(key)
        except (ValueError, TypeError):
            raise TokenEncryptionError("Token encryption key is invalid.") from None

    def encrypt(self, payload: dict) -> str:
        return self._cipher.encrypt(json.dumps(payload).encode()).decode()

    def decrypt(self, blob: str) -> dict:
        try:
            return json.loads(self._cipher.decrypt(blob.encode()))
        except InvalidToken:
            raise TokenEncryptionError("Stored OAuth tokens cannot be decrypted with this key.") from None


def _legacy_cipher() -> TokenCipher:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenEncryptionError(
            "TOKEN_ENCRYPTION_KEY is not set; refusing to store or read OAuth tokens."
        )
    return TokenCipher(key.encode())


def _fernet() -> Fernet:
    return _legacy_cipher()._cipher


def encrypt_tokens(payload: dict) -> str:
    return _legacy_cipher().encrypt(payload)


def decrypt_tokens(blob: str) -> dict:
    return _legacy_cipher().decrypt(blob)
