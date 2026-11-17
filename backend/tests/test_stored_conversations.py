"""Local synthetic conversation journeys; no provider or model invocation."""
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from .test_google_identity import workspace, login, add_mail, ORIGIN
from app.database import get_db
from app.models import GmailAccount, Email, Thread
from app.stored_conversations import create_app


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
    db.commit(); return thread


def test_conversation_order_direction_escape_and_source_status(candidate):
    c, db, a = candidate; t=seed(db,a)
    html = c.get(f'/workspace/threads/{t.id}').text
    assert html.index('Earlier unique') < html.index('Latest &lt;script&gt;')
    assert 'Received' in html and 'Sent' in html and 'UTC' in html
    assert '<script>not' not in html and 'Synthetic fixture' in html
    assert 'May be incomplete' in html and 'current Gmail membership unverified' in html
    assert 'This view contains messages stored locally' in html


@pytest.mark.parametrize('query', ['unique-body-needle','100%','a_b','writer@example','SAMPLE review'])
def test_body_metadata_literal_search(candidate, query):
    c,db,a=candidate; seed(db,a)
    page=c.get('/workspace', params={'q':query})
    assert page.status_code==200 and '<article class="card">' in page.text
    assert 'No attachments' in page.text and 'plain text' in page.text


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


def test_google_entrypoint_protects_new_routes(workspace):
    c,_=workspace
    assert c.get('/workspace').status_code==401


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




def test_no_sync_tables_registered_in_fresh_process():
    import subprocess,sys
    code="from app.stored_conversations import create_app; from app.database import Base; forbidden={'mailbox_sync','thread_snapshots','message_remote_states'}; assert not forbidden.intersection(Base.metadata.tables)"
    subprocess.run([sys.executable,'-c',code],check=True)


def test_provider_is_never_called_and_credentials_are_unchanged(candidate,monkeypatch):
    from app.integrations.gmail.client import GmailClient
    c,db,a=candidate;t=seed(db,a);blob=a.credentials_blob
    def forbidden(*args,**kwargs):raise AssertionError('UI must not contact provider')
    monkeypatch.setattr(GmailClient,'__init__',forbidden)
    before=[(x.id,x.gmail_message_id,x.body_text) for x in db.scalars(select(Email)).all()]
    for route in ['/workspace','/workspace?q=needle',f'/workspace/threads/{t.id}','/workspace?q=missing']:
        assert c.get(route).status_code==200
    db.expire_all()
    assert db.get(GmailAccount,a.id).credentials_blob==blob
    assert [(x.id,x.gmail_message_id,x.body_text) for x in db.scalars(select(Email)).all()]==before


def test_unknown_subject_rejected_for_every_route(candidate):
    from app.google_main import issue_session,COOKIE
    c,db,a=candidate;t=seed(db,a)
    c.cookies.clear();c.cookies.set(COOKIE,issue_session('not-linked'))
    for route in ['/workspace','/workspace?q=needle',f'/workspace/threads/{t.id}']:
        r=c.get(route);assert r.status_code==401 and 'Earlier unique' not in r.text


def test_invalid_inputs_stay_readable_and_do_not_reflect_values(candidate):
    c,db,a=candidate
    for path in ['/workspace?before=0','/workspace?q='+('x'*201),'/workspace/threads/notanumber']:
        r=c.get(path);assert r.status_code==422 and 'Clear filters and return' in r.text
