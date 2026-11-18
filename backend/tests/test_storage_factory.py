"""Fake driver/key callbacks and ordinary synthetic SQLite only, never SQLCipher."""
import sqlite3
import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient
from app.storage_factory import prepare_storage, StorageUnavailable
from app.protected_candidate import create_app


@pytest.fixture
def fixture_db(tmp_path):
    path=tmp_path/'fixture.db'
    c=sqlite3.connect(path);c.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY,value TEXT)');c.execute("INSERT INTO fixture VALUES(1,'synthetic')");c.commit();c.close()
    return path


def spec(path,events,**changes):
    def provider():events.append('key');return b'synthetic-not-a-real-key'
    def opener():events.append('open');return sqlite3.connect('file:'+str(path)+'?mode=rw',uri=True,check_same_thread=False)
    def keyer(c,k):assert k==b'synthetic-not-a-real-key';events.append('apply')
    def runtime(c):events.append('runtime');return True # Fake only, not encryption evidence.
    result=dict(driver_module=sqlite3,open_existing=opener,key_provider=provider,
                apply_key=keyer,validate_runtime=runtime,required_schema={'fixture':('id','value')})
    result.update(changes);return result


def test_lazy_order_and_each_physical_connection(fixture_db):
    events=[];storage=prepare_storage(**spec(fixture_db,events));assert events==[]
    for _ in range(2):
        with storage.session() as s:assert s.execute(text('SELECT value FROM fixture')).scalar()=='synthetic'
    assert events==['key','open','apply','runtime']*2
    assert storage.engine.url.render_as_string()=='sqlite://'
    assert 'synthetic-not-a-real-key' not in repr(storage)
    storage.close()


def test_key_and_runtime_fail_closed(fixture_db):
    for override in ({'key_provider':lambda:b''},{'validate_runtime':lambda c:False}):
        events=[];storage=prepare_storage(**spec(fixture_db,events,**override))
        with pytest.raises(StorageUnavailable,match='^Storage is unavailable.$'):
            with storage.session() as s:s.execute(text('SELECT 1'))
        storage.close()


def test_error_sanitized_and_failed_connection_closed(fixture_db):
    events=[];closed=[]
    class Wrapper:
        def close(self):closed.append(True)
    def explode(c,key):raise ValueError('secret-key-and-private-path')
    storage=prepare_storage(**spec(fixture_db,events,open_existing=Wrapper,apply_key=explode))
    with pytest.raises(StorageUnavailable) as e:
        with storage.session() as s:s.execute(text('SELECT 1'))
    assert str(e.value)=='Storage is unavailable.' and e.value.__suppress_context__
    assert closed==[True];storage.close()


def test_partial_schema_and_missing_file_no_creation(fixture_db,tmp_path):
    for path,required in [(fixture_db,{'fixture':('missing',)}),(tmp_path/'absent.db',{'fixture':('id',)})]:
        before=path.read_bytes() if path.exists() else None
        storage=prepare_storage(**spec(path,[],required_schema=required))
        with pytest.raises(StorageUnavailable):
            with storage.session() as s:s.execute(text('SELECT 1'))
        assert (path.read_bytes() if path.exists() else None)==before
        storage.close()


def test_schema_spec_immutable_and_names_checked(fixture_db):
    required={'fixture':['id','value']};storage=prepare_storage(**spec(fixture_db,[],required_schema=required))
    required['fixture'].append('nonexistent')
    with storage.session() as s:assert s.execute(text('SELECT 1')).scalar()==1
    storage.close()
    with pytest.raises(ValueError):prepare_storage(**spec(fixture_db,[],driver_module=None))
    for required in ({},{'fixture;DROP TABLE fixture':('id',)},{'fixture':('x"',)}):
        with pytest.raises(ValueError):prepare_storage(**spec(fixture_db,[],required_schema=required))


def test_explicit_candidate_no_startup_ddl_and_legacy_default(fixture_db):
    from app.integrated_candidate import create_app as legacy
    from app.database import get_db
    events=[];app=create_app(**spec(fixture_db,events))
    assert get_db in app.dependency_overrides
    assert get_db not in legacy().dependency_overrides
    before=fixture_db.read_bytes()
    with TestClient(app):pass
    assert events==[] and fixture_db.read_bytes()==before
    # Account auth uses this app's explicit dependency. No schema fallback.
    bad=create_app(**spec(fixture_db,[],required_schema={'accounts':('id',)}))
    @bad.get('/storage-probe')
    def probe(db=__import__('fastapi').Depends(get_db)):
        return {'result':db.execute(text('SELECT 1')).scalar()}
    response=TestClient(bad).get('/storage-probe')
    assert response.status_code==503 and 'Storage is unavailable' in response.text
    assert 'fixture.db' not in response.text


def test_import_has_no_database_io():
    import subprocess,sys
    code='''import sqlite3
sqlite3.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no connect'))
import app.storage_factory,app.protected_candidate
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
