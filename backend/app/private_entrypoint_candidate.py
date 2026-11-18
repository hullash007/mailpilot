"""Explicit single-tester private pilot factory. No import-time app creation.

Launch only after an independently reviewed offline release operation. This
module never downloads artifacts, migrates data, changes Caddy or stops services.
"""
import os
from pathlib import Path
from .private_bootstrap_candidate import PrivateConfig, create_private_app, PrivateBootstrapUnavailable
from .protected_auth_candidate import AuthConfig
from .google_main import SCOPES
from .integrations.gmail.oauth import OAuthFlowConfig
from .pinned_arm_runtime_candidate import load_reviewed_arm_driver

DATABASE = Path('/var/lib/mailpilot/mailpilot-protected-v1.db')
REQUIRED = ('PUBLIC_ORIGIN','GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REDIRECT_URI',
    'GOOGLE_ALLOWED_EMAIL','TOKEN_ENCRYPTION_KEY','OAUTH_STATE_SECRET','MAILPILOT_SCHEMA_VERSION')


def create_from_environment():
    """Explicit entrypoint factory, suitable for reviewed uvicorn --factory.

    Session and state use the existing approved signing seed with already
    separated purposes. DB key derives from token seed with explicit HKDFv1.
    Neither seed is copied to arguments, new files, responses or logs here.
    """
    try:
        values={name:os.environ[name] for name in REQUIRED}
        if any(not value for value in values.values()) or values['MAILPILOT_SCHEMA_VERSION']!='1':
            raise ValueError()
        if any(ch in values['GOOGLE_ALLOWED_EMAIL'] for ch in ',; \r\n'):
            raise ValueError()
        oauth=OAuthFlowConfig(values['GOOGLE_CLIENT_ID'],values['GOOGLE_CLIENT_SECRET'],
            values['GOOGLE_REDIRECT_URI'],tuple(SCOPES))
        auth=AuthConfig(values['PUBLIC_ORIGIN'],oauth,values['OAUTH_STATE_SECRET'].encode(),
            allowed_email=values['GOOGLE_ALLOWED_EMAIL'])
        config=PrivateConfig(auth=auth,database_path=DATABASE,schema_version=int(values['MAILPILOT_SCHEMA_VERSION']),
            token_key=values['TOKEN_ENCRYPTION_KEY'].encode('ascii'),state_key=values['OAUTH_STATE_SECRET'].encode())
        driver=load_reviewed_arm_driver()
        return create_private_app(config=config,reviewed_driver=driver)
    except Exception:
        raise PrivateBootstrapUnavailable('Private workspace configuration or storage is unavailable.') from None
