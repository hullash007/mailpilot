"""Explicit owner-private reader entrypoint. Prior factory/path remain available.

Invoked only by a reviewed immutable launcher. Reuses the existing protected
constructor, native loader, storage, environment, cookie, scopes and pilot gate.
No DDL, mail import, token writer or new secret provisioning.
"""
import hashlib
import hmac
import os
from .private_entrypoint_candidate import create_from_environment
from .protected_auth_candidate import AuthConfig
from .integrations.gmail.oauth import OAuthFlowConfig
from .google_main import SCOPES
from .services.crypto import TokenCipher
from .services.gmail_reader import ReadPolicy
from .private_reader_candidate import install_private_reader


def create_reader_from_environment():
    app = create_from_environment()  # all existing private bootstrap validation
    try:
        oauth = OAuthFlowConfig(os.environ['GOOGLE_CLIENT_ID'], os.environ['GOOGLE_CLIENT_SECRET'],
            os.environ['GOOGLE_REDIRECT_URI'], tuple(SCOPES))
        state_key = os.environ['OAUTH_STATE_SECRET'].encode()
        auth = AuthConfig(os.environ['PUBLIC_ORIGIN'], oauth, state_key,
            allowed_email=os.environ['GOOGLE_ALLOWED_EMAIL'])
        page_key = hmac.new(state_key, b'mailpilot-reader-continuation-v1', hashlib.sha256).digest()
        install_private_reader(app, storage=app.state.prepared_storage, auth=auth,
            cipher=TokenCipher(os.environ['TOKEN_ENCRYPTION_KEY'].encode('ascii')),
            page_key=page_key, policy=ReadPolicy(8, 11, 15))
        # Existing stored-mail path remains available, with the same session.
        # Only successful new Google callbacks select the current-provider inbox.
        @app.middleware('http')
        async def reader_landing(request, call_next):
            response = await call_next(request)
            if (request.url.path == '/auth/google/callback' and response.status_code == 303
                    and response.headers.get('location') == '/workspace'):
                response.headers['location'] = '/mail'
            return response
        return app
    except Exception:
        app.state.prepared_storage.close()
        raise RuntimeError('Private reader startup failed validation.') from None
