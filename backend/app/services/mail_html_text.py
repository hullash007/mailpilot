"""Bounded HTML-to-text conversion, never a browser or network client.

Only inline hidden markers are understood. Stylesheets, layout and images are not
rendered. Output remains untrusted mail text and MUST be HTML-escaped by callers.
"""
import re
from html.parser import HTMLParser


class HTMLTextLimit(ValueError):
    pass


class _Text(HTMLParser):
    DROP = frozenset(('head', 'script', 'style', 'template', 'noscript', 'iframe',
                      'object', 'embed', 'svg', 'math', 'canvas', 'form', 'button',
                      'select', 'textarea', 'title'))
    VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                      'link', 'meta', 'param', 'source', 'track', 'wbr'))
    BLOCK = frozenset(('p', 'div', 'section', 'article', 'header', 'footer', 'aside',
                       'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
                       'table', 'tr', 'blockquote', 'pre', 'br', 'hr'))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.out = []
        self.remaining = 128001
        self.events = 30000
        self.clipped = False

    def tick(self):
        self.events -= 1
        if self.events < 0:
            raise HTMLTextLimit('HTML structure too large')

    def emit(self, value):
        if self.stack and self.stack[-1][1]:
            return
        if len(value) > self.remaining:
            self.clipped = True
        value = value[:self.remaining]
        self.remaining -= len(value)
        if value:
            self.out.append(value)

    def handle_starttag(self, tag, attrs):
        self.tick()
        if len(self.stack) >= 100:
            raise HTMLTextLimit('HTML structure too deep')
        attrs = dict(attrs)
        style = re.sub(r'\s+', '', attrs.get('style') or '').lower()
        hidden = (tag in self.DROP or ':' in tag or 'hidden' in attrs or
                  (attrs.get('aria-hidden') or '').lower() == 'true' or
                  bool(re.search(r'(?:^|;)(?:display:none|visibility:(?:hidden|collapse)|mso-hide:all)(?:!important)?(?:;|$)', style)))
        if tag in self.BLOCK:
            self.emit('\n')
        elif tag in ('td', 'th'):
            self.emit(' | ')
        if tag not in self.VOID:
            self.stack.append((tag, hidden or bool(self.stack and self.stack[-1][1])))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.tick()
        # Do not let a mismatched close expose suppressed text from a malformed
        # subtree. Well-formed closings pop their own nested subtree.
        matches = [i for i, (name, _) in enumerate(self.stack) if name == tag]
        if matches:
            i = matches[-1]
            if not any(hidden for _, hidden in self.stack[i + 1:]):
                del self.stack[i:]
        if tag in self.BLOCK:
            self.emit('\n')

    def handle_data(self, data):
        self.tick()
        self.emit(data)

    def handle_comment(self, data):
        self.tick()


def html_to_text(value):
    parser = _Text()
    parser.feed(value)
    parser.close()
    result = ''.join(parser.out).replace('\r\n', '\n').replace('\r', '\n')
    result = re.sub(r'[^\S\n]+', ' ', result)
    result = re.sub(r' *\n *', '\n', result)
    # Layout tables often contain empty spacer cells. Keep real cell text,
    # but do not fill the reader with meaningless pipe-only rows.
    result = re.sub(r'(?m)^[ |]*\|[ |]*(?:\n|$)', '', result)
    result = re.sub(r'\n{3,}', '\n\n', result).strip()
    return result[:128000], parser.clipped or len(result) > 128000
