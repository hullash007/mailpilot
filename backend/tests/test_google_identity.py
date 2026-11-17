"""Google-only sessions and own-mail boundaries. Synthetic identities and mail only."""
import time
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.config import get_settings
from app.database import Base, get_db
from app.models import GmailAccount, Email, Thread
from app import google_main as g
from app.services import oauth_state

ORIGIN = 'https://mailpilot.example'


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setenv('APP_ACCESS_TOKEN', 'legacy-owner-key-does-not-authenticate')
    monkeypatch.setenv('OAUTH_STATE_SECRET', 's' * 64)
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('PUBLIC_ORIGIN', ORIGIN)
    monkeypatch.setenv('COOKIE_SECURE', '1')
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'fixture-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'fixture-secret')
    monkeypatch.setenv('GOOGLE_ALLOWED_EMAIL', '')
    get_settings.cache_clear()
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    app = g.create_app()
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, base_url=ORIGIN)
    g._attempts.clear()
    yield client, db
    client.close()
    db.close()
    engine.dispose()
    g._attempts.clear()
    get_settings.cache_clear()


def claims(subject='subject-A', address='a@example.com', state='fixture-state'):
    return {'sub': subject, 'email': address, 'email_verified': True,
            'iss': 'https://accounts.google.com', 'aud': 'fixture-client',
            'exp': int(time.time()) + 600, 'nonce': oauth_state.sign_state(state)}


def login(client, db, monkeypatch, subject='subject-A', address='a@example.com'):
    state = 'fixture-state'
    monkeypatch.setattr(g, '_authorize', lambda: ('https://accounts.example/consent', state, 'pkce-verifier'))
    monkeypatch.setattr(g, '_exchange_identity', lambda code, s, v: ({'token': 'synthetic', 'refresh_token': 'synthetic-refresh'}, claims(subject, address, s)))
    start = client.get('/login', follow_redirects=False)
    assert start.status_code == 303 and start.headers['location'] == 'https://accounts.example/consent'
    assert 'Secure' in start.headers['set-cookie'] and 'HttpOnly' in start.headers['set-cookie'] and 'SameSite=lax' in start.headers['set-cookie']
    return client.get('/auth/google/callback?code=dummy&state=' + state, follow_redirects=False)


def add_mail(db, account_id, marker):
    thread = Thread(account_id=account_id, gmail_thread_id=marker)
    db.add(thread); db.flush()
    row = Email(account_id=account_id, thread_id=thread.id, gmail_message_id='same-message-id', subject=marker, sender=marker+'@example.com', snippet="Don&#39;t render &lt;script&gt;bad()&lt;/script&gt;", body_text='<script>'+marker+'</script>')
    db.add(row); db.commit()
    return row.id


def test_no_legacy_key_routes_or_shared_data(workspace):
    client, db = workspace
    assert client.get('/').status_code == 200
    assert 'No separate access key' in client.get('/').text
    for path in ['/accounts', '/emails', '/inbox', '/dashboard', '/connect', '/auth/google/start', '/auth/google/login', '/docs', '/openapi.json']:
        assert client.get(path, headers={'Authorization': 'Bearer legacy-owner-key-does-not-authenticate'}).status_code == 404
    client.cookies.set('mailpilot_access', 'legacy-owner-key-does-not-authenticate')
    assert client.get('/app').status_code == 401
    assert client.post('/access', data={'token':'legacy-owner-key-does-not-authenticate'}).status_code == 405


def test_google_login_creates_subject_session_without_key(workspace, monkeypatch):
    client, db = workspace
    response = login(client, db, monkeypatch)
    assert response.status_code == 303 and response.headers['location'] == '/app'
    cookie = response.headers.get_list('set-cookie')[0]
    assert all(value in cookie for value in ['HttpOnly', 'Secure', 'SameSite=lax'])
    assert 'legacy-owner-key' not in cookie
    assert g.session_subject(client.cookies.get(g.COOKIE)) == 'subject-A'
    assert client.get('/app').status_code == 200
    assert 'a@example.com' in client.get('/app').text


def test_two_subjects_same_message_id_are_isolated(workspace, monkeypatch):
    client, db = workspace
    assert login(client, db, monkeypatch).status_code == 303
    a = db.get(g.GooglePrincipal, 'subject-A').account_id
    a_mail = add_mail(db, a, 'PRIVATE-A')
    a_session = client.cookies.get(g.COOKIE)
    assert login(client, db, monkeypatch, 'subject-B', 'b@example.com').status_code == 303
    b = db.get(g.GooglePrincipal, 'subject-B').account_id
    b_mail = add_mail(db, b, 'PRIVATE-B')
    page = client.get('/app')
    assert 'PRIVATE-B' in page.text and 'PRIVATE-A' not in page.text
    assert client.get('/app/messages/'+str(a_mail)).status_code == 404
    assert client.get('/app/messages/'+str(b_mail)).status_code == 200
    client.cookies.set(g.COOKIE, a_session, domain='mailpilot.example', path='/')
    page = client.get('/app')
    assert 'PRIVATE-A' in page.text and 'PRIVATE-B' not in page.text
    assert client.get('/app/messages/'+str(b_mail)).status_code == 404
    assert client.get('/app/messages/999999').status_code == 404
    assert 'Don&#x27;t render &lt;script&gt;' in page.text
    assert '<script>bad()' not in page.text
    detail = client.get('/app/messages/'+str(a_mail)).text
    assert '&lt;script&gt;PRIVATE-A&lt;/script&gt;' in detail
    assert '<script>PRIVATE-A' not in detail


def test_unbound_legacy_email_cannot_be_claimed_by_matching_email(workspace, monkeypatch):
    client, db = workspace
    account = GmailAccount(email_address='a@example.com', credentials={'token':'legacy-token'})
    db.add(account); db.commit(); add_mail(db, account.id, 'LEGACY-PRIVATE')
    assert login(client, db, monkeypatch).status_code == 409
    assert db.get(g.GooglePrincipal, 'subject-A') is None
    assert client.get('/app').status_code == 401
    assert account.credentials['token'] == 'legacy-token'


@pytest.mark.parametrize('field,value', [('iss','https://evil.example'), ('aud','wrong-client'), ('exp',0), ('email_verified',False), ('email_verified','true'), ('sub',''), ('sub',123), ('email',''), ('nonce','wrong')])
def test_invalid_identity_claims_refused(workspace, field, value):
    item = claims(); item[field] = value
    with pytest.raises(ValueError):
        g._validate_identity(item, 'fixture-state')


def test_configured_pilot_account_limit(workspace, monkeypatch):
    monkeypatch.setenv('GOOGLE_ALLOWED_EMAIL','another@example.com'); get_settings.cache_clear()
    with pytest.raises(ValueError):g._validate_identity(claims(), 'fixture-state')


def test_state_cookie_required_replay_refused(workspace, monkeypatch):
    client, db = workspace
    assert client.get('/auth/google/callback?code=dummy&state=fixture-state').status_code == 403
    assert login(client, db, monkeypatch).status_code == 303
    assert client.get('/auth/google/callback?code=dummy&state=fixture-state').status_code == 403


def test_sync_uses_subject_and_bounded_five_with_origin(workspace, monkeypatch):
    client, db = workspace; login(client, db, monkeypatch)
    account_id = db.get(g.GooglePrincipal, 'subject-A').account_id
    seen=[]
    monkeypatch.setattr(g, 'ingest_account', lambda session, account, max_results: seen.append((account.id,max_results)))
    assert client.post('/app/sync').status_code == 403
    assert client.post('/app/sync', headers={'Origin':'null'}).status_code == 403
    assert client.post('/app/sync', headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/app/sync?account_id=999', headers={'Origin':ORIGIN}, follow_redirects=False).status_code == 303
    assert seen == [(account_id,5)]


def test_session_expiry_tampering_rotation_and_logout(workspace, monkeypatch):
    client, db = workspace; login(client, db, monkeypatch)
    cookie = client.cookies.get(g.COOKIE)
    assert g.session_subject(cookie+'bad') is None
    now = time.time()
    monkeypatch.setattr(g.time,'time',lambda:now+g.TTL+1)
    assert g.session_subject(cookie) is None
    monkeypatch.setattr(g.time,'time',lambda:now)
    assert client.post('/logout').status_code == 403
    assert client.post('/logout',headers={'Origin':ORIGIN},follow_redirects=False).status_code == 303
    assert client.get('/app').status_code == 401
    monkeypatch.setenv('OAUTH_STATE_SECRET','new-key'*12);get_settings.cache_clear()
    assert g.session_subject(cookie) is None


def test_host_and_security_headers(workspace):
    client, db = workspace
    assert client.get('/',headers={'Host':'wrong.example'}).status_code == 400
    response=client.get('/')
    for header in ['cache-control','content-security-policy','strict-transport-security','x-frame-options','referrer-policy','x-content-type-options']:
        assert header in response.headers


def test_actual_authorization_library_carries_openid_nonce_and_pkce(workspace):
    from urllib.parse import urlsplit,parse_qs
    url,state,verifier=g._authorize()
    query=parse_qs(urlsplit(url).query)
    assert query['state']==[state]
    assert query['nonce']==[oauth_state.sign_state(state)]
    assert query['redirect_uri']==['http://localhost:8000/auth/google/callback']
    assert set(query['scope'][0].split())==set(g.SCOPES)
    assert query['code_challenge_method']==['S256']
    assert len(verifier)>=43


def legacy_fixture(db, count=2):
    account=GmailAccount(email_address='a@example.com',credentials={'token':'original-legacy-token'})
    db.add(account);db.commit()
    for n in range(count):add_mail_unique(db,account.id,'legacy-'+str(n))
    return account.id


def add_mail_unique(db, account_id, marker):
    thread=Thread(account_id=account_id,gmail_thread_id=marker);db.add(thread);db.flush()
    row=Email(account_id=account_id,thread_id=thread.id,gmail_message_id=marker,subject=marker,sender='fixture@example.com',recipients='a@example.com',snippet='snippet',body_text='body-'+marker)
    db.add(row);db.commit()


def fake_mailbox(db,account_id,monkeypatch,mutate=None):
    snapshot=g._mail_snapshot(db,account_id)
    messages={row[1]:dict(zip(['gmail_message_id','gmail_thread_id','subject','sender','recipients','snippet','body_text','internal_date'],row[1:])) for row in snapshot}
    if mutate:mutate(messages)
    class Fresh:
        def __init__(self,credentials):assert credentials['token']=='fresh-token'
        def get_profile(self):return {'emailAddress':'a@example.com'}
        def get_message(self,identifier):return messages[identifier]
    monkeypatch.setattr(g,'GmailClient',Fresh)
    return snapshot


def test_exact_owner_migration_requires_fresh_subject_and_full_message_proof(workspace,monkeypatch):
    client,db=workspace;account_id=legacy_fixture(db)
    original=fake_mailbox(db,account_id,monkeypatch)
    account=g.migrate_verified_owner(db,claims(),{'token':'fresh-token'},'fixture-state',account_id,'a@example.com',2)
    assert account.id==account_id and account.google_user_id=='subject-A'
    assert db.get(g.GooglePrincipal,'subject-A').account_id==account_id
    assert g._mail_snapshot(db,account_id)==original
    assert account.credentials['token']=='original-legacy-token'
    assert 'fresh-token' not in account.credentials_blob
    with pytest.raises(Exception):g.migrate_verified_owner(db,claims('other-sub'),{'token':'fresh-token'},'fixture-state',account_id,'a@example.com',2)


@pytest.mark.parametrize('defect',['body','message_id','thread_id','sender','date','count','wrong_owner','invalid_identity'])
def test_legacy_migration_rejects_without_changing_data(workspace,monkeypatch,defect):
    client,db=workspace;account_id=legacy_fixture(db)
    fields={'body':'body_text','message_id':'gmail_message_id','thread_id':'gmail_thread_id','sender':'sender','date':'internal_date'}
    def mutate(messages):
        if defect in fields:next(iter(messages.values()))[fields[defect]]='different'
    original=fake_mailbox(db,account_id,monkeypatch,mutate)
    identity=claims()
    if defect=='invalid_identity':identity['nonce']='wrong'
    with pytest.raises(Exception):
        g.migrate_verified_owner(db,identity,{'token':'fresh-token'},'fixture-state',account_id,'other@example.com' if defect=='wrong_owner' else 'a@example.com',1 if defect=='count' else 2)
    db.rollback();account=db.get(GmailAccount,account_id)
    assert account.google_user_id is None
    assert account.credentials['token']=='original-legacy-token'
    assert db.get(g.GooglePrincipal,'subject-A') is None
    assert g._mail_snapshot(db,account_id)==original


def test_legacy_migration_compare_and_set_refuses_changed_credentials(workspace,monkeypatch):
    client,db=workspace;account_id=legacy_fixture(db);fake_mailbox(db,account_id,monkeypatch)
    original_client=g.GmailClient
    class Changed(original_client):
        def get_profile(self):
            account=db.get(GmailAccount,account_id);account.credentials={'token':'intervening-token'};db.commit()
            return super().get_profile()
    monkeypatch.setattr(g,'GmailClient',Changed)
    with pytest.raises(Exception):g.migrate_verified_owner(db,claims(),{'token':'fresh-token'},'fixture-state',account_id,'a@example.com',2)
    assert db.get(GmailAccount,account_id).credentials['token']=='intervening-token'
    assert db.get(GmailAccount,account_id).google_user_id is None
    assert db.get(g.GooglePrincipal,'subject-A') is None


def test_legacy_migration_rolls_back_binding_when_commit_fails(workspace,monkeypatch):
    client,db=workspace;account_id=legacy_fixture(db);fake_mailbox(db,account_id,monkeypatch)
    def fail():raise RuntimeError('synthetic commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(db,'commit',fail)
        with pytest.raises(RuntimeError):g.migrate_verified_owner(db,claims(),{'token':'fresh-token'},'fixture-state',account_id,'a@example.com',2)
    account=db.get(GmailAccount,account_id)
    assert account.google_user_id is None and account.credentials['token']=='original-legacy-token'
    assert db.get(g.GooglePrincipal,'subject-A') is None


def test_oversized_message_id_is_validation_error_not_server_error(workspace,monkeypatch):
    client,db=workspace;login(client,db,monkeypatch)
    assert client.get('/app/messages/9223372036854775808').status_code==422
    assert client.get('/app/messages/0').status_code==422


def test_conflicting_first_account_insert_is_safe_409(workspace,monkeypatch):
    from sqlalchemy.exc import IntegrityError
    client,db=workspace
    original_flush=db.flush
    def conflict(objects=None):
        if db.new:raise IntegrityError('synthetic insert',{},Exception('synthetic unique conflict'))
        return original_flush(objects)
    with monkeypatch.context() as patch:
        patch.setattr(db,'flush',conflict)
        with pytest.raises(Exception) as result:g.link_verified(db,claims(),{'token':'dummy'})
        assert getattr(result.value,'status_code',None)==409
    assert db.get(g.GooglePrincipal,'subject-A') is None


def test_legacy_dry_run_changes_no_account_binding_or_ciphertext(workspace,monkeypatch):
    client,db=workspace;account_id=legacy_fixture(db);original=fake_mailbox(db,account_id,monkeypatch)
    before=db.get(GmailAccount,account_id).credentials_blob
    proof=g.migrate_verified_owner(db,claims(),{'token':'fresh-token'},'fixture-state',account_id,'a@example.com',2,dry_run=True)
    assert proof['matched_messages']==2 and len(proof['mail_sha256'])==64
    assert db.get(GmailAccount,account_id).credentials_blob==before
    assert db.get(GmailAccount,account_id).google_user_id is None
    assert db.get(g.GooglePrincipal,'subject-A') is None
    assert g._mail_snapshot(db,account_id)==original


def test_inconsistent_subject_account_binding_refused(workspace,monkeypatch):
    client,db=workspace;login(client,db,monkeypatch)
    binding=db.get(g.GooglePrincipal,'subject-A');account=db.get(GmailAccount,binding.account_id)
    account.google_user_id='wrong-subject';db.commit()
    assert client.get('/app').status_code==401


def test_callback_dry_run_does_not_bind_or_issue_google_session(workspace,monkeypatch):
    client,db=workspace;account_id=legacy_fixture(db);fake_mailbox(db,account_id,monkeypatch)
    monkeypatch.setenv('GOOGLE_ALLOWED_EMAIL','a@example.com')
    monkeypatch.setenv('MAILPILOT_LEGACY_BIND_ACCOUNT_ID',str(account_id))
    monkeypatch.setenv('MAILPILOT_LEGACY_BIND_MESSAGE_COUNT','2')
    monkeypatch.setenv('MAILPILOT_LEGACY_BIND_DRY_RUN','1')
    get_settings.cache_clear()
    monkeypatch.setattr(g,'_authorize',lambda:('https://accounts.example/consent','fixture-state','verifier'))
    monkeypatch.setattr(g,'_exchange_identity',lambda *args:({'token':'fresh-token'},claims()))
    client.get('/login',follow_redirects=False)
    response=client.get('/auth/google/callback?code=dummy&state=fixture-state',follow_redirects=False)
    assert response.status_code==200 and 'Qualification dry run only' in response.text
    assert client.cookies.get(g.COOKIE) is None
    assert db.get(g.GooglePrincipal,'subject-A') is None
    account=db.get(GmailAccount,account_id)
    assert account.google_user_id is None and account.credentials['token']=='original-legacy-token'


def test_concurrent_same_subject_first_login_has_one_binding(workspace,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from sqlalchemy import func
    client,unused=workspace
    engine=create_engine('sqlite:///'+str(tmp_path/'concurrent.db'),connect_args={'check_same_thread':False,'timeout':10})
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    barrier=threading.Barrier(2)
    def worker():
        with factory() as db:
            original=db.flush;first=True
            def synchronized(objects=None):
                nonlocal first
                if first and db.new:
                    first=False;barrier.wait(timeout=5)
                return original(objects)
            db.flush=synchronized
            return g.link_verified(db,claims(),{'token':'synthetic'}).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert results[0]==results[1]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(g.GooglePrincipal))==1
        assert db.scalar(select(func.count()).select_from(GmailAccount))==1
    engine.dispose()
