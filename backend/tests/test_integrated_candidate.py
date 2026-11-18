"""Changed composition seams only, synthetic SQLite and injected fake provider."""
import pytest
from sqlalchemy import select,event,text
from fastapi.testclient import TestClient
from app.database import get_db
from app.models import Email,GmailAccount
from app.action_models import ActionBase,ActionDecision
from app.integrated_candidate import create_app
from app.sync_models import MailboxSync,ThreadSnapshot,SyncRun,MessageRemoteState
from app.services import mailbox_sync as sync,action_review as action
from .test_google_identity import workspace,login,add_mail,ORIGIN
from .test_mailbox_sync import Client

@pytest.fixture
def combined(workspace,monkeypatch):
    _,db=workspace;ActionBase.metadata.create_all(db.bind)
    app=create_app();app.dependency_overrides[get_db]=lambda:db
    c=TestClient(app,base_url=ORIGIN);login(c,db,monkeypatch)
    account=db.scalar(select(GmailAccount));mid=add_mail(db,account.id,'SYNTHETIC_LOCAL')
    row=db.get(Email,mid);row.body_text='Please review the synthetic fixture.';row.is_sample=True;db.commit()
    yield c,db,app,account,mid
    c.close()

def test_single_routes_dispatch_and_no_real_sync(combined):
    c,db,app,a,mid=combined
    keys=[(r.path,tuple(sorted(r.methods or []))) for r in app.routes if hasattr(r,'methods')]
    assert len(keys)==len(set(keys))
    assert c.post('/app/sync',headers={'Origin':ORIGIN}).status_code==404
    for path in ['/actions/source/999','/workspace/threads/999']:
        response=c.get(path);assert response.status_code==404
        assert ('Review could not continue' if path.startswith('/actions') else 'Stored workspace unavailable') in response.text
    for path in ['/actions/history?before=x','/workspace?before=x']:
        response=c.get(path);assert response.status_code==422
        assert 'Choose a valid stored page' in response.text
    assert c.get('/missing').status_code==404
    c.cookies.clear()
    assert c.get('/actions/history').status_code==401 and c.get('/workspace').status_code==401

def test_absent_sync_schema_fallback_and_partial_fails_closed(combined):
    c,db,app,a,mid=combined
    db.commit()
    for table in (SyncRun.__table__,ThreadSnapshot.__table__,MessageRemoteState.__table__,MailboxSync.__table__):table.drop(db.bind)
    response=c.get('/workspace');assert response.status_code==200
    assert 'Sync metadata is not installed' in response.text and 'SYNTHETIC_LOCAL' in response.text
    thread=db.get(Email,mid).thread_id
    assert 'No full provider snapshot' in c.get('/workspace/threads/'+str(thread)).text
    MailboxSync.__table__.create(db.bind)
    assert c.get('/workspace').status_code==503

def test_one_fake_tick_preserves_decision_and_real_records_ineligible(combined):
    c,db,app,a,mid=combined
    fp=action.review(db,a.id,mid)['observation'].fingerprint;action.decide(db,a.id,mid,fp,0,'confirmed')
    assert 'Limited stored sample' in c.get('/workspace').text
    sync.start_sync(db,a.id)
    provider=Client();provider.threads['t1'][0]['body_text']='Please review a fake provider request.'
    result=sync.sync_tick(db,a.id,provider)
    assert result['phase']=='catchup' and 'Initial sync incomplete' in c.get('/workspace').text
    source=db.scalar(select(Email).where(Email.gmail_message_id=='m1'))
    assert source.is_sample is False
    assert 'fake provider request' not in c.get('/actions').text
    assert 'confirmed' in c.get('/actions/history').text
    with pytest.raises(Exception) as exc:action.review(db,a.id,source.id)
    assert exc.value.status_code==404
    state=db.get(MailboxSync,a.id);state.completed_generation=2
    snap=db.scalar(select(ThreadSnapshot));snap.observed_generation=1;db.commit()
    assert 'not observed in latest completed sync' in c.get('/workspace/threads/'+str(source.thread_id)).text
    assert 'fake provider request' not in c.get('/workspace').text
    db.expire_all();assert db.scalar(select(ActionDecision)).status=='confirmed'

def test_provider_update_cannot_keep_prior_sample_eligibility(combined):
    c,db,app,a,mid=combined
    row=db.get(Email,mid);row.gmail_message_id='m1';db.commit()
    fp=action.review(db,a.id,mid)['observation'].fingerprint;action.decide(db,a.id,mid,fp,0,'confirmed')
    sync.start_sync(db,a.id);provider=Client();sync.sync_tick(db,a.id,provider)
    db.expire_all();assert db.get(Email,mid).is_sample is False
    assert 'Source unavailable' in c.get('/actions/history').text
    assert db.scalar(select(ActionDecision)).status=='confirmed'
    with pytest.raises(Exception) as exc:action.review(db,a.id,mid)
    assert exc.value.status_code==404

def test_local_startup_and_gets_have_no_ddl_writes_or_network(combined,monkeypatch):
    import socket
    c,db,app,a,mid=combined;writes=[]
    def blocked(*args,**kwargs):raise AssertionError('Network forbidden in integrated fixture')
    monkeypatch.setattr(socket,'create_connection',blocked)
    def trace(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','DROP','ALTER')):writes.append(statement)
    event.listen(db.bind,'before_cursor_execute',trace)
    try:
        with TestClient(app,base_url=ORIGIN) as started:
            started.cookies.update(c.cookies)
            for path in ['/workspace','/actions','/actions/history','/app']:
                assert started.get(path).status_code==200
        assert writes==[]
    finally:event.remove(db.bind,'before_cursor_execute',trace)


def test_non_auth_workspace_errors_keep_retry_not_login(combined):
    c,db,app,a,mid=combined
    for path,status in [('/workspace/threads/999',404),('/workspace?before=x',422)]:
        response=c.get(path)
        assert response.status_code==status
        assert 'Return to stored conversations' in response.text
        assert 'Sign in with Google</a>' not in response.text
