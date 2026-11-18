"""Opt-in LOCAL candidate UI. Existing google_main app imports none of this.

Run only against a synthetic candidate DB until separately qualified. No provider
sync route is added. All reads still use Google's verified-subject own_account.
"""
from urllib.parse import urlencode
from fastapi import Depends, Query, Path, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy.orm import Session
from .google_main import create_app as google_app, own_account, page, esc
from .database import get_db
from .models import GmailAccount
from .services import workspace as views
from .services.inbox import CATEGORIES, PRIORITIES


def _stamp(value):
    return value.strftime('%d %b %Y, %H:%M UTC') if value else 'Time unavailable'


def _shell(title, body):
    return page('<style>a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #17223b;outline-offset:3px}button:disabled{opacity:.65;cursor:wait}nav a{padding:7px 0;min-height:30px}.card h2{font-size:20px;margin-top:0}</style><nav aria-label="Workspace"><a href="/workspace">Stored conversations</a><a href="/app">Original stored inbox</a></nav>'
                '<p class="notice">Private pilot · Stored mail only. Read-only; AI is off.</p>'
                '<h1>' + esc(title) + '</h1>' + body)


def create_app():
    app = google_app()
    # Newer stored baseline already registers these paths. One explicit replacement.
    app.router.routes[:]=[r for r in app.routes if not getattr(r,"path","").startswith("/workspace")]
    return install_routes(app)

def install_routes(app,views_override=None):
    local_views=views_override or views

    @app.exception_handler(HTTPException)
    async def candidate_error(request: Request, exc: HTTPException):
        if not request.url.path.startswith('/workspace'):
            return await http_exception_handler(request, exc)
        response = _shell('Stored workspace unavailable',
            '<p class="notice">' + esc(exc.detail) + '</p><p>Stored mail was not changed.</p>' +
            ('<a class="button" href="/login">Sign in with Google</a>' if exc.status_code == 401 else
             '<a class="button" href="/workspace">Return to stored conversations</a>'))
        response.status_code = exc.status_code
        return response


    @app.get('/workspace')
    def workspace(q: str = Query('', max_length=200), category: str = '', priority: str = '',
                  before: int | None = Query(None, ge=1, le=9223372036854775807),
                  account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        status = local_views.sync_status(db, account.id)
        data = local_views.search(db, account.id, q, category, priority, before)
        body = '<p>Signed in as ' + esc(account.email_address) + '</p><section class="card" aria-label="Sync status"><h2>' + esc(status['label']) + '</h2><p>' + esc(status['detail']) + '</p><small>Last sync: ' + esc(_stamp(status['last_success'])) + '</small></section>'
        body += '<form class="filters" method="get" action="/workspace"><label>Search stored mail<input name="q" value="' + esc(q) + '" maxlength="200" placeholder="Subject, sender or plain text"></label>'
        for name, current, options in [('category', category, CATEGORIES), ('priority', priority, PRIORITIES)]:
            body += '<label>' + name.capitalize() + '<select name="' + name + '"><option value="">All</option>'
            for option in sorted(options):
                body += '<option value="' + option + '"' + (' selected' if current == option else '') + '>' + option.capitalize() + '</option>'
            body += '</select></label>'
        body += '<button>Search</button><a href="/workspace">Clear filters</a><span id="search-state" role="status" aria-live="polite"></span></form><script>document.querySelector(".filters").addEventListener("submit",function(){document.getElementById("search-state").textContent="Searching stored mail...";this.querySelector("button").disabled=true;});</script><p><small>Search stored plain text and metadata, not your entire Gmail.</small></p><details><summary>Search scope and result order</summary><p>Literal search of stored subject, sender, preview and plain text. No attachments, raw HTML or whole-mailbox coverage. Newest stored additions first, not email date. Deleted/excluded/unavailable snapshot members are withheld from search. Conversation groups and matching-message counts apply to this page only; a conversation may recur on another page.</p></details>'
        if data['withheld']:
            body += '<p class="notice">Some matches are retained or unavailable and are not shown as current search results.</p>'
        if not data['messages']:
            body += '<section class="card"><h2>No stored matches on this page</h2><p>Try a different word or clear filters. This does not mean Gmail has no matching mail.</p></section>'
        for row in data['messages']:
            body += '<article class="card"><h2><a href="/workspace/threads/' + str(row['thread_id']) + '">' + esc(row['subject']) + '</a></h2><p>' + esc(row['sender']) + '</p><small>' + esc(_stamp(row['date'])) + ' · ' + esc(row['status']) + (' · Synthetic fixture' if row['is_sample'] else '') + '</small><p>' + esc(row['excerpt']) + '</p><small>' + esc(row['category']) + ' / ' + esc(row['priority']) + ' · ' + str(row['matched_messages']) + ' matching stored messages on this page</small></article>'
        if data['next_before']:
            body += '<nav><a href="/workspace?' + esc(urlencode(dict(q=q, category=category, priority=priority, before=data['next_before']))) + '">Older stored results</a></nav>'
        if before:
            body += '<p><a href="/workspace?' + esc(urlencode(dict(q=q, category=category, priority=priority))) + '">First page</a></p>'
        return _shell('Your stored conversations', body)

    @app.get('/workspace/threads/{thread_id}')
    def conversation(thread_id: int = Path(ge=1, le=9223372036854775807),
                     account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        data = local_views.conversation(db, account.id, thread_id)
        snap = data['snapshot']
        if snap is None:
            notice = 'Incomplete stored conversation. No full provider snapshot has been verified.'
        elif snap.availability == 'unavailable':
            notice = 'Provider conversation unavailable at last check. Retained text below is not a current Gmail snapshot.'
        elif data['unobserved']:
            notice = 'Not observed in the latest completed sync. Retained conversation is stale or unverified, not proof of deletion.'
        else:
            notice = str(snap.message_count) + ' eligible messages in snapshot; ' + str(snap.excluded_count) + ' excluded. Snapshot fetched ' + _stamp(snap.fetched_at) + '. Stored, not live.'
        body = '<p class="notice">' + esc(notice) + '</p><p><small>Oldest known email time first; unknown times last. Untrusted plain text only: links, HTML, scripts and attachments are not loaded.</small></p>'
        for row in data['messages']:
            body += '<article class="card"><h2>' + ('Sent' if row['sent'] else 'Received') + ' · ' + esc(_stamp(row['date'])) + '</h2><p>From: ' + esc(row['sender']) + '<br>To: ' + esc(row['recipients'][:2000]) + '</p><p><small>' + esc(row['status']) + (' · Synthetic fixture' if row['is_sample'] else '') + '</small></p><p><a href="/app/messages/' + str(row['id']) + '">Open stored source message</a></p><pre>' + esc(row['text']) + '</pre>'
            if row['truncated']: body += '<p><small>Stored text preview limited to 32,000 characters.</small></p>'
            body += '</article>'
        if data['omitted']: body += '<p class="notice">Only the first 500 stored messages are shown. This view is incomplete.</p>'
        if not data['messages']: body += '<p>No message text is stored for this conversation.</p>'
        return _shell(data['subject'], body)

    return app
