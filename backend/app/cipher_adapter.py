"""Pinned local candidate adapter. No SQLCipher import, install, or key custody.

The caller supplies a reviewed DBAPI module, a private raw-key provider, an
existing database and an explicit schema version. This does not migrate mail.
"""
from pathlib import Path
import stat
from .storage_factory import prepare_storage, StorageUnavailable

EXPECTED_CIPHER = '4.20.0 community'
EXPECTED_SQLITE = '3.54.1'
EXPECTED_PROVIDER = 'openssl'


def prepare_cipher_storage(*, driver_module, database_path, key_provider,
                           expected_user_version, required_schema, require_foreign_keys=False):
    if type(require_foreign_keys) is not bool:
        raise ValueError('An explicit foreign key policy is required.')
    path = Path(database_path)
    if not path.is_absolute() or type(expected_user_version) is not int or not 0 <= expected_user_version <= 2147483647:
        raise ValueError('Explicit absolute database path and schema version are required.')
    if driver_module is None:
        raise ValueError('An explicit reviewed cipher driver is required.')

    def open_existing():
        # Every physical connection rechecks. mode=rw independently prevents
        # empty creation if the file disappears between stat and connect.
        # It does not eliminate hostile filesystem substitution races.
        for item in (path, *path.parents):
            if item.is_symlink():
                raise StorageUnavailable('Storage is unavailable.')
        if not stat.S_ISREG(path.stat().st_mode):
            raise StorageUnavailable('Storage is unavailable.')
        return driver_module.connect(path.as_uri() + '?mode=rw', uri=True,
                                     timeout=1.0, check_same_thread=False)

    def apply_key(connection, key):
        if len(key) != 32:
            raise StorageUnavailable('Storage is unavailable.')
        cursor = connection.cursor()
        try:
            # Byte-only32rawkey excludes SQL metacharacters. This necessarily
            # creates transient Python/SQL copies; it is not zeroization.
            cursor.execute('PRAGMA key="x\'' + key.hex() + '\'"')
        finally:
            cursor.close()
        # Disable both SQL and DBAPI extension loading at connection setup.
        # A successful call is a trusted-driver contract, not attestation.
        connection.enable_load_extension(False)
        def authorize(action, arg1, arg2, database, trigger):
            if action == driver_module.SQLITE_FUNCTION and str(arg2 or arg1).lower() == 'load_extension':
                return driver_module.SQLITE_DENY
            return driver_module.SQLITE_OK
        connection.set_authorizer(authorize)

    def validate_runtime(connection):
        cursor = connection.cursor()
        try:
            cursor.execute('PRAGMA cipher_version')
            if cursor.fetchone() != (EXPECTED_CIPHER,):
                return False
            cursor.execute('SELECT sqlite_version()')
            if cursor.fetchone() != (EXPECTED_SQLITE,):
                return False
            cursor.execute('PRAGMA cipher_provider')
            if cursor.fetchone() != (EXPECTED_PROVIDER,):
                return False
            # Explicit per-connection temp policy, no global default mutation.
            cursor.execute('PRAGMA temp_store=MEMORY')
            cursor.execute('PRAGMA temp_store')
            if cursor.fetchone() != (2,):
                return False
            if require_foreign_keys:
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.execute('PRAGMA foreign_keys')
                if cursor.fetchone() != (1,):
                    return False
            # This first real database metadata read forces key verification.
            # Unsupported cipher pragmas returning no rows never count as pass.
            cursor.execute('PRAGMA user_version')
            return cursor.fetchone() == (expected_user_version,)
        finally:
            cursor.close()

    return prepare_storage(driver_module=driver_module, open_existing=open_existing,
                           key_provider=key_provider, apply_key=apply_key,
                           validate_runtime=validate_runtime, required_schema=required_schema)
