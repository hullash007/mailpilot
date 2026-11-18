"""Offline official Flow/verifier integration. No real credentials or network.

Locally-generated ephemeral RSA key signs fictional JWTs; requests adapters serve
fake certificate/token/profile responses. This is not live Google key proof.
"""
import io
import json
import time
from urllib.parse import parse_qs

import pytest
import requests
from requests.adapters import BaseAdapter
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from google.auth import jwt
from google.auth.crypt import RSASigner
from google.oauth2 import id_token

from app.integrations.gmail import identity_adapter as adapter
from app.integrations.gmail import oauth
from app.google_main import SCOPES
from app.services.oauth_state import StateSigner


@pytest.fixture
def h(monkeypatch):
    from types import SimpleNamespace
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    state_signer = StateSigner(b's' * 32)
    config = oauth.OAuthFlowConfig('fake-client', 'fake-client-secret', 'https://candidate.test/auth/google/callback', tuple(SCOPES))
    state = 'fictional-state'
    claims = dict(iss='https://accounts.google.com', aud=config.client_id, sub='fictional-subject',
        email='fictional@example.test', email_verified=True, nonce=state_signer.sign(state),
        iat=int(time.time()), exp=int(time.time())+600)
    changes, seen, sessions = {}, [], []
    def encoded():
        payload = {**claims, **changes.get('claims', {})}
        return jwt.encode(RSASigner.from_string(private, key_id='local-fixture'), payload).decode()
    class OfflineHTTP(BaseAdapter):
        def send(self, request, **kwargs):
            seen.append((request, kwargs))
            assert kwargs['timeout'] == adapter.TIMEOUT
            assert kwargs['verify'] is True
            assert kwargs['stream'] is True
            assert kwargs['proxies'] == {}
            url = request.url
            response = requests.Response()
            response.status_code = changes.get('status', {}).get(url, 200)
            if url == adapter.TOKEN_URL:
                assert request.method == 'POST'
                body = parse_qs(request.body)
                assert body['code'] == ['fake-code']
                assert body['code_verifier'] == [changes.get('expected_verifier', 'v' * 64)]
                payload = dict(access_token='fictional-access-token', token_type='Bearer', expires_in=3600,
                    refresh_token='fictional-refresh-token', id_token=encoded(), scope=' '.join(SCOPES))
                payload.update(changes.get('token', {}))
            elif url == adapter.CERTS_URL:
                assert request.method == 'GET'
                assert 'Authorization' not in request.headers
                payload = {'local-fixture': changes.get('cert', public)}
            elif url == adapter.PROFILE_URL:
                assert request.method == 'GET'
                assert request.headers['Authorization'] == 'Bearer fictional-access-token'
                payload = changes.get('profile', {'emailAddress': 'FICTIONAL@example.test'})
            else:
                raise AssertionError('Unexpected URL attempted')
            response.request = request
            response.url = url
            data = json.dumps(payload).encode()
            if changes.get('oversize') == url:
                data = b'x' * (adapter.MAX_RESPONSE+1)
            response.raw = io.BytesIO(data)
            response.headers['content-type'] = 'application/json'
            if response.status_code == 302:
                response.headers['Location'] = 'https://untrusted.example.test/steal'
            return response
        def close(self):
            pass
    def session():
        value = requests.Session(); value.mount('https://', OfflineHTTP()); sessions.append(value)
        return value
    def flow_factory(cfg, state):
        flow = oauth.build_explicit_flow(cfg, state)
        flow.oauth2session.mount('https://', OfflineHTTP())
        sessions.append(flow.oauth2session)
        return flow
    def poison(*a, **kw):
        raise AssertionError('Ambient settings accessed')
    monkeypatch.setattr(oauth, 'get_settings', poison)
    value = adapter.GoogleIdentityAdapter(state_signer=state_signer, flow_factory=flow_factory,
        session_factory=session, verify_token=id_token.verify_oauth2_token)
    return SimpleNamespace(value=value, cfg=config, state=state, signer=state_signer,
                           changes=changes, seen=seen, sessions=sessions)


def exchange(h):
    return h.value(h.cfg, 'fake-code', h.state, 'v'*64)


def test_offline_official_flow_signature_profile_and_transport(h):
    credentials, claims = exchange(h)
    assert claims['sub'] == 'fictional-subject'
    assert credentials['refresh_token'] == 'fictional-refresh-token'
    assert set(credentials['scopes']) == set(SCOPES)
    assert [r.url for r, _ in h.seen] == [adapter.TOKEN_URL, adapter.CERTS_URL, adapter.PROFILE_URL]
    assert len(h.sessions) == 3
    assert all(not s.trust_env and s.max_redirects == 0 for s in h.sessions)
    # Same adapter may serve independent calls, without reusing per-flow sessions.
    second = exchange(h)
    assert second == (credentials, claims)
    assert len(h.sessions) == 6


@pytest.mark.parametrize('changes', [
    {'claims': {'iss': 'https://untrusted.test'}}, {'claims': {'aud': 'wrong-client'}},
    {'claims': {'exp': 1}}, {'claims': {'nonce': 'wrong-nonce'}},
    {'claims': {'sub': ''}}, {'claims': {'email_verified': False}},
    {'claims': {'email': ''}}, {'cert': 'not-a-public-key'},
    {'token': {'id_token': None}}, {'token': {'access_token': ''}},
    {'profile': {'emailAddress': 'someone-else@example.test'}},
])
def test_rejections_do_not_return_credentials(h, changes):
    h.changes.update(changes)
    with pytest.raises(adapter.IdentityExchangeError) as error:
        exchange(h)
    assert str(error.value) == 'Google identity exchange failed.'
    assert 'fictional-access-token' not in str(error.value)


@pytest.mark.parametrize('url', [adapter.TOKEN_URL, adapter.CERTS_URL, adapter.PROFILE_URL])
@pytest.mark.parametrize('status', [302, 500])
def test_status_redirects_fail_without_following_or_leaking(h, url, status):
    h.changes['status'] = {url: status}
    with pytest.raises(adapter.IdentityExchangeError):
        exchange(h)
    assert all(r.url != 'https://untrusted.example.test/steal' for r, _ in h.seen)
    assert h.seen[-1][0].url == url


@pytest.mark.parametrize('url', [adapter.TOKEN_URL, adapter.CERTS_URL, adapter.PROFILE_URL])
def test_response_byte_limit(h, url):
    h.changes['oversize'] = url
    with pytest.raises(adapter.IdentityExchangeError):
        exchange(h)
    assert h.seen[-1][0].url == url


def test_missing_actual_scope_is_not_requested_scope_fallback(h):
    h.changes['token'] = {'scope': ''}
    with pytest.raises(adapter.IdentityExchangeError):
        exchange(h)
    assert [r.url for r, _ in h.seen] == [adapter.TOKEN_URL]


def test_unexpected_endpoint_blocked_before_send_and_no_dependency_defaults(h):
    session = requests.Session()
    adapter._lock_transport(session, adapter.CERTS_URL, 'GET')
    with pytest.raises(adapter.IdentityExchangeError):
        session.get('https://untrusted.example.test')
    with pytest.raises(TypeError):
        adapter.GoogleIdentityAdapter(state_signer=h.signer)
    with pytest.raises(ValueError):
        adapter.GoogleIdentityAdapter(state_signer=None, flow_factory=lambda: None,
            session_factory=requests.Session, verify_token=id_token.verify_oauth2_token)


def test_offline_signed_identity_joins_protected_app_journey(h, tmp_path, monkeypatch):
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
    from app.protected_auth_candidate import AuthConfig, create_app, COOKIE
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
        from app.models import OAuthState
        expected_verifier = db.get(OAuthState, state).code_verifier
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
    storage.close()
