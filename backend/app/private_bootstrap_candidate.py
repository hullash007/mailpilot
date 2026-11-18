"""Explicit source-only private bootstrap. No environment/driver auto-discovery.

Production installation, ownership, manifests and launch policy belong to an
external reviewed release operation, not this constructor. No startup DDL.
"""
import base64
from dataclasses import dataclass, field
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text
from .account_controls_candidate import create_app, AuthConfig
from .account_control_models import ControlBase
from .action_models import ActionBase
from .database import Base
from .cipher_adapter import prepare_cipher_storage
from .integrations.gmail.identity_adapter import google_identity_adapter
from .integrations.gmail.oauth import build_explicit_flow
from .services.crypto import TokenCipher
from .services.oauth_state import StateSigner

SCHEMA_VERSION = 1
KDF_SALT = b'mailpilot-storage-kdf-v1'
KDF_INFO = b'mailpilot/sqlcipher4/raw-key/schema-v1/private-hermes-profile-v1'


class PrivateBootstrapUnavailable(RuntimeError):
    pass


def database_key(token_key):
    """Coupled custody/recovery: this is NOT an independent encryption secret."""
    if type(token_key) is not bytes or len(token_key) != 44:
        raise ValueError('An explicit valid token encryption seed is required.')
    try:
        raw = base64.b64decode(token_key, altchars=b'-_', validate=True)
        if len(raw) != 32 or base64.urlsafe_b64encode(raw) != token_key:
            raise ValueError()
    except Exception:
        raise ValueError('An explicit valid token encryption seed is required.') from None
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=KDF_SALT, info=KDF_INFO).derive(raw)


def required_schema():
    return {table.name: tuple(table.columns.keys()) for metadata in
        (Base.metadata, ActionBase.metadata, ControlBase.metadata) for table in metadata.tables.values()}


@dataclass(frozen=True)
class PrivateConfig:
    auth: AuthConfig
    database_path: Path
    schema_version: int
    token_key: bytes = field(repr=False)
    state_key: bytes = field(repr=False)

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION
                or not isinstance(self.auth, AuthConfig) or not self.auth.allowed_email
                or not isinstance(self.database_path, Path) or not self.database_path.is_absolute()
                or type(self.state_key) is not bytes or len(self.state_key) < 32):
            raise ValueError('Explicit private pilot configuration is required.')
        database_key(self.token_key)


def create_private_app(*, config, reviewed_driver, identity_adapter=None, flow_factory=build_explicit_flow):
    """No ambient settings, no scope/audience change, no import or AI route.

    reviewed_driver must already be selected through a verified immutable release
    loader. This function does not import a DBAPI by name or verify wheel provenance.
    Adapter override is a trusted fixture injection, never browser input.
    """
    if not isinstance(config, PrivateConfig) or reviewed_driver is None:
        raise ValueError('Explicit private pilot dependencies are required.')
    storage = None
    try:
        if config.database_path.stat().st_mode & 0o077:
            raise ValueError()
        key = database_key(config.token_key)
        storage = prepare_cipher_storage(driver_module=reviewed_driver,
            database_path=config.database_path, key_provider=lambda: key,
            expected_user_version=config.schema_version, required_schema=required_schema(), require_foreign_keys=True)
        # Eager read-only validation before returning an application. No DDL.
        with storage.session() as db:
            db.execute(text('SELECT 1'))
            # Only qualified singleton control clock; unexpected values fail closed.
            from .account_control_models import ControlClock
            rows = db.query(ControlClock).all()
            if len(rows) != 1 or rows[0].id != 1 or rows[0].generation < 0:
                raise ValueError()
        signer = StateSigner(config.state_key)
        return create_app(storage=storage, config=config.auth, cipher=TokenCipher(config.token_key),
            state_signer=signer, flow_factory=flow_factory,
            exchange_identity=identity_adapter if identity_adapter is not None else google_identity_adapter(state_signer=signer))
    except Exception:
        if storage is not None:
            storage.close()
        raise PrivateBootstrapUnavailable('Private workspace configuration or storage is unavailable.') from None
