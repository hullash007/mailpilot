"""Local-controls source/fake composition. No real account operations authorized.

The injected exchange adapter is trusted to verify Google's token signature and
Gmail profile correspondence before returning credentials and identity claims.
Fake adapters test composition only, not Google authentication. No import route,
legacy binding migration, token refresh endpoint, startup DDL or ambient settings.
"""
import base64
from collections import deque
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse

from .database import get_db
from .google_main import TTL, GooglePrincipal, own_account, page, SCOPES
from .integrated_candidate import create_app as integrated_app
from .integrations.gmail.oauth import OAuthFlowConfig
from .services import oauth_state
from .services.crypto import TokenCipher
from .services.browser_boundary import BrowserBoundary
from .storage_factory import PreparedStorage
from .services import account_controls_candidate as controls
from .google_main import esc


COOKIE = 'mailpilot_controls_session'


class Sessions:
    """Distinct cookie/purpose; durable per-subject generation invalidates sessions."""
    def __init__(self, key, origin):
        self._key = hmac.new(key, ('mailpilot-controls-origin-v1:' + origin).encode(), hashlib.sha256).digest()

    def _sign(self, value):
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def issue(self, subject, generation):
        raw = json.dumps({'sub': subject, 'gen': generation, 'iat': int(time.time()),
            'nonce': secrets.token_hex(24)}, separators=(',', ':'))
        value = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
        return value + '.' + self._sign(value)

    def identity(self, cookie):
        try:
            if not cookie or len(cookie) > 2048:
                return None
            value, signature = cookie.split('.')
            if not hmac.compare_digest(signature, self._sign(value)):
                return None
            claims = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
            if (set(claims) != {'sub', 'gen', 'iat', 'nonce'}
                    or type(claims['sub']) is not str or not 0 < len(claims['sub']) <= 255
                    or type(claims['gen']) is not int or claims['gen'] < 0
                    or type(claims['iat']) is not int or not 0 <= time.time() - claims['iat'] <= TTL
                    or type(claims['nonce']) is not str or len(claims['nonce']) != 48):
                return None
            return claims['sub'], claims['gen']
        except Exception:
            return None


from .protected_auth_candidate import AuthConfig, _validate


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

    def browser_identity(request):
        identity = sessions.identity(request.cookies.get(COOKIE))
        if identity is None:
            raise HTTPException(401, 'Sign in with Google.')
        return identity

    def account_for_browser(request: Request, db=Depends(get_db)):
        subject, generation = browser_identity(request)
        try:
            return controls.authenticated_account(db, subject, generation,
                write=request.method not in {'GET', 'HEAD', 'OPTIONS'})
        except Exception:
            db.rollback()
            raise HTTPException(401, 'Sign in with Google.') from None
    app.dependency_overrides[own_account] = account_for_browser

    @app.get('/')
    def home():
        return page('<h1>Account controls candidate</h1><p>Source qualification only. Fictional local tests are not real Google authentication. Stored mail only; AI is off. No import, migration or provider refresh route.</p><a class="button" href="/login">Start configured sign-in</a><p><a href="/account">Account controls</a></p>')

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
            controls.issue_login(db, state, flow.code_verifier)
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
            verifier, issued_generation = controls.consume_login(db, state)
            if verifier is None:
                raise ValueError('State unavailable.')
            credentials, claims = exchange_identity(config.oauth, code, state, verifier)
            _validate(config, state_signer, state, claims, credentials)
            generation = controls.link_identity(db, claims, credentials, cipher, issued_generation)
        except Exception:
            db.rollback()
            raise HTTPException(403, 'Identity or credential storage could not be verified. Restart sign-in.') from None
        response = RedirectResponse('/workspace', status_code=303)
        response.set_cookie(COOKIE, sessions.issue(claims['sub'], generation), max_age=TTL, httponly=True, secure=True, samesite='lax')
        response.delete_cookie(oauth_state.COOKIE_NAME, httponly=True, secure=True, samesite='lax')
        return response

    @app.post('/logout')
    def logout():
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite='lax')
        return response
    @app.get('/account')
    def settings(request: Request, account=Depends(account_for_browser)):
        connected = bool(account.credentials_blob)
        body = '<h1>Account controls</h1><p>' + esc(account.email_address) + '</p>'
        body += '<p>Local credentials: ' + ('stored' if connected else 'removed') + '.</p>'
        body += '<p>Source candidate. These local operations do not revoke the Google grant or delete mail in Gmail. No provider request is made.</p>'
        body += '<p>Disconnect removes local credentials and invalidates other MailPilot sessions. Imported mail stays readable in this browser. A new explicit Google sign-in can reconnect.</p>'
        body += '<p>Erase removes active mailbox records, local action decisions, sync metadata and credentials. It blocks ordinary access from all prior MailPilot sessions. A minimal subject fence and operation receipts remain to block older callbacks and confirm outcomes. Pending login state expires after 10 minutes. Backups, snapshots and logs are not erased by this control. No retention or backup-expiry promise is made. Already displayed or saved copies on your devices are not removed.</p>'
        for kind, label in [('disconnect', 'Disconnect locally'), ('erase', 'Erase active local data')]:
            operation_id = secrets.token_hex(24)
            body += '<form method="post" action="/account/' + kind + '"><input type="hidden" name="operation_id" value="' + operation_id + '">'
            body += '<p><label><input type="checkbox" name="confirmed" value="yes" required> I understand the ' + kind + ' limits above.</label></p><button>' + label + '</button></form>'
        body += '<p><a href="/workspace">Stored conversations</a> | <a href="/login">Start a new Google sign-in</a></p>'
        return page('<section class="card">' + body + '</section>')

    def local_control(request, db, kind, operation_id, confirmed):
        subject, generation = browser_identity(request)
        if (confirmed != 'yes' or len(operation_id) != 48
                or any(c not in '0123456789abcdef' for c in operation_id)):
            raise HTTPException(422, 'Confirm the operation using account controls.')
        try:
            op = controls.apply_control(db, subject=subject, session_generation=generation,
                kind=kind, operation_id=operation_id)
            body = '<h1>Local ' + kind + ' committed</h1><p>Receipt: ' + esc(op.id) + '</p>'
            body += '<p>This confirms a past local transaction, not the current Google grant state. Remote revocation was not attempted. Gmail messages were not deleted.</p>'
            if kind == 'disconnect':
                body += '<p>Local credentials were removed and earlier sessions were invalidated. Imported mail was kept. A later explicit sign-in can reconnect.</p>'
            else:
                body += '<p>Active mailbox records and local decisions were erased. Minimal subject fence and operation receipts remain. Backups, snapshots and logs were not erased.</p>'
            body += '<p><a href="/account">Account controls</a> | <a href="/">Home</a></p>'
            response = page('<section class="card">' + body + '</section>')
            if kind == 'disconnect' and controls.fence_generation(db, subject) == op.generation:
                binding = db.get(GooglePrincipal, subject)
                if binding is not None:
                    response.set_cookie(COOKIE, sessions.issue(subject, op.generation), max_age=TTL, httponly=True, secure=True, samesite='lax')
            return response
        except Exception:
            db.rollback()
            response = page('<section class="card"><h1>Account operation unconfirmed</h1><p>We could not confirm the transaction outcome. Do not assume records were removed or unchanged. No remote revocation was attempted. Retry the same submitted form to check its durable receipt, or seek support before another operation.</p><p>Receipt: ' + esc(operation_id) + '</p></section>')
            response.status_code = 503
            return response

    @app.post('/account/disconnect')
    def disconnect(request: Request, operation_id: str = Form(...), confirmed: str = Form(''), db=Depends(get_db)):
        # Renew only this browser at the advanced fence after confirmed disconnect.
        # Prior sessions lose ordinary access; the original signed session can only
        # reconcile this exact receipt, not authorize a distinct operation.
        return local_control(request, db, 'disconnect', operation_id, confirmed)

    @app.post('/account/erase')
    def erase(request: Request, operation_id: str = Form(...), confirmed: str = Form(''), db=Depends(get_db)):
        return local_control(request, db, 'erase', operation_id, confirmed)

    return app
