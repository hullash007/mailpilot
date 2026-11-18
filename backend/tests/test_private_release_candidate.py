"""Fictional five-table baseline. Fake cipher answers are NOT encryption proof.

Actual native ARM app/cipher compatibility is separate accepted finite R4 evidence.
These local source tests exercise migration, bootstrap and failure semantics only.
"""
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
import sqlite3,os
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import GmailAccount,OAuthState,Thread,Email
from app.google_main import GooglePrincipal,SCOPES
from app.private_bootstrap_candidate import PrivateConfig,database_key,create_private_app,PrivateBootstrapUnavailable
from app.protected_auth_candidate import AuthConfig
from app.integrations.gmail.oauth import OAuthFlowConfig
from app.services.crypto import TokenCipher
from app.offline_conversion_candidate import convert_offline,BASELINE,ConversionUnconfirmed
import app.offline_conversion_candidate as migration


class FakeCursor:
    def __init__(self,raw,driver):self.raw=raw;self.driver=driver;self.answer=None
    def execute(self,sql,*args,**kwargs):
        self.driver.statements.append(sql)
        self.answer=None
        if self.driver.fail and self.driver.fail in sql:raise RuntimeError('SECRET DRIVER FAILURE')
        fixed={'PRAGMA cipher_version':('4.20.0 community',),'PRAGMA cipher_provider':('openssl',),
            'SELECT sqlite_version()':('3.54.1',)}
        if sql.startswith('PRAGMA key='):
            self.answer=[];return self
        if sql in fixed:
            self.answer=[fixed[sql]];return self
        self.raw.execute(sql,*args,**kwargs);return self
    def fetchone(self):
        if self.answer is not None:return self.answer.pop(0) if self.answer else None
        return self.raw.fetchone()
    def fetchall(self):
        if self.answer is not None:r=self.answer;self.answer=[];return r
        return self.raw.fetchall()
    def __getattr__(self,name):return getattr(self.raw,name)


class FakeConnection:
    def __init__(self,raw,driver):self.raw=raw;self.driver=driver
    def cursor(self,*a,**kw):return FakeCursor(self.raw.cursor(*a,**kw),self.driver)
    def execute(self,sql,*args):return self.cursor().execute(sql,*args)
    def __getattr__(self,name):return getattr(self.raw,name)


class FakeDriver:
    def __init__(self):self.statements=[];self.fail=''
    def connect(self,*args,**kwargs):return FakeConnection(sqlite3.connect(*args,**kwargs),self)
    def __getattr__(self,name):return getattr(sqlite3,name)


@pytest.fixture
def fixture(tmp_path):
    source=tmp_path/'original.db';target=tmp_path/'protected.db'
    engine=create_engine('sqlite:///'+str(source))
    from app.database import Base
    Base.metadata.create_all(engine,tables=list(BASELINE))
    key=Fernet.generate_key();cipher=TokenCipher(key)
    with Session(engine) as db:
        a=GmailAccount(email_address='pilot@example.test',google_user_id='fictional-subject',credentials_blob=cipher.encrypt({'token':'fake-access','refresh_token':'fake-refresh'}));db.add(a);db.flush()
        db.add(GooglePrincipal(subject='fictional-subject',account_id=a.id))
        for i in range(5):
            t=Thread(account_id=a.id,gmail_thread_id='thread-'+str(i),subject='Fictional '+str(i));db.add(t);db.flush()
            db.add(Email(thread_id=t.id,account_id=a.id,gmail_message_id='message-'+str(i),subject='Fictional '+str(i),
                body_text='Fictional body '+str(i),headers={'sample':'fake'},label_ids=['INBOX'],is_sample=True))
        db.add(OAuthState(state='unused-old-state',code_verifier='unused-old-verifier',expires_at=datetime.utcnow()+timedelta(minutes=5)))
        db.commit()
    engine.dispose();source.chmod(0o600)
    cfg=AuthConfig('https://candidate.test',OAuthFlowConfig('fake-client','fake-secret','https://candidate.test/auth/google/callback',tuple(SCOPES)),b'k'*32,allowed_email='pilot@example.test')
    return dict(source=source,destination=target,token_key=key,allowed_email='pilot@example.test',reviewed_driver=FakeDriver(),schema_version=1),cfg


def test_five_table_conversion_preserves_every_raw_row_token_and_source_then_bootstrap(fixture):
    kw,cfg=fixture;before=kw['source'].read_bytes()
    result=convert_offline(**kw)
    assert result.schema_version==1 and result.counts=={'gmail_accounts':1,'oauth_states':1,'threads':5,'emails':5,'google_principals':1}
    assert kw['source'].read_bytes()==before
    assert kw['destination'].stat().st_mode&0o777==0o600
    with sqlite3.connect(kw['source']) as a,sqlite3.connect(kw['destination']) as b:
        for t in BASELINE:assert a.execute('SELECT * FROM '+t.name).fetchall()==b.execute('SELECT * FROM '+t.name).fetchall()
        assert b.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert b.execute('PRAGMA user_version').fetchone()==(1,)
    kw['reviewed_driver'].statements.clear()
    private=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    app=create_private_app(config=private,reviewed_driver=kw['reviewed_driver'],identity_adapter=lambda *a:None)
    assert not any(sql.startswith(('CREATE','ALTER','INSERT','DELETE','UPDATE')) for sql in kw['reviewed_driver'].statements)
    c=TestClient(app,base_url=cfg.origin,follow_redirects=False)
    assert c.get('/workspace').status_code==401
    assert c.post('/app/sync',headers={'origin':cfg.origin}).status_code==404
    assert c.post('/account/revoke',headers={'origin':cfg.origin}).status_code==404
    assert c.get('/login').status_code==303
    app.state.prepared_storage.close()


def test_kdf_explicit_versioned_deterministic_and_secret_repr(fixture):
    kw,cfg=fixture
    derived=database_key(kw['token_key'])
    assert len(derived)==32 and derived==database_key(kw['token_key'])
    assert derived!=__import__('base64').urlsafe_b64decode(kw['token_key'])
    assert derived!=database_key(Fernet.generate_key())
    for key in [b'',b'a'*44,b'+'*43+b'=',kw['token_key'][:-1]]:
        with pytest.raises(ValueError):database_key(key)
    p=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    assert kw['token_key'].decode() not in repr(p)
    for version in [True,0,73,2]:
        with pytest.raises(ValueError):replace(p,schema_version=version)


@pytest.mark.parametrize('change',['version','table','column','binding','foreign_owner','allowlist','count','mode','symlink','target_exists'])
def test_unqualified_source_refused_without_touching_original(fixture,change):
    kw,cfg=fixture
    if change=='mode':kw['source'].chmod(0o644)
    elif change=='symlink':
        alias=kw['source'].with_name('alias.db');alias.symlink_to(kw['source']);kw['source']=alias
    elif change=='target_exists':kw['destination'].write_bytes(b'EXISTING DESTINATION')
    elif change=='allowlist':kw['allowed_email']='other@example.test'
    else:
        with sqlite3.connect(kw['source']) as db:
            if change=='version':db.execute('PRAGMA user_version=73')
            elif change=='table':db.execute('CREATE TABLE private_extra (id INTEGER)')
            elif change=='column':db.execute('ALTER TABLE emails ADD COLUMN private_extra TEXT')
            elif change=='binding':db.execute("UPDATE google_principals SET subject='other-subject'")
            elif change=='foreign_owner':db.execute('UPDATE emails SET account_id=999')
            elif change=='count':db.execute('DELETE FROM google_principals')
    before=kw['source'].read_bytes()
    with pytest.raises((ConversionUnconfirmed,ValueError)) as error:convert_offline(**kw)
    assert 'SECRET' not in str(error.value)
    assert kw['source'].read_bytes()==before
    if change=='target_exists':assert kw['destination'].read_bytes()==b'EXISTING DESTINATION'
    else:assert not kw['destination'].exists()


@pytest.mark.parametrize('point',['CREATE TABLE','INSERT INTO "emails"','PRAGMA integrity_check','PRAGMA foreign_keys'])
def test_driver_failure_is_unconfirmed_retains_private_building_not_original_change(fixture,point):
    kw,cfg=fixture;before=kw['source'].read_bytes();kw['reviewed_driver'].fail=point
    with pytest.raises(ConversionUnconfirmed) as e:convert_offline(**kw)
    assert 'unconfirmed' in str(e.value) and 'SECRET' not in str(e.value)
    assert kw['source'].read_bytes()==before and not kw['destination'].exists()
    building=list(kw['destination'].parent.glob('protected.db.building-*'));assert len(building)==1
    assert building[0].stat().st_mode&0o777==0o600


@pytest.mark.parametrize('point',['before_link','after_link'])
def test_publication_ambiguity_never_overwrites_or_blind_retries(fixture,monkeypatch,point):
    kw,cfg=fixture;before=kw['source'].read_bytes();original=migration.os.link
    def fail(*args):
        if point=='after_link':original(*args)
        raise RuntimeError('PRIVATE FAILURE')
    monkeypatch.setattr(migration.os,'link',fail)
    with pytest.raises(ConversionUnconfirmed):convert_offline(**kw)
    assert kw['source'].read_bytes()==before
    assert kw['destination'].exists()==(point=='after_link')
    assert list(kw['destination'].parent.glob('protected.db.building-*'))
    if point=='after_link':
        with pytest.raises(ValueError):convert_offline(**kw)


def test_source_change_detection_and_bootstrap_bad_version_mode_clock_fail_closed(fixture,monkeypatch):
    kw,cfg=fixture;original=migration._digest;count=[0]
    def changed(path):count[0]+=1;return original(path) if count[0]==1 else b'changed'
    monkeypatch.setattr(migration,'_digest',changed)
    with pytest.raises(ConversionUnconfirmed):convert_offline(**kw)
    assert not kw['destination'].exists()
    monkeypatch.setattr(migration,'_digest',original)
    # Preserve failed artifact; choose a NEW explicit target, not a blind retry.
    kw['destination']=kw['destination'].with_name('protected-second.db')
    convert_offline(**kw)
    config=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    with sqlite3.connect(kw['destination']) as db:db.execute('PRAGMA user_version=73')
    with pytest.raises(PrivateBootstrapUnavailable):create_private_app(config=config,reviewed_driver=kw['reviewed_driver'])
    with sqlite3.connect(kw['destination']) as db:
        db.execute('PRAGMA user_version=1');db.execute('DELETE FROM candidate_control_clock')
    with pytest.raises(PrivateBootstrapUnavailable):create_private_app(config=config,reviewed_driver=kw['reviewed_driver'])
    kw['destination'].chmod(0o644)
    with pytest.raises(PrivateBootstrapUnavailable):create_private_app(config=config,reviewed_driver=kw['reviewed_driver'])


@pytest.mark.parametrize('mode',['wal','journal_artifact'])
def test_unquiesced_sqlite_modes_rejected(fixture,mode):
    kw,cfg=fixture
    if mode=='wal':
        with sqlite3.connect(kw['source']) as db:db.execute('PRAGMA journal_mode=WAL')
    else:kw['source'].with_name(kw['source'].name+'-journal').write_bytes(b'FICTIONAL JOURNAL')
    with pytest.raises((ConversionUnconfirmed,ValueError)):convert_offline(**kw)
    assert not kw['destination'].exists()


def test_explicit_entrypoint_has_no_settings_driver_before_valid_config_or_ddl(fixture,monkeypatch):
    import app.private_entrypoint_candidate as entry
    import app.config as legacy
    kw,cfg=fixture;calls=[]
    def poison(*a,**kw):raise AssertionError('ambient legacy settings accessed')
    monkeypatch.setattr(legacy,'get_settings',poison)
    for name in entry.REQUIRED:monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(entry,'load_reviewed_arm_driver',lambda:calls.append('driver') or kw['reviewed_driver'])
    monkeypatch.setattr(entry,'create_private_app',lambda **kw:calls.append(kw) or 'FIXTURE APP')
    with pytest.raises(PrivateBootstrapUnavailable):entry.create_from_environment()
    assert calls==[]
    values=dict(PUBLIC_ORIGIN=cfg.origin,GOOGLE_CLIENT_ID='fake-client',GOOGLE_CLIENT_SECRET='fake-secret',
        GOOGLE_REDIRECT_URI=cfg.origin+'/auth/google/callback',GOOGLE_ALLOWED_EMAIL='pilot@example.test',
        TOKEN_ENCRYPTION_KEY=kw['token_key'].decode(),OAUTH_STATE_SECRET='s'*32,MAILPILOT_SCHEMA_VERSION='1')
    for name,value in values.items():monkeypatch.setenv(name,value)
    assert entry.create_from_environment()=='FIXTURE APP'
    config=calls[-1]['config'];assert config.schema_version==1 and config.database_path==entry.DATABASE
    assert config.token_key==kw['token_key'] and config.auth.allowed_email=='pilot@example.test'
    assert not kw['reviewed_driver'].statements
    for name,value in [('MAILPILOT_SCHEMA_VERSION','73'),('GOOGLE_ALLOWED_EMAIL','a@example.test,b@example.test'),('PUBLIC_ORIGIN','http://wrong.test')]:
        old=values[name];monkeypatch.setenv(name,value);count=len(calls)
        with pytest.raises(PrivateBootstrapUnavailable):entry.create_from_environment()
        assert len(calls)==count;monkeypatch.setenv(name,old)


def test_pinned_runtime_static_inventory_and_custody_gate_without_native_loading(tmp_path,monkeypatch):
    import app.pinned_arm_runtime_candidate as runtime
    import hashlib,stat
    root=tmp_path/'runtime';root.mkdir();(root/'package.py').write_text('# fictional artifact\n')
    expected={'package.py':hashlib.sha256((root/'package.py').read_bytes()).hexdigest()}
    monkeypatch.setattr(runtime,'EXPECTED_FILES',expected)
    # Model root-owned secure ancestry for local fixture. This is not a claim
    # that the actual world-writable /tmp is a permissible production runtime.
    original=Path.lstat
    ancestors=set(root.parents)
    def modeled(path,*a,**kw):
        st=original(path,*a,**kw)
        if path in ancestors:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
        return st
    monkeypatch.setattr(Path,'lstat',modeled)
    runtime.verify_runtime_tree(root,owner_uid=os.getuid())
    (root/'package.py').write_text('CHANGED')
    with pytest.raises(ValueError):runtime.verify_runtime_tree(root,owner_uid=os.getuid())
    (root/'package.py').write_text('# fictional artifact\n');(root/'extra.py').write_text('EXTRA')
    with pytest.raises(ValueError):runtime.verify_runtime_tree(root,owner_uid=os.getuid())
    (root/'extra.py').unlink();(root/'package.py').chmod(0o666)
    with pytest.raises(ValueError):runtime.verify_runtime_tree(root,owner_uid=os.getuid())
    (root/'package.py').chmod(0o644);(root/'package.py').unlink();(root/'package.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):runtime.verify_runtime_tree(root,owner_uid=os.getuid())
    monkeypatch.setitem(runtime.sys.modules,'sqlcipher3',object())
    with pytest.raises(ValueError):runtime.load_reviewed_arm_driver()


def test_every_validated_cipher_connection_enforces_foreign_keys(fixture):
    kw,cfg=fixture;convert_offline(**kw)
    private=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    app=create_private_app(config=private,reviewed_driver=kw['reviewed_driver'],identity_adapter=lambda *a:None)
    with app.state.prepared_storage.engine.connect() as c:
        assert c.exec_driver_sql('PRAGMA foreign_keys').scalar()==1
        with pytest.raises(Exception):c.exec_driver_sql("INSERT INTO threads (id,account_id,gmail_thread_id,subject,snippet) VALUES (99,999,'bad','bad','bad')")
    app.state.prepared_storage.close()


def test_migrated_fictional_mailbox_auth_workspace_then_controls_no_ambient_path(fixture,monkeypatch):
    from types import SimpleNamespace
    import time
    from app.services.oauth_state import StateSigner
    from tests.test_account_controls_candidate import start,finish,op,apply
    import app.models as legacy_models
    import app.google_main as legacy_google
    kw,cfg=fixture;before=kw['source'].read_bytes();convert_offline(**kw)
    def poison(*a,**kw):raise AssertionError('legacy settings or token property accessed')
    monkeypatch.setattr(legacy_models,'encrypt_tokens',poison);monkeypatch.setattr(legacy_models,'decrypt_tokens',poison)
    monkeypatch.setattr(legacy_google,'get_settings',poison)
    signer=StateSigner(b's'*32);seen=[]
    def flow(config,state):
        return SimpleNamespace(code_verifier='fictional-verifier-'+state,
            authorization_url=lambda **kw:('https://accounts.google.com/?state='+state,state))
    def exchange(oauth,code,state,verifier):
        assert verifier=='fictional-verifier-'+state;seen.append(state)
        return (dict(token='fictional-new-access',refresh_token=None,token_uri='https://oauth2.googleapis.com/token',
            client_id=oauth.client_id,client_secret=oauth.client_secret,scopes=list(SCOPES)),
            dict(iss='https://accounts.google.com',aud=oauth.client_id,exp=int(time.time())+600,email_verified=True,
                sub='fictional-subject',email='pilot@example.test',nonce=signer.sign(state)))
    private=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    app=create_private_app(config=private,reviewed_driver=kw['reviewed_driver'],identity_adapter=exchange,flow_factory=flow)
    c=TestClient(app,base_url=cfg.origin,follow_redirects=False)
    assert finish(c,start(c)).status_code==303
    page=c.get('/workspace');assert page.status_code==200 and 'Fictional 4' in page.text
    with app.state.prepared_storage.session() as db:
        assert TokenCipher(kw['token_key']).decrypt(db.get(GmailAccount,1).credentials_blob)['refresh_token']=='fake-refresh'
    assert apply(c,'disconnect',op(c,'disconnect')).status_code==200
    assert c.get('/workspace').status_code==200
    assert apply(c,'erase',op(c,'erase')).status_code==200
    assert c.get('/workspace').status_code==401 and len(seen)==1
    assert kw['source'].read_bytes()==before # original rollback source still unchanged
    app.state.prepared_storage.close()


def test_composed_private_signed_out_workspace_has_login_not_401_loop(fixture):
    # Fictional-only source seam test, not a native cipher or provider proof.
    kw,cfg=fixture
    convert_offline(**kw)
    private=PrivateConfig(cfg,kw['destination'],1,kw['token_key'],b's'*32)
    app=create_private_app(config=private,reviewed_driver=kw['reviewed_driver'],identity_adapter=lambda *a:None)
    c=TestClient(app,base_url=cfg.origin,follow_redirects=False)
    try:
        for path in ['/workspace','/workspace/threads/1']:
            response=c.get(path)
            assert response.status_code==401
            assert '<a class="button" href="/login">Sign in with Google</a>' in response.text
            assert 'Return to stored conversations' not in response.text
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-frame-options']=='DENY'
            assert response.headers['x-content-type-options']=='nosniff'
            assert 'frame-ancestors' in response.headers['content-security-policy']
            assert 'Fictional body' not in response.text
            assert 'Private pilot · Stored mail only. Read-only; AI is off.' in response.text
            assert not response.cookies
        assert c.post('/workspace',headers={'origin':'https://wrong.example.test'}).status_code==403
        assert c.get('/missing').status_code==404
    finally:
        c.close()
        app.state.prepared_storage.close()
