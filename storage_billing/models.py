import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone


class StorageFile(models.Model):
    """
    Canonical record of a billable customer-uploaded physical file and its lifecycle.
    Decoupled from source models (e.g. ProjectAttachment) so that source deletions
    or cascades do not erase billing evidence.
    """
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('DELETED', 'Deleted'),
        ('PURGED', 'Purged'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='storage_files',
        help_text="Canonical tenant organization that owns this billable file"
    )
    source_module = models.CharField(
        max_length=50,
        default='projects',
        help_text="Module originating the file (e.g. 'projects')"
    )
    source_model = models.CharField(
        max_length=50,
        default='ProjectAttachment',
        help_text="Django model name of the source record"
    )
    source_object_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Primary key/UUID of the source record (stable, non-empty string)"
    )
    original_filename = models.CharField(max_length=255)
    file_path = models.CharField(
        max_length=1024,
        help_text="Storage-relative file path or storage reference key"
    )
    storage_backend = models.CharField(
        max_length=50,
        default='private_filesystem',
        help_text="Storage backend identifier"
    )
    content_type = models.CharField(max_length=100, blank=True, null=True)
    size_bytes = models.BigIntegerField(default=0, help_text="File size in bytes")

    # Lifecycle timestamps
    uploaded_at = models.DateTimeField(default=timezone.now, db_index=True)
    deleted_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Timestamp when customer deleted the file (stops billable lifecycle)"
    )
    purged_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when physical file was purged from backend disk/storage"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='ACTIVE',
        db_index=True
    )
    uploaded_by = models.ForeignKey(
        'users.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_storage_files'
    )
    deleted_by = models.ForeignKey(
        'users.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_storage_files'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'storage_billing_file'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['organization', 'status', 'uploaded_at'], name='storage_file_org_stat_up_idx'),
            models.Index(fields=['organization', 'deleted_at'], name='storage_file_org_del_idx'),
            models.Index(fields=['source_module', 'source_object_id'], name='storage_file_source_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'source_module', 'source_model', 'source_object_id'],
                name='unique_storage_file_source_per_org'
            )
        ]

    def __str__(self):
        return f"{self.original_filename} ({self.size_bytes} B) - Org {self.organization_id} [{self.status}]"

    def is_billable_on_date(self, target_date):
        """
        Rule 8 & 9:
        A file counts on EVERY calendar day during which it existed in the customer's billable lifecycle.
        Upload and deletion days BOTH count.
        target_date is a datetime.date object evaluated in project canonical UTC.
        """
        upload_date = self.uploaded_at.date()
        if upload_date > target_date:
            return False
        if self.deleted_at is None:
            return True
        return target_date <= self.deleted_at.date()


class StorageEvent(models.Model):
    """
    Append-only immutable audit trail recording file lifecycle operations.
    Deletion event timing serves as authoritative billing stop.
    """
    EVENT_TYPE_CHOICES = [
        ('UPLOAD', 'Upload'),
        ('DELETE', 'Delete'),
        ('PURGE', 'Purge'),
    ]

    id = models.BigAutoField(primary_key=True)
    storage_file = models.ForeignKey(
        StorageFile,
        on_delete=models.PROTECT,
        related_name='events',
        help_text="Target StorageFile record"
    )
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='storage_events',
        help_text="Canonical tenant organization for event isolation"
    )
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    size_bytes = models.BigIntegerField(help_text="Snapshot of file size at event time")
    actor = models.ForeignKey(
        'users.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='storage_events'
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'storage_billing_event'
        ordering = ['-occurred_at', '-id']
        indexes = [
            models.Index(fields=['organization', 'event_type', 'occurred_at'], name='storage_event_org_type_idx'),
            models.Index(fields=['storage_file', 'occurred_at'], name='storage_event_file_occ_idx'),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.storage_file_id} ({self.size_bytes} B) at {self.occurred_at}"


class StorageDailyUsage(models.Model):
    """
    Canonical daily usage and charge summary per organization and UTC calendar date.
    Maintains immutable pricing and calculation snapshots so historical billing
    does not fluctuate when rates change.
    """
    id = models.BigAutoField(primary_key=True)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.PROTECT,
        related_name='storage_daily_usages',
        help_text="Organization for this daily usage snapshot"
    )
    usage_date = models.DateField(db_index=True, help_text="UTC calendar date of usage")
    billable_bytes = models.BigIntegerField(
        default=0,
        help_text="Sum of size_bytes for all distinct files present at any point during usage_date"
    )
    billable_gb = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        default=Decimal('0.0000'),
        help_text="Exact commercial decimal GB: billable_bytes / 1,000,000,000"
    )
    storage_credits = models.PositiveIntegerField(
        default=0,
        help_text="Whole storage credits (ceil of billable_bytes / credit_size_bytes, or 0 if 0 bytes)"
    )

    # Immutable pricing snapshots
    storage_credit_size_bytes_snapshot = models.BigIntegerField(
        default=1000000000,
        help_text="Snapshot of storage_credit_size_bytes used for calculation"
    )
    monthly_credit_price_snapshot = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('20.00'),
        help_text="Price per credit per month at calculation time"
    )
    daily_credit_rate_snapshot = models.DecimalField(
        max_digits=30,
        decimal_places=12,
        default=Decimal('0.000000000000'),
        help_text="monthly_credit_price / days_in_month (high precision Decimal, 12 places)"
    )
    storage_charge = models.DecimalField(
        max_digits=30,
        decimal_places=12,
        default=Decimal('0.000000000000'),
        help_text="Daily storage charge unrounded to currency cents: credits * monthly_credit_price / days_in_month (12 places)"
    )
    days_in_month = models.PositiveSmallIntegerField(
        default=30,
        help_text="Calendar days in that usage month (28, 29, 30, or 31)"
    )
    calculation_version = models.PositiveSmallIntegerField(
        default=1,
        help_text="Version of the calculation formula applied"
    )

    is_finalized = models.BooleanField(
        default=False,
        help_text="True if the usage day has passed and charge is permanently frozen"
    )
    calculated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'storage_billing_daily_usage'
        ordering = ['-usage_date', 'organization']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'usage_date'],
                name='unique_org_usage_date'
            )
        ]
        indexes = [
            models.Index(fields=['organization', 'usage_date'], name='storage_usage_org_date_idx'),
        ]

    def __str__(self):
        return (
            f"StorageDailyUsage Org {self.organization_id} [{self.usage_date}]: "
            f"{self.billable_bytes} B ({self.storage_credits} credits = ₹{self.storage_charge})"
        )
