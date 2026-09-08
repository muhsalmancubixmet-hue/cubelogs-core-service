import logging
from datetime import date, timedelta
from django.utils import timezone

try:
    from celery import shared_task
except ImportError:
    def shared_task(func):
        return func

from core.models import Organization
from storage_billing.services import StorageService

logger = logging.getLogger(__name__)


@shared_task
def audit_workspace_storage(target_org_id: int | None = None, date_str: str | None = None) -> dict:
    """
    Storage reconciliation and daily usage calculation task.
    
    NOTE: In Phase 1A, this task is implemented for isolated execution but is NOT
    added to CELERY_BEAT_SCHEDULE to avoid runtime failures on pre-migration databases.

    Behavior:
    - Iterates over active organizations.
    - Computes or reconciles StorageDailyUsage for the specified date (defaults to today and yesterday).
    - Idempotent and deterministic: safe to run multiple times without duplicating usage or charges.
    - Strictly ZERO wallet debits, zero invoice adjustments, and zero physical file mutations.
    """
    today = timezone.now().date()
    target_dates = []

    if date_str:
        try:
            target_dates.append(date.fromisoformat(date_str))
        except ValueError:
            logger.error("Invalid date_str format passed to audit_workspace_storage: %s", date_str)
            return {'status': 'FAILED', 'error': f"Invalid date format: {date_str}"}
    else:
        # Default: audit both today (ongoing) and yesterday (finalized)
        yesterday = today - timedelta(days=1)
        target_dates = [yesterday, today]

    orgs_qs = Organization.objects.filter(is_deleted=False)
    if target_org_id is not None:
        orgs_qs = orgs_qs.filter(id=target_org_id)

    orgs = list(orgs_qs)
    logger.info(
        "Starting storage audit for %d organization(s) across %d date(s): %s",
        len(orgs), len(target_dates), [str(d) for d in target_dates]
    )

    processed_records = 0
    errors = 0

    for org in orgs:
        for audit_date in target_dates:
            try:
                usage_obj, created = StorageService.calculate_or_update_daily_usage(
                    organization=org,
                    usage_date=audit_date
                )
                processed_records += 1
                logger.debug(
                    "Storage audit: Org %s on %s -> %s bytes (%d credits), created=%s",
                    org.name, audit_date, usage_obj.billable_bytes, usage_obj.storage_credits, created
                )
            except Exception as exc:
                errors += 1
                logger.error(
                    "Storage audit error for Org %s (ID %s) on date %s: %s",
                    org.name, org.id, audit_date, str(exc), exc_info=True
                )

    result = {
        'status': 'SUCCESS' if errors == 0 else 'PARTIAL_SUCCESS',
        'orgs_count': len(orgs),
        'dates_audited': [str(d) for d in target_dates],
        'processed_records': processed_records,
        'errors_count': errors,
    }
    logger.info("Storage audit finished: %s", result)
    return result
