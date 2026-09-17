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


# --------------------------------------------------------------------------------
# Rich-Text Media Hardening: Base64 Block & Orphan Attachment Reconciliation
# --------------------------------------------------------------------------------

import html.parser
from django.core.exceptions import ValidationError

BASE64_IMAGE_PATTERN = re.compile(
    r'data:image/[^,;]+(?:\s*;\s*[^,;]+)*\s*;\s*base64\s*,',
    re.IGNORECASE
)


def validate_rich_text_no_base64(content, field_name='rich-text'):
    """
    Authoritative backend check: Rejects raw inline base64 image data URIs.
    Direct API requests containing raw base64 images will fail validation with HTTP 400.
    """
    if not content or not isinstance(content, str):
        return content
    if BASE64_IMAGE_PATTERN.search(content):
        raise ValidationError(f"Raw base64 images are not allowed in {field_name}. Upload images as attachments.")
    return content


class AttachmentIdExtractor(html.parser.HTMLParser):
    """
    Standard library HTML parser to safely extract integer attachment IDs
    from data-attachment-id and data-inline-image-id attributes.
    """
    def __init__(self):
        super().__init__()
        self.attachment_ids = set()

    def handle_starttag(self, tag, attrs):
        for attr, val in attrs:
            if attr.lower() in ('data-attachment-id', 'data-inline-image-id'):
                if val:
                    val_str = str(val).strip()
                    if val_str.isdigit():
                        self.attachment_ids.add(int(val_str))


def extract_attachment_ids_from_html(content):
    """
    Safely extracts a set of integer attachment IDs from HTML attributes.
    Silently ignores malformed or unrelated HTML without raising exceptions.
    """
    if not content or not isinstance(content, str):
        return set()

    extractor = AttachmentIdExtractor()
    try:
        extractor.feed(content)
    except Exception:
        pass

    # Regex fallback / supplement for resilience across partial or broken markup
    for m in re.findall(r'data-(?:attachment|inline-image)-id=["\']?(\d+)["\']?', content, re.IGNORECASE):
        extractor.attachment_ids.add(int(m))

    return extractor.attachment_ids


def get_entity_rich_text_fields(entity):
    """
    Returns list of rich-text content strings across all covered rich-text fields of the entity.
    """
    from projects.models import Project, ProjectEpic, ProjectStory, ProjectTask
    if isinstance(entity, ProjectStory):
        return [entity.description, entity.acceptance_criteria]
    elif isinstance(entity, (Project, ProjectEpic, ProjectTask)):
        return [entity.description]
    return []


def reconcile_entity_rich_text_attachments(entity, user=None):
    """
    Reconciles rich-text attachments for a supported entity (Project, Epic, Story, Task).
    1. Extracts the UNION of data-attachment-id references across ALL rich-text fields of the entity.
    2. Enforces reference ownership security (rejects if reference belongs to another organization).
    3. Any direct non-comment attachment belonging to this entity absent from the combined referenced set
       is deleted via canonical ORM delete (triggering StorageFile/StorageEvent lifecycle).
    Never deletes comment/chat attachments, drafts awaiting linking, or cross-entity attachments.
    """
    if not entity or not getattr(entity, 'pk', None):
        return

    from projects.models import Project, ProjectEpic, ProjectStory, ProjectTask, ProjectAttachment
    from projects.signals import _resolve_attachment_organization

    filter_kwargs = None
    active_org = None

    if isinstance(entity, ProjectStory):
        filter_kwargs = {'story': entity}
        active_org = entity.project.company if entity.project else None
    elif isinstance(entity, ProjectTask):
        filter_kwargs = {'task': entity}
        active_org = entity.story.project.company if (entity.story and entity.story.project) else None
    elif isinstance(entity, ProjectEpic):
        filter_kwargs = {'epic': entity}
        active_org = entity.company
    elif isinstance(entity, Project):
        filter_kwargs = {'project': entity}
        active_org = entity.company
    else:
        # Deferred / unsupported entity (e.g. Subtask, Retrospective)
        return

    if not filter_kwargs or not active_org:
        return

    # Extract UNION of all data-attachment-id references across all covered fields
    field_values = get_entity_rich_text_fields(entity)
    referenced_ids = set()
    for val in field_values:
        if val:
            referenced_ids |= extract_attachment_ids_from_html(val)

    # Reference ownership security check (Section 11):
    # Ensure no referenced attachment belongs to another organization
    for att_id in referenced_ids:
        att = ProjectAttachment.objects.filter(id=att_id).first()
        if att:
            att_org = _resolve_attachment_organization(att)
            if att_org and active_org and att_org.id != active_org.id:
                from rest_framework.exceptions import ValidationError as DRFValidationError
                raise DRFValidationError(f"Cross-organization attachment reference {att_id} is not permitted.")

    # Query candidate entity attachments:
    # - Directly linked to this exact entity
    # - Belongs to this organization
    # - NOT a chat/comment attachment (comment__isnull=True)
    # - Belongs to entity attachment scope (both is_inline=True images and is_inline=False paperclip files)
    # - NOT a temporary draft awaiting link (is_temporary=False)
    candidates = ProjectAttachment.objects.filter(
        **filter_kwargs,
        company=active_org,
        comment__isnull=True,
        is_temporary=False,
    )

    # Candidate entity attachments absent from the final referenced set are orphaned
    orphans = candidates.exclude(id__in=referenced_ids)
    for orphan in orphans:
        if user:
            orphan._storage_deleted_by = user
        orphan.delete()
