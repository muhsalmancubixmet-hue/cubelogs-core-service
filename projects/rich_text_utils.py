import re
import html

from core.rich_text import sanitize_rich_text_html, is_rich_text_empty, extract_plain_text

# Approved color palettes
ALLOWED_TEXT_COLORS = {
    '#334155', '#64748b', '#ef4444', '#f97316',
    '#eab308', '#22c55e', '#3b82f6', '#a855f7'
}

ALLOWED_HIGHLIGHT_COLORS = {
    '#fef08a', '#bbf7d0', '#bfdbfe', '#fbcfe8', '#e2e8f0', 'transparent'
}

def unescape_escaped_html(text):
    """
    Unescapes HTML entity strings like &lt;p&gt; into <p> if present.
    """
    if not text:
        return ""
    s = text.strip()

    # Unwrap string quotes if double-encoded JSON string
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            import json
            unwrapped = json.loads(s)
            if isinstance(unwrapped, str):
                s = unwrapped
        except Exception:
            s = s[1:-1]

    # Unescape HTML entities if &lt; or &gt; present
    if '&lt;' in s or '&gt;' in s or '&amp;' in s:
        s = html.unescape(s)

    return s

def sanitize_url(url):
    """
    Validates and sanitizes link and image URLs.
    """
    if not url:
        return ''
    trimmed = url.strip()
    if re.match(r'^(javascript|vbscript|file):', trimmed, re.IGNORECASE):
        return ''
    if trimmed.startswith('blob:') or trimmed.startswith('/'):
        return trimmed
    if re.match(r'^(https?://|mailto:)', trimmed, re.IGNORECASE):
        return trimmed
    if re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}(/.*)?$', trimmed):
        return f'https://{trimmed}'
    return trimmed

def sanitize_html(html_content):
    return sanitize_rich_text_html(html_content)


def convert_markdown_inline_to_html(text):
    """
    Converts inline Markdown formatting (bold, italic, strikethrough, underline, code, images, links)
    to valid HTML.
    """
    if not text:
        return ""

    s = text

    # Images: ![alt](url) -> <img src="url" alt="alt" style="max-width: 100%; height: auto; border-radius: 8px; margin: 8px 0; display: block;" />
    def img_repl(m):
        alt = m.group(1) or 'Image'
        url = sanitize_url(m.group(2))
        if not url:
            return f'<span>[{alt}]</span>'
        return f'<img src="{url}" alt="{html.escape(alt)}" style="max-width: 100%; height: auto; border-radius: 8px; margin: 8px 0; display: block;" />'
    s = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', img_repl, s)

    # Bold: **text** -> <strong>text</strong>
    s = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', s)

    # Italic: *text* -> <em>text</em>
    s = re.sub(r'(?<!\*)\*(?!\*)(.*?)\*', r'<em>\1</em>', s)

    # Strikethrough: ~~text~~ -> <s>text</s>
    s = re.sub(r'~~(.*?)~~', r'<s>\1</s>', s)

    # Inline Code: `code` -> <code>code</code>
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)

    # Links: [text](url) -> <a href="url" target="_blank" rel="noopener noreferrer">text</a>
    def link_repl(m):
        link_text = m.group(1)
        url = sanitize_url(m.group(2))
        if not url:
            return link_text
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{link_text}</a>'
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_repl, s)

    return s

def normalize_to_canonical_html(content):
    """
    Main idempotent normalization function.
    Converts legacy content (Markdown, raw HTML, escaped HTML, mixed content, plain text)
    into clean, valid canonical HTML.
    """
    if not content or not str(content).strip():
        return ""

    s = str(content).strip()

    # Step 1: Unescape escaped HTML / unwraps string quotes
    s = unescape_escaped_html(s)

    # Step 2: Extract code blocks (both markdown ```code``` and existing <pre><code>...</code></pre>)
    code_blocks = []
    def save_markdown_code_block(m):
        code_text = m.group(1)
        code_blocks.append(f'<pre><code>{html.escape(code_text)}</code></pre>')
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    s = re.sub(r'```(?:\w+)?\n?(.*?)\n?```', save_markdown_code_block, s, flags=re.DOTALL)

    def save_html_code_block(m):
        code_blocks.append(m.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    s = re.sub(r'<pre\s*[^>]*>\s*<code[^>]*>.*?</code>\s*</pre>', save_html_code_block, s, flags=re.DOTALL | re.IGNORECASE)

    # Step 3: Process line by line for block elements
    lines = s.split('\n')
    output_blocks = []
    in_checklist = False
    in_bullet_list = False
    in_numbered_list = False
    current_list_items = []

    def flush_list():
        nonlocal in_checklist, in_bullet_list, in_numbered_list, current_list_items
        if not current_list_items:
            return
        if in_checklist:
            output_blocks.append('<ul class="task-list" style="list-style: none; padding-left: 0;">' + ''.join(current_list_items) + '</ul>')
        elif in_bullet_list:
            output_blocks.append('<ul>' + ''.join(current_list_items) + '</ul>')
        elif in_numbered_list:
            output_blocks.append('<ol>' + ''.join(current_list_items) + '</ol>')

        in_checklist = False
        in_bullet_list = False
        in_numbered_list = False
        current_list_items = []

    for line in lines:
        stripped = line.strip()

        # Placeholders for code block
        if stripped.startswith('__CODE_BLOCK_') and stripped.endswith('__'):
            flush_list()
            idx = int(stripped.replace('__CODE_BLOCK_', '').replace('__', ''))
            output_blocks.append(code_blocks[idx])
            continue

        # Check existing HTML block tags
        if re.match(r'^\s*<(h1|h2|h3|p|blockquote|ul|ol|pre|hr|div|img|figure|picture)[^>]*>', stripped, re.IGNORECASE):
            flush_list()
            output_blocks.append(convert_markdown_inline_to_html(line))
            continue

        # Horizontal Rule
        if stripped in ('---', '***', '___'):
            flush_list()
            output_blocks.append('<hr>')
            continue

        # Headings
        if stripped.startswith('### '):
            flush_list()
            output_blocks.append(f'<h3>{convert_markdown_inline_to_html(stripped[4:])}</h3>')
            continue
        if stripped.startswith('## '):
            flush_list()
            output_blocks.append(f'<h2>{convert_markdown_inline_to_html(stripped[3:])}</h2>')
            continue
        if stripped.startswith('# '):
            flush_list()
            output_blocks.append(f'<h1>{convert_markdown_inline_to_html(stripped[2:])}</h1>')
            continue

        # Blockquotes
        if stripped.startswith('> '):
            flush_list()
            output_blocks.append(f'<blockquote><p>{convert_markdown_inline_to_html(stripped[2:])}</p></blockquote>')
            continue

        # Checklists (e.g. "- [x] task" or "- [x]" or "- [ ] task" or "- [ ]")
        if re.match(r'^- \[[xX]\](?:\s+(.*))?$', stripped):
            if not in_checklist:
                flush_list()
                in_checklist = True
            m_check = re.match(r'^- \[[xX]\](?:\s+(.*))?$', stripped)
            item_raw = m_check.group(1) or ''
            item_text = convert_markdown_inline_to_html(item_raw)
            current_list_items.append(f'<li class="task-item" data-checked="true"><input type="checkbox" disabled checked /> <s>{item_text}</s></li>')
            continue
        if re.match(r'^- \[\ \](?:\s+(.*))?$', stripped):
            if not in_checklist:
                flush_list()
                in_checklist = True
            m_check = re.match(r'^- \[\ \](?:\s+(.*))?$', stripped)
            item_raw = m_check.group(1) or ''
            item_text = convert_markdown_inline_to_html(item_raw)
            current_list_items.append(f'<li class="task-item" data-checked="false"><input type="checkbox" disabled /> {item_text}</li>')
            continue

        # Bullet List
        if stripped.startswith('- '):
            if not in_bullet_list:
                flush_list()
                in_bullet_list = True
            item_text = convert_markdown_inline_to_html(stripped[2:])
            current_list_items.append(f'<li>{item_text}</li>')
            continue

        # Numbered List
        m_num = re.match(r'^\d+\.\s+(.*)$', stripped)
        if m_num:
            if not in_numbered_list:
                flush_list()
                in_numbered_list = True
            item_text = convert_markdown_inline_to_html(m_num.group(1))
            current_list_items.append(f'<li>{item_text}</li>')
            continue

        # Empty line
        if not stripped:
            flush_list()
            continue

        # Normal paragraph line
        flush_list()
        inline_converted = convert_markdown_inline_to_html(line)
        output_blocks.append(f'<p>{inline_converted}</p>')

    flush_list()

    final_html = '\n'.join(output_blocks)

    # Step 4: Sanitize final HTML
    final_html = sanitize_html(final_html)

    return final_html
