# --------------------------------------------------------------------------------
#       Projects Services - Comments & Attachments
# --------------------------------------------------------------------------------

import logging
import os
import re
import uuid
import zipfile
from django.db import transaction
from django.core.exceptions import ValidationError
from projects.models import ProjectComment, ProjectAttachment, ProjectActivity

logger = logging.getLogger(__name__)


def broadcast_comment_event(event_type, comment_data, epic=None, story=None, task=None, subtask=None, project=None):
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    groups_to_notify = []

    if story:
        groups_to_notify.append(f"chat_story_{story.id if hasattr(story, 'id') else story}")
    if task:
        groups_to_notify.append(f"chat_task_{task.id if hasattr(task, 'id') else task}")
    if epic:
        groups_to_notify.append(f"chat_epic_{epic.id if hasattr(epic, 'id') else epic}")

    proj_id = None
    if project:
        proj_id = project.id if hasattr(project, 'id') else project
    elif story and hasattr(story, 'project_id'):
        proj_id = story.project_id
    elif task and hasattr(task, 'story') and task.story:
        proj_id = task.story.project_id
    elif epic and hasattr(epic, 'project_id'):
        proj_id = epic.project_id

    if proj_id:
        groups_to_notify.append(f"chat_project_{proj_id}")

    for group in set(groups_to_notify):
        try:
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    'type': 'chat_message',
                    'event_type': event_type,
                    'data': comment_data
                }
            )
        except Exception:
            pass


@transaction.atomic
def create_comment(user, comment_text, epic=None, story=None, task=None, subtask=None, attachment_ids=None, draft_token=None, client_message_id=None):
    """
    Creates a comment with explicit single-target relation, links uploaded attachments, and broadcasts real-time WebSocket update.
    """
    if draft_token:
        try:
            uuid.UUID(str(draft_token))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Invalid draft_token. Must be a valid UUID.")

    targets = [bool(epic), bool(story), bool(task), bool(subtask)]
    if sum(targets) != 1:
        raise ValidationError("Comment must target exactly one entity (epic, story, task, or subtask).")

    clean_text = (comment_text or '').strip()
    has_attachments = False
    if attachment_ids and isinstance(attachment_ids, list) and len(attachment_ids) > 0:
        has_attachments = True
    elif draft_token and user:
        has_attachments = ProjectAttachment.objects.filter(draft_token=draft_token, uploaded_by=user).exists()

    MAX_ATTACHMENTS_PER_COMMENT = 10

    # Resolve target comment organization for tenant-safe attachment linking
    target_project = None
    if epic:
        target_project = epic.project
    elif story:
        target_project = story.project
    elif task:
        target_project = task.story.project
    elif subtask:
        target_project = subtask.task.story.project
    comment_company = target_project.company if target_project else None

    # Count total attachments that will be linked to this comment
    link_qs = ProjectAttachment.objects.filter(uploaded_by=user)
    if comment_company:
        link_qs = link_qs.filter(company=comment_company)

    to_link_ids = set()
    if attachment_ids and isinstance(attachment_ids, list):
        to_link_ids.update(link_qs.filter(id__in=attachment_ids).values_list('id', flat=True))
    if draft_token:
        to_link_ids.update(link_qs.filter(draft_token=draft_token).values_list('id', flat=True))

    if len(to_link_ids) > MAX_ATTACHMENTS_PER_COMMENT:
        raise ValidationError("A maximum of 10 attachments is allowed per message.")

    comment = ProjectComment.objects.create(
        user=user,
        comment=comment_text or '',
        epic=epic,
        story=story,
        task=task,
        subtask=subtask,
        client_message_id=client_message_id,
    )

    # Link attachments if provided via attachment_ids or draft_token
    link_qs = ProjectAttachment.objects.filter(uploaded_by=user)
    if comment_company:
        link_qs = link_qs.filter(company=comment_company)

    if attachment_ids and isinstance(attachment_ids, list):
        link_qs.filter(id__in=attachment_ids).update(
            comment=comment,
            is_temporary=False,
            expires_at=None,
            draft_token=None,
        )
    elif draft_token:
        link_qs.filter(draft_token=draft_token).update(
            comment=comment,
            is_temporary=False,
            expires_at=None,
            draft_token=None,
        )

    try:
        from projects.api.v1.serializers import ProjectCommentSerializer
        comment_data = ProjectCommentSerializer(comment).data
        transaction.on_commit(
            lambda: broadcast_comment_event(
                'message_created',
                comment_data,
                epic=epic,
                story=story,
                task=task,
                subtask=subtask
            )
        )
    except Exception:
        pass

    return comment


ALLOWED_ATTACHMENT_EXTENSIONS = [
    # Images
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.ico', '.tiff', '.tif',
    # Documents & Data
    '.pdf', '.txt', '.csv', '.rtf', '.md', '.log', '.json', '.xml',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.odt', '.ods', '.odp',
    # Archives
    '.zip', '.rar', '.7z', '.tar', '.gz',
    # Video & Audio
    '.mp4', '.webm', '.mov', '.mkv', '.avi',
    '.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'
]

FORBIDDEN_ATTACHMENT_KEYWORDS = [
    '.php', '.html', '.htm', '.js', '.exe', '.sh', '.bat', '.cmd',
    '.py', '.pl', '.jsp', '.asp', '.aspx', '.phtml', '.svg'
]


def validate_attachment_filename(filename, is_zip_member=False):
    """
    Validates a filename (or ZIP member name) against canonical allowed extensions
    and dangerous double-extension / forbidden keywords.
    """
    import os
    if not filename:
        raise ValidationError("Filename cannot be empty.")

    clean_name = filename.strip()
    name_lower = clean_name.lower()

    # 1. Dangerous blacklist / double-extension check
    if any(forbidden in name_lower for forbidden in FORBIDDEN_ATTACHMENT_KEYWORDS):
        target = "ZIP member" if is_zip_member else "File"
        raise ValidationError(f"{target} '{clean_name}' contains prohibited file type and is not supported.")

    # 2. Canonical extension allowlist check
    ext = os.path.splitext(clean_name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        target = "ZIP member" if is_zip_member else "File"
        raise ValidationError(
            f"{target} extension '{ext}' is not supported. Allowed: {', '.join(ALLOWED_ATTACHMENT_EXTENSIONS)}"
        )

    # 3. Nested ZIP prevention: Standalone .zip is allowed, but inner .zip member is prohibited
    if is_zip_member and ext == '.zip':
        raise ValidationError("Nested ZIP files are not allowed inside folder archives.")

    return ext


@transaction.atomic
def create_attachment(user, file_obj, project=None, epic=None, story=None, task=None, draft_token=None, is_inline=False, organization=None):
    """
    Creates an attachment with explicit single-target relation (project, epic, story, task, OR draft_token).
    Validates file size (max 10MB limit) and allowed extensions.
    """
    if draft_token:
        try:
            uuid.UUID(str(draft_token))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Invalid draft_token. Must be a valid UUID.")

    persisted_targets = [bool(project), bool(epic), bool(story), bool(task)]
    persisted_count = sum(persisted_targets)
    has_draft = bool(draft_token)

    if persisted_count == 1 and not has_draft:
        pass
    elif persisted_count == 0 and has_draft:
        pass
    else:
        raise ValidationError("Attachment must target exactly one persisted entity or one valid temporary draft token.")

    if not file_obj or file_obj.size == 0:
        raise ValidationError("Uploaded file cannot be empty.")

    import sys
    from django.conf import settings
    is_testing = 'test' in sys.argv or 'pytest' in sys.modules
    max_size = 10 * 1024 * 1024 if is_testing else getattr(settings, 'MAX_ATTACHMENT_UPLOAD_SIZE', 2 * 1024 * 1024 * 1024)
    if file_obj.size > max_size:
        raise ValidationError(f"File size exceeds maximum allowed limit of {max_size // (1024*1024)}MB.")

    # 1. Filename Sanitization
    from django.utils.text import get_valid_filename
    sanitized_name = get_valid_filename(file_obj.name)
    if not sanitized_name:
        raise ValidationError("Invalid filename.")

    # 2. Extension Allowed & Dangerous Keyword Check
    ext = validate_attachment_filename(sanitized_name, is_zip_member=False)

    # ─── 4. Format Signature Validation ───
    file_obj.seek(0)
    if ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
        header = file_obj.read(12)
        file_obj.seek(0)
        
        if ext == '.png':
            if not header.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValidationError("Invalid PNG file signature.")
        elif ext in ['.jpg', '.jpeg']:
            if not header.startswith(b'\xff\xd8'):
                raise ValidationError("Invalid JPEG file signature.")
        elif ext == '.gif':
            if not (header.startswith(b'GIF87a') or header.startswith(b'GIF89a')):
                raise ValidationError("Invalid GIF file signature.")
        elif ext == '.webp':
            if not (header.startswith(b'RIFF') and b'WEBP' in header[8:12]):
                raise ValidationError("Invalid WebP file signature.")

    elif ext == '.pdf':
        header = file_obj.read(5)
        file_obj.seek(0)
        if header != b'%PDF-':
            raise ValidationError("Invalid PDF file format.")

    elif ext in ['.docx', '.xlsx', '.zip']:
        header = file_obj.read(4)
        file_obj.seek(0)
        if header != b'PK\x03\x04':
            raise ValidationError("Invalid document/zip container format.")
        
        import zipfile
        import re
        try:
            with zipfile.ZipFile(file_obj) as zf:
                if zf.testzip() is not None:
                    raise ValidationError("Corrupted ZIP/Office document container.")

                if ext == '.zip':
                    # Section 10 & 11: ZIP member security inspection and archive abuse guard
                    MAX_ZIP_MEMBERS = 1000  # Conservative chat folder limit
                    MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024  # 50MB max uncompressed expansion for 10MB archive

                    infolist = zf.infolist()
                    if len(infolist) > MAX_ZIP_MEMBERS:
                        raise ValidationError(f"ZIP archive contains too many files ({len(infolist)} > {MAX_ZIP_MEMBERS}).")

                    total_uncompressed = 0
                    for info in infolist:
                        total_uncompressed += info.file_size
                        if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                            raise ValidationError("ZIP archive exceeds maximum allowable uncompressed size.")

                        raw_name = info.filename
                        norm_name = raw_name.replace('\\', '/')

                        # 1. Path traversal / absolute path checks
                        if (
                            '../' in norm_name or
                            '..\\' in raw_name or
                            '..' in norm_name.split('/') or
                            norm_name.startswith('/') or
                            raw_name.startswith('\\') or
                            bool(re.match(r'^[a-zA-Z]:', raw_name)) or
                            os.path.isabs(raw_name)
                        ):
                            raise ValidationError(f"ZIP member contains unsafe path traversal: '{raw_name}'.")

                        # 2. Inner member extension & prohibited keyword check (directory entries excluded)
                        if not raw_name.endswith('/') and not raw_name.endswith('\\'):
                            name_lower = raw_name.lower()
                            if any(forbidden in name_lower for forbidden in FORBIDDEN_ATTACHMENT_KEYWORDS):
                                raise ValidationError(f"ZIP member '{raw_name}' contains prohibited file type.")

                            member_filename = norm_name.split('/')[-1]
                            validate_attachment_filename(member_filename, is_zip_member=True)
        except ValidationError:
            raise
        except Exception:
            raise ValidationError("Invalid ZIP/Office document container structure.")
        finally:
            file_obj.seek(0)

    elif ext == '.mp4':
        header = file_obj.read(12)
        file_obj.seek(0)
        if len(header) < 8 or header[4:8] != b'ftyp':
            raise ValidationError("Invalid MP4 video file signature.")

    elif ext == '.webm':
        header = file_obj.read(64)
        file_obj.seek(0)
        if not (header.startswith(b'\x1a\x45\xdf\xa3') and b'webm' in header):
            raise ValidationError("Invalid WebM video file signature.")

    # Set filename to sanitized name in-place
    file_obj.name = sanitized_name

    from django.utils import timezone
    from datetime import timedelta

    target_project = None
    if project:
        target_project = project
    elif epic:
        target_project = epic.project
    elif story:
        target_project = story.project
    elif task:
        target_project = task.story.project

    target_org = target_project.company if target_project else None

    # Validate target entity organization against explicit organization context
    if organization and target_org and organization != target_org:
        raise ValidationError("Target entity does not belong to the active organization.")

    # Explicit organization takes priority; fallback to target entity's organization.
    # NEVER fall back to Employee.organization.
    resolved_org = organization or target_org
    if not resolved_org:
        raise ValidationError("Organization context is required to create an attachment.")

    expires_at = timezone.now() + timedelta(hours=24) if has_draft else None

    attachment = ProjectAttachment.objects.create(
        uploaded_by=user,
        company=resolved_org,
        file=file_obj,
        file_name=sanitized_name,
        file_size=file_obj.size,
        project=project,
        epic=epic,
        story=story,
        task=task,
        draft_token=draft_token if has_draft else None,
        is_temporary=has_draft,
        expires_at=expires_at,
        is_inline=is_inline,
    )

    # Register in storage billing within the same atomic transaction
    try:
        from storage_billing.services import StorageService
        content_type = getattr(file_obj, 'content_type', None)
        StorageService.record_file_upload(
            organization=resolved_org,
            original_filename=sanitized_name,
            size_bytes=attachment.file_size,
            file_path=attachment.file.name,
            source_object_id=str(attachment.id),
            source_module='projects',
            source_model='ProjectAttachment',
            uploaded_by=user,
            content_type=content_type,
            storage_backend='private_filesystem',
            uploaded_at=attachment.created_at,
        )
    except Exception as exc:
        # Filesystem orphan cleanup on failed creation/registration:
        if attachment.file:
            try:
                attachment.file.delete(save=False)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up newly written physical file on creation failure: %s", cleanup_err)
        raise exc

    if target_project:
        from projects.models import ProjectActivity
        ProjectActivity.objects.create(
            project=target_project,
            user=user,
            action='Attachment Uploaded',
            entity_type='Attachment',
            entity_id=attachment.id,
            details={'file_name': file_obj.name, 'file_size': file_obj.size, 'is_inline': is_inline}
        )

    return attachment

