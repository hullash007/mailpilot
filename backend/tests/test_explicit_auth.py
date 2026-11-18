"""Fake-only explicit primitives. Does not exercise browser auth or app wiring."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock
import hmac
from hashlib import sha256

import pytest
from cryptography.fernet import Fernet
from app.integrations.gmail import oauth
from app.services import crypto, oauth_state


def poison():
    raise AssertionError('ambient settings accessed')


def test_explicit_flow_no_ambient_or_shared_mutable_state(monkeypatch):
    monkeypatch.setattr(oauth, 'get_settings', poison)
    fake = Mock(side_effect=lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(oauth.Flow, 'from_client_config', fake)
    config = oauth.OAuthFlowConfig('fake-id', 'fake-secret', 'https://example.test/callback', ('readonly',))
    first = oauth.build_explicit_flow(config, 'state-a')
    second = oauth.build_explicit_flow(config, 'state-b')
    assert first is not second
    assert first.redirect_uri == config.redirect_uri
    assert fake.call_args_list[0].kwargs == dict(scopes=['readonly'], state='state-a')
    assert fake.call_args_list[0].args[0]['web']['client_secret'] == 'fake-secret'
    fake.call_args_list[0].kwargs['scopes'].append('changed')
    assert fake.call_args_list[1].kwargs['scopes'] == ['readonly']
    assert config.scopes == ('readonly',)
    assert 'fake-secret' not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.client_id = 'other'


@pytest.mark.parametrize('args', [('', 'secret', 'https://example.test/cb', ('r',)),
    ('id', '', 'https://example.test/cb', ('r',)), ('id', 'secret', '', ('r',)),
    ('id', 'secret', 'https://example.test/cb', []),
    ('id', 'secret', 'https://example.test/cb', ()),
    ('id', 'secret', 'https://example.test/cb', ('',))])
def test_missing_flow_config_fails(args):
    with pytest.raises(ValueError):
        oauth.OAuthFlowConfig(*args)


def test_explicit_cipher_no_ambient_format_and_wrong_key(monkeypatch):
    monkeypatch.setattr(crypto, 'get_settings', poison)
    key = Fernet.generate_key()
    cipher = crypto.TokenCipher(key)
    other = crypto.TokenCipher(Fernet.generate_key())
    payload = {'token': 'fictional-a', 'refresh_token': 'fictional-refresh'}
    blob = cipher.encrypt(payload)
    assert cipher.decrypt(blob) == payload
    import json
    assert json.loads(Fernet(key).decrypt(blob.encode())) == payload
    assert cipher.decrypt(Fernet(key).encrypt(json.dumps(payload).encode()).decode()) == payload
    with pytest.raises(crypto.TokenEncryptionError):
        other.decrypt(blob)
    with pytest.raises(crypto.TokenEncryptionError):
        cipher.decrypt('plaintext')
    for bad in (None, b'', b'bad-key', 'not-bytes'):
        with pytest.raises(crypto.TokenEncryptionError) as exc:
            crypto.TokenCipher(bad)
        assert 'bad-key' not in str(exc.value)


def test_explicit_signer_no_fallback_and_format(monkeypatch):
    monkeypatch.setattr(oauth_state, 'get_settings', poison)
    key = b'a' * 32
    signer = oauth_state.StateSigner(key)
    other = oauth_state.StateSigner(b'b' * 32)
    signature = signer.sign('fictional-state')
    assert signature == hmac.new(key, b'fictional-state', sha256).hexdigest()
    assert signer.matches(signature, 'fictional-state')
    assert not signer.matches(None, 'fictional-state')
    assert not signer.matches(signature, 'other-state')
    assert not other.matches(signature, 'fictional-state')
    for bad in (None, b'', b'short', 'not-bytes'):
        with pytest.raises(ValueError):
            oauth_state.StateSigner(bad)


def test_legacy_adapters_compatible(monkeypatch):
    key = Fernet.generate_key().decode()
    settings = SimpleNamespace(google_client_id='legacy-id', google_client_secret='legacy-secret',
        google_redirect_uri='https://legacy.test/callback', gmail_scopes=['legacy-scope'],
        token_encryption_key=key, oauth_state_secret='s' * 32)
    for module in (oauth, crypto, oauth_state):
        monkeypatch.setattr(module, 'get_settings', lambda: settings)
    fake = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(oauth.Flow, 'from_client_config', fake)
    assert oauth.build_flow('legacy-state').redirect_uri == settings.google_redirect_uri
    assert fake.call_args.kwargs == dict(scopes=['legacy-scope'], state='legacy-state')
    assert crypto.decrypt_tokens(crypto.encrypt_tokens({'token': 'fake'})) == {'token': 'fake'}
    assert oauth_state.cookie_matches(oauth_state.sign_state('legacy'), 'legacy')
    assert oauth_state.sign_state('legacy') == oauth_state.StateSigner(b's' * 32).sign('legacy')
    settings.oauth_state_secret = ''
    assert oauth_state.cookie_matches(oauth_state.sign_state('fallback'), 'fallback')
