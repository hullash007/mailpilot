import base64
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.gmail_reader_fixture import create_fixture_app,FakeMailbox
from app.services.gmail_reader import PageSigner,InvalidPage,ReadPolicy,GmailReadService,MailUnavailable,ReconnectRequired
from app.gmail_reader_candidate import install_reader
from app.google_main import own_account
from app.database import get_db

@pytest.fixture
def app():return create_fixture_app()
@pytest.fixture
def client(app):return TestClient(app)

def test_actual_reader_inbox_thread_query_pages(client):
    r=client.get('/mail');assert r.status_code==200 and 'Design review' in r.text and 'Unread' in r.text
    assert 'Fictional mailbox' in r.text and 'alex@harbor.example' in r.text
    import re,html
    link=html.unescape(re.search(r'href="([^"]+)">Older conversations',r.text).group(1))
    assert 'Dinner next Thursday' in client.get(link).text
    s=client.get('/mail?q=lease&folder=');assert '2024 lease renewal' in s.text
    t=client.get('/mail/threads/H1');assert '2 messages' in t.text and 'The new direction looks good' in t.text

def test_folders_and_allmail(client):
    assert '2024 lease renewal' in client.get('/mail?folder=Label_archive').text
    allmail=client.get('/mail?folder=');assert 'folder=' in allmail.text
    assert 'Starred' in client.get('/mail?folder=STARRED').text

def test_refresh_fetches_new_data(app,client):
    assert 'A new message just arrived' not in client.get('/mail').text
    app.state.boxes['harbor'].new_message()
    assert 'A new message just arrived' in client.get('/mail').text

def test_two_account_same_thread_selector_cannot_leak(client):
    assert client.get('/mail/threads/H1').status_code==200
    client.get('/preview/as/cedar')
    r=client.get('/mail/threads/H1');assert r.status_code==503 and 'maya@studio' not in r.text
    assert 'sam@cedar.example' in client.get('/mail').text and 'C1' in client.get('/mail').text

def test_mail_html_and_scripts_never_render(app,client):
    msg=app.state.boxes['harbor'].rows['H1']['messages'][0]
    msg['payload']['headers'][0]['value']='<img src=x onerror=alert(1)>'
    msg['payload']['body']['data']=base64.urlsafe_b64encode(b'<script>steal()</script>\nhttps://evil.example').decode()
    r=client.get('/mail/threads/H1');assert '<script>steal' not in r.text and '&lt;script&gt;' in r.text and '<img src=x' not in r.text
    assert "default-src 'none'" in r.headers['content-security-policy'] and r.headers['cache-control']=='no-store'

def test_pagination_bound_to_principal_folder_query_expiry():
    now=[1000];signer=PageSigner(b'x'*32,clock=lambda:now[0]);c=signer.issue('A','INBOX','q','opaque')
    assert signer.open(c,'A','INBOX','q')=='opaque'
    for args in [('B','INBOX','q'),('A','SENT','q'),('A','INBOX','other')]:
        with pytest.raises(InvalidPage):signer.open(c,*args)
    with pytest.raises(InvalidPage):signer.open(c+'a','A','INBOX','q')
    now[0]+=1801
    with pytest.raises(InvalidPage):signer.open(c,'A','INBOX','q')

def test_bad_cursor_before_provider(app,client):
    b=app.state.boxes['harbor'];r=client.get('/mail?cursor=bad');assert r.status_code==409 and b.calls==[]

def test_profile_mismatch_stops_before_mail_read():
    b=FakeMailbox();s=GmailReadService(b,email='wrong@other.example',policy=ReadPolicy(8,11,15))
    with pytest.raises(MailUnavailable):s.verify()
    assert len(b.calls)==1

def test_duplicate_and_foreign_thread_rejected():
    b=FakeMailbox();s=GmailReadService(b,email=b.email,policy=ReadPolicy(8,11,15));b.rows['H1']['messages'][0]['threadId']='foreign'
    with pytest.raises(MailUnavailable):s.conversation('H1')

def test_deadline_late_read_and_aggregate_call_guard():
    now=[1];b=FakeMailbox();s=GmailReadService(b,email=b.email,policy=ReadPolicy(1,4,2),clock=lambda:now[0]);original=b.profile
    def slow(**kw):now[0]=10;return original(**kw)
    b.profile=slow
    with pytest.raises(MailUnavailable):s.verify()
    assert len(b.calls)==1

def test_readonly_adapter_constructs_only_read_endpoints():
    from app.integrations.gmail.reader_candidate import GmailReadProvider
    calls=[]
    class Service:
        def users(self):return self
        def threads(self):return self
        def labels(self):return self
        def list(self,**kw):calls.append(('list',kw));return self
        def get(self,**kw):calls.append(('get',kw));return self
        def getProfile(self,**kw):calls.append(('profile',kw));return self
        def execute(self,**kw):calls.append(('execute',kw));return {}
    p=GmailReadProvider(service=Service(),set_timeout=lambda x:None,granted_scopes=('https://www.googleapis.com/auth/gmail.readonly',))
    p.threads(label='INBOX',query='older_than:1y',page_token='opaque',page_size=8,timeout=5)
    p.thread('H1',metadata_only=False,timeout=5)
    assert calls[0][1]['userId']=='me' and calls[0][1]['q']=='older_than:1y'
    assert all(c[1]=={'num_retries':0} for c in calls if c[0]=='execute')
    with pytest.raises(ValueError):GmailReadProvider(service=Service(),set_timeout=lambda x:None,granted_scopes=('https://www.googleapis.com/auth/gmail.modify',))

def test_nothing_installed_by_default_and_no_write_routes():
    a=FastAPI();assert not any(r.path.startswith('/mail') for r in a.routes)
    a=create_fixture_app()
    assert all(r.methods<= {'GET','HEAD'} for r in a.routes)

def test_plain_parts_all_text_not_just_first_attachment_and_cap():
    from app.services.gmail_reader import plain_parts
    enc=lambda s:base64.urlsafe_b64encode(s.encode()).decode()
    p={'mimeType':'multipart/mixed','parts':[{'mimeType':'text/plain','body':{'data':enc('first')}},{'mimeType':'text/plain','body':{'data':enc('second')}},{'filename':'notes.pdf','body':{'attachmentId':'private'}}]}
    body,trunc,files=plain_parts(p);assert body=='first\n\nsecond' and not trunc and files==['notes.pdf']
    p={'mimeType':'text/plain','body':{'data':enc('a'*128001)}};assert plain_parts(p)[1]

def test_provider_failure_sanitized(app,client):
    def bad(**kw):raise RuntimeError('private-token-secret')
    app.state.boxes['harbor'].threads=bad
    r=client.get('/mail');assert r.status_code==503 and 'private-token' not in r.text

def test_local_fixture_rejects_remote_host(client):assert client.get('/mail',headers={'host':'foreign.example'}).status_code==403

# Compose new routes with the existing explicit protected auth constructor.
# Reuses fake exchange fixture; no Google, SQLCipher or real credentials.
from tests.test_protected_auth_candidate import harness, login

def test_protected_auth_reader_journey_no_anonymous_reads(harness):
    h=harness;boxes={'subject-a':FakeMailbox('a@example.test','A'),'subject-b':FakeMailbox('b@example.test','B')};selected=[]
    def bound(account,db):selected.append(account.google_user_id);return boxes[account.google_user_id]
    install_reader(h.app,provider_for=bound,page_signer=PageSigner(b'z'*32),policy=ReadPolicy(8,11,15))
    a,b=h.client(),h.client()
    assert a.get('/mail').status_code==401 and selected==[]
    login(h,a);assert 'a@example.test' in a.get('/mail').text and selected==['subject-a']
    h.identity.update(sub='subject-b',email='b@example.test',refresh_token='refresh-b');login(h,b)
    assert b.get('/mail/threads/A1').status_code==503
    assert 'b@example.test' in b.get('/mail').text
    assert 'A1' in a.get('/mail').text
    assert all(x in ('subject-a','subject-b') for x in selected)
    with h.storage.session() as db:
        from app.models import Email,Thread
        assert db.query(Email).count()==0 and db.query(Thread).count()==0

def test_page_token_does_not_include_google_subject():
    import json
    s=PageSigner(b'x'*32);token=s.issue('private-google-subject','INBOX','q','opaque')
    decoded=base64.urlsafe_b64decode(token.split('.')[0]+'='*(-len(token.split('.')[0])%4))
    assert b'private-google-subject' not in decoded

def test_reconnect_required_has_no_partial_mail(app,client):
    def revoked(**kw):raise ReconnectRequired('private_error')
    app.state.boxes['harbor'].profile=revoked
    r=client.get('/mail');assert r.status_code==503 and 'Reconnect your Gmail' in r.text and 'private_error' not in r.text and 'Design review' not in r.text

def test_malformed_and_aggregate_mime_are_bounded():
    from app.services.gmail_reader import plain_parts,thread,MailUnavailable
    with pytest.raises(MailUnavailable):plain_parts({'body':[]})
    with pytest.raises(MailUnavailable):plain_parts({'parts':[{}]*2001})
    with pytest.raises(MailUnavailable):thread({'id':'t','messages':[{'id':'m','threadId':'t','payload':[]}]},expected='t',metadata_only=True)
    piece={'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'a'*500000).decode()}}
    with pytest.raises(MailUnavailable):plain_parts({'parts':[piece]*4})

@pytest.mark.parametrize('path',['/mail?folder=bad%2Ffolder','/mail/threads/A1?folder=bad%2Ffolder'])
def test_invalid_folder_has_clear_client_error(client,path):
    assert client.get(path).status_code==400

def test_label_failure_keeps_independently_refreshed_inbox(app,client):
    from app.services.gmail_reader import MailUnavailable
    def failed(**kw):raise MailUnavailable('private-label-error')
    app.state.boxes['harbor'].labels=failed
    r=client.get('/mail')
    assert r.status_code==200 and 'Your label list could not be loaded' in r.text and 'Design review' in r.text and 'private-label-error' not in r.text

def test_label_reconnect_does_not_show_partial_inbox(app,client):
    def revoked(**kw):raise ReconnectRequired('private-label-error')
    app.state.boxes['harbor'].labels=revoked
    r=client.get('/mail')
    assert r.status_code==503 and 'Reconnect your Gmail' in r.text and 'Design review' not in r.text
