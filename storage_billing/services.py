import calendar
import math
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction, models, IntegrityError
from django.utils import timezone

from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage
from subscribers.models import GlobalBillingSettings

DEFAULT_CREDIT_SIZE_BYTES = 1_000_000_000  # Commercial decimal GB (1,000,000,000 bytes)
DEFAULT_MONTHLY_CREDIT_PRICE = Decimal('20.00')


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
        target_date must be a datetime.date object.
        """
        upload_date = storage_file.uploaded_at.date()
        if upload_date > target_date:
            return False
        if storage_file.deleted_at is None:
            return True
        return target_date <= storage_file.deleted_at.date()

    @staticmethod
    def get_billable_files_qs(organization, target_date):
        """
        Queries distinct StorageFile records that count towards target_date for an organization.
        Criteria:
            uploaded_at.date <= target_date AND (deleted_at IS NULL OR deleted_at.date >= target_date)
        """
        return StorageFile.objects.filter(
            organization=organization,
            uploaded_at__date__lte=target_date
        ).filter(
            models.Q(deleted_at__isnull=True) | models.Q(deleted_at__date__gte=target_date)
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
        metadata: dict | None = None
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
                    actor=uploaded_by,
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
        """
        # Check existing row
        existing = StorageDailyUsage.objects.filter(
            organization=organization,
            usage_date=usage_date
        ).first()

        if existing and existing.is_finalized and not force_recompute:
            return existing, False

        # 1. Fetch Global Billing Settings for rates
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

        # 3. Calculate credits and rate
        billable_gb, credits = StorageCalculationService.calculate_storage_credits(
            total_billable_bytes, credit_size
        )
        days_in_month = StorageCalculationService.get_days_in_month(usage_date.year, usage_date.month)
        daily_rate = StorageCalculationService.calculate_daily_credit_rate(monthly_price, days_in_month)
        charge = StorageCalculationService.calculate_daily_charge(credits, monthly_price, days_in_month)

        # 4. Finalization status: past UTC days are finalized by default
        today_utc = timezone.now().date()
        final_flag = is_finalized if is_finalized is not None else (usage_date < today_utc)

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

        usage_obj, created = StorageDailyUsage.objects.update_or_create(
            organization=organization,
            usage_date=usage_date,
            defaults=defaults
        )
        return usage_obj, created
