"""Local synthetic conversation journeys; no provider or model invocation."""
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from .test_google_identity import workspace, login, add_mail, ORIGIN
from app.database import get_db
from app.models import GmailAccount, Email, Thread
from app.sync_models import MailboxSync, ThreadSnapshot, MessageRemoteState
from app.workspace_candidate import create_app


@pytest.fixture
def candidate(workspace, monkeypatch):
    _, db = workspace
    app = create_app(); app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, base_url=ORIGIN)
    login(client, db, monkeypatch)
    account = db.scalar(select(GmailAccount))
    yield client, db, account
    client.close()


def seed(db, account):
    thread = Thread(account_id=account.id, gmail_thread_id='thread1', subject='SAMPLE project review')
    db.add(thread); db.flush()
    # Reverse insertion order must not determine conversation date order.
    for mid, day, text, labels in [('new',17,'Latest <script>not executable</script>',['SENT']),
                                  ('old',16,'Earlier unique-body-needle 100% a_b', ['INBOX'])]:
        db.add(Email(account_id=account.id, thread_id=thread.id, gmail_message_id=mid, subject='SAMPLE review',
                     sender='writer@example.test', recipients='owner@example.test', body_text=text,
                     internal_date=datetime(2026,11,day,9), label_ids=labels, is_sample=True))
    db.add(ThreadSnapshot(account_id=account.id, gmail_thread_id='thread1', fetched_at=datetime.utcnow(),
                          message_count=2, excluded_count=0, message_ids=['old','new'], provider_history_id='100'))
    db.commit(); return thread


def test_conversation_order_direction_escape_and_source_status(candidate):
    c, db, a = candidate; t=seed(db,a)
    html = c.get(f'/workspace/threads/{t.id}').text
    assert html.index('Earlier unique') < html.index('Latest &lt;script&gt;')
    assert 'Received' in html and 'Sent' in html and 'UTC' in html
    assert '<script>not' not in html and 'Synthetic fixture' in html
    assert '2 eligible messages' in html and 'not live' in html


@pytest.mark.parametrize('query', ['unique-body-needle','100%','a_b','writer@example','SAMPLE review'])
def test_body_metadata_literal_search(candidate, query):
    c,db,a=candidate; seed(db,a)
    page=c.get('/workspace', params={'q':query})
    assert page.status_code==200 and '<article class="card">' in page.text
    assert 'No attachments' in page.text and 'plain text' in page.text


def test_deleted_excluded_and_snapshot_missing_not_in_search(candidate):
    c,db,a=candidate; t=seed(db,a)
    db.add(MessageRemoteState(account_id=a.id,gmail_message_id='old',deleted_at=datetime.utcnow()))
    snap=db.scalar(select(ThreadSnapshot));snap.message_ids=[];snap.message_count=0
    db.commit()
    assert 'No stored matches' in c.get('/workspace').text
    detail=c.get(f'/workspace/threads/{t.id}').text
    assert 'retained after remote deletion' in detail and 'retained outside last snapshot' in detail
    assert 'Earlier unique' in detail  #retention preserved, explicitly not current


def test_unavailable_and_incomplete_are_clear(candidate):
    c,db,a=candidate;t=seed(db,a)
    snap=db.scalar(select(ThreadSnapshot));snap.availability='unavailable';db.commit()
    assert 'Provider conversation unavailable' in c.get(f'/workspace/threads/{t.id}').text
    assert 'No stored matches' in c.get('/workspace').text
    db.delete(snap);db.commit()
    assert 'Incomplete stored conversation' in c.get(f'/workspace/threads/{t.id}').text


@pytest.mark.parametrize('phase,error,blocked,last_success,label',[
    ('initial','',False,None,'Initial sync incomplete'),
    ('catchup','',False,None,'Initial sync incomplete'),
    ('ready','provider_503',False,None,'Sync needs attention'),
    ('initial','page_token_restart_limit',True,None,'Sync blocked'),
    ('ready','',False,datetime.utcnow()-timedelta(hours=2),'may be stale'),
    ('ready','',False,datetime.utcnow(),'Last sync completed')])
def test_status_states(candidate,phase,error,blocked,last_success,label):
    c,db,a=candidate
    db.add(MailboxSync(account_id=a.id,phase=phase,error_code=error,blocked=blocked,last_success=last_success));db.commit()
    assert label in c.get('/workspace').text


def test_account_isolation_and_expired_session_errors(candidate):
    c,db,a=candidate;t=seed(db,a)
    b=GmailAccount(email_address='other@example.test',google_user_id='other');db.add(b);db.commit()
    add_mail(db,b.id,'FOREIGN'); foreign=db.scalar(select(Thread).where(Thread.account_id==b.id))
    assert 'FOREIGN' not in c.get('/workspace?account_id='+str(b.id)).text
    res=c.get(f'/workspace/threads/{foreign.id}')
    assert res.status_code==404 and 'Return to stored conversations' in res.text and 'FOREIGN' not in res.text
    c.cookies.clear();res=c.get('/workspace')
    assert res.status_code==401 and 'Sign in with Google' in res.text


def test_safe_get_journey_no_provider_or_data_writes(candidate):
    from sqlalchemy import event
    c,db,a=candidate;t=seed(db,a); writes=[]
    def trace(conn,cursor,statement,params,context,executemany):
        if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE')):writes.append(statement)
    event.listen(db.bind,'before_cursor_execute',trace)
    try:
        assert c.get('/workspace').status_code==200
        assert c.get('/workspace?q=unique-body-needle').status_code==200
        assert c.get(f'/workspace/threads/{t.id}').status_code==200
        assert c.get('/app').status_code==200
    finally:event.remove(db.bind,'before_cursor_execute',trace)
    assert not writes


def test_public_google_entrypoint_has_only_stored_workspace(workspace):
    # Public0391767 added stored /workspace after this older sync candidate.
    c,_=workspace
    assert c.get('/workspace').status_code==401
    import subprocess,sys
    subprocess.run([sys.executable,'-c',"from app.google_main import app;from app.database import Base;assert 'mailbox_sync' not in Base.metadata.tables;assert 'action_decisions_candidate' not in Base.metadata.tables;assert not any(r.path.startswith('/actions') for r in app.routes)"],check=True)


def test_conversation_list_groups_matches_and_keeps_source_links(candidate):
    c,db,a=candidate;t=seed(db,a)
    page=c.get('/workspace')
    assert page.text.count('<article class="card">')==1
    assert '2 matching stored messages on this page' in page.text
    assert 'Searching stored mail...' in page.text and 'role="status"' in page.text


def test_filter_cursor_and_reset_do_not_expose_other_mail(candidate):
    c,db,a=candidate;t=seed(db,a)
    rows=db.scalars(select(Email)).all()
    for row in rows: row.category='personal';row.priority='high'
    db.commit()
    assert 'SAMPLE review' in c.get('/workspace?category=personal&priority=high').text
    assert 'No stored matches' in c.get('/workspace?category=receipt').text
    assert c.get('/workspace?category=wrong').status_code==422


def test_budget_pause_is_not_provider_failure_or_complete(candidate):
    c,db,a=candidate
    db.add(MailboxSync(account_id=a.id,phase='initial',error_code='budget_exhausted'));db.commit()
    text=c.get('/workspace').text
    assert 'Sync paused safely' in text and 'progress is saved' in text
    assert 'Sync needs attention' not in text and 'Last sync completed' not in text


def test_retry_states_have_concise_copy(candidate):
    c,db,a=candidate
    row=MailboxSync(account_id=a.id,retry_at=datetime.utcnow()+timedelta(minutes=1));db.add(row);db.commit()
    assert 'Sync paused, retry scheduled' in c.get('/workspace').text
    row.retry_exhausted=True;db.commit()
    assert 'Sync paused for review' in c.get('/workspace').text


def test_unobserved_completed_generation_is_stale_not_deleted(candidate):
    c,db,a=candidate;t=seed(db,a)
    snap=db.scalar(select(ThreadSnapshot));snap.observed_generation=1
    state=MailboxSync(account_id=a.id,generation=2,completed_generation=1,phase='initial');db.add(state);db.commit()
    assert 'SAMPLE review' in c.get('/workspace').text  #incompletegeneration notabsenceproof
    state.completed_generation=2;state.phase='ready';db.commit()
    assert 'No stored matches' in c.get('/workspace').text
    detail=c.get(f'/workspace/threads/{t.id}').text
    assert 'Not observed in the latest completed sync' in detail
    assert 'not proof of deletion' in detail and 'Earlier unique' in detail
    assert 'retained after remote deletion' not in detail
