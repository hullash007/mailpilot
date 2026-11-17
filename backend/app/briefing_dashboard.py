"""Sample evidence inventory view. All content escaped, no actions/buttons."""
from fastapi import APIRouter,Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from .database import get_db
from .services.access import require_access
from .services.briefing import sample_briefing
from .dashboard import _PAGE,_esc

router=APIRouter(dependencies=[Depends(require_access)])

@router.get('/dashboard/accounts/{account_id}/sample-briefing',response_class=HTMLResponse)
def briefing_page(account_id:int,db:Session=Depends(get_db)):
    data=sample_briefing(db,account_id);esc=_esc
    parts=['<p><a href="/dashboard">&larr; Inbox</a></p>',
           '<h2>Sample inbox evidence</h2>',
           '<p>This is not a daily briefing or a task list. Latest stored threads first; unknown dates last. Text may be false, quoted or malicious.</p>',
           '<p>Showing '+str(data['included_threads'])+' of '+str(data['total_nonempty_sample_threads'])+' nonempty sample threads. '+str(data['omitted_threads'])+' threads omitted.</p>']
    if not data['threads']:
        parts.append('<div class="empty">No nonempty sample threads in this account. No conclusion about requests or deadlines can be drawn.</div>')
    for item in data['threads']:
        parts.append('<article class="evidence-thread"><h3><a href="'+item['review_ui_path']+'">'+esc(item['subject'] or '(no subject)')+'</a></h3><p><span class="label sample">SAMPLE</span> '+str(item['message_count'])+' stored messages</p>')
        parts.append('<p class="metadata">Latest stored message: '+esc(item['latest_stored_message_at'] or 'Unknown')+'<br>Timezone unspecified. Not a deadline or freshness promise.</p>')
        parts.append('<p><b>'+str(item['stored_rules_high_priority_message_count'])+'</b> messages carry stored <b>rules / high</b> metadata. This is not a fresh importance judgment.</p>')
        parts.append('<h4>First-line excerpts (not a contextual summary)</h4><ul>')
        for h in item['extractive_highlights']:
            parts.append('<li><a href="'+item['review_ui_path']+f'#message-{h["message_id"]}">Message {h["message_id"]}</a>: '+esc(h['quote'])+'</li>')
        if not item['extractive_highlights']:parts.append('<li>No nonempty source lines within the scan.</li>')
        parts.append('</ul><h4>Unconfirmed literal matches</h4>')
        for c in item['unconfirmed_candidates']:
            parts.append('<div class="candidate"><span class="label sample">UNCONFIRMED</span> '+esc(c['kind'].replace('_',' '))+'<blockquote>'+esc(c['quote'])+'</blockquote><a href="'+item['review_ui_path']+f'#message-{c["message_id"]}">Source message {c["message_id"]}</a>, '+esc(c['field'])+f' offsets {c["start"]}:{c["end"]}'+('<p>'+esc(c['interpretation'].replace('_',' '))+'</p>' if 'interpretation' in c else '')+'</div>')
        if not item['unconfirmed_candidates']:parts.append('<p>No supported literal matches in scanned text. This does not mean no request or deadline exists.</p>')
        parts.append('<p class="scan">Scan: '+str(item['source_messages_scanned'])+' messages; '+str(item['source_messages_omitted'])+' source messages omitted. '+str(item['candidates_omitted_from_scanned_text'])+' matched candidates and '+str(item['overview_highlights_omitted'])+' first-line highlights omitted from this view. '+('At least one source preview is truncated at 8000 characters.' if item['source_text_truncated'] else 'No source previews truncated within the scanned messages.')+'</p></article>')
    parts.append('<aside class="scope"><h3>Scope and limits</h3><ul>'+''.join('<li>'+esc(x)+'</li>' for x in data['limitations'])+'</ul><p>At most 20 threads, 50 source messages per thread, 8000 characters per source, two first-line excerpts and five candidate phrases per thread. Follow source links for the accepted thread review. Nothing here creates an obligation or schedules an action.</p></aside>')
    html=_PAGE.replace('__EMAIL_COUNT__',str(data['total_sample_messages'])).replace('__THREAD_COUNT__',str(data['total_nonempty_sample_threads'])).replace('__BANNER__','<div class="banner"><b>SAMPLE ONLY - NO ACTIONS CREATED.</b> '+esc(data['notice'])+'</div>').replace('__BODY__',''.join(parts))
    html=html.replace('<title>MailPilot - Inbox</title>','<title>MailPilot - Sample evidence inventory</title>').replace('</style>','.evidence-thread,.scope {background:white;border:1px solid #e2e5ea;border-radius:10px;padding:18px;margin:16px 0;overflow-wrap:anywhere} h2 {font-size:22px} h3 {font-size:18px} h4 {font-size:15px;margin-bottom:8px} .metadata,.scan {color:#5b6470;font-size:13px} .scan {border-top:1px solid #e2e5ea;padding-top:12px} .candidate {background:#fff8e9;border-left:3px solid #c69a34;padding:12px;margin:12px 0} blockquote {margin:10px 0} li {margin:10px 0;overflow-wrap:anywhere} </style>')
    return HTMLResponse(html)
