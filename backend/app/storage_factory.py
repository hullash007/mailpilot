"""Explicit, injectable storage preparation. No driver import, keys, DDL or globals.

This seam is not a production key delivery or database conversion implementation.
Injected open_existing/apply_key/validate_runtime callbacks are trusted adapters:
open_existing must refuse a missing database, not create one. Runtime validation
must positively identify the configured cipher; schema reads alone are not proof.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


class StorageUnavailable(RuntimeError):
    """Safe error for callers. Adapter errors may contain secret material."""


def _schema_spec(required: Mapping[str, tuple[str, ...]]):
    if not required:
        raise ValueError('An explicit existing schema is required.')
    frozen = {}
    for table, columns in required.items():
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', table) or not columns:
            raise ValueError('Invalid schema specification.')
        if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', col) for col in columns):
            raise ValueError('Invalid schema specification.')
        frozen[table] = tuple(columns)
    return frozen


def _validate_schema(connection, required):
    cursor = connection.cursor()
    try:
        for table, columns in required.items():
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if cursor.fetchone() != (table,):
                raise StorageUnavailable('Storage is unavailable.')
            # Names came only from the restricted immutable schema specification.
            cursor.execute('PRAGMA table_info("' + table + '")')
            present = {row[1] for row in cursor.fetchall()}
            if not set(columns) <= present:
                raise StorageUnavailable('Storage is unavailable.')
    finally:
        cursor.close()


@dataclass(frozen=True, repr=False)
class PreparedStorage:
    """App-scoped sessions. No key, key URL, or driver errors stored here."""
    engine: object
    sessions: Callable

    def session(self):
        return self.sessions()

    def close(self):
        self.engine.dispose()


def prepare_storage(*, driver_module, open_existing: Callable, key_provider: Callable,
                    apply_key: Callable, validate_runtime: Callable,
                    required_schema: Mapping[str, tuple[str, ...]]):
    """Build a lazy app-scoped factory, validate each new physical connection.

    No database is opened here. No connection URL or environment key is accepted.
    NullPool prevents an old validated connection bypassing changed key material.
    Callback implementations are explicit inputs, not auto-discovered fallbacks.
    """
    if driver_module is None:
        raise ValueError('An explicit driver module is required.')
    required = _schema_spec(required_schema)
    if any(not callable(fn) for fn in (open_existing, key_provider, apply_key, validate_runtime)):
        raise ValueError('Explicit storage adapters are required.')

    def connect():
        connection = None
        try:
            key = key_provider()
            if not isinstance(key, bytes) or not key:
                raise StorageUnavailable('Storage is unavailable.')
            connection = open_existing()
            apply_key(connection, key)
            del key
            # A successful PRAGMA/key setter is not positive cipher validation.
            if validate_runtime(connection) is not True:
                raise StorageUnavailable('Storage is unavailable.')
            _validate_schema(connection, required)
            return connection
        except Exception:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            raise StorageUnavailable('Storage is unavailable.') from None

    engine = create_engine('sqlite://', module=driver_module, creator=connect,
                           poolclass=NullPool, echo=False, hide_parameters=True)
    return PreparedStorage(engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
