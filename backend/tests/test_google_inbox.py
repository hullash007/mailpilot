"""Own-subject stored inbox browsing only. No Google/model/real-mail calls."""
from urllib.parse import urlsplit, parse_qs
import re
import html
import pytest
from sqlalchemy import select, event
from .test_google_identity import workspace, login, add_mail
from app import google_main as g
from app.models import GmailAccount, Email, Thread


def signed(workspace, monkeypatch):
    client, db = workspace
    assert login(client, db, monkeypatch).status_code == 303
    account = db.scalar(select(GmailAccount).where(GmailAccount.google_user_id == 'subject-A'))
    return client, db, account


def seed(db, account, count=1):
    t = Thread(account_id=account.id, gmail_thread_id='browse-fixture')
    db.add(t); db.flush()
    rows = [Email(account_id=account.id, thread_id=t.id, gmail_message_id='browse-'+str(i),
                  subject='SAMPLE record '+str(i), sender='sender@example.com',
                  snippet='Fabricated text only', category='notification', priority='normal',
                  body_text='Not searched body') for i in range(count)]
    db.add_all(rows); db.commit()
    return rows


def older_url(text):
    match = re.search(r'<a class="button" href="([^"]+)">Older stored records</a>', text)
    return html.unescape(match.group(1)) if match else None


def test_subject_pagination_never_lists_other_account(workspace, monkeypatch):
    client, db, a = signed(workspace, monkeypatch)
    own = seed(db, a, 56)
    other = GmailAccount(email_address='b@example.com', google_user_id='subject-B', credentials={'token':'fixture'})
    db.add(other); db.commit(); foreign = add_mail(db, other.id, 'OTHER-PRIVATE-MARKER')
    first = client.get('/app?limit=25&account_id='+str(other.id))
    assert first.status_code == 200 and 'OTHER-PRIVATE-MARKER' not in first.text
    seen = []
    page = first
    for _ in range(3):
        seen.extend(int(x) for x in re.findall(r'<article class="card"><h2><a href="/app/messages/(\d+)">',page.text))
        url = older_url(page.text)
        if not url: break
        page = client.get(url)
        assert 'OTHER-PRIVATE-MARKER' not in page.text
    assert seen == sorted([r.id for r in own], reverse=True)
    assert len(seen) == len(set(seen)) == 56
    assert client.get('/app/messages/'+str(foreign)).status_code == 404
    assert older_url(page.text) is None


@pytest.mark.parametrize('term,expected', [('100%', 'literal-percent'), ('a_b','literal-underscore'), ('bang!','literal-bang'), ('sender-needle','sender-hit'), ('snippet-needle','snippet-hit'), ('body-secret',None)])
def test_literal_search_fields(workspace, monkeypatch, term, expected):
    client, db, a = signed(workspace, monkeypatch)
    rows = seed(db, a, 6)
    for row,title in zip(rows,['literal-percent','literal-underscore','literal-bang','sender-hit','snippet-hit','decoy']): row.subject=title
    rows[0].snippet='100%'; rows[1].snippet='a_b'; rows[2].snippet='bang!'
    rows[3].sender='sender-needle@example.com'; rows[4].snippet='snippet-needle'; rows[5].body_text='body-secret'
    db.commit()
    page=client.get('/app',params={'q':term})
    titles=[html.unescape(x) for x in re.findall(r'<article class="card"><h2><a[^>]+>(.*?)</a></h2>',page.text)]
    assert titles==([expected] if expected else [])


def test_combined_filters_persist_in_cursor_and_reset_cursor(workspace, monkeypatch):
    client, db, a = signed(workspace, monkeypatch); rows=seed(db,a,7)
    for row in rows:row.subject='needle';row.category='receipt';row.priority='high'
    rows[0].category='social';rows[1].priority='low';db.commit()
    page=client.get('/app',params={'q':'needle','category':'receipt','priority':'high','limit':2})
    url=older_url(page.text);query=parse_qs(urlsplit(url).query)
    assert query['q']==['needle'] and query['category']==['receipt'] and query['priority']==['high'] and query['limit']==['2']
    form=re.search(r'<form class="filters"(.*?)</form>',page.text).group(1)
    assert 'method="get"' in form and 'action="/app"' in form and 'name="before"' not in form
    assert '<option value="receipt" selected>' in form
    second=client.get(url);assert 'Return to first page' in second.text


@pytest.mark.parametrize('params', [{'before':0},{'before':-1},{'before':2**63},{'before':'no'},{'limit':0},{'limit':51},{'limit':'no'},{'q':'x'*201},{'category':'wrong'},{'priority':'wrong'}])
def test_invalid_query_is_rejected(workspace, monkeypatch, params):
    client, db, a=signed(workspace,monkeypatch)
    assert client.get('/app',params=params).status_code==422


def test_search_input_and_snippet_are_escaped_and_truncation_visible(workspace, monkeypatch):
    client,db,a=signed(workspace,monkeypatch);rows=seed(db,a)
    rows[0].subject='SAMPLE "<&';rows[0].snippet='&lt;script&gt;bad()&lt;/script&gt;'+'x'*1100;db.commit()
    page=client.get('/app');assert '<script>' not in page.text and '&lt;script&gt;' in page.text
    assert 'Snippet preview truncated at 1000 characters' in page.text
    probe='"><script>bad()</script>'
    filtered=client.get('/app',params={'q':probe});assert '<script>' not in filtered.text and '&quot;&gt;&lt;script&gt;' in filtered.text


def test_unknown_account_selectors_cannot_switch_subject_or_mutate(workspace, monkeypatch):
    client,db,a=signed(workspace,monkeypatch);seed(db,a)
    before=[(r.id,r.subject,r.body_text) for r in db.scalars(select(Email)).all()]
    monkeypatch.setattr(g,'ingest_account',lambda *a,**kw:pytest.fail('GET must not ingest'))
    statements=[]
    event.listen(db.bind,'before_cursor_execute',lambda conn,cursor,statement,*args:statements.append(statement))
    page=client.get('/app?subject=subject-B&account_id=999&google_user_id=subject-B')
    assert statements and all(x.lstrip().upper().startswith('SELECT') for x in statements)
    assert page.status_code==200 and 'a@example.com' in page.text
    assert [(r.id,r.subject,r.body_text) for r in db.scalars(select(Email)).all()]==before
    client.cookies.clear();assert client.get('/app?q=anything&before=1').status_code==401
