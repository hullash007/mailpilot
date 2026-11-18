"""Finite fake auth journey; no Google/network, migrations or cipher runtime."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.google_main import GooglePrincipal, SCOPES
from app.models import GmailAccount, OAuthState, Email, Thread
from app.integrations.gmail.oauth import OAuthFlowConfig
from app.services import crypto, oauth_state
from app.storage_factory import PreparedStorage
from app.protected_auth_candidate import AuthConfig, create_app, Sessions, COOKIE

ORIGIN = 'https://candidate.test'


@pytest.fixture
def harness(monkeypatch, tmp_path):
    def poison(*a, **kw):
        raise AssertionError('ambient settings or legacy token property accessed')
    import app.config as settings
    import app.google_main as google
    import app.services.browser_boundary as boundary
    import app.integrations.gmail.oauth as oauth
    for module in (settings, google, crypto, oauth_state, boundary, oauth):
        monkeypatch.setattr(module, 'get_settings', poison)
    import app.models as models
    monkeypatch.setattr(models, 'encrypt_tokens', poison)
    monkeypatch.setattr(models, 'decrypt_tokens', poison)
    engine = create_engine('sqlite:///' + str(tmp_path/'auth-fixture.db'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)  # explicit disposable fixture setup, not app startup
    storage = PreparedStorage(engine, sessionmaker(bind=engine))
    cipher = crypto.TokenCipher(Fernet.generate_key())
    signer = oauth_state.StateSigner(b's' * 32)
    cfg = AuthConfig(ORIGIN, OAuthFlowConfig('fake-id', 'fake-secret', ORIGIN + '/auth/google/callback', tuple(SCOPES)), b'k' * 32)
    calls, authorizations = [], []
    identity = dict(sub='subject-a', email='a@example.test', refresh_token='refresh-a')
    changes = {}
    def flow(config, state):
        assert config is cfg.oauth
        def authorization_url(**kwargs):
            authorizations.append((state, kwargs))
            return 'https://accounts.google.com/o/oauth2/auth?state=' + state, state
        return SimpleNamespace(code_verifier='verifier-' + state, authorization_url=authorization_url)
    def exchange(config, code, state, verifier):
        calls.append((config, code, state, verifier))
        if changes.get('raise'):
            raise RuntimeError('SENSITIVE ADAPTER DETAIL')
        creds = dict(token='access-' + identity['sub'], refresh_token=identity.get('refresh_token'),
                     token_uri='https://oauth2.googleapis.com/token', client_id=config.client_id,
                     client_secret=config.client_secret, scopes=list(SCOPES))
        claims = dict(iss='https://accounts.google.com', aud=config.client_id, exp=int(time.time())+600,
                      email_verified=True, sub=identity['sub'], email=identity['email'], nonce=signer.sign(state))
        claims.update(changes.get('claims', {}))
        creds.update(changes.get('creds', {}))
        return creds, claims
    kwargs = dict(storage=storage, config=cfg, cipher=cipher, state_signer=signer, flow_factory=flow, exchange_identity=exchange)
    app = create_app(**kwargs)
    h = SimpleNamespace(app=app, kwargs=kwargs, storage=storage, cipher=cipher, signer=signer,
        cfg=cfg, calls=calls, authorizations=authorizations, identity=identity, changes=changes)
    h.client = lambda: TestClient(app, base_url=ORIGIN, follow_redirects=False)
    yield h
    storage.close()


def start(client):
    response = client.get('/login')
    assert response.status_code == 303, response.text
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie
    return parse_qs(urlsplit(response.headers['location']).query)['state'][0]


def finish(client, state, **extra):
    return client.get('/auth/google/callback', params={'code': 'fake-code', 'state': state, **extra})


def login(h, client):
    state = start(client)
    response = finish(client, state)
    assert response.status_code == 303, response.text
    assert response.headers['location'] == '/workspace'
    return state


def test_complete_two_account_workspace_token_isolation(harness):
    h = harness
    a, b = h.client(), h.client()
    assert a.get('/workspace').status_code == 401
    login(h, a)
    h.identity.update(sub='subject-b', email='b@example.test', refresh_token='refresh-b')
    login(h, b)
    assert len(h.calls) == 2
    for _, _, state, verifier in h.calls:
        assert verifier == 'verifier-' + state
    with h.storage.session() as db:
        accounts = db.scalars(select(GmailAccount).order_by(GmailAccount.id)).all()
        assert len(accounts) == 2
        for account in accounts:
            suffix = account.google_user_id[-1]
            assert h.cipher.decrypt(account.credentials_blob)['refresh_token'] == 'refresh-' + suffix
            assert 'access-' not in account.credentials_blob
            thread = Thread(account_id=account.id, gmail_thread_id='thread-'+suffix, subject='Stored '+suffix)
            db.add(thread); db.flush()
            db.add(Email(account_id=account.id, thread_id=thread.id, gmail_message_id='msg-'+suffix,
                subject='Private '+suffix, body_text='Body only for '+suffix, is_sample=True))
        db.commit()
        second_message = db.scalar(select(Email.id).where(Email.subject=='Private b'))
    av = a.get('/workspace'); bv = b.get('/workspace')
    assert av.status_code == bv.status_code == 200
    assert 'Private a' in av.text and 'Private b' not in av.text
    assert 'Private b' in bv.text and 'Private a' not in bv.text
    # Browser-provided account selectors are not authority.
    assert 'Private b' not in a.get('/workspace?account_id=2').text
    assert a.get('/actions/source/'+str(second_message)).status_code == 404
    assert a.get('/app/sync').status_code == 404
    assert a.post('/app/sync', headers={'origin': ORIGIN}).status_code == 404
    assert a.get('/access').status_code == 404
    assert a.post('/logout').status_code == 403
    assert a.post('/logout', headers={'origin': ORIGIN}).status_code == 303
    assert a.get('/workspace').status_code == 401
    assert b.get('/workspace').status_code == 200


def test_state_browser_binding_expiry_and_replay_precede_exchange(harness):
    h = harness
    a, b = h.client(), h.client()
    state = start(a)
    assert finish(b, state).status_code == 403
    assert not h.calls
    assert finish(a, state).status_code == 303
    assert len(h.calls) == 1
    # Keep the original signed state cookie to test server-side single use too.
    a.cookies.set(oauth_state.COOKIE_NAME, h.signer.sign(state))
    assert finish(a, state).status_code == 403
    assert len(h.calls) == 1
    expired = start(b)
    with h.storage.session() as db:
        db.get(OAuthState, expired).expires_at = datetime.utcnow()-timedelta(seconds=1)
        db.commit()
    assert finish(b, expired).status_code == 403
    assert len(h.calls) == 1


@pytest.mark.parametrize('changes', [
    {'claims': {'sub': ''}}, {'claims': {'aud': 'other-client'}},
    {'claims': {'nonce': 'other-nonce'}}, {'claims': {'iss': 'bad-issuer'}},
    {'claims': {'exp': 1}}, {'claims': {'email_verified': False}},
    {'creds': {'scopes': ['https://www.googleapis.com/auth/gmail.modify']}},
    {'creds': {'client_id': 'other-client'}}, {'raise': True},
])
def test_invalid_identity_or_adapter_failure_sanitized_no_persistence(harness, changes):
    h = harness; h.changes.update(changes)
    c = h.client(); response = finish(c, start(c))
    assert response.status_code == 403
    assert 'SENSITIVE' not in response.text and 'fake-secret' not in response.text
    assert COOKIE not in response.cookies
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.id)) is None
        assert db.scalar(select(GooglePrincipal.subject)) is None


def test_refresh_token_retention_is_only_for_same_bound_subject(harness):
    h = harness; a, b = h.client(), h.client()
    login(h, a)
    with h.storage.session() as db:
        original = db.scalar(select(GmailAccount.credentials_blob))
    # Different subject with the existing address must never claim stored tokens.
    h.identity.update(sub='subject-b', refresh_token=None)
    assert finish(b, start(b)).status_code == 403
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.credentials_blob)) == original
        assert db.get(GooglePrincipal, 'subject-b') is None
    h.identity.update(sub='subject-a', refresh_token=None)
    login(h, a)
    with h.storage.session() as db:
        row = db.scalar(select(GmailAccount))
        assert h.cipher.decrypt(row.credentials_blob)['refresh_token'] == 'refresh-a'
        row.credentials_blob = 'BROKEN-SENSITIVE-BLOB'; db.commit()
    # Decryption failure cannot default to another key or overwrite old blob.
    response = finish(a, start(a))
    assert response.status_code == 403 and 'BROKEN' not in response.text
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.credentials_blob)) == 'BROKEN-SENSITIVE-BLOB'


def test_missing_dependencies_invalid_host_and_cross_app_sessions(harness):
    h = harness
    for name in h.kwargs:
        with pytest.raises((TypeError, ValueError)):
            create_app(**{**h.kwargs, name: None})
    with pytest.raises(ValueError):
        replace(h.cfg, origin='http://candidate.test')
    with pytest.raises(ValueError):
        replace(h.cfg, session_key=b'')
    with pytest.raises(ValueError):
        replace(h.cfg, oauth=replace(h.cfg.oauth, redirect_uri='https://elsewhere.test/callback'))
    with pytest.raises(ValueError):
        replace(h.cfg, oauth=replace(h.cfg.oauth, scopes=('https://www.googleapis.com/auth/gmail.modify',)))
    c = h.client()
    assert c.get('/', headers={'host': 'evil.test'}).status_code == 400
    login(h, c)
    cookie = c.cookies.get(COOKIE)
    assert Sessions(h.cfg.session_key, ORIGIN).subject(cookie) == 'subject-a'
    assert Sessions(b'z'*32, ORIGIN).subject(cookie) is None
    other_app = create_app(**{**h.kwargs, 'config': replace(h.cfg, session_key=b'z'*32)})
    other = TestClient(other_app, base_url=ORIGIN, follow_redirects=False)
    other.cookies.set(COOKIE, cookie)
    assert other.get('/workspace').status_code == 401


def test_concurrent_callback_consumes_state_once(harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h = harness; c = h.client(); state = start(c)
    cookie = h.signer.sign(state); barrier = Barrier(2)
    def attempt(_):
        client = h.client()
        client.cookies.set(oauth_state.COOKIE_NAME, cookie)
        barrier.wait()
        return finish(client, state).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [303, 403]
    assert len(h.calls) == 1
    with h.storage.session() as db:
        assert len(db.scalars(select(GooglePrincipal)).all()) == 1


def test_origin_and_legacy_session_cookie_cannot_authenticate(harness):
    h = harness; c = h.client(); login(h, c)
    value = c.cookies.get(COOKIE)
    assert Sessions(h.cfg.session_key, 'https://other.test').subject(value) is None
    c.cookies.clear()
    c.cookies.set('mailpilot_google_session', value)
    assert c.get('/workspace').status_code == 401


def test_credential_compare_and_set_refuses_inflight_overwrite(harness, monkeypatch):
    h = harness; c = h.client(); login(h, c)
    h.identity['refresh_token'] = None
    original_encrypt = h.cipher.encrypt
    newer = original_encrypt(dict(token='newer-access', refresh_token='newer-refresh'))
    changed = []
    def concurrent_update(payload):
        if not changed:
            changed.append(True)
            with h.storage.session() as db:
                row = db.scalar(select(GmailAccount).where(GmailAccount.google_user_id=='subject-a'))
                row.credentials_blob = newer
                db.commit()
        return original_encrypt(payload)
    monkeypatch.setattr(h.cipher, 'encrypt', concurrent_update)
    response = finish(c, start(c))
    assert response.status_code == 403
    with h.storage.session() as db:
        assert db.scalar(select(GmailAccount.credentials_blob)) == newer


def test_startup_has_no_ddl_or_ambient_dependency(harness):
    from sqlalchemy import event
    h = harness; statements = []
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(h.storage.engine, 'before_cursor_execute', record)
    with TestClient(h.app, base_url=ORIGIN) as client:
        assert client.get('/').status_code == 200
    assert not statements
