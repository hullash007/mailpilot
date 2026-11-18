"""Explicit offline baseline copy to NEW cipher file. Source/fake candidate only.

Caller must quiesce writers and separately establish owner authority before real
use. Never overwrite input/output. Failed building files are retained private
for outcome investigation; no automatic retry or blind rollback. No CLI/env.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool
from .database import Base
from .models import GmailAccount, OAuthState, Thread, Email
from .google_main import GooglePrincipal
from .account_control_models import ControlBase, ControlClock
from .action_models import ActionBase
from .private_bootstrap_candidate import SCHEMA_VERSION, database_key, required_schema
from .cipher_adapter import EXPECTED_CIPHER, EXPECTED_SQLITE, EXPECTED_PROVIDER, prepare_cipher_storage

BASELINE = (GmailAccount.__table__, OAuthState.__table__, Thread.__table__, Email.__table__, GooglePrincipal.__table__)
# Explicit FK order differs from the inventory declaration.
COPY_ORDER = (GmailAccount.__table__, OAuthState.__table__, Thread.__table__, Email.__table__, GooglePrincipal.__table__)


class ConversionUnconfirmed(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversionResult:
    schema_version: int
    counts: dict
    old_database_retained: bool = True
    # Metadata receipt only, no key/mail/address/hash of sensitive cell values.


def _private_regular(path, *, existing):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError('An explicit absolute database path is required.')
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ValueError('Symlink database paths are not qualified.')
    if existing:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError('Database requires owner-only regular-file access.')
    elif path.exists():
        raise ValueError('Destination already exists.')


def _digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(65536),b''):
            h.update(b)
    return h.digest()


def _rows(connection, table):
    names = tuple(table.columns.keys())
    columns = ','.join('"'+n+'"' for n in names)
    rows = connection.execute('SELECT '+columns+' FROM "'+table.name+'"').fetchall()
    # Raw DBAPI values preserve SQL JSON/text/date/token bytes. No ORM re-encoding.
    return sorted((tuple(row) for row in rows),key=repr)


def _source_inventory(c, allowed_email):
    if c.execute('PRAGMA journal_mode').fetchone() != ('delete',):
        raise ValueError('Baseline journal mode is not qualified.')
    if c.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError()
    if c.execute('PRAGMA user_version').fetchone() != (0,):
        raise ValueError('Unexpected baseline schema version.')
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != {t.name for t in BASELINE}:
        raise ValueError('Unexpected baseline tables.')
    for table in BASELINE:
        cols=tuple(row[1] for row in c.execute('PRAGMA table_info("'+table.name+'")'))
        if cols != tuple(table.columns.keys()):
            raise ValueError('Unexpected baseline columns.')
    if c.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Baseline references are inconsistent.')
    accounts=c.execute('SELECT id,email_address,google_user_id,credentials_blob FROM gmail_accounts').fetchall()
    principals=c.execute('SELECT subject,account_id FROM google_principals').fetchall()
    # This release is deliberately the existing single-mailbox pilot, not a
    # migration that can silently broaden an audience or bind by email alone.
    if len(accounts)!=1 or len(principals)!=1:
        raise ValueError('Expected one explicitly allowed mailbox.')
    aid,address,subject,blob=accounts[0]
    if (type(address) is not str or address.casefold()!=allowed_email.casefold()
            or type(subject) is not str or not 0<len(subject)<=255
            or principals[0]!=(subject,aid) or type(blob) is not str or not blob):
        raise ValueError('Baseline principal binding is not qualified.')
    if c.execute('SELECT 1 FROM emails e JOIN threads t ON e.thread_id=t.id WHERE e.account_id!=t.account_id LIMIT 1').fetchone():
        raise ValueError('Baseline mail ownership is inconsistent.')
    if c.execute('SELECT 1 FROM threads WHERE account_id!=? UNION SELECT 1 FROM emails WHERE account_id!=? LIMIT 1',(aid,aid)).fetchone():
        raise ValueError('Baseline ownership is inconsistent.')


def _new_cipher_connection(driver, path, raw_key):
    c=driver.connect(path.as_uri()+'?mode=rw',uri=True,timeout=1.0,check_same_thread=False)
    try:
        c.execute('PRAGMA key="x\''+raw_key.hex()+'\'"')
        if (c.execute('PRAGMA cipher_version').fetchone()!=(EXPECTED_CIPHER,)
                or c.execute('SELECT sqlite_version()').fetchone()!=(EXPECTED_SQLITE,)
                or c.execute('PRAGMA cipher_provider').fetchone()!=(EXPECTED_PROVIDER,)):
            raise ValueError('Cipher runtime is not qualified.')
        c.enable_load_extension(False)
        c.set_authorizer(lambda action,a,b,d,t:driver.SQLITE_DENY if action==driver.SQLITE_FUNCTION
            and str(b or a).lower()=='load_extension' else driver.SQLITE_OK)
        c.execute('PRAGMA temp_store=MEMORY');c.execute('PRAGMA foreign_keys=ON')
        if c.execute('PRAGMA temp_store').fetchone()!=(2,) or c.execute('PRAGMA foreign_keys').fetchone()!=(1,):
            raise ValueError('Connection policy is not qualified.')
        return c
    except Exception:
        c.close();raise


def convert_offline(*, source, destination, token_key, allowed_email, reviewed_driver, schema_version):
    """No live service stop, chmod/chown, key transport or rollback is performed.

    Any exception after the building file exists may mean bytes were written.
    It is never a claim that source/destination are unchanged. Preserve artifacts
    and investigate before retrying, including failure after publication.
    """
    if (type(schema_version) is not int or schema_version!=SCHEMA_VERSION
            or type(allowed_email) is not str or not allowed_email or reviewed_driver is None):
        raise ValueError('Explicit reviewed conversion inputs are required.')
    _private_regular(source,existing=True);_private_regular(destination,existing=False)
    if source==destination:
        raise ValueError('Use a distinct new database.')
    if any(source.with_name(source.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):
        raise ValueError('Quiescent baseline without journal artifacts is required.')
    raw_key=database_key(token_key)
    before=_digest(source)
    building=destination.with_name(destination.name+'.building-'+secrets.token_hex(12))
    src=None;engine=None;storage=None
    try:
        src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        src.execute('BEGIN')
        _source_inventory(src,allowed_email)
        original={t.name:_rows(src,t) for t in BASELINE}
        fd=os.open(building,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        engine=create_engine('sqlite://',module=reviewed_driver,
            creator=lambda:_new_cipher_connection(reviewed_driver,building,raw_key),poolclass=NullPool,
            hide_parameters=True,echo=False)
        with engine.begin() as conn:
            # Explicit OFFLINE schema operation. Never called by app startup.
            Base.metadata.create_all(conn);ActionBase.metadata.create_all(conn);ControlBase.metadata.create_all(conn)
            raw=conn.connection.driver_connection
            for table in COPY_ORDER:
                names=tuple(table.columns.keys())
                placeholders=','.join('?' for n in names)
                columns=','.join('"'+n+'"' for n in names)
                for row in original[table.name]:
                    raw.execute('INSERT INTO "'+table.name+'" ('+columns+') VALUES ('+placeholders+')',row)
            raw.execute('INSERT INTO candidate_control_clock (id,generation) VALUES (1,0)')
            raw.execute('PRAGMA user_version='+str(schema_version))
            if raw.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Converted references are inconsistent.')
        engine.dispose();engine=None
        storage=prepare_cipher_storage(driver_module=reviewed_driver,database_path=building,key_provider=lambda:raw_key,
            expected_user_version=schema_version,required_schema=required_schema(),require_foreign_keys=True)
        with storage.engine.connect() as conn:
            raw=conn.connection.driver_connection
            if raw.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise ValueError('Converted data did not validate.')
            for table in BASELINE:
                if _rows(raw,table)!=original[table.name]:
                    raise ValueError('Converted rows did not match.')
        storage.close();storage=None
        src.rollback();src.close();src=None
        if _digest(source)!=before:
            raise ValueError('Baseline changed during conversion.')
        # Same-directory exclusive publication. Never replace any destination.
        # A failure after this link may leave a complete destination: unconfirmed.
        with building.open('rb') as f:os.fsync(f.fileno())
        os.link(building,destination)
        building.unlink()
        directory=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return ConversionResult(schema_version,{name:len(rows) for name,rows in original.items()})
    except Exception:
        raise ConversionUnconfirmed('Offline conversion outcome is unconfirmed. Preserve source and staging artifacts; do not retry blindly.') from None
    finally:
        if src is not None:src.close()
        if engine is not None:engine.dispose()
        if storage is not None:storage.close()
