from fastapi.testclient import TestClient
from app.private_reader_candidate import install_private_reader
from app.services.gmail_reader import ReadPolicy
from tests.test_protected_auth_candidate import harness as auth_harness,ORIGIN
from tests.test_account_controls_candidate import h,login


def install(h):
    install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15))


def test_anonymous_browser_has_usable_signin_but_json_stays401(h):
    install(h);c=h.client()
    for path in ('/mail','/mail/threads/fictional','/workspace','/account'):
        r=c.get(path,headers={'Accept':'text/html'})
        assert r.status_code==401 and 'text/html' in r.headers['content-type']
        assert 'href="/login"' in r.text and 'cannot send, delete or change' in r.text
        assert r.headers['cache-control']=='no-store'
        for accept in ('application/json','*/*'):
            r=c.get(path,headers={'Accept':accept})
            assert r.status_code==401
            if path.startswith('/mail'):assert r.json()=={'detail':'Sign in with Google.'}
    r=c.get('/',headers={'Accept':'text/html'})
    assert r.status_code==200 and 'href="/login"' in r.text and 'candidate' not in r.text
    assert 'no-store'==r.headers['cache-control']
    assert c.get('/',headers={'Accept':'text/html','Host':'foreign.invalid'}).status_code==400
    assert c.get('/mail',headers={'Accept':'text/html','Host':'foreign.invalid'}).status_code==400


def test_same_browser_login_state_callback_redirect_no_broader_scope(h):
    install(h);c=h.client()
    r=c.get('/mail',headers={'Accept':'text/html'});assert 'Sign in with Google' in r.text
    login(h,c)
    assert len(h.calls)==1
    assert h.calls[0][0].scopes==h.cfg.oauth.scopes
    assert c.get('/workspace',headers={'Accept':'text/html'}).status_code==200
