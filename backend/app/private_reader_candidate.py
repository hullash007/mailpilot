"""Explicit source-only protected reader composition. No current entrypoint calls it.

Uses the existing controls cookie and durable fence, not email as an identity.
Fresh lifecycle checks bracket each network response. No lock across network;
already-issued provider reads cannot be recalled after disconnect.
"""
from dataclasses import dataclass
import hmac
from fastapi import Depends, HTTPException, Request
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
