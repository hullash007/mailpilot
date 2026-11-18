"""Source seam proof only. Fake cipher metadata over ordinary synthetic SQLite.

Never imports/loads sqlcipher3 or the acquired wheel.
"""
import sqlite3
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from app.cipher_adapter import prepare_cipher_storage
from app.storage_factory import StorageUnavailable


class FakeCursor:
    def __init__(self,connection):self.connection=connection;self.cursor=connection.raw.cursor();self.fake=None
    def execute(self,sql,args=()):
        self.connection.events.append(sql.split('=')[0] if sql.startswith('PRAGMA key') else sql)
        self.fake=None
        if sql.startswith('PRAGMA key='):return self
        values={'PRAGMA cipher_version':self.connection.driver.cipher,'SELECT sqlite_version()':self.connection.driver.sqlite,'PRAGMA cipher_provider':self.connection.driver.provider}
        if sql in values:self.fake=[(values[sql],)] if values[sql] is not None else [];return self
        if sql=='PRAGMA temp_store' and self.connection.driver.bad_temp:self.fake=[(0,)];return self
        self.cursor.execute(sql,args);return self
    def fetchone(self):return self.fake.pop(0) if self.fake else (None if self.fake==[] else self.cursor.fetchone())
    def fetchall(self):return self.fake if self.fake is not None else self.cursor.fetchall()
    def close(self):self.cursor.close()
    def __getattr__(self,name):return getattr(self.cursor,name)


class FakeConnection:
    def __init__(self,raw,driver):self.raw=raw;self.driver=driver;self.events=driver.events
    def cursor(self):return FakeCursor(self)
    def enable_load_extension(self,on):
        self.events.append(('extensions',on))
        if self.driver.fail_extension:raise sqlite3.OperationalError('synthetic failure')
        self.raw.enable_load_extension(on)
    def set_authorizer(self,fn):self.driver.authorizer=fn;self.raw.set_authorizer(fn)
    def close(self):self.driver.closed+=1;self.raw.close()
    def __getattr__(self,name):return getattr(self.raw,name)


class FakeDriver:
    cipher='4.20.0 community';sqlite='3.54.1';provider='openssl';bad_temp=False;fail_extension=False
    def __init__(self):self.events=[];self.calls=[];self.closed=0
    def connect(self,*args,**kwargs):
        self.calls.append((args,kwargs));return FakeConnection(sqlite3.connect(*args,**kwargs),self)
    def __getattr__(self,name):return getattr(sqlite3,name)


@pytest.fixture
def setup(tmp_path):
    p=tmp_path/'synthetic # ?.db';c=sqlite3.connect(p);c.executescript('CREATE TABLE fixture(id INTEGER PRIMARY KEY);PRAGMA user_version=7;');c.close()
    return p,FakeDriver()


def prepare(path,driver,**override):
    args=dict(driver_module=driver,database_path=path,key_provider=lambda:b'F'*32,expected_user_version=7,required_schema={'fixture':('id',)})
    args.update(override);return prepare_cipher_storage(**args)


def read(storage):
    with storage.session() as s:return s.execute(text('SELECT count(*) FROM fixture')).scalar()


def test_each_connection_key_runtime_temp_extension_version_order(setup):
    p,d=setup;s=prepare(p,d);assert not d.calls
    assert read(s)==0 and read(s)==0 and len(d.calls)==2
    assert d.events[0]=='PRAGMA key' and d.events[1]==('extensions',False)
    assert d.events.index('PRAGMA user_version')>d.events.index('PRAGMA temp_store')
    assert d.authorizer(d.SQLITE_FUNCTION,None,'load_extension',None,None)==d.SQLITE_DENY
    assert d.authorizer(d.SQLITE_FUNCTION,None,'lower',None,None)==d.SQLITE_OK
    for args,kw in d.calls:
        assert args[0].endswith('?mode=rw') and '%23' in args[0] and '%3F' in args[0]
        assert kw==dict(uri=True,timeout=1.0,check_same_thread=False)
        assert '464646' not in args[0]
    with s.session() as db:
        with pytest.raises(Exception) as exc:db.execute(text("SELECT load_extension('not-an-extension')"))
        assert 'not authorized' in str(exc.value).lower()
    s.close()


def test_missing_symlink_and_directory_refused_without_driver(setup,tmp_path):
    p,d=setup;link=tmp_path/'link';link.symlink_to(p)
    for candidate in (tmp_path/'absent.db',link,tmp_path):
        s=prepare(candidate,d)
        with pytest.raises(StorageUnavailable):read(s)
        s.close()
    assert not d.calls and not (tmp_path/'absent.db').exists()


def test_wrong_missing_runtime_schema_or_key_fail_closed(setup):
    p,d=setup
    cases=[dict(key_provider=lambda:b''),dict(key_provider=lambda:b'x'*31),dict(expected_user_version=8),dict(required_schema={'fixture':('missing',)})]
    for override in cases:
        s=prepare(p,d,**override)
        with pytest.raises(StorageUnavailable):read(s)
        s.close()
    for cipher in (None,'4.19.0 community','4.20.0 enterprise'):
        d.cipher=cipher;s=prepare(p,d)
        with pytest.raises(StorageUnavailable):read(s)
        s.close()
    d.cipher='4.20.0 community'
    for attr,wrong in (('sqlite','3.53.1'),('provider','unknown')):
        original=getattr(d,attr);setattr(d,attr,wrong);s=prepare(p,d)
        with pytest.raises(StorageUnavailable):read(s)
        s.close();setattr(d,attr,original)


def test_temp_or_extension_control_failure_closes_connection(setup):
    p,d=setup
    for attr in ('bad_temp','fail_extension'):
        setattr(d,attr,True);before=d.closed;s=prepare(p,d)
        with pytest.raises(StorageUnavailable):read(s)
        assert d.closed==before+1;s.close();setattr(d,attr,False)


def test_parameter_contract_and_import_no_io(setup):
    p,d=setup
    for kwargs in (dict(database_path='relative.db'),dict(expected_user_version=True),dict(expected_user_version=-1),dict(driver_module=None)):
        with pytest.raises(ValueError):prepare(p,d,**kwargs)
    import subprocess,sys
    code="import sys,sqlite3;sqlite3.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no IO'));import app.cipher_adapter;assert 'sqlcipher3' not in sys.modules"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
