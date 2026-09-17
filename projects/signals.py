# --------------------------------------------------------------------------------
#       Projects Signals - Storage Billing & File Lifecycle
# --------------------------------------------------------------------------------

import logging
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from projects.models import ProjectAttachment

logger = logging.getLogger(__name__)


def _safe_storage_delete(storage, file_name):
    """
    Safely deletes a physical file from storage backend after DB transaction commits.
    Catches and logs failures without throwing exceptions or affecting billing records.
    """
    try:
        if file_name and storage.exists(file_name):
            storage.delete(file_name)
    except Exception as exc:
        logger.warning("Failed to delete physical file from storage backend: %s", exc)


def _resolve_attachment_organization(instance):
    """
    Resolves the organization/company for an attachment safely.
    Prefers instance.company.
    Falls back to deterministic server-side parent relationships for legacy rows.
    Does NOT use uploaded_by.organization as fallback.
    """
    if instance.company_id:
        return instance.company

    # Deterministic parent relationships:
    if instance.project_id and instance.project:
        return instance.project.company
    if instance.epic_id and instance.epic:
        return instance.epic.company
    if instance.story_id and instance.story and instance.story.project:
        return instance.story.project.company
    if instance.task_id and instance.task and instance.task.story and instance.task.story.project:
        return instance.task.story.project.company
    if instance.comment_id and instance.comment:
        c = instance.comment
        if c.epic_id and c.epic:
            return c.epic.company
        if c.story_id and c.story and c.story.project:
            return c.story.project.company
        if c.task_id and c.task and c.task.story and c.task.story.project:
            return c.task.story.project.company
        if c.subtask_id and c.subtask and c.subtask.task and c.subtask.task.story and c.subtask.task.story.project:
            return c.subtask.task.story.project.company

    return None


@receiver(post_delete, sender=ProjectAttachment)
def handle_project_attachment_post_delete(sender, instance, **kwargs):
    """
    Canonical single storage-ledger deletion hook for ProjectAttachment.
    Handles:
    - Direct attachment delete
    - Bulk / QuerySet delete
    - Cascade delete from Comment, Task, Story, Epic, Project
    - Cleanup of expired drafts
    """
    org = _resolve_attachment_organization(instance)
    deleted_by = getattr(instance, '_storage_deleted_by', None)

    if org and instance.id:
        try:
            from storage_billing.models import StorageFile
            from storage_billing.services import StorageService

            storage_file = StorageFile.objects.filter(
                organization=org,
                source_module='projects',
                source_model='ProjectAttachment',
                source_object_id=str(instance.id)
            ).first()

            if storage_file and storage_file.status == 'ACTIVE':
                StorageService.record_file_deletion(
                    storage_file=storage_file,
                    deleted_by=deleted_by,
                    deleted_at=timezone.now(),
                    metadata={'attachment_file_name': instance.file_name}
                )
            elif not storage_file:
                logger.info(
                    "No StorageFile found for deleted ProjectAttachment %s (org_id=%s). Historical or pre-storage record.",
                    instance.id, org.id
                )
        except Exception as exc:
            logger.error("Error updating storage ledger on ProjectAttachment post_delete: %s", exc)
    elif not org:
        logger.warning(
            "Could not resolve organization for ProjectAttachment %s during post_delete. Skipping storage ledger update.",
            instance.id
        )

    # Physical file cleanup ONLY after transaction commit
    if instance.file:
        storage = instance.file.storage
        file_name = instance.file.name
        if file_name:
            transaction.on_commit(
                lambda storage=storage, file_name=file_name: _safe_storage_delete(storage, file_name)
            )
