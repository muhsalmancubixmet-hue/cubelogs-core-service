import calendar
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from django.db import IntegrityError
from django.test import TestCase

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageEvent
from storage_billing.services import StorageCalculationService, StorageService


class StorageCalculationServiceTestCase(TestCase):
    def test_days_in_month_calculation(self):
        # February non-leap year (2025)
        self.assertEqual(StorageCalculationService.get_days_in_month(2025, 2), 28)
        # February leap year (2024, 2028)
        self.assertEqual(StorageCalculationService.get_days_in_month(2024, 2), 29)
        self.assertEqual(StorageCalculationService.get_days_in_month(2028, 2), 29)
        # 30-day months (April, June, September, November)
        self.assertEqual(StorageCalculationService.get_days_in_month(2026, 9), 30)
        self.assertEqual(StorageCalculationService.get_days_in_month(2026, 4), 30)
        # 31-day months (January, March, May, July, August, October, December)
        self.assertEqual(StorageCalculationService.get_days_in_month(2026, 8), 31)
        self.assertEqual(StorageCalculationService.get_days_in_month(2026, 1), 31)

    def test_daily_credit_rate_precision(self):
        # Monthly price ₹20.00 in 30-day month => 20 / 30 = 0.666666666667 (12 decimals)
        rate_30 = StorageCalculationService.calculate_daily_credit_rate(Decimal("20.00"), 30)
        self.assertIsInstance(rate_30, Decimal)
        self.assertEqual(rate_30, Decimal("0.666666666667"))

        # Monthly price ₹20.00 in 28-day February => 20 / 28 = 0.714285714286 (12 decimals)
        rate_28 = StorageCalculationService.calculate_daily_credit_rate(Decimal("20.00"), 28)
        self.assertEqual(rate_28, Decimal("0.714285714286"))

        # Monthly price ₹20.00 in 29-day leap February => 20 / 29 = 0.689655172414 (12 decimals)
        rate_29 = StorageCalculationService.calculate_daily_credit_rate(Decimal("20.00"), 29)
        self.assertEqual(rate_29, Decimal("0.689655172414"))

        # Monthly price ₹20.00 in 31-day month => 20 / 31 = 0.645161290323 (12 decimals)
        rate_31 = StorageCalculationService.calculate_daily_credit_rate(Decimal("20.00"), 31)
        self.assertEqual(rate_31, Decimal("0.645161290323"))

    def test_storage_credits_conversion_and_rounding(self):
        """
        1 credit = 1 GB (1,000,000,000 bytes).
        Whole credits rounded UP (ceil).
        Zero bytes => 0 credits.
        """
        # 0 bytes => 0 credits
        gb, credits = StorageCalculationService.calculate_storage_credits(0)
        self.assertEqual(credits, 0)
        self.assertEqual(gb, Decimal("0.0000"))

        # 200,000,000 bytes (0.2 GB) => 1 credit
        gb, credits = StorageCalculationService.calculate_storage_credits(200_000_000)
        self.assertEqual(credits, 1)
        self.assertEqual(gb, Decimal("0.2000"))

        # 1,000,000,000 bytes (1.0 GB) => 1 credit
        gb, credits = StorageCalculationService.calculate_storage_credits(1_000_000_000)
        self.assertEqual(credits, 1)
        self.assertEqual(gb, Decimal("1.0000"))

        # 1,100,000,000 bytes (1.1 GB) => 2 credits
        gb, credits = StorageCalculationService.calculate_storage_credits(1_100_000_000)
        self.assertEqual(credits, 2)
        self.assertEqual(gb, Decimal("1.1000"))

        # 2,400,000,000 bytes (2.4 GB) => 3 credits
        gb, credits = StorageCalculationService.calculate_storage_credits(2_400_000_000)
        self.assertEqual(credits, 3)
        self.assertEqual(gb, Decimal("2.4000"))

        # 1 byte => 1 credit
        gb, credits = StorageCalculationService.calculate_storage_credits(1)
        self.assertEqual(credits, 1)

    def test_daily_charge_calculation_decimal_deterministic(self):
        # Direct formula: 1 credit in 30-day month at ₹20/mo => 1 * 20 / 30 = 0.666666666667 (12 decimals)
        charge_1 = StorageCalculationService.calculate_daily_charge(1, Decimal("20.00"), 30)
        self.assertEqual(charge_1, Decimal("0.666666666667"))

        # 3 credits in 30-day month at ₹20/mo => (3 * 20) / 30 = ₹2.000000000000 (12 decimals)
        charge_3 = StorageCalculationService.calculate_daily_charge(3, Decimal("20.00"), 30)
        self.assertEqual(charge_3, Decimal("2.000000000000"))

        # 0 credits => ₹0.000000000000
        charge_0 = StorageCalculationService.calculate_daily_charge(0, Decimal("20.00"), 30)
        self.assertEqual(charge_0, Decimal("0.000000000000"))

        # 0 or negative days => safe ₹0.000000000000
        charge_0_days = StorageCalculationService.calculate_daily_charge(2, Decimal("20.00"), 0)
        self.assertEqual(charge_0_days, Decimal("0.000000000000"))


class StorageLifecycleServiceTestCase(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Beta Corp", settings=self.org_settings)

    def test_record_file_upload_emits_event(self):
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            original_filename="manual.pdf",
            size_bytes=5_000_000,
            file_path="project_attachments/2026/09/manual.pdf",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-555",
            content_type="application/pdf"
        )
        self.assertEqual(file_obj.status, "ACTIVE")
        self.assertEqual(file_obj.size_bytes, 5_000_000)

        # Check event
        events = StorageEvent.objects.filter(storage_file=file_obj)
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.event_type, "UPLOAD")
        self.assertEqual(event.size_bytes, 5_000_000)

    def test_record_file_deletion_and_idempotency(self):
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="del-test-1",
            original_filename="diagram.png",
            size_bytes=2_000_000,
            file_path="project_attachments/2026/09/diagram.png"
        )
        del_time = datetime(2026, 9, 8, 14, 0, 0, tzinfo=dt_timezone.utc)
        file_obj = StorageService.record_file_deletion(file_obj, deleted_at=del_time)

        self.assertEqual(file_obj.status, "DELETED")
        self.assertEqual(file_obj.deleted_at, del_time)

        events = StorageEvent.objects.filter(storage_file=file_obj)
        self.assertEqual(events.count(), 2)  # UPLOAD and DELETE
        del_event = events.filter(event_type="DELETE").first()
        self.assertIsNotNone(del_event)
        self.assertEqual(del_event.occurred_at, del_time)

        # Idempotent: Calling record_file_deletion again does not add duplicate event
        StorageService.record_file_deletion(file_obj, deleted_at=del_time)
        self.assertEqual(StorageEvent.objects.filter(storage_file=file_obj).count(), 2)

    def test_record_file_purge(self):
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="purge-test-1",
            original_filename="archive.zip",
            size_bytes=100_000_000,
            file_path="project_attachments/2026/09/archive.zip"
        )
        file_obj = StorageService.record_file_purge(file_obj)
        self.assertEqual(file_obj.status, "PURGED")
        self.assertIsNotNone(file_obj.purged_at)

        events = StorageEvent.objects.filter(storage_file=file_obj)
        self.assertEqual(events.count(), 2)  # UPLOAD + PURGE
        self.assertTrue(events.filter(event_type="PURGE").exists())

    def test_source_idempotent_registration_same_source(self):
        """
        Hardening Issue #3 & Section 8:
        Service called twice for the same canonical source:
        => exactly one StorageFile
        => exactly one initial UPLOAD event
        """
        f1 = StorageService.record_file_upload(
            organization=self.org,
            original_filename="first_attempt.pdf",
            size_bytes=10_000_000,
            file_path="project_attachments/2026/09/first_attempt.pdf",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-dup-101"
        )

        # Second call with same canonical source (e.g. upload dual-write + 12-hour audit)
        f2 = StorageService.record_file_upload(
            organization=self.org,
            original_filename="first_attempt.pdf",
            size_bytes=10_000_000,
            file_path="project_attachments/2026/09/first_attempt.pdf",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-dup-101"
        )

        self.assertEqual(f1.id, f2.id)
        self.assertEqual(
            StorageFile.objects.filter(
                organization=self.org,
                source_module="projects",
                source_model="ProjectAttachment",
                source_object_id="att-dup-101"
            ).count(),
            1
        )
        self.assertEqual(StorageEvent.objects.filter(storage_file=f1).count(), 1)
        self.assertEqual(StorageEvent.objects.filter(storage_file=f1, event_type="UPLOAD").count(), 1)

    def test_db_unique_constraint_for_canonical_source(self):
        """
        Hardening Issue #3 & Section 6:
        Direct DB insert of duplicate (org, source_module, source_model, source_object_id)
        must raise IntegrityError.
        """
        from django.db import IntegrityError
        StorageFile.objects.create(
            organization=self.org,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="db-constraint-test",
            original_filename="f1.pdf",
            file_path="path/1",
            size_bytes=1000
        )

        with self.assertRaises(IntegrityError):
            StorageFile.objects.create(
                organization=self.org,
                source_module="projects",
                source_model="ProjectAttachment",
                source_object_id="db-constraint-test",
                original_filename="f2.pdf",
                file_path="path/2",
                size_bytes=2000
            )

    def test_same_source_different_org_or_model_allowed(self):
        """
        Hardening Issue #3:
        Same source_object_id in different orgs or with different source_model is valid.
        """
        org_other = Organization.objects.create(
            name="Gamma Ltd",
            subdomain="gamma",
            settings=OrgSettings.objects.create()
        )

        f_org1 = StorageService.record_file_upload(
            organization=self.org,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="shared-key-100",
            original_filename="shared1.pdf",
            size_bytes=1000,
            file_path="path/shared1"
        )

        # Allowed: Same source_object_id in different org
        f_org2 = StorageService.record_file_upload(
            organization=org_other,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="shared-key-100",
            original_filename="shared2.pdf",
            size_bytes=1000,
            file_path="path/shared2"
        )
        self.assertNotEqual(f_org1.id, f_org2.id)

        # Allowed: Same org & source_object_id, but different source_model
        f_diff_model = StorageService.record_file_upload(
            organization=self.org,
            source_module="projects",
            source_model="InvoiceAttachment",
            source_object_id="shared-key-100",
            original_filename="invoice_att.pdf",
            size_bytes=1000,
            file_path="path/inv"
        )
        self.assertNotEqual(f_org1.id, f_diff_model.id)

    def test_daily_usage_not_double_counted_on_repeated_registration(self):
        """
        Repeated registration calls do not double daily billable bytes or credits.
        """
        usage_date = date(2026, 9, 7)
        upload_time = datetime(2026, 9, 7, 8, 0, 0, tzinfo=dt_timezone.utc)

        # First registration: 1 GB
        StorageService.record_file_upload(
            organization=self.org,
            original_filename="payload.iso",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/payload.iso",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="unique-payload-99",
            uploaded_at=upload_time
        )

        # Repeated registration: identical canonical source
        StorageService.record_file_upload(
            organization=self.org,
            original_filename="payload.iso",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/payload.iso",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="unique-payload-99",
            uploaded_at=upload_time
        )

        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, usage_date)
        self.assertEqual(usage.billable_bytes, 1_000_000_000)
        self.assertEqual(usage.storage_credits, 1)

    def test_events_are_append_only_and_immutable(self):
        """
        Hardening Issue #4 & Section 9:
        Service operations append rather than overwrite events.
        Initial UPLOAD event remains unaltered when DELETE and PURGE events are appended.
        """
        upload_time = datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="event-immut-1",
            original_filename="contract.pdf",
            size_bytes=4_000_000,
            file_path="project_attachments/2026/09/contract.pdf",
            uploaded_at=upload_time
        )

        upload_event = StorageEvent.objects.get(storage_file=file_obj, event_type="UPLOAD")
        initial_upload_time = upload_event.occurred_at
        initial_upload_size = upload_event.size_bytes

        # Delete the file
        delete_time = datetime(2026, 9, 7, 15, 0, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_deletion(file_obj, deleted_at=delete_time)

        # Total events is now 2
        self.assertEqual(StorageEvent.objects.filter(storage_file=file_obj).count(), 2)

        # Verify upload event is unaltered
        upload_event.refresh_from_db()
        self.assertEqual(upload_event.occurred_at, initial_upload_time)
        self.assertEqual(upload_event.size_bytes, initial_upload_size)
        self.assertEqual(upload_event.event_type, "UPLOAD")

        # Purge the file
        purge_time = datetime(2026, 9, 7, 18, 0, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_purge(file_obj, purged_at=purge_time)

        # Total events is now 3: UPLOAD, DELETE, PURGE
        all_events = list(StorageEvent.objects.filter(storage_file=file_obj).order_by("occurred_at"))
        self.assertEqual(len(all_events), 3)
        self.assertEqual([e.event_type for e in all_events], ["UPLOAD", "DELETE", "PURGE"])

    def test_concurrency_race_recovery_returns_existing_file(self):
        """
        Hardening V2 & Section 6 & 7:
        Simulate a concurrent registration race where Worker A commits the record,
        and Worker B hits the unique constraint during StorageFile.objects.create.
        The recovery path must safely resolve the existing StorageFile without raising 500.
        """
        from unittest.mock import patch
        existing_file = StorageService.record_file_upload(
            organization=self.org,
            original_filename="concurrent.pdf",
            size_bytes=5_000_000,
            file_path="project_attachments/2026/09/concurrent.pdf",
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="race-key-999"
        )

        # Worker B attempts creation and encounters IntegrityError during the insert:
        with patch.object(StorageFile.objects, 'create', side_effect=IntegrityError("UNIQUE constraint failed")):
            resolved_file = StorageService.record_file_upload(
                organization=self.org,
                original_filename="concurrent.pdf",
                size_bytes=5_000_000,
                file_path="project_attachments/2026/09/concurrent.pdf",
                source_module="projects",
                source_model="ProjectAttachment",
                source_object_id="race-key-999"
            )

        self.assertEqual(resolved_file.id, existing_file.id)
        # Verify no duplicate UPLOAD event was created during recovery
        self.assertEqual(StorageEvent.objects.filter(storage_file=existing_file).count(), 1)

    def test_unrelated_integrity_error_is_not_swallowed(self):
        """
        Hardening V2 & Section 6:
        If an IntegrityError is raised for an unrelated database reason,
        it must NOT be swallowed by the race recovery path and must properly re-raise.
        """
        from unittest.mock import patch
        with patch.object(StorageFile.objects, 'create', side_effect=IntegrityError("Foreign key violation")):
            with self.assertRaises(IntegrityError):
                StorageService.record_file_upload(
                    organization=self.org,
                    original_filename="fail.pdf",
                    size_bytes=5_000_000,
                    file_path="project_attachments/2026/09/fail.pdf",
                    source_module="projects",
                    source_model="ProjectAttachment",
                    source_object_id="unrelated-failure-key"
                )

    def test_source_object_id_validation_rejects_none(self):
        """
        Hardening V3 & Section 8 & 11:
        source_object_id=None must be rejected with ValueError before creating records.
        """
        with self.assertRaises(ValueError) as ctx:
            StorageService.record_file_upload(
                organization=self.org,
                original_filename="null_source.pdf",
                size_bytes=1000,
                file_path="project_attachments/null_source.pdf",
                source_object_id=None
            )
        self.assertIn("source_object_id", str(ctx.exception))

    def test_source_object_id_validation_rejects_empty_string(self):
        """
        Hardening V3 & Section 8 & 11:
        source_object_id='' must be rejected with ValueError.
        """
        with self.assertRaises(ValueError) as ctx:
            StorageService.record_file_upload(
                organization=self.org,
                original_filename="empty_source.pdf",
                size_bytes=1000,
                file_path="project_attachments/empty_source.pdf",
                source_object_id=""
            )
        self.assertIn("source_object_id", str(ctx.exception))

    def test_source_object_id_validation_rejects_whitespace_string(self):
        """
        Hardening V3 & Section 8 & 11:
        source_object_id='   ' must be rejected with ValueError.
        """
        with self.assertRaises(ValueError) as ctx:
            StorageService.record_file_upload(
                organization=self.org,
                original_filename="whitespace_source.pdf",
                size_bytes=1000,
                file_path="project_attachments/whitespace_source.pdf",
                source_object_id="   "
            )
        self.assertIn("source_object_id", str(ctx.exception))

    def test_source_object_id_numeric_normalized_to_string(self):
        """
        Hardening V3 & Section 8 & 11:
        source_object_id=123 must be normalized to '123'.
        """
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            original_filename="numeric_source.pdf",
            size_bytes=1000,
            file_path="project_attachments/numeric_source.pdf",
            source_object_id=123
        )
        self.assertEqual(file_obj.source_object_id, "123")
        self.assertIsInstance(file_obj.source_object_id, str)

    def test_source_object_id_whitespace_stripped(self):
        """
        Hardening V3 & Section 9:
        source_object_id='  padded-id-99  ' must be stripped to 'padded-id-99'.
        """
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            original_filename="padded.pdf",
            size_bytes=1000,
            file_path="project_attachments/padded.pdf",
            source_object_id="  padded-id-99  "
        )
        self.assertEqual(file_obj.source_object_id, "padded-id-99")
