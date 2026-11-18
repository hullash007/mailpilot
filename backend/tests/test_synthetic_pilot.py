"""One synthetic app journey, no actual Google/provider/model/acquired driver."""
import sqlite3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.synthetic_fixture import bootstrap,manifest,VERSION
from app.synthetic_pilot import create_app
from app.models import Email

ORIGIN='http://127.0.0.1:8091'

def client(path):return TestClient(create_app(fixture_path=path),base_url=ORIGIN)
def pick(c,identity='1'):
    return c.post('/synthetic/identity',data={'identity':identity},headers={'Origin':ORIGIN})


def test_coherent_journey_restart_isolation_and_safe_recovery(tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Network forbidden')))
    p=bootstrap(tmp_path/'synthetic.db')
    with client(p) as c:
        assert c.get('/workspace').status_code==401
        assert 'SYNTHETIC LOCAL PREVIEW' in pick(c).text
        page=c.get('/workspace');assert 'Cedar launch checklist' in page.text and 'Harbor launch checklist' not in page.text
        assert c.get('/workspace/threads/2').status_code==404
        assert c.get('/actions/source/2').status_code==404
        action=c.get('/actions').text
        import re
        fp=re.search('name="fingerprint" value="([a-f0-9]+)"',action).group(1)
        response=c.post('/actions/1',data={'fingerprint':fp,'revision':'0','status':'confirmed'},headers={'Origin':ORIGIN})
        assert response.status_code==200 and 'confirmed' in c.get('/actions/history').text
        assert c.post('/synthetic/tick',headers={'Origin':ORIGIN}).status_code==200
        assert 'Initial sync incomplete' in c.get('/synthetic/status').text
        assert 'FAKE PROVIDER: Cedar update' in c.get('/workspace').text
        assert 'FAKE PROVIDER: Cedar update' not in c.get('/actions').text
        assert c.get('/workspace?before=bad').status_code==422
        assert c.get('/workspace').status_code==200
        assert pick(c,'2').status_code==200
        assert 'Harbor launch checklist' in c.get('/workspace').text
        assert 'Cedar launch checklist' not in c.get('/workspace').text
        assert 'confirmed' not in c.get('/actions/history').text
        assert c.get('/workspace/threads/1').status_code==404
    # A newly built app with fresh engine and dependencies, same fixture only.
    with client(p) as restarted:
        pick(restarted)
        assert 'confirmed' in restarted.get('/actions/history').text
        assert 'Initial sync incomplete' in restarted.get('/synthetic/status').text
        restarted.post('/synthetic/tick',headers={'Origin':ORIGIN})
        assert 'Last sync completed' in restarted.get('/synthetic/status').text
    e=create_engine('sqlite:///'+str(p))
    with Session(e) as db:
        row=db.scalar(select(Email).where(Email.gmail_message_id=='fakeprovider1'));assert row is not None and row.is_sample is False
    e.dispose()


def test_bootstrap_exclusive_and_no_startup_mutation(tmp_path):
    p=bootstrap(tmp_path/'fixture.db');before=p.read_bytes()
    with pytest.raises(FileExistsError):bootstrap(p)
    assert p.read_bytes()==before
    with client(p) as c:assert c.get('/').status_code==200
    assert p.read_bytes()==before
    unrelated=tmp_path/'unrelated.db';unrelated.write_bytes(b'not a fixture')
    with client(unrelated) as c:assert pick(c).status_code==503
    assert unrelated.read_bytes()==b'not a fixture'
    missing=tmp_path/'missing.db'
    with client(missing) as c:assert pick(c).status_code==503
    assert not missing.exists()
    wrong=bootstrap(tmp_path/'wrong-version.db');connection=sqlite3.connect(wrong);connection.execute('PRAGMA user_version=1');connection.close();before=wrong.read_bytes()
    with client(wrong) as c:assert pick(c).status_code==503
    assert wrong.read_bytes()==before


def test_local_boundary_no_google_routes_and_same_origin(tmp_path):
    p=bootstrap(tmp_path/'fixture.db')
    with client(p) as c:
        for route in ('/login','/auth/google/callback','/app','/app/sync','/access'):
            assert c.get(route).status_code in (404,405)
        assert c.post('/synthetic/identity',data={'identity':'1'}).status_code==403
        assert c.post('/synthetic/tick',headers={'Origin':'http://evil.example'}).status_code==403
        assert c.get('/',headers={'Host':'evil.example'}).status_code==403
        assert c.get('/').headers['cache-control']=='no-store'
    with pytest.raises(ValueError):create_app(fixture_path=p,origin='http://0.0.0.0:8091')


def test_partial_schema_failclosed_and_restore_fixture_recovery(tmp_path):
    p=bootstrap(tmp_path/'fixture.db')
    connection=sqlite3.connect(p);connection.execute('DROP TABLE thread_snapshots');connection.close()
    with client(p) as c:assert pick(c).status_code==503
    # Never automatic repair/migration. New explicit fixture path is the reset.
    replacement=bootstrap(tmp_path/'replacement.db')
    with client(replacement) as c:assert pick(c).status_code==200
