"""Account-scoped stored inbox workspace. GET-only, no external actions."""
import re
from urllib.parse import urlencode
from fastapi import APIRouter,Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from .database import get_db
from .services.access import require_access
from .services.inbox import account_list,inbox,stored_message,CATEGORIES,PRIORITIES
from .dashboard import _PAGE,_esc
router=APIRouter(dependencies=[Depends(require_access)])

def shell(body,notice='Stored mail only. No sync, model calls or mail changes. Data may be stale.'):
    page=re.sub(r'<section class="stats">.*?</section>', '', _PAGE, flags=re.S)
    return HTMLResponse(page.replace('__BANNER__','<div class="banner">'+_esc(notice)+'</div>').replace('__BODY__',body).replace('</style>','input,select,button {font:inherit;padding:9px;max-width:100%} .stored-source {background:#e8eefc;color:#0b3d91} .filters {display:flex;gap:10px;flex-wrap:wrap;margin:18px 0} .mail-card {padding:16px;border:1px solid #e2e5ea;border-radius:10px;background:white;margin:12px 0;overflow-wrap:anywhere} pre {white-space:pre-wrap;overflow-wrap:anywhere;font:inherit} nav {margin:16px 0} </style>'))

@router.get('/inbox',response_class=HTMLResponse)
def choose_account(db:Session=Depends(get_db)):
    d=account_list(db)
    body='<h2>Choose a stored account</h2><p>One owner can access these connected records. Account IDs are selectors, not separate user logins.</p><ul>'
    body+=''.join(f'<li><a href="/inbox/{a["id"]}">'+_esc(a['address'])+'</a></li>' for a in d['accounts'])+'</ul>'
    if not d['accounts']:body+='<p>No stored accounts. OAuth connection and ingestion are separate setup steps.</p>'
    if d['more_accounts_omitted']:body+='<p>Only the first 100 account records are listed.</p>'
    return shell(body)

@router.get('/inbox/{account_id}',response_class=HTMLResponse)
def workspace(account_id:int,q:str='',category:str='',priority:str='',before:int|None=None,limit:int=25,db:Session=Depends(get_db)):
    d=inbox(db,account_id,q,category,priority,before,limit);esc=_esc
    body='<nav><a href="/inbox">Choose another account</a> | <a href="/dashboard">Legacy overview</a></nav><h2>Stored inbox - account '+str(account_id)+'</h2><p>Search subject, sender and snippet only. Case matching follows database collation and may differ across languages/backends. Results use stored record order, not message time.</p>'
    body+='<form method="get" class="filters"><label>Literal search <input name="q" maxlength="200" value="'+esc(q)+'"></label>'
    for name,values,current in [('category',CATEGORIES,category),('priority',PRIORITIES,priority)]:
        body+='<label>'+name.title()+' <select name="'+name+'"><option value="">All</option>'+''.join('<option value="'+x+'"'+(' selected' if x==current else '')+'>'+x+'</option>' for x in sorted(values))+'</select></label>'
    body+='<input type="hidden" name="limit" value="'+str(limit)+'"><button type="submit">Search stored mail</button></form>'
    if not d['messages']:body+='<p>No matches in stored subject/sender/snippet. This does not establish that an email does not exist.</p>'
    for m in d['messages']:
        body+='<article class="mail-card"><span class="label '+('sample' if m['is_sample'] else 'stored-source')+'">'+('SAMPLE' if m['is_sample'] else 'STORED MAIL')+'</span><h3><a href="/inbox/'+str(account_id)+'/messages/'+str(m['id'])+'">'+esc(m['subject'] or '(no subject)')+'</a></h3><p>'+esc(m['sender'])+'</p><p>'+esc(m['snippet'])+(' [preview truncated]' if m['snippet_truncated'] else '')+'</p><p>Stored classification: '+esc(m['category'])+' / '+esc(m['priority'])+' ('+esc(m['source'])+')</p></article>'
    base='/inbox/'+str(account_id)
    body+='<nav><a href="'+base+'?'+esc(urlencode(dict(q=q,category=category,priority=priority,limit=limit)))+'">First page</a>'
    if d['next_before']:body+=' | <a href="'+base+'?'+esc(urlencode(dict(q=q,category=category,priority=priority,limit=limit,before=d['next_before'])))+'">Older stored records</a>'
    body+='</nav><p>Up to 50 records per page. Body text and attachments are not searched. Nothing here implies a complete or freshly synced mailbox.</p>'
    return shell(body)

@router.get('/inbox/{account_id}/messages/{message_id}',response_class=HTMLResponse)
def message_page(account_id:int,message_id:int,db:Session=Depends(get_db)):
    m=stored_message(db,account_id,message_id);esc=_esc
    body=f'<nav><a href="/inbox/{account_id}">Back to account inbox</a></nav><article class="mail-card"><span class="label '+('sample' if m['is_sample'] else 'stored-source')+'">'+('SAMPLE' if m['is_sample'] else 'STORED MAIL - UNTRUSTED TEXT')+'</span><h2>'+esc(m['subject'] or '(no subject)')+'</h2><p>From: '+esc(m['sender'])+'<br>To: '+esc(m['recipients'])+(' [recipient preview truncated at 2000 characters]' if m['recipients_truncated'] else '')+'<br>Stored time: '+esc(m['date'] or 'Unknown')+' (timezone unspecified)</p><pre>'+esc(m['text'])+'</pre>'+('<p>Preview truncated at 32000 characters.</p>' if m['text_truncated'] else '')+'</article>'
    return shell(body,'Stored source text, not instructions for this app. HTML/remote images are not loaded. No reply, draft, label or send action is available.')
