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
    Storage reconciliation, daily usage calculation, and bounded catch-up task.
    Runs periodically (e.g. at 00:05 and 12:05 UTC) via Celery Beat.

    Behavior:
    - Iterates over active organizations (is_deleted=False).
    - If date_str is specified: audits that single date (enforcing go-live lower bound).
    - If date_str is None: audits rolling catch-up window from max(today - catchup_days, golive_date) to today.
    - Idempotent and deterministic: safe to run multiple times without duplicating usage or charges.
    - Finalized rows remain untouched (force_recompute is NEVER used in scheduled runs).
    - Missing closed dates are reconstructed as provisional (is_finalized=False).
    - Dates created during their usage day are finalized when closed.
    - Strictly ZERO wallet debits, zero invoice adjustments, and zero Razorpay calls.
    - Per-organization and per-date failure isolation.
    """
    counters = {
        'organizations_scanned': 0,
        'dates_considered': 0,
        'rows_created': 0,
        'rows_updated': 0,
        'rows_finalized': 0,
        'finalized_skipped': 0,
        'historical_provisional_created': 0,
        'pre_golive_skipped': 0,
        'errors_count': 0,
    }

    golive_date = StorageService.get_metering_start_date()

    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            logger.error("Invalid date_str format passed to audit_workspace_storage: %s", date_str)
            return {'status': 'FAILED', 'error': f"Invalid date format: {date_str}"}

        if target_date < golive_date:
            logger.warning(
                "Requested date %s is before storage metering go-live date %s. Skipping.",
                target_date, golive_date
            )
            counters['pre_golive_skipped'] += 1
            return {
                'status': 'SUCCESS',
                'start_date': str(target_date),
                'end_date': str(target_date),
                **counters
            }
        start_date = target_date
        end_date = target_date
    else:
        today_utc = timezone.now().date()
        catchup_days = StorageService.get_metering_catchup_days()
        lookback_start = today_utc - timedelta(days=catchup_days)
        start_date = max(lookback_start, golive_date)
        end_date = today_utc

        if today_utc < golive_date:
            logger.info(
                "Current UTC date %s is before storage metering go-live date %s. Skipping.",
                today_utc, golive_date
            )
            counters['pre_golive_skipped'] += 1
            return {
                'status': 'SUCCESS',
                'start_date': str(start_date),
                'end_date': str(end_date),
                **counters
            }

    orgs_qs = Organization.objects.filter(is_deleted=False)
    if target_org_id is not None:
        orgs_qs = orgs_qs.filter(id=target_org_id)

    orgs = list(orgs_qs)
    logger.info(
        "Starting storage audit for %d organization(s) from %s to %s",
        len(orgs), start_date, end_date
    )

    for org in orgs:
        counters['organizations_scanned'] += 1
        try:
            org_res = StorageService.reconcile_usage_date_range(
                organization=org,
                start_date=start_date,
                end_date=end_date
            )
            for k in [
                'dates_considered',
                'rows_created',
                'rows_updated',
                'rows_finalized',
                'finalized_skipped',
                'historical_provisional_created',
                'pre_golive_skipped',
                'errors_count',
            ]:
                counters[k] += org_res.get(k, 0)
        except Exception as exc:
            counters['errors_count'] += 1
            logger.error(
                "Storage audit error for Org %s (ID %s): %s",
                org.name, org.id, str(exc), exc_info=True
            )

    result = {
        'status': 'SUCCESS' if counters['errors_count'] == 0 else 'PARTIAL_SUCCESS',
        'orgs_count': len(orgs),
        'start_date': str(start_date),
        'end_date': str(end_date),
        **counters
    }
    logger.info("Storage audit finished: %s", result)
    return result
