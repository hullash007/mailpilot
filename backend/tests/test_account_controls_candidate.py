"""Explicit disposable, two-account controls proof. No provider or real DB."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, event
from app.account_control_models import ControlBase, ControlClock, SubjectFence, PendingLogin, ControlOperation
from app.account_controls_candidate import create_app, COOKIE
from app.services import account_controls_candidate as service
from app.models import GmailAccount, Thread, Email
from app.google_main import GooglePrincipal
from app.action_models import ActionBase, ActionDecision
from app.sync_models import MailboxSync, SyncRun, MessageRemoteState, ThreadSnapshot
from tests.test_protected_auth_candidate import harness as auth_harness, ORIGIN


@pytest.fixture
def h(auth_harness):
    h = auth_harness
    ControlBase.metadata.create_all(h.storage.engine)
    ActionBase.metadata.create_all(h.storage.engine)
    with h.storage.session() as db:
        db.add(ControlClock(id=1, generation=0)); db.commit()
    h.app = create_app(**h.kwargs)
    h.client = lambda: TestClient(h.app, base_url=ORIGIN, follow_redirects=False)
    return h


def start(c):
    r = c.get('/login'); assert r.status_code == 303, r.text
    return parse_qs(urlsplit(r.headers['location']).query)['state'][0]


def finish(c, state):
    return c.get('/auth/google/callback', params={'code':'fictional', 'state':state})


def login(h, c, subject='a'):
    h.identity.update(sub='subject-'+subject, email=subject+'@example.test', refresh_token='refresh-'+subject)
    r = finish(c, start(c)); assert r.status_code == 303, r.text


def op(c, kind):
    r = c.get('/account'); assert r.status_code == 200, r.text
    return re.search('action="/account/'+kind+'"><input type="hidden" name="operation_id" value="([a-f0-9]+)"', r.text)[1]


def apply(c, kind, oid, **extra):
    return c.post('/account/'+kind, headers={'origin':ORIGIN}, data={'operation_id':oid, 'confirmed':'yes', **extra})


def seed(h):
    from datetime import datetime
    with h.storage.session() as db:
        for a in db.scalars(select(GmailAccount)).all():
            t=Thread(account_id=a.id, gmail_thread_id='thread', subject='Stored '+a.email_address); db.add(t);db.flush()
            e=Email(account_id=a.id,thread_id=t.id,gmail_message_id='message',subject='Mail '+a.email_address);db.add(e);db.flush()
            db.add_all([MailboxSync(account_id=a.id),SyncRun(account_id=a.id,phase='initial',outcome='done'),
                MessageRemoteState(account_id=a.id,gmail_message_id='message'),
                ThreadSnapshot(account_id=a.id,gmail_thread_id='thread',fetched_at=datetime.utcnow(),message_count=1),
                ActionDecision(account_id=a.id,source_email_id=e.id,detector_version='fake',evidence_fingerprint='fake',status='dismissed')])
        db.commit()


def snapshot(h, account_id):
    with h.storage.engine.connect() as c:
        return {m.__tablename__:list(c.execute(select(m.__table__).where(m.account_id==account_id))) for m in service.ERASE_MODELS}


@pytest.mark.parametrize('kind',['disconnect','erase'])
def test_two_account_complete_control_and_stale_callback_fence(h, kind):
    a,b,other=h.client(),h.client(),h.client()
    login(h,a); other.cookies.update(a.cookies); login(h,b,'b');seed(h)
    with h.storage.session() as db:
        bid=db.get(GooglePrincipal,'subject-b').account_id
        bcreds=db.get(GmailAccount,bid).credentials_blob
    before=snapshot(h,bid); bcookie=b.cookies.get(COOKIE)
    h.identity.update(sub='subject-a',email='a@example.test',refresh_token='refresh-a')
    pending=h.client(); state=start(pending)
    oid=op(a,kind);r=apply(a,kind,oid,account_id=str(bid));assert r.status_code==200,r.text
    assert 'not attempted' in r.text and 'committed' in r.text
    assert other.get('/workspace').status_code==401
    assert b.get('/workspace').status_code==200 and b.cookies.get(COOKIE)==bcookie
    assert snapshot(h,bid)==before
    with h.storage.session() as db:
        assert db.get(GmailAccount,bid).credentials_blob==bcreds
        assert db.get(SubjectFence,'subject-a').status==('erased' if kind=='erase' else 'disconnected')
        binding=db.get(GooglePrincipal,'subject-a')
        if kind=='erase':
            assert binding is None
            assert db.scalar(select(Email.account_id).where(Email.account_id !=bid)) is None
        else:
            assert db.get(GmailAccount,binding.account_id).credentials_blob==''
    assert finish(pending,state).status_code==403
    assert apply(a,kind,oid).status_code==200 # exact durable receipt replay
    if kind=='disconnect':
        assert a.get('/workspace').status_code==200
        assert 'removed' in a.get('/account').text
        assert apply(a,'erase',op(a,'erase')).status_code==200
    else:
        assert a.get('/workspace').status_code==401
    # Only an explicit new flow AFTER the fence may recreate/reconnect.
    login(h,a)
    assert a.get('/workspace').status_code==200


@pytest.mark.parametrize('kind',['disconnect','erase'])
def test_callback_verified_before_control_but_persisted_after_is_fenced(h,kind):
    a,p=h.client(),h.client();login(h,a)
    state=start(p); oid=op(a,kind)
    ready,proceed=Event(),Event()
    original=h.kwargs['exchange_identity']
    def exchange(*args):
        result=original(*args);ready.set();assert proceed.wait(5);return result
    app=create_app(**{**h.kwargs,'exchange_identity':exchange})
    p2=TestClient(app,base_url=ORIGIN,follow_redirects=False);p2.cookies.update(p.cookies)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(finish,p2,state);assert ready.wait(5)
        assert apply(a,kind,oid).status_code==200
        proceed.set();assert future.result().status_code==403
    with h.storage.session() as db:
        binding=db.get(GooglePrincipal,'subject-a')
        assert binding is None if kind=='erase' else db.get(GmailAccount,binding.account_id).credentials_blob==''


def test_callback_committed_before_control_cannot_win_after_final_disconnect(h):
    a,p=h.client(),h.client();login(h,a);oid=op(a,'disconnect')
    state=start(p);assert finish(p,state).status_code==303
    assert apply(a,'disconnect',oid).status_code==200
    assert p.get('/workspace').status_code==401
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.credentials_blob))==''


def test_missing_clock_inventory_schema_and_foreign_account_receipt_fail_closed(h):
    a,b=h.client(),h.client();login(h,a);login(h,b,'b')
    oid=op(a,'erase')
    with h.storage.engine.begin() as c:c.execute(text('CREATE TABLE extra_account_data (account_id INTEGER)'))
    r=apply(a,'erase',oid);assert r.status_code==503 and 'unconfirmed' in r.text
    assert a.get('/workspace').status_code==200
    with h.storage.engine.begin() as c:c.execute(text('DROP TABLE extra_account_data'))
    assert apply(a,'erase',oid).status_code==200
    assert apply(b,'erase',oid).status_code==503
    with h.storage.engine.begin() as c:c.execute(text('DELETE FROM candidate_control_clock'))
    assert b.get('/login').status_code==503


def test_csrf_confirmation_and_old_session_cannot_start_new_control(h):
    a,other=h.client(),h.client();login(h,a);other.cookies.update(a.cookies)
    oid=op(a,'disconnect')
    assert a.post('/account/disconnect',data={'operation_id':oid,'confirmed':'yes'}).status_code==403
    assert apply(a,'disconnect',oid,confirmed='').status_code==422
    assert apply(a,'disconnect',oid).status_code==200
    assert apply(other,'erase','f'*48).status_code==503
    assert other.get('/account').status_code==401


@pytest.mark.parametrize('failure',['before_commit','after_commit','readback'])
def test_control_outcome_ambiguity_and_exact_retry(h,monkeypatch,failure):
    from sqlalchemy.orm import Session
    a=h.client();login(h,a);oid=op(a,'erase')
    original_commit=Session.commit
    broken=[]
    def commit(db):
        target=any(isinstance(x,ControlOperation) for x in db.new)
        if target and not broken:
            broken.append(True)
            if failure=='before_commit':raise RuntimeError('PRIVATE PATH TOKEN')
            original_commit(db)
            if failure=='after_commit':raise RuntimeError('PRIVATE PATH TOKEN')
            # A committed write followed by uncertain durable-read failure.
            original_expire=db.expire_all
            def expire():raise RuntimeError('PRIVATE PATH TOKEN')
            db.expire_all=expire
            return
        return original_commit(db)
    monkeypatch.setattr(Session,'commit',commit)
    r=apply(a,'erase',oid)
    assert r.status_code==503 and 'unconfirmed' in r.text and 'PRIVATE' not in r.text
    assert 'removed or unchanged' in r.text
    with h.storage.session() as db:
        assert (db.get(ControlOperation,oid) is None)==(failure=='before_commit')
    assert apply(a,'erase',oid).status_code==200


def test_restart_no_startup_ddl_and_unrelated_login_not_invalidated(h):
    a,b,p=h.client(),h.client(),h.client();login(h,a)
    h.identity.update(sub='subject-b',email='b@example.test',refresh_token='refresh-b')
    state=start(p)
    assert apply(a,'erase',op(a,'erase')).status_code==200
    assert finish(p,state).status_code==303 # A's clock advance is not B's fence
    statements=[]
    event.listen(h.storage.engine,'before_cursor_execute',lambda conn,cursor,statement,*args:statements.append(statement))
    app=create_app(**h.kwargs)
    with TestClient(app,base_url=ORIGIN,follow_redirects=False) as restarted:
        assert restarted.get('/').status_code==200
    assert not statements
    # New app reads the same durable fence, not process-memory state.
    new=TestClient(create_app(**h.kwargs),base_url=ORIGIN,follow_redirects=False)
    new.cookies.update(a.cookies);assert new.get('/workspace').status_code==401


def test_true_overlap_control_waits_for_callback_transaction_then_fences_session(h,monkeypatch):
    a,p=h.client(),h.client();login(h,a);oid=op(a,'disconnect');state=start(p)
    held,release,control_attempt=Event(),Event(),Event()
    original_link, original_lock=service._link,service.lock_clock
    def delayed_link(*args):
        held.set();assert release.wait(5);return original_link(*args)
    def observed_lock(db):
        if held.is_set():control_attempt.set()
        return original_lock(db)
    monkeypatch.setattr(service,'_link',delayed_link)
    monkeypatch.setattr(service,'lock_clock',observed_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback=pool.submit(finish,p,state);assert held.wait(5)
        control=pool.submit(apply,a,'disconnect',oid);assert control_attempt.wait(5)
        release.set();assert callback.result().status_code==303
        assert control.result().status_code==200
    assert p.get('/workspace').status_code==401
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.credentials_blob))==''


@pytest.mark.parametrize('table',[m.__tablename__ for m in service.ERASE_MODELS]+['google_principals','gmail_accounts'])
def test_each_delete_failure_rolls_back_fence_and_all_records(h,table):
    a,b=h.client(),h.client();login(h,a);login(h,b,'b');seed(h)
    oid=op(a,'erase')
    with h.storage.session() as db: aid=db.get(GooglePrincipal,'subject-a').account_id
    before=snapshot(h,aid)
    def break_delete(conn,cursor,statement,*args):
        if statement.startswith('DELETE FROM '+table+' '):
            raise RuntimeError('PRIVATE DELETE DETAIL')
    event.listen(h.storage.engine,'before_cursor_execute',break_delete)
    r=apply(a,'erase',oid)
    event.remove(h.storage.engine,'before_cursor_execute',break_delete)
    assert r.status_code==503 and 'PRIVATE' not in r.text
    assert snapshot(h,aid)==before
    with h.storage.session() as db:
        assert db.get(SubjectFence,'subject-a') is None
        assert db.get(ControlOperation,oid) is None
        assert db.get(GmailAccount,aid).credentials_blob
    assert apply(a,'erase',oid).status_code==200


def test_foreign_keys_enabled_and_missing_inventory_column_blocks_erase(h):
    # Every subsequently opened fixture connection enforces declared FKs.
    event.listen(h.storage.engine,'connect',lambda dbapi,record:dbapi.execute('PRAGMA foreign_keys=ON'))
    a=h.client();login(h,a);seed(h)
    oid=op(a,'erase');assert apply(a,'erase',oid).status_code==200
    login(h,a);oid=op(a,'erase')
    with h.storage.engine.begin() as db:db.execute(text('ALTER TABLE action_decisions_candidate ADD COLUMN unknown_private TEXT'))
    assert apply(a,'erase',oid).status_code==503
    assert a.get('/workspace').status_code==200


def test_no_provider_routes_no_cross_purpose_cookie_and_no_expired_state_exchange(h):
    from datetime import datetime,timedelta
    from app.protected_auth_candidate import COOKIE as OTHER_COOKIE
    a=h.client();login(h,a)
    for path in ['/app/sync','/refresh','/account/revoke','/access']:
        assert a.post(path,headers={'origin':ORIGIN}).status_code==404
    old=a.cookies.get(COOKIE);a.cookies.clear();a.cookies.set(OTHER_COOKIE,old)
    assert a.get('/workspace').status_code==401
    state=start(a)
    with h.storage.session() as db:
        db.get(PendingLogin,state).expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    count=len(h.calls);assert finish(a,state).status_code==403 and len(h.calls)==count


from tests.test_identity_adapter import h as official_h
from app.integrations.gmail import oauth

def test_official_identity_joins_controls_then_disconnect_and_erase(official_h, tmp_path, monkeypatch):
    h = official_h
    """Full callback -> official local signature -> cipher -> subject workspace."""
    from urllib.parse import urlsplit
    from fastapi.testclient import TestClient
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import GmailAccount
    from app.services.crypto import TokenCipher
    from app.storage_factory import PreparedStorage
    from app.account_controls_candidate import AuthConfig, create_app, COOKIE
    from app.services import oauth_state
    import app.models as models
    import app.google_main as legacy
    def poison(*a, **kw):
        raise AssertionError('Legacy settings or token property reached')
    monkeypatch.setattr(legacy, 'get_settings', poison)
    monkeypatch.setattr(models, 'encrypt_tokens', poison)
    monkeypatch.setattr(models, 'decrypt_tokens', poison)
    engine = create_engine('sqlite:///'+str(tmp_path/'fixture.db'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    ControlBase.metadata.create_all(engine)
    ActionBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(ControlClock.__table__.insert().values(id=1,generation=0))
    storage = PreparedStorage(engine, sessionmaker(bind=engine))
    cipher = TokenCipher(Fernet.generate_key())
    cfg = AuthConfig('https://candidate.test', h.cfg, b'k'*32)
    # Adapter h uses the real official Flow/verifier with fake HTTP sessions.
    # Login uses official flow too. Fake provider requires known verifier only.
    app = create_app(storage=storage, config=cfg, cipher=cipher, state_signer=h.signer,
                     flow_factory=oauth.build_explicit_flow, exchange_identity=h.value)
    client = TestClient(app, base_url=cfg.origin, follow_redirects=False)
    start = client.get('/login')
    assert start.status_code == 303, start.text
    query = parse_qs(urlsplit(start.headers['location']).query)
    assert query['code_challenge_method'] == ['S256']
    state = query['state'][0]
    # Bind offline token fixture to this real flow's pending state/PKCE value.
    h.changes['claims'] = {'nonce': h.signer.sign(state)}
    with storage.session() as db:
        expected_verifier = db.get(PendingLogin, state).code_verifier
    h.changes['expected_verifier'] = expected_verifier
    assert len(expected_verifier) >= 43
    response = client.get('/auth/google/callback', params={'code':'fake-code', 'state':state})
    assert response.status_code == 303, response.text
    assert response.headers['location'] == '/workspace'
    assert COOKIE in client.cookies
    assert client.get('/workspace').status_code == 200
    with storage.session() as db:
        row = db.scalar(select(GmailAccount))
        assert row.google_user_id == 'fictional-subject'
        assert cipher.decrypt(row.credentials_blob)['refresh_token'] == 'fictional-refresh-token'
    assert apply(client, 'disconnect', op(client, 'disconnect')).status_code == 200
    assert client.get('/workspace').status_code == 200
    assert apply(client, 'erase', op(client, 'erase')).status_code == 200
    assert client.get('/workspace').status_code == 401
    assert len(h.seen) == 3  # Only token/cert/profile exchange; controls never call provider.
    storage.close()
