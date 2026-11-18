"""Explicit read-client candidate. Not installed by any current entrypoint.

Reads provider data on deliberate navigation/refresh, never imports or persists
mail. Authentication stays the existing verified-subject own_account dependency.
A trusted provider_for(account,db) supplies ONLY that principal's bound reader.
No write scope, Gmail mutation, AI or background work is added.
"""
from html import escape
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from fastapi import Depends,Query,Request,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from .google_main import own_account
from .database import get_db
from .services.gmail_reader import GmailReadService,ReadPolicy,PageSigner,InvalidPage,MailUnavailable,ReconnectRequired,provider_id

STYLE='''*{box-sizing:border-box}body{margin:0;background:#f6f7fb;color:#20293b;font:15px/1.55 system-ui,sans-serif}a{color:#2549a1;text-decoration:none}a:hover{text-decoration:underline}button,input{font:inherit}button,.button{background:#263f88;color:white;border:0;border-radius:9px;padding:10px 15px;cursor:pointer}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #e5a834;outline-offset:3px}header{background:white;border-bottom:1px solid #e1e5ee;padding:16px 28px;display:flex;gap:15px;justify-content:space-between;align-items:center;flex-wrap:wrap}.brand{font-size:22px;letter-spacing:-.7px;font-weight:750}.identity{font-size:13px;color:#58647c;overflow-wrap:anywhere}.layout{display:grid;grid-template-columns:180px minmax(0,1fr);max-width:1250px;margin:auto;gap:24px;padding:28px}aside nav{display:flex;flex-direction:column;gap:5px}aside a{padding:10px 12px;border-radius:8px;color:#53617a}aside a.active{background:#e5eafa;color:#1d347a;font-weight:650}.labeltitle{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#7a8397;margin:24px 12px 8px}h1{font-size:28px;letter-spacing:-1px;margin:0}h2{font-size:17px;margin:0}.heading,.search,.pagination{display:flex;align-items:center;gap:12px;justify-content:space-between;flex-wrap:wrap}.subtle{color:#68758c;font-size:13px}.search{margin:22px 0}.search input{min-width:0;flex:1;border:1px solid #dce1ec;background:white;border-radius:10px;padding:12px 15px}.message{display:block;background:white;border:1px solid #e1e5ee;border-radius:12px;padding:18px 20px;margin:10px 0;color:inherit;overflow-wrap:anywhere}.message:hover{border-color:#aebddd;text-decoration:none}.message.unread{border-left:4px solid #345bd1}.row{display:flex;gap:12px;justify-content:space-between;align-items:baseline;flex-wrap:wrap}.sender{color:#33415d;font-size:13px}.subject{margin:7px 0;font-size:16px}.snippet{color:#6b7589;margin:0;font-size:14px}.badge{font-size:11px;padding:3px 8px;border-radius:8px;background:#e7edfc;color:#284b9e}.pagination{margin:24px 0}.empty,.error{background:white;border:1px solid #e1e5ee;border-radius:12px;padding:30px}.account-link{margin-left:14px;font-size:13px}.search-context{font-size:14px;overflow-wrap:anywhere}.search-context a{margin-left:12px}.snippet{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.subject{font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui,sans-serif;color:#35405a;margin:24px 0 0}.mailhead{padding-bottom:16px;border-bottom:1px solid #e8ebf2}.fixture{background:#fcf2d9;color:#745410;text-align:center;padding:6px 16px;font-size:12px}details{font-size:13px;color:#68758c;margin-top:14px}footer{margin:25px 0;font-size:12px;color:#7a8397}@media(max-width:700px){header{padding:14px 16px}.layout{grid-template-columns:1fr;padding:18px 14px;gap:18px}aside{display:flex;gap:4px;overflow-x:auto;padding-bottom:5px}aside nav{display:contents}aside a{white-space:nowrap;padding:8px 11px}.labeltitle{display:none}.labels{margin-top:5px}h1{font-size:26px}.message{padding:14px}.subject{margin:5px 0}.snippet{font-size:13px}.search{gap:8px}.search input{flex-basis:65%}.row time{font-size:11px}.identity{font-size:12px}}@media(max-width:350px){.search input{flex-basis:100%;width:100%}.search button{width:100%}}button,.button,aside a,.account-link{min-height:44px}.account-link{display:inline-flex;align-items:center}'''

def display_date(value,with_time=False):
    try:
        parsed=parsedate_to_datetime(value)
        return parsed.strftime('%d %b %Y %H:%M %z' if with_time else '%d %b %Y')
    except (ValueError,TypeError,OverflowError):return value

def esc(v):return escape(str(v),quote=True)
def url(path='/mail',**kw):return path+('?' + urlencode({k:v for k,v in kw.items() if v or k=='folder'}) if kw else '')
def shell(title,body,*,email='',folder='INBOX',labels=(),fixture=False):
    nav=''.join('<a class="'+('active' if folder==ident else '')+'" href="'+esc(url(folder=ident))+'">'+name+'</a>' for ident,name in [('INBOX','Inbox'),('STARRED','Starred'),('SENT','Sent'),('','All mail')])
    extra=''.join('<a href="'+esc(url(folder=l['id']))+'">'+esc(l['name'])+'</a>' for l in labels)
    html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+' | MailPilot</title><style>'+STYLE+'</style></head><body>'
    if fixture:html+='<div class="fixture">Fictional mailbox · Local source preview · No Google connection</div>'
    html+='<header><a class="brand" href="/mail">MailPilot<span style="color:#6478ac">.</span></a><div class="identity">'+esc(email)+' <a class="account-link" href="/account">Account</a></div></header><div class="layout"><aside><nav aria-label="Mailbox">'+nav+'</nav><div class="labeltitle">Your labels</div><nav class="labels" aria-label="Labels">'+extra+'</nav></aside><main>'+body+'<footer>MailPilot</footer></main></div></body></html>'
    return HTMLResponse(html,headers={'Cache-Control':'no-store','Referrer-Policy':'same-origin','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

def install_reader(app,*,provider_for,page_signer,policy,fixture=False,account_dependency=own_account):
    if not callable(provider_for) or not isinstance(page_signer,PageSigner) or not isinstance(policy,ReadPolicy):raise ValueError('Explicit reader dependencies required')
    def reader(account,db):
        if not account.google_user_id:raise HTTPException(401,'Sign in with Google')
        s=GmailReadService(provider_for(account,db),email=account.email_address,policy=policy);s.verify();return s
    def failure(e,account):
        if isinstance(e,ReconnectRequired):title='Connect to Gmail again';detail='Sign in with Google to continue reading your mailbox.';link='<a class="button" href="/login">Reconnect with Google</a>'
        elif isinstance(e,InvalidPage):title='Open the first page';detail=str(e);link='<a class="button" href="/mail">Back to inbox</a>'
        else:title='Your mailbox could not be loaded';detail='Gmail could not complete this request. Try again in a moment.';link='<a class="button" href="/mail">Return to inbox</a>'
        response=shell(title,'<section class="error"><h1>'+title+'</h1><p>'+esc(detail)+'</p>'+link+'</section>',email=account.email_address,fixture=fixture);response.status_code=409 if isinstance(e,InvalidPage) else 503;return response
    def valid_folder(folder):
        if folder:
            try:provider_id(folder)
            except MailUnavailable:raise HTTPException(400,'Invalid mail folder.') from None
    @app.get('/mail',response_class=HTMLResponse)
    def inbox(q:str=Query('',max_length=500),folder:str=Query('INBOX',max_length=128),cursor:str=Query('',max_length=9000),account=Depends(account_dependency),db=Depends(get_db)):
        valid_folder(folder)
        try:
            token=page_signer.open(cursor,account.google_user_id,folder,q) if cursor else None
            s=reader(account,db);labels_notice=False
            try:labels=s.labels()
            except ReconnectRequired:raise
            except MailUnavailable:labels=[];labels_notice=True
            data=s.listing(folder,q,token)
            title=next((x['name'] for x in labels if x['id']==folder),{'INBOX':'Inbox','SENT':'Sent','STARRED':'Starred','':'All mail'}.get(folder,'Mail'))
            body='<div class="heading"><div><h1>'+esc('Search results' if q else title)+'</h1><div class="subtle">'+esc(account.email_address)+'</div></div><a class="button" href="'+esc(url(q=q,folder=folder))+'">Refresh mail</a></div>'
            if q:body+='<p class="search-context">Results for <strong>'+esc(q)+'</strong> <a href="/mail">Clear search</a></p>'
            body+='<form class="search" action="/mail" method="get"><input aria-label="Search all Gmail" name="q" value="'+esc(q)+'" placeholder="Search all your mail" maxlength="500"><input type="hidden" name="folder" value=""><button>Search</button></form>'
            body+='<div class="row subtle"><span>'+esc('Matching conversations' if q else title)+' · '+str(len(data['threads']))+' on this page</span><span>Last checked '+esc(data['checked'])+'</span></div>'
            if labels_notice:body+='<p class="subtle" role="status">Your label list could not be loaded. Conversations below were refreshed.</p>'
            if not data['threads']:
                heading='No matching conversations' if q else ('No starred conversations' if folder=='STARRED' else 'No conversations here yet')
                detail='Try a different name, phrase or date.' if q else 'Choose another folder or refresh to check your mailbox again.'
                body+='<section class="empty"><h2>'+heading+'</h2><p>'+detail+'</p><a href="/mail">Go to Inbox</a></section>'
            for t in data['threads']:
                m=t['latest'];body+='<a class="message '+('unread' if t['unread'] else '')+'" href="'+esc(url('/mail/threads/'+t['id'],folder=folder,q=q))+'"><div class="row"><span class="sender">'+esc(m['sender'])+'</span><time class="subtle" title="'+esc(m['date'])+'">'+esc(display_date(m['date']))+'</time></div><div class="subject">'+esc(t['subject'])+(' <span class="badge">Unread</span>' if t['unread'] else '')+'</div><p class="snippet">'+esc(m['snippet'])+'</p><div class="subtle">'+str(len(t['messages']))+' message'+('s' if len(t['messages'])!=1 else '')+(' · Starred' if m['starred'] else '')+'</div></a>'
            body+='<nav class="pagination" aria-label="Mailbox pages">'
            if cursor:body+='<a href="'+esc(url(q=q,folder=folder))+'">Newest results</a>'
            if data['next_token']:
                c=page_signer.issue(account.google_user_id,folder,q,data['next_token']);body+='<a class="button" href="'+esc(url(q=q,folder=folder,cursor=c))+'">Older conversations</a>'
            body+='</nav>'
            return shell(title,body,email=account.email_address,folder=folder,labels=labels,fixture=fixture)
        except (MailUnavailable,InvalidPage) as e:return failure(e,account)
    @app.get('/mail/threads/{thread_id}',response_class=HTMLResponse)
    def conversation(thread_id:str,folder:str=Query('INBOX',max_length=128),q:str=Query('',max_length=500),account=Depends(account_dependency),db=Depends(get_db)):
        valid_folder(folder)
        try:
            s=reader(account,db);data=s.conversation(thread_id)
            back='search results' if q else {'INBOX':'Inbox','STARRED':'Starred','SENT':'Sent','':'All mail'}.get(folder,'your mail')
            body='<p><a href="'+esc(url(folder=folder,q=q))+'">← Back to '+back+'</a></p><div class="heading"><h1>'+esc(data['subject'])+'</h1><a class="button" href="'+esc(url('/mail/threads/'+data['id'],folder=folder,q=q))+'">Refresh conversation</a></div><p class="subtle">'+str(len(data['messages']))+' message'+('s' if len(data['messages'])!=1 else '')+'</p>'
            for m in data['messages']:
                body+='<article class="message"><div class="mailhead"><div class="row"><h2>'+esc(m['sender'])+'</h2><time class="subtle" title="'+esc(m['date'])+'">'+esc(display_date(m['date'],with_time=True))+'</time></div><div class="subtle">To: '+esc(m['to'])+'</div></div><pre>'+esc(m['body'] or 'No readable inline text is available for this message.')+'</pre>'
                if m.get('body_format')=='html_text':body+='<p class="subtle">Converted from HTML to text. Images, styles and interactive content are not shown.</p>'
                if m['truncated']:body+='<p class="subtle">Long message text is shortened in this view.</p>'
                if m['attachments']:body+='<details><summary>Attachments ('+str(len(m['attachments']))+')</summary><p>'+esc(', '.join(m['attachments']))+'</p><p>Open Gmail to view or download these attachments.</p></details>'
                body+='</article>'
            return shell(data['subject'],body,email=account.email_address,folder=folder,fixture=fixture)
        except (MailUnavailable,InvalidPage) as e:return failure(e,account)
    return app
