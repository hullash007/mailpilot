"""Explicit source-only auth composition. No production provider adapter bundled.

The injected exchange adapter is trusted to verify Google's token signature and
Gmail profile correspondence before returning credentials and identity claims.
Fake adapters test composition only, not Google authentication. No import route,
legacy binding migration, token refresh endpoint, startup DDL or ambient settings.
"""
import base64
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update, exists

from .database import get_db
from .google_main import TTL, GooglePrincipal, own_account, page, SCOPES
from .integrated_candidate import create_app as integrated_app
from .integrations.gmail.oauth import OAuthFlowConfig
from .models import GmailAccount
from .services import oauth_state
from .services.crypto import TokenCipher
from .services.browser_boundary import BrowserBoundary
from .storage_factory import PreparedStorage


COOKIE = 'mailpilot_protected_session'


@dataclass(frozen=True)
class AuthConfig:
    origin: str
    oauth: OAuthFlowConfig
    session_key: bytes = field(repr=False)
    allowed_email: str = ''

    def __post_init__(self):
        url = urlsplit(self.origin)
        if (url.scheme != 'https' or not url.hostname or url.path or url.query or url.fragment
                or url.username or url.password or not isinstance(self.oauth, OAuthFlowConfig)
                or self.oauth.redirect_uri != self.origin + '/auth/google/callback'
                or self.oauth.scopes != tuple(SCOPES)
                or type(self.session_key) is not bytes or len(self.session_key) < 32
                or type(self.allowed_email) is not str):
            raise ValueError('Explicit protected authentication configuration is incomplete or unsafe.')


class Sessions:
    """Legacy session payload shape, distinct cookie and origin-derived signing key."""
    def __init__(self, key, origin):
        self._key = hmac.new(key, ('mailpilot-protected-origin-v1:' + origin).encode(), hashlib.sha256).digest()

    def _sign(self, value):
        return hmac.new(self._key, b'mailpilot-google-session-v1:' + value.encode(), hashlib.sha256).hexdigest()

    def issue(self, subject):
        raw = json.dumps({'sub': subject, 'iat': int(time.time()), 'nonce': secrets.token_hex(24)}, separators=(',', ':'))
        value = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
        return value + '.' + self._sign(value)

    def subject(self, cookie):
        try:
            if not cookie or len(cookie) > 1024:
                return None
            value, signature = cookie.split('.')
            if not hmac.compare_digest(signature, self._sign(value)):
                return None
            claims = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
            if (set(claims) != {'sub', 'iat', 'nonce'}
                    or type(claims['sub']) is not str or not 0 < len(claims['sub']) <= 255
                    or type(claims['iat']) is not int or not 0 <= time.time() - claims['iat'] <= TTL
                    or type(claims['nonce']) is not str or len(claims['nonce']) != 48):
                return None
            return claims['sub']
        except Exception:
            return None


def _validate(config, signer, state, claims, credentials):
    # Defense in depth AFTER the adapter has performed cryptographic verification.
    if (type(claims) is not dict or type(credentials) is not dict
            or claims.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}
            or claims.get('aud') != config.oauth.client_id
            or type(claims.get('exp')) is not int or claims['exp'] <= time.time()
            or claims.get('email_verified') is not True
            or type(claims.get('sub')) is not str or not 0 < len(claims['sub']) <= 255
            or type(claims.get('email')) is not str or not claims['email']
            or claims.get('nonce') != signer.sign(state)
            or (config.allowed_email and claims['email'].casefold() != config.allowed_email.casefold())):
        raise ValueError('Identity could not be verified.')
    scopes = credentials.get('scopes')
    if (type(scopes) is not list or any(type(s) is not str for s in scopes)
            or 'https://www.googleapis.com/auth/gmail.readonly' not in scopes
            or not set(scopes) <= set(SCOPES)
            or credentials.get('client_id') != config.oauth.client_id
            or credentials.get('client_secret') != config.oauth.client_secret
            or credentials.get('token_uri') != 'https://oauth2.googleapis.com/token'
            or type(credentials.get('token')) is not str or not credentials['token']
            or (credentials.get('refresh_token') is not None and type(credentials['refresh_token']) is not str)):
        raise ValueError('Credentials could not be verified.')


def _link(db, claims, credentials, cipher):
    subject, address = claims['sub'], claims['email']
    binding = db.get(GooglePrincipal, subject)
    credentials = dict(credentials)
    if binding is None:
        if db.scalar(select(GmailAccount.id).where(GmailAccount.email_address == address)) is not None:
            raise ValueError('Existing mailbox requires explicit migration.')
        account = GmailAccount(email_address=address, google_user_id=subject,
                               credentials_blob=cipher.encrypt(credentials))
        db.add(account)
        db.flush()
        db.add(GooglePrincipal(subject=subject, account_id=account.id))
    else:
        account = db.get(GmailAccount, binding.account_id)
        if (account is None or account.google_user_id != subject
                or account.email_address.casefold() != address.casefold()):
            raise ValueError('Stored binding does not match.')
        original_blob = account.credentials_blob
        if not credentials.get('refresh_token'):
            old = cipher.decrypt(original_blob)
            credentials['refresh_token'] = old.get('refresh_token')
        encrypted = cipher.encrypt(credentials)
        changed = db.execute(update(GmailAccount).where(
            GmailAccount.id == account.id, GmailAccount.google_user_id == subject,
            GmailAccount.credentials_blob == original_blob,
            exists(select(GooglePrincipal.subject).where(
                GooglePrincipal.subject == subject, GooglePrincipal.account_id == account.id)))
            .values(credentials_blob=encrypted).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ValueError('Credential binding changed during sign-in.')
    db.commit()
    return account


def create_app(*, storage, config, cipher, state_signer, flow_factory, exchange_identity):
    """All dependencies are required. Adapters are trusted, not user input.

    exchange_identity(config, code, state, verifier) must verify token signature,
    issuer/audience/expiry and Google-vs-Gmail profile correspondence. It must not
    persist credentials. This module rechecks claims but cannot replace that proof.
    """
    if (not isinstance(storage, PreparedStorage) or not isinstance(config, AuthConfig)
            or not isinstance(cipher, TokenCipher) or not isinstance(state_signer, oauth_state.StateSigner)
            or not callable(flow_factory) or not callable(exchange_identity)):
        raise ValueError('Explicit protected authentication dependencies are required.')
    sessions = Sessions(config.session_key, config.origin)
    app = integrated_app(storage=storage)
    app.state.explicit_origin = config.origin
    app.state.prepared_storage = storage
    app.router.routes[:] = [r for r in app.routes if getattr(r, 'path', '').startswith(('/workspace', '/actions'))]
    app.user_middleware[:] = [m for m in app.user_middleware if m.cls is not BrowserBoundary]
    attempts, attempt_lock = deque(), threading.Lock()
    netloc = urlsplit(config.origin).netloc

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            storage.close()
    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.headers.get('host') != netloc:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse('Invalid host.', status_code=400)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('origin') != config.origin:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse('Same-origin request required.', status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin',
            'Strict-Transport-Security': 'max-age=31536000',
            'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    def account_for_browser(request: Request, db=Depends(get_db)):
        subject = sessions.subject(request.cookies.get(COOKIE))
        binding = db.get(GooglePrincipal, subject) if subject else None
        account = db.get(GmailAccount, binding.account_id) if binding else None
        if account is None or account.google_user_id != subject:
            raise HTTPException(401, 'Sign in with Google.')
        return account
    app.dependency_overrides[own_account] = account_for_browser

    @app.get('/')
    def home():
        return page('<h1>Explicit authentication candidate</h1><p>Source qualification only. No provider adapter is bundled. Fake tests are not real Google authentication. Stored mail only; AI is off. No import, migration or provider refresh route.</p><a class="button" href="/login">Start configured sign-in</a>')

    @app.get('/app')
    def original_inbox():
        return RedirectResponse('/workspace', status_code=303)

    @app.get('/login')
    def login(db=Depends(get_db)):
        with attempt_lock:
            now = time.monotonic()
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, 'Try again in one minute.')
            attempts.append(now)
        try:
            state = secrets.token_urlsafe(32)
            flow = flow_factory(config.oauth, state)
            url, returned = flow.authorization_url(access_type='offline', prompt='consent', nonce=state_signer.sign(state))
            if returned != state or type(flow.code_verifier) is not str or not flow.code_verifier:
                raise ValueError('Invalid OAuth flow.')
            oauth_state.issue_state(db, state, flow.code_verifier)
            response = RedirectResponse(url, status_code=303)
            response.set_cookie(oauth_state.COOKIE_NAME, state_signer.sign(state), max_age=600, httponly=True, secure=True, samesite='lax')
            return response
        except Exception:
            db.rollback()
            raise HTTPException(503, 'Sign-in is unavailable. Restart sign-in.') from None

    @app.get('/auth/google/callback')
    def callback(request: Request, code: str, state: str, db=Depends(get_db)):
        if not state_signer.matches(request.cookies.get(oauth_state.COOKIE_NAME), state):
            raise HTTPException(403, 'OAuth state does not match this browser.')
        try:
            verifier = oauth_state.consume_state(db, state)
            if verifier is None:
                raise ValueError('State unavailable.')
            credentials, claims = exchange_identity(config.oauth, code, state, verifier)
            _validate(config, state_signer, state, claims, credentials)
            _link(db, claims, credentials, cipher)
        except Exception:
            db.rollback()
            raise HTTPException(403, 'Identity or credential storage could not be verified. Restart sign-in.') from None
        response = RedirectResponse('/workspace', status_code=303)
        response.set_cookie(COOKIE, sessions.issue(claims['sub']), max_age=TTL, httponly=True, secure=True, samesite='lax')
        response.delete_cookie(oauth_state.COOKIE_NAME, httponly=True, secure=True, samesite='lax')
        return response

    @app.post('/logout')
    def logout():
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite='lax')
        return response
    return app
