import calendar
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import transaction, models, IntegrityError
from django.utils import timezone

from core.models import Organization
from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage
from subscribers.models import GlobalBillingSettings

logger = logging.getLogger(__name__)

DEFAULT_CREDIT_SIZE_BYTES = 1_000_000_000  # Commercial decimal GB (1,000,000,000 bytes)
DEFAULT_MONTHLY_CREDIT_PRICE = Decimal('20.00')

_UNSET = object()


class StorageCalculationService:
    """
    Pure, deterministic calculation engine for storage metrics, credits, and monetary charges.
    Strictly uses Decimal and calendar arithmetic. Never uses binary floating-point.
    """

    @staticmethod
    def get_days_in_month(year: int, month: int) -> int:
        """Returns total calendar days for the given year and month (28, 29, 30, or 31)."""
        return calendar.monthrange(year, month)[1]

    @staticmethod
    def calculate_daily_credit_rate(monthly_price: Decimal, days_in_month: int) -> Decimal:
        """
        Calculates daily rate per credit: monthly_credit_price / days_in_month.
        Retains high-precision Decimal representation (12 decimal places).
        """
        if days_in_month <= 0:
            return Decimal('0.000000000000')
        raw_rate = Decimal(str(monthly_price)) / Decimal(str(days_in_month))
        return raw_rate.quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_storage_credits(
        billable_bytes: int,
        credit_size_bytes: int = DEFAULT_CREDIT_SIZE_BYTES
    ) -> tuple[Decimal, int]:
        """
        Rule 4, 8 & 10:
        1. billable_gb is ALWAYS commercial GB actually consumed:
           billable_gb = billable_bytes / 1,000,000,000
           This value NEVER changes when Backoffice credit size changes.
        2. Whole storage credits = ceil(billable_bytes / credit_size_bytes).
           If billable_bytes == 0: credits = 0.
        Returns (billable_gb, whole_credits).
        Fails safely if credit_size_bytes is zero or negative.
        """
        if credit_size_bytes <= 0:
            raise ValueError(
                f"storage_credit_size_bytes must be a positive integer greater than 0, got {credit_size_bytes}"
            )

        if billable_bytes <= 0:
            return Decimal('0.0000'), 0

        # Commercial decimal GB: always exactly 1,000,000,000 bytes
        billable_gb = (Decimal(str(billable_bytes)) / Decimal('1000000000')).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP
        )

        # Whole credits: ceil(billable_bytes / credit_size_bytes)
        credits_ratio = Decimal(str(billable_bytes)) / Decimal(str(credit_size_bytes))
        whole_credits = int(math.ceil(credits_ratio))

        return billable_gb, whole_credits

    @staticmethod
    def calculate_daily_charge(
        whole_credits: int,
        monthly_price: Decimal,
        days_in_month: int | None = None
    ) -> Decimal:
        """
        Calculates daily storage charge directly from the unrounded financial formula:
            raw_daily_charge = (whole_credits * monthly_price) / days_in_month
        without intermediate rate rounding.
        Preserves high Decimal precision (12 decimal places) during daily metering.
        Currency rounding to 0.01 happens only later at monthly invoice aggregation.
        """
        if whole_credits <= 0:
            return Decimal('0.000000000000')
        if days_in_month is not None:
            if days_in_month <= 0:
                return Decimal('0.000000000000')
            raw_charge = (Decimal(str(whole_credits)) * Decimal(str(monthly_price))) / Decimal(str(days_in_month))
        else:
            raw_charge = Decimal(str(whole_credits)) * Decimal(str(monthly_price))
        return raw_charge.quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_UP)

    @staticmethod
    def is_file_billable_on_date(storage_file: StorageFile, target_date) -> bool:
        """
        Rule 8:
        A file counts on EVERY calendar day during which it existed in the customer's billable lifecycle.
        Upload and deletion days BOTH count.
        Evaluated using explicit UTC half-open boundaries [day_start, next_day_start).
        """
        if isinstance(target_date, datetime):
            target_date = target_date.date()
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=dt_timezone.utc)
        next_day_start = day_start + timedelta(days=1)

        if storage_file.uploaded_at >= next_day_start:
            return False
        if storage_file.deleted_at is None:
            return True
        return storage_file.deleted_at >= day_start

    @staticmethod
    def get_billable_files_qs(organization, target_date):
        """
        Queries distinct StorageFile records that count towards target_date for an organization.
        Criteria:
            uploaded_at < next_day_start AND (deleted_at IS NULL OR deleted_at >= day_start)
        Evaluated using explicit UTC half-open boundaries [day_start, next_day_start).
        """
        if isinstance(target_date, datetime):
            target_date = target_date.date()
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=dt_timezone.utc)
        next_day_start = day_start + timedelta(days=1)

        return StorageFile.objects.filter(
            organization=organization,
            uploaded_at__lt=next_day_start
        ).filter(
            models.Q(deleted_at__isnull=True) | models.Q(deleted_at__gte=day_start)
        )


class StorageService:
    """
    Service coordinating storage file lifecycle ingestion, deletion events,
    and daily usage computation.
    """

    @classmethod
    def record_file_upload(
        cls,
        organization,
        original_filename: str,
        size_bytes: int,
        file_path: str,
        source_object_id: str | int | None = None,
        source_module: str = 'projects',
        source_model: str = 'ProjectAttachment',
        uploaded_by=None,
        content_type: str | None = None,
        storage_backend: str = 'private_filesystem',
        uploaded_at=None,
        metadata: dict | None = None,
        event_actor=_UNSET,
    ) -> StorageFile:
        """
        Immediately records a customer-uploaded physical file and appends an UPLOAD event.
        Trusted size_bytes must come from backend filesystem/upload handler, not client payload.
        Requires a stable, non-null, non-empty source_object_id.
        """
        if source_object_id is None:
            raise ValueError("source_object_id is required and cannot be None.")
        normalized_source_id = str(source_object_id).strip()
        if not normalized_source_id:
            raise ValueError("source_object_id cannot be empty or whitespace.")

        now = uploaded_at or timezone.now()

        # Fast-path check for sequential duplicate requests
        existing = StorageFile.objects.filter(
            organization=organization,
            source_module=source_module,
            source_model=source_model,
            source_object_id=normalized_source_id
        ).first()
        if existing:
            return existing

        actor = uploaded_by if event_actor is _UNSET else event_actor

        try:
            with transaction.atomic():
                storage_file = StorageFile.objects.create(
                    organization=organization,
                    source_module=source_module,
                    source_model=source_model,
                    source_object_id=normalized_source_id,
                    original_filename=original_filename,
                    file_path=file_path,
                    storage_backend=storage_backend,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    uploaded_at=now,
                    status='ACTIVE',
                    uploaded_by=uploaded_by,
                )

                StorageEvent.objects.create(
                    storage_file=storage_file,
                    organization=organization,
                    event_type='UPLOAD',
                    occurred_at=now,
                    size_bytes=size_bytes,
                    actor=actor,
                    metadata=metadata or {},
                )

                return storage_file
        except IntegrityError:
            # Tightly scoped recovery: if concurrent worker won the canonical source race,
            # retrieve and return the canonical StorageFile.
            existing = StorageFile.objects.filter(
                organization=organization,
                source_module=source_module,
                source_model=source_model,
                source_object_id=normalized_source_id
            ).first()
            if existing:
                return existing
            # If not caused by canonical source race (e.g. invalid FK or missing required field), re-raise!
            raise

    @classmethod
    def record_file_deletion(
        cls,
        storage_file: StorageFile,
        deleted_by=None,
        deleted_at=None,
        metadata: dict | None = None
    ) -> StorageFile:
        """
        Records customer deletion of a file.
        Immediately stops the customer's billable lifecycle.
        Idempotent: if already deleted, returns without creating duplicate events.
        """
        if storage_file.status in ('DELETED', 'PURGED') and storage_file.deleted_at is not None:
            return storage_file

        now = deleted_at or timezone.now()

        with transaction.atomic():
            storage_file.status = 'DELETED'
            storage_file.deleted_at = now
            storage_file.deleted_by = deleted_by
            storage_file.save(update_fields=['status', 'deleted_at', 'deleted_by', 'updated_at'])

            StorageEvent.objects.create(
                storage_file=storage_file,
                organization=storage_file.organization,
                event_type='DELETE',
                occurred_at=now,
                size_bytes=storage_file.size_bytes,
                actor=deleted_by,
                metadata=metadata or {},
            )

        return storage_file

    @classmethod
    def record_file_purge(
        cls,
        storage_file: StorageFile,
        purged_at=None,
        metadata: dict | None = None
    ) -> StorageFile:
        """
        Records the physical removal of a file from disk/storage backend.
        Note: Customer billing lifecycle stops at deleted_at, not purged_at.
        """
        now = purged_at or timezone.now()

        with transaction.atomic():
            storage_file.status = 'PURGED'
            storage_file.purged_at = now
            if storage_file.deleted_at is None:
                storage_file.deleted_at = now
            storage_file.save(update_fields=['status', 'purged_at', 'deleted_at', 'updated_at'])

            StorageEvent.objects.create(
                storage_file=storage_file,
                organization=storage_file.organization,
                event_type='PURGE',
                occurred_at=now,
                size_bytes=storage_file.size_bytes,
                metadata=metadata or {},
            )

        return storage_file

    @classmethod
    def get_metering_start_date(cls) -> date:
        raw = getattr(settings, 'STORAGE_METERING_START_DATE', '2026-09-09')
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw.strip())
            except ValueError as exc:
                raise ValueError(
                    f"Invalid STORAGE_METERING_START_DATE format '{raw}': expected YYYY-MM-DD"
                ) from exc
        raise ValueError(f"Invalid STORAGE_METERING_START_DATE type: {type(raw).__name__}")

    @classmethod
    def get_metering_catchup_days(cls) -> int:
        raw = getattr(settings, 'STORAGE_METERING_CATCHUP_DAYS', 14)
        try:
            val = int(raw)
            if val < 0:
                raise ValueError()
            return val
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid STORAGE_METERING_CATCHUP_DAYS: expected non-negative integer, got {raw}"
            )

    @classmethod
    def calculate_or_update_daily_usage(
        cls,
        organization,
        usage_date,
        is_finalized: bool | None = None,
        force_recompute: bool = False
    ) -> tuple[StorageDailyUsage, bool]:
        """
        Computes or updates the canonical StorageDailyUsage row for (organization, usage_date).
        Idempotent: re-running updates the existing row deterministically.
        If the row is already finalized and force_recompute is False, it remains untouched.
        Preserves existing pricing snapshots on provisional rows: once a daily row is created,
        subsequent recalculations reuse its stored snapshots, immune to global setting changes.
        """
        if isinstance(usage_date, datetime):
            usage_date = usage_date.date()

        with transaction.atomic():
            # Serialize concurrent metering for the same organization using DB row lock
            Organization.objects.select_for_update().get(pk=organization.pk)

            # Check existing row
            existing = StorageDailyUsage.objects.filter(
                organization=organization,
                usage_date=usage_date
            ).select_for_update().first()

            if existing and existing.is_finalized and not force_recompute:
                return existing, False

            # 1. Fetch pricing: preserve existing snapshots for unfinalized rows
            if existing and existing.storage_credit_size_bytes_snapshot:
                credit_size = existing.storage_credit_size_bytes_snapshot
                monthly_price = existing.monthly_credit_price_snapshot
            else:
                g_settings = GlobalBillingSettings.get_settings()
                credit_size = getattr(g_settings, 'storage_credit_size_bytes', DEFAULT_CREDIT_SIZE_BYTES)
                if credit_size is None or credit_size <= 0:
                    raise ValueError(
                        f"GlobalBillingSettings.storage_credit_size_bytes must be a positive integer greater than 0, got {credit_size}"
                    )
                monthly_price = getattr(g_settings, 'storage_credit_monthly_price', DEFAULT_MONTHLY_CREDIT_PRICE)

            # 2. Query distinct billable files on usage_date
            files_qs = StorageCalculationService.get_billable_files_qs(organization, usage_date)
            agg = files_qs.aggregate(total_bytes=models.Sum('size_bytes'))
            total_billable_bytes = agg['total_bytes'] or 0

            # 3. Calculate credits and rate using preserved snapshot pricing
            billable_gb, credits = StorageCalculationService.calculate_storage_credits(
                total_billable_bytes, credit_size
            )
            days_in_month = StorageCalculationService.get_days_in_month(usage_date.year, usage_date.month)
            daily_rate = StorageCalculationService.calculate_daily_credit_rate(monthly_price, days_in_month)
            charge = StorageCalculationService.calculate_daily_charge(credits, monthly_price, days_in_month)

            # 4. Finalization status:
            # - If caller explicitly specifies is_finalized, respect it.
            # - Otherwise: only finalize closed past UTC days if row existed during that usage day
            #   (row.created_at < next_day_start). Reconstructed missing closed-day rows remain unfinalized.
            today_utc = timezone.now().date()
            next_day_start = datetime(
                usage_date.year, usage_date.month, usage_date.day, 0, 0, 0, tzinfo=dt_timezone.utc
            ) + timedelta(days=1)

            if is_finalized is not None:
                final_flag = is_finalized
            else:
                final_flag = bool(
                    usage_date < today_utc
                    and existing is not None
                    and existing.created_at < next_day_start
                )

            defaults = {
                'billable_bytes': total_billable_bytes,
                'billable_gb': billable_gb,
                'storage_credits': credits,
                'storage_credit_size_bytes_snapshot': credit_size,
                'monthly_credit_price_snapshot': Decimal(str(monthly_price)),
                'daily_credit_rate_snapshot': daily_rate,
                'storage_charge': charge,
                'days_in_month': days_in_month,
                'calculation_version': 1,
                'is_finalized': final_flag,
                'calculated_at': timezone.now(),
            }

            try:
                usage_obj, created = StorageDailyUsage.objects.update_or_create(
                    organization=organization,
                    usage_date=usage_date,
                    defaults=defaults
                )
            except IntegrityError:
                # Handle race condition where another transaction inserted concurrently
                usage_obj = StorageDailyUsage.objects.filter(
                    organization=organization,
                    usage_date=usage_date
                ).first()
                if not usage_obj:
                    raise
                if not usage_obj.is_finalized or force_recompute:
                    for attr, val in defaults.items():
                        setattr(usage_obj, attr, val)
                    usage_obj.save()
                created = False

            return usage_obj, created

    @classmethod
    def reconcile_usage_date_range(
        cls,
        organization,
        start_date: date,
        end_date: date
    ) -> dict:
        """
        Reconciles daily usage snapshots across a contiguous date range [start_date, end_date].
        - Enforces go-live lower bound (STORAGE_METERING_START_DATE).
        - Processes dates in ascending order.
        - Closed dates created during usage day are finalized.
        - Missing closed dates are created as provisional (unfinalized).
        - Existing finalized dates are skipped safely without recomputation.
        - Ongoing today is created/updated provisionally.
        - Returns structured counters for telemetry and audit.
        """
        golive_date = cls.get_metering_start_date()
        today_utc = timezone.now().date()

        if isinstance(start_date, datetime):
            start_date = start_date.date()
        if isinstance(end_date, datetime):
            end_date = end_date.date()

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        counters = {
            'dates_considered': 0,
            'rows_created': 0,
            'rows_updated': 0,
            'rows_finalized': 0,
            'finalized_skipped': 0,
            'historical_provisional_created': 0,
            'pre_golive_skipped': 0,
            'errors_count': 0,
        }

        # Check if entire range is before go-live
        if end_date < golive_date:
            skipped_days = (end_date - start_date).days + 1
            counters['pre_golive_skipped'] = skipped_days
            logger.info(
                "Date range %s to %s is entirely before go-live %s. Skipped %d days.",
                start_date, end_date, golive_date, skipped_days
            )
            return counters

        # Bound start_date to go-live
        if start_date < golive_date:
            skipped_days = (golive_date - start_date).days
            counters['pre_golive_skipped'] = skipped_days
            logger.info(
                "Start date %s is before go-live %s. Skipped %d days; starting at %s.",
                start_date, golive_date, skipped_days, golive_date
            )
            start_date = golive_date

        curr = start_date
        while curr <= end_date:
            counters['dates_considered'] += 1
            try:
                # Check if already finalized to skip unnecessary recomputation
                existing = StorageDailyUsage.objects.filter(
                    organization=organization,
                    usage_date=curr
                ).first()

                if existing and existing.is_finalized:
                    counters['finalized_skipped'] += 1
                else:
                    usage_obj, created = cls.calculate_or_update_daily_usage(
                        organization=organization,
                        usage_date=curr
                    )
                    if created:
                        counters['rows_created'] += 1
                        if curr < today_utc:
                            counters['historical_provisional_created'] += 1
                    else:
                        counters['rows_updated'] += 1
                        if usage_obj.is_finalized and not (existing and existing.is_finalized):
                            counters['rows_finalized'] += 1
            except Exception as exc:
                counters['errors_count'] += 1
                logger.error(
                    "Error calculating daily usage for Org %s (ID %s) on date %s: %s",
                    organization.name, organization.id, curr, str(exc), exc_info=True
                )
            curr += timedelta(days=1)

        return counters


class StorageReconciliationService:
    """
    Shared service for reconciling source media records (e.g. ProjectAttachment)
    with the storage billing ledger (StorageFile and StorageEvent).
    """

    @classmethod
    def reconcile_project_attachments(
        cls,
        organization=None,
        dry_run: bool = True
    ) -> dict:
        """
        Scans ProjectAttachment rows and ensures each valid physical media file
        has a corresponding StorageFile and initial UPLOAD StorageEvent.

        Key Invariants:
        - Tenant context is strictly derived from attachment.company.
          Never falls back to Employee.organization or uploaded_by.organization.
        - Verifies physical file existence before registration.
        - Uses actual storage.size(file.name) for StorageFile.size_bytes.
        - Idempotent: Skips if StorageFile already exists for (company, 'projects', 'ProjectAttachment', str(id)).
        - Timestamp: Sets StorageFile.uploaded_at = attachment.created_at.
        - Event: Exactly one UPLOAD event with actor=None and audit metadata.
        - Temporary/expired attachments: Reconciled as normal if physical file exists.
        - Partial failure isolation: Each attachment is processed in an isolated savepoint.
        - Dry run: If dry_run=True, executes all validation checks but creates ZERO database rows.
        """
        from projects.models import ProjectAttachment

        qs = ProjectAttachment.objects.all().order_by('id')

        resolved_org = organization
        if resolved_org is not None:
            if not hasattr(resolved_org, 'id'):
                from core.models import Organization
                try:
                    resolved_org = Organization.objects.get(id=resolved_org)
                except (Organization.DoesNotExist, ValueError):
                    raise ValueError(f"Organization with id '{organization}' does not exist.")
            qs = qs.filter(company=resolved_org)

        stats = {
            'dry_run': dry_run,
            'scanned': 0,
            'created': 0,
            'already_tracked': 0,
            'missing_file': 0,
            'missing_company': 0,
            'tenant_mismatch': 0,
            'size_mismatch': 0,
            'errors': 0,
            'details': [],
        }

        for att in qs.iterator(chunk_size=500):
            stats['scanned'] += 1

            company = att.company
            if not company:
                stats['missing_company'] += 1
                stats['details'].append(f"Attachment {att.id}: missing company.")
                continue

            # Verify canonical parent organization matches attachment.company if parent exists
            target_comp = None
            if att.project_id and att.project:
                target_comp = att.project.company
            elif att.epic_id and att.epic:
                target_comp = att.epic.company
            elif att.story_id and att.story and att.story.project:
                target_comp = att.story.project.company
            elif att.task_id and att.task and att.task.story and att.task.story.project:
                target_comp = att.task.story.project.company
            elif att.comment_id and att.comment:
                c = att.comment
                if c.epic_id and c.epic:
                    target_comp = c.epic.company
                elif c.story_id and c.story and c.story.project:
                    target_comp = c.story.project.company
                elif c.task_id and c.task and c.task.story and c.task.story.project:
                    target_comp = c.task.story.project.company
                elif c.subtask_id and c.subtask and c.subtask.task and c.subtask.task.story and c.subtask.task.story.project:
                    target_comp = c.subtask.task.story.project.company

            if target_comp and company.id != target_comp.id:
                stats['tenant_mismatch'] += 1
                stats['details'].append(
                    f"Attachment {att.id}: company ID {company.id} does not match target entity company ID {target_comp.id}."
                )
                continue

            # Tenant-scoped idempotency check
            existing_sf = StorageFile.objects.filter(
                organization=company,
                source_module='projects',
                source_model='ProjectAttachment',
                source_object_id=str(att.id)
            ).first()

            if existing_sf:
                stats['already_tracked'] += 1
                continue

            # Verify physical file existence in storage backend
            if not (att.file and att.file.name):
                stats['missing_file'] += 1
                stats['details'].append(f"Attachment {att.id}: file field is empty or missing name.")
                continue

            try:
                storage = att.file.storage
                if not storage.exists(att.file.name):
                    stats['missing_file'] += 1
                    stats['details'].append(f"Attachment {att.id}: physical file missing from storage backend.")
                    continue
                actual_size = storage.size(att.file.name)
            except Exception as exc:
                stats['errors'] += 1
                stats['details'].append(f"Attachment {att.id}: storage backend error: {exc}")
                continue

            has_size_mismatch = (att.file_size is not None and att.file_size != actual_size)
            if has_size_mismatch:
                stats['size_mismatch'] += 1

            if dry_run:
                stats['created'] += 1
                continue

            # Non-dry-run: Register inside an isolated per-item savepoint
            try:
                with transaction.atomic():
                    metadata = {
                        "is_backfill": True,
                        "reconciliation_source": "ProjectAttachment",
                        "original_attachment_created_at": att.created_at.isoformat(),
                        "project_attachment_file_size": att.file_size,
                        "actual_storage_size": actual_size,
                    }
                    if has_size_mismatch:
                        metadata["size_mismatch_detected"] = True

                    filename = att.file_name or os.path.basename(att.file.name)
                    StorageService.record_file_upload(
                        organization=company,
                        original_filename=filename,
                        size_bytes=actual_size,
                        file_path=att.file.name,
                        source_object_id=str(att.id),
                        source_module='projects',
                        source_model='ProjectAttachment',
                        uploaded_by=att.uploaded_by,
                        uploaded_at=att.created_at,
                        metadata=metadata,
                        event_actor=None,
                    )
                    stats['created'] += 1
            except Exception as exc:
                stats['errors'] += 1
                stats['details'].append(f"Attachment {att.id}: registration error: {exc}")

        return stats
