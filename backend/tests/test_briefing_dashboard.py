from .test_thread_review import client
from app.models import Email
from app.config import get_settings


def test_page_matches_data_and_escapes(client):
    tc,s,a,b,t=client
    row=s.query(Email).first();row.body_text='Please review <script>bad</script> by tomorrow.';s.commit()
    d=tc.get(f'/accounts/{a}/sample-briefing').json()
    response=tc.get(f'/dashboard/accounts/{a}/sample-briefing');html=response.text
    assert response.status_code==200
    assert 'Sample inbox evidence' in html and 'NO ACTIONS CREATED' in html
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert 'unresolved' in html and 'Timezone unspecified' in html
    for item in d['threads']:
        assert item['review_ui_path'] in html
        for c in item['unconfirmed_candidates']:
            assert f'offsets {c["start"]}:{c["end"]}' in html
    assert 'stored <b>rules / high</b> metadata' in html
    assert 'not a contextual summary' in html


def test_dashboard_navigation_only_eligible(client):
    tc,s,a,b,t=client
    link=f'/dashboard/accounts/{a}/sample-briefing'
    assert link in tc.get('/dashboard').text
    row=s.query(Email).first();row.is_sample=False;s.commit()
    assert link not in tc.get('/dashboard').text
    assert tc.get(link).status_code==404


def test_empty_account_and_access(client,monkeypatch):
    tc,s,a,b,t=client
    assert 'No nonempty sample threads' in tc.get(f'/dashboard/accounts/{b}/sample-briefing').text
    assert tc.get('/dashboard/accounts/9999/sample-briefing').status_code==404
    monkeypatch.setenv('APP_ACCESS_TOKEN','test-page');get_settings.cache_clear()
    try:
        assert tc.get(f'/dashboard/accounts/{a}/sample-briefing').status_code==401
        assert tc.get(f'/dashboard/accounts/{a}/sample-briefing',headers={'Authorization':'Bearer test-page'}).status_code==200
    finally:get_settings.cache_clear()


def test_html_shows_omission_warning(client):
    tc,s,a,b,t=client
    row=s.query(Email).first();row.body_text='Please review by tomorrow.\n'*500;s.commit()
    html=tc.get(f'/dashboard/accounts/{a}/sample-briefing').text
    assert 'truncated at 8000 characters' in html and 'omitted from this view' in html
    assert html.count('class="candidate"')==5
