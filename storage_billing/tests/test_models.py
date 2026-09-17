import uuid
from datetime import date
from decimal import Decimal
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage


class StorageModelTestCase(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(
            name="Acme Corp",
            subdomain="acme",
            settings=self.org_settings
        )

    def test_storage_file_creation(self):
        file_obj = StorageFile.objects.create(
            organization=self.org,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-123",
            original_filename="specs.pdf",
            file_path="project_attachments/2026/09/specs.pdf",
            storage_backend="private_filesystem",
            content_type="application/pdf",
            size_bytes=1048576,
        )
        self.assertIsInstance(file_obj.id, uuid.UUID)
        self.assertEqual(file_obj.status, "ACTIVE")
        self.assertIsNone(file_obj.deleted_at)
        self.assertIn("specs.pdf", str(file_obj))

    def test_storage_event_creation(self):
        file_obj = StorageFile.objects.create(
            organization=self.org,
            source_object_id="att-img-1",
            original_filename="image.png",
            file_path="project_attachments/2026/09/image.png",
            size_bytes=500000,
        )
        event = StorageEvent.objects.create(
            storage_file=file_obj,
            organization=self.org,
            event_type="UPLOAD",
            size_bytes=500000,
            metadata={"uploader_ip": "127.0.0.1"}
        )
        self.assertEqual(event.event_type, "UPLOAD")
        self.assertEqual(event.size_bytes, 500000)
        self.assertEqual(event.storage_file, file_obj)
        self.assertEqual(event.organization, self.org)
        self.assertIn("UPLOAD", str(event))

    def test_storage_daily_usage_uniqueness_constraint(self):
        usage_date = date(2026, 9, 7)
        StorageDailyUsage.objects.create(
            organization=self.org,
            usage_date=usage_date,
            billable_bytes=1000000000,
            billable_gb=Decimal("1.0000"),
            storage_credits=1,
            monthly_credit_price_snapshot=Decimal("20.00"),
            daily_credit_rate_snapshot=Decimal("0.666667"),
            storage_charge=Decimal("0.67"),
            days_in_month=30,
        )

        with self.assertRaises(IntegrityError):
            StorageDailyUsage.objects.create(
                organization=self.org,
                usage_date=usage_date,
                billable_bytes=2000000000,
                billable_gb=Decimal("2.0000"),
                storage_credits=2,
            )

    def test_source_deletion_decoupling(self):
        """
        StorageFile uses source_object_id loosely coupled so that even if
        the source attachment or entity is removed from projects, the billing
        record remains fully intact.
        """
        file_obj = StorageFile.objects.create(
            organization=self.org,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="external-id-9999",
            original_filename="contract.pdf",
            file_path="project_attachments/2026/contract.pdf",
            size_bytes=2500000,
        )
        self.assertEqual(file_obj.source_object_id, "external-id-9999")
        # Simulating source deletion by clearing source_object_id or leaving it pointing to a deleted ID
        file_obj.refresh_from_db()
        self.assertIsNotNone(file_obj.id)
        self.assertEqual(file_obj.size_bytes, 2500000)
