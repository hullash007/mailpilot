"""Loopback-only coherent synthetic preview. Ordinary SQLite, NOT encrypted.

No Google login/provider/model routes, no acquired driver and no startup DDL.
"""
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
import sqlite3,re
from fastapi import Depends,HTTPException,Request,Form
from fastapi.responses import HTMLResponse,RedirectResponse,Response
from sqlalchemy import select
from .cipher_adapter import prepare_cipher_storage
from .synthetic_driver import SyntheticDriver
from .integrated_candidate import create_app as integrated_app
from .database import get_db
from .google_main import own_account,esc
from .models import GmailAccount
from .sync_models import MailboxSync
from .services import mailbox_sync
from .services.browser_boundary import BrowserBoundary
from .synthetic_fixture import VERSION,MARKER,manifest,FakeProvider

COOKIE='mailpilot_synthetic_identity'
NOTICE='<aside style="background:#fff5cd;border:2px solid #926500;border-radius:8px;padding:12px;margin:12px 0"><strong>SYNTHETIC LOCAL PREVIEW</strong><br>Fixture identities and fake provider only. Ordinary SQLite, NOT encrypted. AI is off. No Google, real mail or external actions.</aside>'
NAV='<nav style="display:flex;flex-wrap:wrap;gap:12px;margin:12px 0"><a href="/">Fixture identities</a><a href="/workspace">Stored conversations</a><a href="/synthetic/status">Fake sync status</a><a href="/actions">Potential actions</a><a href="/actions/history">Decision history</a></nav>'

def create_app(*,fixture_path,origin='http://127.0.0.1:8091'):
    url=urlsplit(origin)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.path not in ('','/') or url.query or url.fragment:
        raise ValueError('Preview requires an explicit loopback HTTP origin.')
    path=Path(fixture_path)
    if not path.is_absolute():raise ValueError('Explicit synthetic fixture path required.')
    storage=prepare_cipher_storage(driver_module=SyntheticDriver(),database_path=path,
        key_provider=lambda:b'S'*32,expected_user_version=VERSION,required_schema=manifest())
    app=integrated_app(storage=storage)
    app.state.explicit_origin=origin.rstrip('/')
    app.state.prepared_storage=storage
    # Remove all real identity/provider endpoints and misleading legacy inbox.
    app.router.routes[:]=[r for r in app.routes if getattr(r,'path','') not in ('/','/access','/login','/auth/google/callback','/logout','/app','/app/messages/{message_id}')]
    app.user_middleware[:]=[m for m in app.user_middleware if m.cls is not BrowserBoundary]
    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:storage.close()
    app.router.lifespan_context=lifespan

    @app.middleware('http')
    async def local_boundary(request,call_next):
        client=request.client.host if request.client else ''
        if client not in ('127.0.0.1','::1','testclient') or request.headers.get('host')!=url.netloc:
            return HTMLResponse('Synthetic preview is loopback-only.',status_code=403)
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin')!=origin.rstrip('/'):
            return HTMLResponse('Same-origin synthetic request required.',status_code=403)
        response=await call_next(request)
        if response.headers.get('content-type','').startswith('text/html'):
            data=b''.join([chunk async for chunk in response.body_iterator]).decode()
            data=re.sub(r'<nav aria-label="Workspace">.*?</nav>','',data)
            data=data.replace(' - read-only Gmail workspace',' - synthetic pilot')
            data=data.replace('Manual five-message import only. Full mailbox sync is not enabled.','Seeded fixture only. Run an explicit fake sync tick from Fake sync status.')
            if '<main>' in data:data=data.replace('<main>','<main>'+NOTICE+NAV,1)
            else:data=data.replace('<body>','<body>'+NOTICE+NAV,1)
            data=data.replace('href="/app"','href="/workspace"').replace('Original stored inbox','Stored conversations')
            headers=dict(response.headers);headers.pop('content-length',None)
            response=HTMLResponse(data,status_code=response.status_code,headers=headers)
        response.headers.update({'Cache-Control':'no-store','X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    def fixture_account(request:Request,db=Depends(get_db)):
        value=request.cookies.get(COOKIE)
        if value not in ('1','2'):raise HTTPException(401,'Choose a synthetic fixture identity from Fixture identities.')
        row=db.get(GmailAccount,int(value))
        if row is None or row.google_user_id!='fixture-'+value:raise HTTPException(401,'Fixture identity unavailable.')
        return row
    app.dependency_overrides[own_account]=fixture_account

    def shell(body):
        return HTMLResponse('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>MailPilot synthetic pilot</title><style>body{font:16px/1.5 system-ui;max-width:920px;margin:20px auto;padding:16px;color:#17223b}button,input{font:inherit;padding:10px}button{margin:8px 8px 8px 0}article{border:1px solid #aab4c4;padding:16px;border-radius:8px;margin:16px 0}a{color:#184b9b}nav{margin:16px 0}</style></head><body>'+body+'</body></html>')
    @app.get('/')
    def home():
        return shell('<h1>Try the MailPilot journey</h1><p>This preview connects no account. Switch between two fictional mailboxes to inspect isolation. These switches are not Google authentication.</p><form method="post" action="/synthetic/identity"><button name="identity" value="1">Use Cedar fixture</button><button name="identity" value="2">Use Harbor fixture</button></form><article><h2>One stored-mail journey</h2><p>Search and open a conversation. Review the sample request and save a local decision. Run one fake provider tick, then reopen the preview with the same fixture file to see saved decisions and sync progress.</p><p>No email is sent, deleted or relabeled. Local decisions describe only what you marked.</p></article>')
    @app.post('/synthetic/identity')
    def identity(identity:str=Form()):
        if identity not in ('1','2'):raise HTTPException(422,'Choose a fixture identity.')
        response=RedirectResponse('/synthetic/status',status_code=303)
        response.set_cookie(COOKIE,identity,httponly=True,samesite='strict',max_age=3600)
        return response
    @app.get('/synthetic/status')
    def status(account=Depends(fixture_account),db=Depends(get_db)):
        from .services.workspace import sync_status
        sync=sync_status(db,account.id)
        return shell('<h1>'+esc(account.email_address)+' - fixture workspace</h1><p>'+esc(sync['label'])+'</p><p>'+esc(sync['detail'])+'</p><p>These labels refer only to the fake provider. Nothing connects to Gmail.</p><p><a href="/workspace">Search stored conversations</a></p><p><a href="/actions">Review sample potential actions</a></p><form method="post" action="/synthetic/tick"><button>Run one fake sync tick</button></form><p>First tick leaves catch-up incomplete. A later explicit tick can finish fake history replay. No background worker runs.</p>')
    @app.post('/synthetic/tick')
    def tick(account=Depends(fixture_account),db=Depends(get_db)):
        if db.get(MailboxSync,account.id) is None:mailbox_sync.start_sync(db,account.id)
        mailbox_sync.sync_tick(db,account.id,FakeProvider(account.id))
        return RedirectResponse('/synthetic/status',status_code=303)
    return app


if __name__=='__main__':
    import argparse,uvicorn
    parser=argparse.ArgumentParser(description='Loopback-only SYNTHETIC preview. No real accounts or encryption.')
    parser.add_argument('fixture');args=parser.parse_args()
    uvicorn.run(create_app(fixture_path=args.fixture),host='127.0.0.1',port=8091,log_level='warning')
