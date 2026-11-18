"""Explicit Google exchange adapter. Source/offline tests are not live proof.

No persistence, account selection, ambient settings, migration or import. The
protected callback still owns one-time state, policy checks and session issuance.
"""
from collections.abc import Mapping
import hmac
import json
import time
from types import SimpleNamespace

import requests
from google.oauth2 import id_token

from .oauth import OAuthFlowConfig, build_explicit_flow
from ...services.oauth_state import StateSigner

TOKEN_URL = 'https://oauth2.googleapis.com/token'
CERTS_URL = 'https://www.googleapis.com/oauth2/v1/certs'
PROFILE_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/profile'
READONLY = 'https://www.googleapis.com/auth/gmail.readonly'
SCOPES = frozenset(('openid', 'https://www.googleapis.com/auth/userinfo.email', READONLY))
MAX_RESPONSE = 256 * 1024
TIMEOUT = (3.05, 10)


class IdentityExchangeError(RuntimeError):
    """Sanitized public error. No underlying token/transport details."""


def _bounded(response, *args, **kwargs):
    """Requests response hook, before OAuth library accesses response.text.

    Limits decoded response bytes and checks elapsed time between chunks. Socket
    connect/read timeouts are NOT a hard end-to-end/DNS deadline.
    """
    started = time.monotonic()
    try:
        if response.status_code != 200:
            raise ValueError('Non-success response.')
        length = response.headers.get('content-length')
        if length and int(length) > MAX_RESPONSE:
            raise ValueError('Response too large.')
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=4096):
            size += len(chunk)
            if size > MAX_RESPONSE or time.monotonic() - started > 15:
                raise ValueError('Response limit exceeded.')
            chunks.append(chunk)
        response._content = b''.join(chunks)
        response._content_consumed = True
        if response.url == TOKEN_URL:
            raw = json.loads(response._content)
            if (type(raw) is not dict or type(raw.get('scope')) is not str
                    or not raw['scope'].strip()):
                raise ValueError('Missing actual granted scope.')
        return response
    finally:
        response.close()


def _lock_transport(session, endpoint, method):
    """Own fresh session only. Fixed destination; no redirects/proxy env/retries."""
    session.trust_env = False
    session.max_redirects = 0
    session.hooks['response'] = [_bounded]
    original_send = session.send
    def send(prepared, **kwargs):
        if prepared.url != endpoint or prepared.method != method:
            raise IdentityExchangeError('Google identity exchange failed.')
        kwargs.update(timeout=TIMEOUT, allow_redirects=False, stream=True, verify=True, proxies={})
        return original_send(prepared, **kwargs)
    session.send = send
    return session


def _claims(config, signer, state, claims, scopes):
    if (type(claims) is not dict or claims.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}
            or claims.get('aud') != config.client_id
            or type(claims.get('exp')) is not int or claims['exp'] <= time.time()
            or claims.get('email_verified') is not True
            or type(claims.get('sub')) is not str or not 0 < len(claims['sub']) <= 255
            or type(claims.get('email')) is not str or not claims['email']
            or type(claims.get('nonce')) is not str
            or not hmac.compare_digest(claims['nonce'], signer.sign(state))
            or READONLY not in scopes or not set(scopes) <= SCOPES):
        raise IdentityExchangeError('Google identity exchange failed.')


class GoogleIdentityAdapter:
    """Concrete installed-library path, with all test seams explicit.

    Sessions/flows are created afresh per exchange. Their factories and verifier
    are trusted code dependencies; constructor does not perform network access.
    """
    def __init__(self, *, state_signer, flow_factory, session_factory, verify_token):
        if (not isinstance(state_signer, StateSigner)
                or any(not callable(fn) for fn in (flow_factory, session_factory, verify_token))):
            raise ValueError('Explicit Google identity adapters are required.')
        self._signer = state_signer
        self._flow = flow_factory
        self._session = session_factory
        self._verify = verify_token

    def __call__(self, config, code, state, verifier):
        flow = cert_session = profile_session = None
        try:
            if (not isinstance(config, OAuthFlowConfig) or set(config.scopes) != SCOPES
                    or any(type(v) is not str or not v or len(v) > 4096 for v in (code, state, verifier))):
                raise ValueError('Invalid exchange input.')
            flow = self._flow(config, state)
            _lock_transport(flow.oauth2session, TOKEN_URL, 'POST')
            flow.code_verifier = verifier
            # Response hook bounds before parsing, redirects forbidden at send.
            token_response = flow.fetch_token(code=code, timeout=TIMEOUT, allow_redirects=False, stream=True)
            # Do not substitute requested scopes when Google omits granted scope.
            actual_scope = token_response.get('scope') if isinstance(token_response, Mapping) else None
            if type(actual_scope) is str and actual_scope.strip():
                scopes = actual_scope.split()
            elif type(actual_scope) is list and actual_scope and all(type(v) is str and v for v in actual_scope):
                scopes = list(actual_scope)
            else:
                raise ValueError('Missing granted scope.')
            credentials = flow.credentials
            if (type(credentials.id_token) is not str or not credentials.id_token
                    or type(credentials.token) is not str or not credentials.token
                    or credentials.client_id != config.client_id
                    or credentials.client_secret != config.client_secret
                    or credentials.token_uri != TOKEN_URL):
                raise ValueError('Invalid credentials.')
            cert_session = _lock_transport(self._session(), CERTS_URL, 'GET')
            def cert_request(url, method='GET', **kwargs):
                if url != CERTS_URL or method != 'GET':
                    raise ValueError('Unexpected certificate destination.')
                response = cert_session.request('GET', CERTS_URL, timeout=TIMEOUT,
                                                allow_redirects=False, stream=True)
                return SimpleNamespace(status=response.status_code, data=response.content,
                                       headers=response.headers)
            claims = self._verify(credentials.id_token, cert_request,
                                  audience=config.client_id, clock_skew_in_seconds=0)
            _claims(config, self._signer, state, claims, scopes)
            profile_session = _lock_transport(self._session(), PROFILE_URL, 'GET')
            response = profile_session.request('GET', PROFILE_URL,
                headers={'Authorization': 'Bearer ' + credentials.token},
                timeout=TIMEOUT, allow_redirects=False, stream=True)
            profile = json.loads(response.content)
            if (type(profile) is not dict or type(profile.get('emailAddress')) is not str
                    or profile['emailAddress'].casefold() != claims['email'].casefold()):
                raise ValueError('Profile identity mismatch.')
            return dict(token=credentials.token, refresh_token=credentials.refresh_token,
                token_uri=credentials.token_uri, client_id=credentials.client_id,
                client_secret=credentials.client_secret, scopes=scopes), dict(claims)
        except Exception:
            raise IdentityExchangeError('Google identity exchange failed.') from None
        finally:
            for session in (getattr(flow, 'oauth2session', None), cert_session, profile_session):
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass


def google_identity_adapter(*, state_signer):
    """Select explicit official dependencies, without calling a provider."""
    return GoogleIdentityAdapter(state_signer=state_signer, flow_factory=build_explicit_flow,
                                 session_factory=requests.Session, verify_token=id_token.verify_oauth2_token)
