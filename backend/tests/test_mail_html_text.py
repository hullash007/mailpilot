import base64
import pytest
from app.services.gmail_reader import message_body, MailUnavailable
from app.services.mail_html_text import html_to_text, HTMLTextLimit


def part(value, mime='text/html', charset='utf-8', **kw):
    return dict(mimeType=mime, headers=[{'name': 'Content-Type', 'value': mime + '; charset=' + charset}], body={'data': base64.urlsafe_b64encode(value.encode(charset)).decode()}, **kw)


def test_html_text_entities_blocks_and_no_remote_content():
    value, clipped = html_to_text('<head><title>hidden title</title></head><h1>Hello &amp; welcome</h1><p>First<br>Second</p><img src="https://remote.invalid/pixel"><a href="https://remote.invalid">Visible link</a>')
    assert 'Hello & welcome' in value and 'First\nSecond' in value
    assert 'Visible link' in value and 'remote.invalid' not in value and 'hidden title' not in value
    assert not clipped


@pytest.mark.parametrize('payload', [
    '<script>hidden()</script>', '<style>.x{display:none}</style>',
    '<div hidden>hidden</div>', '<div aria-hidden="true">hidden</div>',
    '<div style="display: none !important">hidden</div>',
    '<div style="visibility: hidden">hidden</div>',
    '<div style="mso-hide:all">hidden</div>',
    '<svg><text>hidden</text></svg>', '<iframe>hidden</iframe>',
    '<form><label>hidden</label></form>', '<template>hidden</template>',
    '<object>hidden</object>', '<!-- hidden -->',
])
def test_active_and_explicit_hidden_subtrees_removed(payload):
    value, _ = html_to_text('Before' + payload + 'After')
    assert 'hidden' not in value and 'Before' in value and 'After' in value


def test_alternative_prefers_plain_without_duplicate_html():
    p = dict(mimeType='multipart/alternative', parts=[part('Keep plain', 'text/plain'), part('<p>Duplicate HTML</p>')])
    value, clipped, files, kind = message_body(p)
    assert (value, clipped, files, kind) == ('Keep plain', False, [], 'plain')


def test_mixed_preserves_independent_html_body_and_skips_attachment_content():
    p = dict(mimeType='multipart/mixed', parts=[part('First', 'text/plain'), part('<p>Second</p>'), part('<p>Attachment secret</p>', filename='details.html')])
    value, clipped, files, kind = message_body(p)
    assert value == 'First\n\nSecond' and kind == 'html_text'
    assert files == ['details.html'] and not clipped


def test_attachment_disposition_without_filename_not_used_as_body():
    p = part('Private attached text', 'text/plain')
    p['headers'].append({'name': 'Content-Disposition', 'value': 'attachment'})
    assert message_body(p) == ('', False, ['Unnamed attachment'], 'none')


@pytest.mark.parametrize('charset,value', [('windows-1252', 'Café costs €5'), ('iso-8859-1', 'Café'), ('utf-16', 'Hello world'), ('shift-jis', 'こんにちは')])
def test_declared_charsets(charset, value):
    assert message_body(part(value, 'text/plain', charset))[0] == value
    assert message_body(part('<p>' + value + '</p>', charset=charset))[0] == value


def test_unknown_codec_and_bad_bytes_do_not_execute_transform():
    p = part('Safe bytes', 'text/plain')
    p['headers'][0]['value'] = 'text/plain; charset=unicode_escape'
    p['body']['data'] = base64.urlsafe_b64encode(b'\\u0041\xff').decode()
    assert message_body(p)[0] == '\\u0041�'


def test_html_output_bounded_and_truncation_flag():
    value, clipped = html_to_text('<p>' + 'x' * 200000 + '</p>')
    assert len(value) <= 128000 and clipped
    with pytest.raises(HTMLTextLimit): html_to_text('<div>' * 101)
    with pytest.raises(HTMLTextLimit): html_to_text('<br>' * 30001)


def test_malformed_suppressed_tree_not_reopened_by_unrelated_close():
    value, _ = html_to_text('Shown<div hidden>Secret</span>Still secret</div>Shown again')
    assert value.split() == ['Shown', 'Shown', 'again'] and 'secret' not in value.lower()


def test_malformed_base64_fails_closed():
    p = part('safe')
    p['body']['data'] = '*not-base64*'
    with pytest.raises(MailUnavailable): message_body(p)


def test_reader_escapes_converted_text_and_shows_provenance():
    from fastapi.testclient import TestClient
    from app.gmail_reader_fixture import create_fixture_app
    app = create_fixture_app()
    msg = app.state.boxes['harbor'].rows['H2']['messages'][0]
    headers = msg['payload']['headers']
    msg['payload'] = part('<h2>Ticket ready</h2><p>Board &lt;script&gt; &amp; arrive</p><script>hidden()</script><img src="https://remote.invalid/pixel">')
    msg['payload']['headers'] += headers
    response = TestClient(app).get('/mail/threads/H2')
    assert response.status_code == 200
    assert 'Ticket ready' in response.text and '&lt;script&gt;' in response.text
    assert '<script>' not in response.text and 'remote.invalid' not in response.text
    assert 'Converted from HTML to text' in response.text and '1 message</p>' in response.text
    assert '1 messages' not in response.text
    assert "default-src 'none'" in response.headers['content-security-policy']


def test_layout_table_empty_spacers_are_not_readable_text():
    value, _ = html_to_text('<table><tr><td> </td></tr><tr><td><table><tr><td> </td></tr></table></td></tr><tr><td>Time</td><td>08:15</td></tr></table>')
    assert value == '| Time | 08:15'
