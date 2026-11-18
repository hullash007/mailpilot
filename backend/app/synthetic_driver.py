"""Clearly fake cipher DBAPI for the synthetic preview, NOT encryption.

Intercepts key/version metadata only. Uses ordinary SQLite for app SQL.
Never import this from a real entrypoint or treat fake versions as evidence.
"""
import sqlite3
from .synthetic_fixture import MARKER

class _Cursor:
    def __init__(self,c):self.connection=c;self.raw=c.raw.cursor();self.rows=None
    def execute(self,sql,args=()):
        self.rows=None
        if sql.startswith('PRAGMA key='):return self
        values={'PRAGMA cipher_version':'4.20.0 community','SELECT sqlite_version()':'3.54.1','PRAGMA cipher_provider':'openssl'}
        if sql in values:self.rows=[(values[sql],)];return self
        if sql=='PRAGMA user_version':
            if self.raw.execute('SELECT marker FROM synthetic_fixture').fetchall()!=[(MARKER,)]:raise sqlite3.DatabaseError('Not a synthetic fixture')
        self.raw.execute(sql,args);return self
    def fetchone(self):return self.rows.pop(0) if self.rows else (None if self.rows==[] else self.raw.fetchone())
    def fetchall(self):return self.rows if self.rows is not None else self.raw.fetchall()
    def close(self):self.raw.close()
    def __getattr__(self,name):return getattr(self.raw,name)

class _Connection:
    def __init__(self,raw):self.raw=raw
    def cursor(self):return _Cursor(self)
    def __getattr__(self,name):return getattr(self.raw,name)

class SyntheticDriver:
    """No keys, encryption, Google or network. Metadata is deliberately fake."""
    def connect(self,*args,**kwargs):return _Connection(sqlite3.connect(*args,**kwargs))
    def __getattr__(self,name):return getattr(sqlite3,name)
