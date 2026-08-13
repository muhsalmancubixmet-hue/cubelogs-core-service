# --------------------------------------------------------------------------------
#       Projects Services - Comments & Attachments
# --------------------------------------------------------------------------------

import uuid
from django.db import transaction
from django.core.exceptions import ValidationError
from projects.models import ProjectComment, ProjectAttachment, ProjectActivity


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

    if not clean_text and not has_attachments:
        raise ValidationError("Comment text or at least one attachment is required.")

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
    if attachment_ids and isinstance(attachment_ids, list):
        ProjectAttachment.objects.filter(id__in=attachment_ids, uploaded_by=user).update(
            comment=comment,
            is_temporary=False,
            expires_at=None,
            draft_token=None,
        )
    elif draft_token:
        ProjectAttachment.objects.filter(draft_token=draft_token, uploaded_by=user).update(
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


@transaction.atomic
def create_attachment(user, file_obj, project=None, epic=None, story=None, task=None, draft_token=None, is_inline=False):
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

    max_size = 10 * 1024 * 1024  # 10MB product rule limit
    if file_obj.size > max_size:
        raise ValidationError("File size exceeds maximum allowed limit of 10MB.")

    # 1. Filename Sanitization
    from django.utils.text import get_valid_filename
    sanitized_name = get_valid_filename(file_obj.name)
    if not sanitized_name:
        raise ValidationError("Invalid filename.")

    # 2. Extension Allowed Check
    import os
    allowed_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.txt', '.csv', '.doc', '.docx', '.xls', '.xlsx', '.zip']
    ext = os.path.splitext(sanitized_name)[1].lower()
    if ext not in allowed_extensions:
        raise ValidationError(f"File extension '{ext}' is not supported. Allowed: {', '.join(allowed_extensions)}")

    # 3. Double / Spoofed Extension Checks
    filename_lower = sanitized_name.lower()
    forbidden_anywhere = ['.php', '.html', '.htm', '.js', '.exe', '.sh', '.bat', '.cmd', '.py', '.pl', '.jsp', '.asp', '.aspx', '.phtml', '.svg']
    if any(forbidden in filename_lower for forbidden in forbidden_anywhere):
        raise ValidationError("File contains forbidden extensions or dangerous keywords.")

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
        try:
            with zipfile.ZipFile(file_obj) as zf:
                if zf.testzip() is not None:
                    raise ValidationError("Corrupted ZIP/Office document container.")
        except Exception:
            raise ValidationError("Invalid ZIP/Office document container structure.")
        finally:
            file_obj.seek(0)

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

    expires_at = timezone.now() + timedelta(hours=24) if has_draft else None

    attachment = ProjectAttachment.objects.create(
        uploaded_by=user,
        company=getattr(user, 'organization', None),
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

