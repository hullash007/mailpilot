"""Read-only presentation over existing stored messages. No sync models or network."""
from urllib.parse import urlencode
from fastapi import Depends, Query, Path, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from .database import get_db
from .models import GmailAccount
from .services import stored_conversations as views
from .services.inbox import CATEGORIES, PRIORITIES


def _stamp(value):
    return value.strftime('%d %b %Y, %H:%M UTC') if value else 'Time unavailable'


def _shell(title, body):
    from .google_main import page,esc
    return page('<style>a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #17223b;outline-offset:3px}button:disabled{opacity:.65;cursor:wait}nav a{padding:7px 0;min-height:30px}.card h2{font-size:20px;margin-top:0}</style><nav aria-label="Workspace"><a href="/workspace">Stored conversations</a><a href="/app">Original stored inbox</a></nav>'
                '<p class="notice">Stored mail only. Read-only; AI is off.</p>'
                '<h1>' + esc(title) + '</h1>' + body)


def create_app():
    from .google_main import create_app as google_app
    return google_app()

def install_routes(app):
    from .google_main import own_account,esc

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


    @app.exception_handler(RequestValidationError)
    async def invalid_query(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith('/workspace'):
            return await request_validation_exception_handler(request, exc)
        response = _shell('Check your stored search', '<p class="notice">Choose a valid search, filter or stored page.</p><p><a href="/workspace">Clear filters and return</a></p>')
        response.status_code = 422
        return response

    @app.get('/workspace')
    def workspace(q: str = Query('', max_length=200), category: str = '', priority: str = '',
                  before: int | None = Query(None, ge=1, le=9223372036854775807),
                  account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        status = views.storage_status(db, account.id)
        data = views.search(db, account.id, q, category, priority, before)
        body = '<p>Signed in as ' + esc(account.email_address) + '</p><section class="card" aria-label="Stored mail status"><h2>' + esc(status['label']) + '</h2><p>' + esc(status['detail']) + '</p><small>No background sync.</small></section>'
        body += '<form class="filters" method="get" action="/workspace"><label>Search stored mail<input name="q" value="' + esc(q) + '" maxlength="200" placeholder="Subject, sender or plain text"></label>'
        for name, current, options in [('category', category, CATEGORIES), ('priority', priority, PRIORITIES)]:
            body += '<label>' + name.capitalize() + '<select name="' + name + '"><option value="">All</option>'
            for option in sorted(options):
                body += '<option value="' + option + '"' + (' selected' if current == option else '') + '>' + option.capitalize() + '</option>'
            body += '</select></label>'
        body += '<button>Search</button><a href="/workspace">Clear filters</a><span id="search-state" role="status" aria-live="polite"></span></form><script>document.querySelector(".filters").addEventListener("submit",function(){document.getElementById("search-state").textContent="Searching stored mail...";this.querySelector("button").disabled=true;});</script><p><small>Search stored plain text and metadata, not your entire Gmail.</small></p><details><summary>Search scope and result order</summary><p>Literal search of stored subject, sender, preview and plain text. No attachments, raw HTML or whole-mailbox coverage. Newest stored additions first, not email date. Provider membership and completeness are not established. Conversation groups and matching-message counts apply to this page only; a conversation may recur on another page.</p></details>'
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
        data = views.conversation(db, account.id, thread_id)
        notice = 'Stored messages in this conversation. May be incomplete. This view contains messages stored locally by MailPilot. It may not include every message currently in Gmail.'
        body = '<p class="notice">' + esc(notice) + '</p><p><small>Oldest known email time first; unknown times last. Untrusted plain text only: links, HTML, scripts and attachments are not loaded.</small></p>'
        for row in data['messages']:
            body += '<article class="card"><h2>' + ('Sent' if row['sent'] else 'Received') + ' · ' + esc(_stamp(row['date'])) + '</h2><p>From: ' + esc(row['sender']) + '<br>To: ' + esc(row['recipients'][:2000]) + '</p><p><small>' + esc(row['status']) + (' · Synthetic fixture' if row['is_sample'] else '') + '</small></p><p><a href="/app/messages/' + str(row['id']) + '">Open stored source message</a></p><pre>' + esc(row['text']) + '</pre>'
            if row['truncated']: body += '<p><small>Stored text preview limited to 32,000 characters.</small></p>'
            body += '</article>'
        if data['omitted']: body += '<p class="notice">Only the first 500 stored messages are shown. This view is incomplete.</p>'
        if not data['messages']: body += '<p>No message text is stored for this conversation.</p>'
        return _shell(data['subject'], body)

    return app
