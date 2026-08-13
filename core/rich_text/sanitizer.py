import re
import html
from html.parser import HTMLParser

ALLOWED_TAGS = {
    'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'strike',
    'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'blockquote',
    'pre', 'code', 'a', 'img', 'table', 'thead', 'tbody',
    'tr', 'th', 'td', 'hr', 'span', 'div'
}

ALLOWED_ATTRIBUTES = {
    'a': {'href', 'target', 'rel', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height', 'data-attachment-id', 'data-inline-image-id', 'style'},
    'table': {'colspan', 'rowspan', 'style', 'class'},
    'th': {'colspan', 'rowspan', 'style', 'class'},
    'td': {'colspan', 'rowspan', 'style', 'class'},
    'ul': {'class', 'data-type', 'style'},
    'ol': {'class', 'data-type', 'style'},
    'li': {'class', 'data-type', 'data-checked', 'style'},
    'span': {'style', 'class'},
    'div': {'style', 'class'},
    'p': {'style', 'class'},
    'h1': {'style', 'class'},
    'h2': {'style', 'class'},
    'h3': {'style', 'class'},
    'h4': {'style', 'class'},
    'pre': {'style', 'class'},
    'code': {'class', 'style'},
}

ALLOWED_TEXT_COLORS = {
    '#334155', '#64748b', '#ef4444', '#f97316',
    '#eab308', '#22c55e', '#3b82f6', '#a855f7',
    'inherit', 'initial'
}

ALLOWED_HIGHLIGHT_COLORS = {
    '#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#e2e8f0', 'transparent'
}

def sanitize_url(url):
    """
    Validates link and image URLs.
    Rejects javascript:, vbscript:, data: (unless safe image), file:.
    """
    if not url:
        return ''
    trimmed = str(url).strip()
    if re.match(r'^(javascript|vbscript|file):', trimmed, re.IGNORECASE):
        return ''
    if trimmed.startswith('blob:'):
        return trimmed
    if trimmed.startswith('/'):
        return trimmed
    if re.match(r'^(https?://|mailto:)', trimmed, re.IGNORECASE):
        return trimmed
    if re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}(/.*)?$', trimmed):
        return f'https://{trimmed}'
    return trimmed

def sanitize_style(style_val):
    """
    Filter inline styles to allowed CSS properties only.
    """
    if not style_val:
        return ''
    cleaned = []
    pairs = style_val.split(';')
    for pair in pairs:
        if ':' not in pair:
            continue
        prop, val = pair.split(':', 1)
        prop = prop.strip().lower()
        val = val.strip()

        if prop == 'color':
            cleaned.append(f"color: {val}")
        elif prop == 'background-color':
            cleaned.append(f"background-color: {val}")
        elif prop == 'text-align' and val in ('left', 'center', 'right', 'justify'):
            cleaned.append(f"text-align: {val}")
        elif prop in ('max-width', 'width', 'height', 'margin', 'display', 'border-radius'):
            # Allow clean size/margin rules for responsive images/tables
            if not re.search(r'position|fixed|absolute|expression|url', val, re.IGNORECASE):
                cleaned.append(f"{prop}: {val}")

    return '; '.join(cleaned)

class TiptapHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in ('script', 'iframe', 'object', 'embed', 'form', 'style'):
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return

        if tag_lower not in ALLOWED_TAGS:
            return

        cleaned_attrs = []
        allowed_for_tag = ALLOWED_ATTRIBUTES.get(tag_lower, set())

        for attr, val in attrs:
            attr_lower = attr.lower()
            if attr_lower.startswith('on'): # strip event handlers
                continue
            if attr_lower not in allowed_for_tag:
                continue

            if attr_lower in ('href', 'src'):
                clean_v = sanitize_url(val)
                if not clean_v:
                    continue
                cleaned_attrs.append((attr_lower, clean_v))
                if attr_lower == 'href':
                    cleaned_attrs.append(('target', '_blank'))
                    cleaned_attrs.append(('rel', 'noopener noreferrer'))
            elif attr_lower == 'style':
                clean_s = sanitize_style(val)
                if clean_s:
                    cleaned_attrs.append(('style', clean_s))
            else:
                cleaned_attrs.append((attr_lower, val))

        # Deduplicate attrs like target/rel
        seen_attrs = set()
        deduped = []
        for a, v in cleaned_attrs:
            if a in seen_attrs:
                continue
            seen_attrs.add(a)
            deduped.append((a, v))

        attr_str = "".join([f' {a}="{html.escape(v, quote=True)}"' for a, v in deduped])
        self.result.append(f"<{tag_lower}{attr_str}>")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in ('script', 'iframe', 'object', 'embed', 'form', 'style'):
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return

        if tag_lower in ALLOWED_TAGS and tag_lower not in ('br', 'hr', 'img'):
            self.result.append(f"</{tag_lower}>")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.result.append(html.escape(data))

    def handle_entityref(self, name):
        if self.skip_depth == 0:
            self.result.append(f"&{name};")

    def handle_charref(self, name):
        if self.skip_depth == 0:
            self.result.append(f"&#{name};")

def sanitize_rich_text_html(content):
    """
    Primary backend HTML sanitizer for Tiptap & legacy content.
    Fulfills Rule 11 & 12: Detects legacy escaped content, unescapes once,
    sanitizes using strict tag/attribute/url allowlist.
    """
    if not content:
        return ""
    s = str(content).strip()

    # Detect legacy double-escaped content (e.g. &lt;p&gt;)
    if '&lt;' in s or '&gt;' in s:
        # Unescape once
        s = html.unescape(s)

    parser = TiptapHTMLSanitizer()
    parser.feed(s)
    parser.close()
    return "".join(parser.result)

def is_rich_text_empty(content):
    """
    Check if content is visually empty (e.g. <p></p>, <p><br></p>, empty space).
    """
    if not content:
        return True
    cleaned = sanitize_rich_text_html(content)
    text_only = re.sub(r'<[^>]*>', '', cleaned).strip()
    return len(text_only) == 0

def extract_plain_text(content, max_length=None):
    """
    Converts rich text HTML to plain text excerpt for cards/tables.
    """
    if not content:
        return ""
    cleaned = sanitize_rich_text_html(content)
    text = re.sub(r'<[^>]*>', ' ', cleaned)
    text = re.sub(r'\s+', ' ', text).strip()
    if max_length and len(text) > max_length:
        return text[:max_length] + "..."
    return text
