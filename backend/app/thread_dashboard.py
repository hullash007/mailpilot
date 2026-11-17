"""Escaped, sample-only evidence review using the existing dashboard shell."""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from .database import get_db
from .services.access import require_access
from .services.thread_review import review_thread
from .dashboard import _PAGE, _esc

router=APIRouter(dependencies=[Depends(require_access)])

@router.get('/dashboard/accounts/{account_id}/threads/{thread_id}',response_class=HTMLResponse)
def thread_page(account_id:int,thread_id:int,db:Session=Depends(get_db)):
    report=review_thread(db,account_id,thread_id)
    esc=_esc
    parts=['<p><a href="/dashboard">&larr; Inbox</a></p>',
           '<h2>'+esc(report['subject'])+'</h2>',
           '<p>Extractive overview - first line of each message, not an AI summary.</p><ul>']
    for item in report['extractive_highlights']:
        parts.append(f'<li><a href="#message-{item["message_id"]}">Message {item["message_id"]}</a>: '+esc(item['quote'])+'</li>')
    parts.append('</ul><h2>Unconfirmed phrase candidates</h2>')
    if not report['candidates']:parts.append('<p>No supported literal phrases found. This does not mean there are no requests or deadlines.</p>')
    for item in report['candidates']:
        detail=item.get('interpretation','unconfirmed').replace('_',' ')
        parts.append('<article class="evidence"><span class="label sample">UNCONFIRMED</span> <b>'+esc(item['kind'].replace('_',' '))+'</b><blockquote>'+esc(item['quote'])+'</blockquote><p>'+esc(detail)+f' - <a href="#message-{item["message_id"]}">source message {item["message_id"]}</a>, '+esc(item['field'])+f' offsets {item["start"]}:{item["end"]}</p></article>')
    if report['omitted_candidates']:parts.append(f'<p>{report["omitted_candidates"]} additional literal matches omitted by display limit.</p>')
    parts.append('<h2>Source messages (oldest first; unknown dates last)</h2>')
    for m in report['messages']:
        parts.append(f'<article class="evidence" id="message-{m["id"]}"><h3>Message {m["id"]} <span class="label sample">SAMPLE</span></h3><p>'+esc(m['sender'])+'<br>'+esc((m['date']+' (stored time; timezone unspecified)') if m['date'] else 'Date unknown')+'</p><pre>'+esc(m['text'])+'</pre>'+('<p>Source preview truncated at8000 characters; later evidence not analysed.</p>' if m['text_truncated'] else '')+'</article>')
    if report['messages_truncated']:parts.append('<p>Only the first 50 messages are shown/analysed. Later messages may change the context.</p>')
    html=_PAGE.replace('__EMAIL_COUNT__',str(report['total_messages'])).replace('__THREAD_COUNT__','1').replace('__BANNER__','<div class="banner"><b>SAMPLE ONLY - NO ACTIONS CREATED.</b> '+esc(report['notice'])+'</div>').replace('__BODY__',''.join(parts))
    html=html.replace('<title>MailPilot - Inbox</title>','<title>MailPilot - Sample thread review</title>').replace('</style>','.evidence { background:white; border:1px solid #e2e5ea; border-radius:10px; padding:16px; margin:12px 0; overflow-wrap:anywhere; } pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; } blockquote { margin:12px 0; border-left:3px solid #d6a041; padding-left:12px; } li { margin:10px 0; overflow-wrap:anywhere; } h2 {font-size:19px} h3 {font-size:16px} </style>')
    return HTMLResponse(html)
