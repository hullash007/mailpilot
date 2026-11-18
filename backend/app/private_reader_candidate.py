"""Explicit source-only protected reader composition. No current entrypoint calls it.

Uses the existing controls cookie and durable fence, not email as an identity.
Fresh lifecycle checks bracket each network response. No lock across network;
already-issued provider reads cannot be recalled after disconnect.
"""
from dataclasses import dataclass
import hmac
from fastapi import Depends, HTTPException, Request
from .google_main import page
from urllib.parse import urlsplit
from .account_controls_candidate import Sessions, COOKIE
from .services.account_controls_candidate import authenticated_account
from .google_main import GooglePrincipal
from .models import GmailAccount
from .database import get_db
from .services.crypto import TokenCipher
from .services.gmail_reader import PageSigner, ReconnectRequired
from .integrations.gmail.reader_rest_candidate import RestReadProvider
from .gmail_reader_candidate import install_reader


@dataclass(frozen=True)
class ReaderPrincipal:
    id: int
    google_user_id: str
    email_address: str
    generation: int


def install_private_reader(app, *, storage, auth, cipher, page_key, policy, session_factory=None):
    if not isinstance(cipher, TokenCipher):
        raise ValueError('Explicit cipher required')
    sessions = Sessions(auth.session_key, auth.origin)

    def sign_in_page():
        return page('<section class="welcome"><p class="eyebrow">YOUR MAIL, WITH ROOM TO FOCUS</p><h1>Find your email.<br>Follow the conversation.</h1><p class="lead">Search your Gmail, browse your labels and read conversations in one focused workspace.</p><p><a class="button" href="/login">Continue with Google</a></p><small>Use the Google account connected to your MailPilot workspace.</small><div class="features"><section><h2>Find the message you need</h2><p>Search across your mailbox, including older conversations.</p></section><section><h2>Your familiar folders</h2><p>Move between Inbox, Starred, Sent and your own labels.</p></section><section><h2>Read the whole conversation</h2><p>Keep messages together in a clear, focused reading view.</p></section></div></section>')

    @app.middleware('http')
    async def reader_browser_entry(request, call_next):
        # Browser navigation gets a usable entry, not raw API JSON. Explicit
        # JSON/default clients still get the original401. No auth bypass/data.
        browser = request.method in {'GET','HEAD'} and 'text/html' in request.headers.get('accept','').lower()
        if browser and request.url.path == '/' and request.headers.get('host') == urlsplit(auth.origin).netloc:
            response=sign_in_page()
            response.headers.update({'Cache-Control':'no-store','X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
            return response
        response=await call_next(request)
        if browser and request.url.path.startswith(('/mail','/workspace','/account')) and response.status_code==401:
            replacement=sign_in_page()
            # Preserve privacy/security headers installed by existing boundary.
            for name,value in response.headers.items():
                if name.lower() not in ('content-type','content-length'):
                    replacement.headers[name]=value
            replacement.status_code=401
            return replacement
        return response

    def principal(request: Request, db=Depends(get_db)):
        identity = sessions.identity(request.cookies.get(COOKIE))
        try:
            if identity is None:
                raise ValueError()
            subject, generation = identity
            account = authenticated_account(db, subject, generation)
            if not account.credentials_blob:
                raise ValueError()
            actor = ReaderPrincipal(account.id, subject, account.email_address, generation)
            db.rollback()  # release request read transaction before provider I/O
            return actor
        except Exception:
            raise HTTPException(401, 'Sign in with Google.') from None

    def provider_for(actor, unused_request_db):
        try:
            if not isinstance(actor, ReaderPrincipal):
                raise ValueError()
            with storage.session() as db:
                account = authenticated_account(db, actor.google_user_id, actor.generation)
                if account.id != actor.id or account.email_address != actor.email_address:
                    raise ValueError()
                blob = account.credentials_blob
                if type(blob) is not str or not 0 < len(blob) <= 65536:
                    raise ValueError()
                credentials = cipher.decrypt(blob)

            def assert_current():
                try:
                    with storage.session() as current:
                        account = authenticated_account(current, actor.google_user_id, actor.generation)
                        if (account.id != actor.id or account.email_address != actor.email_address
                                or not hmac.compare_digest(account.credentials_blob, blob)):
                            raise ValueError()
                except Exception:
                    raise ReconnectRequired('Reconnect Gmail') from None

            assert_current()
            kwargs = {'credentials': credentials, 'oauth_config': auth.oauth, 'assert_current': assert_current}
            if session_factory is not None:
                kwargs['session_factory'] = session_factory
            return RestReadProvider(**kwargs)
        except Exception:
            raise ReconnectRequired('Reconnect Gmail') from None

    install_reader(app, provider_for=provider_for, page_signer=PageSigner(page_key),
                   policy=policy, account_dependency=principal)
    return app
