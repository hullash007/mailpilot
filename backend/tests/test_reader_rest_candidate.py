import json
from types import SimpleNamespace
import pytest
from app.integrations.gmail.reader_rest_candidate import RestReadProvider,BASE
from app.integrations.gmail.identity_adapter import TOKEN_URL,READONLY
from app.services.gmail_reader import MailUnavailable,ReconnectRequired,ReadPolicy

OAUTH=SimpleNamespace(client_id='fake-id',client_secret='fake-secret')
CREDS=dict(client_id='fake-id',client_secret='fake-secret',token_uri=TOKEN_URL,
    scopes=[READONLY,'openid','https://www.googleapis.com/auth/userinfo.email'],refresh_token='fake-refresh')
TOKEN=dict(access_token='fake-access',token_type='Bearer',expires_in=3600)

class Response:
    def __init__(self,value,status=200,headers=None,chunks=None):
        self.status_code=status;self.headers=headers or {};self.chunks=chunks or [json.dumps(value).encode()];self.closed=False
    def iter_content(self,chunk_size):return iter(self.chunks)
    def close(self):self.closed=True

class Wire:
    def __init__(self,values):self.values=list(values);self.calls=[];self.sessions=[];self.responses=[];self.after=None
    def factory(self):
        wire=self
        class Session:
            closed=False
            def request(self,method,url,**kwargs):
                wire.calls.append((method,url,kwargs))
                assert self.trust_env is False and self.max_redirects==0
                v=wire.values.pop(0);r=v if isinstance(v,Response) else Response(v);wire.responses.append(r)
                if wire.after:wire.after(len(wire.calls))
                return r
            def close(self):self.closed=True
        s=Session();self.sessions.append(s);return s

def provider(wire,guard=lambda:None,**kw):
    return RestReadProvider(credentials=CREDS,oauth_config=OAUTH,assert_current=guard,session_factory=wire.factory,**kw)

def test_fixed_destinations_refresh_once_and_closure():
    w=Wire([TOKEN,{'emailAddress':'a@example.test'},{'labels':[]},{'threads':[]},{'id':'tid'}]);p=provider(w)
    p.profile(timeout=10);p.labels(timeout=10);p.threads(label='INBOX',query='a&evil=1',page_token='opaque/?',page_size=8,timeout=10);p.thread('tid',metadata_only=True,timeout=10)
    assert [(m,u) for m,u,_ in w.calls]==[('POST',TOKEN_URL),('GET',BASE+'/profile'),('GET',BASE+'/labels'),('GET',BASE+'/threads'),('GET',BASE+'/threads/tid')]
    assert w.calls[3][2]['params']['q']=='a&evil=1' and w.calls[3][2]['params']['pageToken']=='opaque/?'
    for method,_,k in w.calls:
        assert k['allow_redirects'] is False and k['stream'] is True and k['verify'] is True and k['proxies']=={}
        assert 0<k['timeout'][0]<=3.05 and 0<k['timeout'][1]<=10
        assert ('Authorization' in k['headers'])==(method=='GET')
    assert all(s.closed for s in w.sessions) and all(r.closed for r in w.responses)
    assert CREDS['refresh_token']=='fake-refresh' and 'fake-access' not in repr(CREDS)

@pytest.mark.parametrize('response',[Response({},302),Response({},429),Response({},headers={'content-length':'70000'}),Response({},chunks=[b'x'*65537]),Response([],200),Response({},chunks=[b'not json'])])
def test_transport_failure_stops_and_closes(response):
    w=Wire([response]);p=provider(w)
    with pytest.raises(MailUnavailable):p.profile(timeout=10)
    assert len(w.calls)==1 and response.closed and w.sessions[0].closed

@pytest.mark.parametrize('changes',[{'scope':'https://www.googleapis.com/auth/gmail.modify'},{'refresh_token':'rotated-fake'},{'access_token':'bad\nheader'},{'token_type':'other'},{'expires_in':0}])
def test_invalid_or_rotated_refresh_never_uses_mail(changes):
    w=Wire([{**TOKEN,**changes}]);p=provider(w)
    with pytest.raises(ReconnectRequired):p.profile(timeout=10)
    assert len(w.calls)==1 and w.responses[0].closed

def test_revocation_after_response_discards_content():
    active=[True]
    def guard():
        if not active[0]:raise ReconnectRequired('Reconnect Gmail')
    w=Wire([TOKEN,{'emailAddress':'sensitive@example.test'}]);p=provider(w,guard)
    w.after=lambda n:active.__setitem__(0,False) if n==2 else None
    with pytest.raises(ReconnectRequired):p.profile(timeout=10)
    assert len(w.calls)==2 and all(r.closed for r in w.responses)

def test_deadline_checked_after_stream_no_retry():
    tick=[0.0];w=Wire([TOKEN]);p=provider(w,clock=lambda:tick[0]);w.after=lambda n:tick.__setitem__(0,11)
    with pytest.raises(MailUnavailable):p.profile(timeout=10)
    assert len(w.calls)==1 and w.responses[0].closed

@pytest.mark.parametrize('status',[400,401,403])
def test_refresh_failure_reconnect_no_retry(status):
    w=Wire([Response({},status)]);p=provider(w)
    with pytest.raises(ReconnectRequired):p.profile(timeout=10)
    assert len(w.calls)==1

from tests.test_account_controls_candidate import h,auth_harness,login,op,apply
from app.private_reader_candidate import install_private_reader
from app.models import GmailAccount,Email,Thread
from app.google_main import GooglePrincipal

def test_controls_private_reader_full_fake_journey_and_no_writeback(h):
    w=Wire([TOKEN,{'emailAddress':'a@example.test'},{'labels':[]},{'threads':[]}])
    install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15),session_factory=w.factory)
    a=h.client();assert a.get('/mail').status_code==401 and not w.calls
    login(h,a)
    with h.storage.session() as db:
        account=db.get(GmailAccount,db.get(GooglePrincipal,'subject-a').account_id);before=account.credentials_blob
    assert a.get('/mail').status_code==200
    with h.storage.session() as db:
        assert db.get(GmailAccount,account.id).credentials_blob==before
        assert db.query(Email).count()==0 and db.query(Thread).count()==0
    assert apply(a,'disconnect',op(a,'disconnect')).status_code==200
    assert a.get('/mail').status_code==401 and len(w.calls)==4

@pytest.mark.parametrize('kind',['disconnect','erase'])
def test_lifecycle_change_during_refresh_discards_response(h,kind):
    w=Wire([TOKEN]);install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15),session_factory=w.factory)
    a=h.client();login(h,a);oid=op(a,kind)
    w.after=lambda n:apply(a,kind,oid)
    r=a.get('/mail');assert r.status_code==503 and len(w.calls)==1
    with h.storage.session() as db:
        principal=db.get(GooglePrincipal,'subject-a')
        assert principal is None if kind=='erase' else not db.get(GmailAccount,principal.account_id).credentials_blob

def test_wrong_mail_profile_never_lists(h):
    w=Wire([TOKEN,{'emailAddress':'other@example.test'}]);install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15),session_factory=w.factory)
    a=h.client();login(h,a);r=a.get('/mail');assert r.status_code==503 and len(w.calls)==2 and 'other@example.test' not in r.text

def test_api_401_does_not_retry_or_refresh_again():
    w=Wire([TOKEN,Response({},401)]);p=provider(w)
    with pytest.raises(ReconnectRequired):p.profile(timeout=10)
    assert len(w.calls)==2 and all(r.closed for r in w.responses)

def test_reconnect_during_refresh_cannot_use_or_overwrite_new_credentials(h):
    w=Wire([TOKEN]);install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15),session_factory=w.factory)
    a=h.client();login(h,a);other=h.client()
    def relink(n):
        if n==1:login(h,other)
    w.after=relink
    assert a.get('/mail').status_code==503 and len(w.calls)==1
    with h.storage.session() as db:
        account=db.get(GmailAccount,db.get(GooglePrincipal,'subject-a').account_id)
        assert h.cipher.decrypt(account.credentials_blob)['refresh_token']=='refresh-a'

def test_stored_broadened_scope_rejected_before_network(h):
    w=Wire([]);install_private_reader(h.app,storage=h.storage,auth=h.cfg,cipher=h.cipher,page_key=b'p'*32,policy=ReadPolicy(8,11,15),session_factory=w.factory)
    a=h.client();login(h,a)
    with h.storage.session() as db:
        account=db.get(GmailAccount,db.get(GooglePrincipal,'subject-a').account_id);credentials=h.cipher.decrypt(account.credentials_blob)
        credentials['scopes'].append('https://www.googleapis.com/auth/gmail.modify');account.credentials_blob=h.cipher.encrypt(credentials);db.commit()
    assert a.get('/mail').status_code==503 and not w.calls

def test_actual_default_session_has_no_retries_no_network():
    import requests
    s=requests.Session()
    try:
        assert s.get_adapter('https://gmail.googleapis.com').max_retries.total==0
    finally:s.close()

def test_explicit_reader_entrypoint_preserves_storage_old_path_and_callback(h,monkeypatch):
    from app import private_reader_entrypoint_candidate as entry
    from fastapi.testclient import TestClient
    from tests.test_protected_auth_candidate import ORIGIN
    monkeypatch.setattr(entry,'create_from_environment',lambda:h.app)
    for k,v in {'GOOGLE_CLIENT_ID':h.cfg.oauth.client_id,'GOOGLE_CLIENT_SECRET':h.cfg.oauth.client_secret,
        'GOOGLE_REDIRECT_URI':h.cfg.oauth.redirect_uri,'OAUTH_STATE_SECRET':'k'*32,
        'PUBLIC_ORIGIN':ORIGIN,'GOOGLE_ALLOWED_EMAIL':'a@example.test','TOKEN_ENCRYPTION_KEY':'unused-fixture-value'}.items():monkeypatch.setenv(k,v)
    # Reuse fixture cipher; no generated/test secret is used by production.
    monkeypatch.setattr(entry,'TokenCipher',lambda key:h.cipher)
    app=entry.create_reader_from_environment()
    c=TestClient(app,base_url=ORIGIN,follow_redirects=False)
    assert c.get('/mail').status_code==401 and c.get('/workspace').status_code==401
    from tests.test_account_controls_candidate import start,finish
    r=finish(c,start(c));assert r.status_code==303 and r.headers['location']=='/mail'
    assert c.get('/workspace').status_code==200
