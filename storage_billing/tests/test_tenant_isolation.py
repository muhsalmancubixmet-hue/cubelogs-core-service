from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from django.test import TestCase

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageDailyUsage
from storage_billing.services import StorageService
from storage_billing.tasks import audit_workspace_storage
from subscribers.models import GlobalBillingSettings, Wallet, MonthlyInvoice


class TenantIsolationAndAuditTaskTestCase(TestCase):
    def setUp(self):
        # Create Org A
        self.settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Alpha Org", subdomain="alpha", settings=self.settings_a)

        # Create Org B
        self.settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Bravo Org", subdomain="bravo", settings=self.settings_b)

        # Configure global settings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_credit_size_bytes = 1_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal("20.00")
        self.g_settings.storage_billing_enabled = True
        self.g_settings.save()

    def test_strict_tenant_isolation(self):
        """
        Org A StorageFile records must NEVER contribute to Org B StorageDailyUsage.
        """
        test_date = date(2026, 9, 7)

        # Org A uploads 5 GB
        StorageService.record_file_upload(
            organization=self.org_a,
            source_object_id="alpha-att-1",
            original_filename="alpha_huge.dat",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/alpha_huge.dat",
            uploaded_at=datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        # Org B uploads 1 GB
        StorageService.record_file_upload(
            organization=self.org_b,
            source_object_id="bravo-att-1",
            original_filename="bravo_small.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/bravo_small.dat",
            uploaded_at=datetime(2026, 9, 7, 11, 0, 0, tzinfo=dt_timezone.utc)
        )

        usage_a, _ = StorageService.calculate_or_update_daily_usage(self.org_a, test_date)
        usage_b, _ = StorageService.calculate_or_update_daily_usage(self.org_b, test_date)

        # Org A has 5 GB / 5 credits
        self.assertEqual(usage_a.billable_bytes, 5_000_000_000)
        self.assertEqual(usage_a.storage_credits, 5)

        # Org B has 1 GB / 1 credit
        self.assertEqual(usage_b.billable_bytes, 1_000_000_000)
        self.assertEqual(usage_b.storage_credits, 1)

    def test_same_source_object_id_in_different_orgs_does_not_collide(self):
        """
        If Org A and Org B both have source attachments with ID 'att-100',
        they must remain separate and never leak across organizations.
        """
        test_date = date(2026, 9, 7)

        f_a = StorageService.record_file_upload(
            organization=self.org_a,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-100",
            original_filename="file_a.pdf",
            size_bytes=500_000_000,
            file_path="project_attachments/2026/09/file_a.pdf",
            uploaded_at=datetime(2026, 9, 7, 9, 0, 0, tzinfo=dt_timezone.utc)
        )

        f_b = StorageService.record_file_upload(
            organization=self.org_b,
            source_module="projects",
            source_model="ProjectAttachment",
            source_object_id="att-100",
            original_filename="file_b.pdf",
            size_bytes=1_500_000_000,
            file_path="project_attachments/2026/09/file_b.pdf",
            uploaded_at=datetime(2026, 9, 7, 9, 0, 0, tzinfo=dt_timezone.utc)
        )

        self.assertNotEqual(f_a.id, f_b.id)
        self.assertEqual(f_a.source_object_id, f_b.source_object_id)
        self.assertEqual(f_a.organization_id, self.org_a.id)
        self.assertEqual(f_b.organization_id, self.org_b.id)

        usage_a, _ = StorageService.calculate_or_update_daily_usage(self.org_a, test_date)
        usage_b, _ = StorageService.calculate_or_update_daily_usage(self.org_b, test_date)

        self.assertEqual(usage_a.billable_bytes, 500_000_000)
        self.assertEqual(usage_a.storage_credits, 1)  # ceil(0.5) = 1

        self.assertEqual(usage_b.billable_bytes, 1_500_000_000)
        self.assertEqual(usage_b.storage_credits, 2)  # ceil(1.5) = 2

    def test_audit_workspace_storage_task(self):
        """
        Celery audit task runs cleanly, reconciles usage across orgs, and
        strictly causes ZERO wallet debits and ZERO MonthlyInvoice creation.
        """
        audit_date = date(2026, 9, 7)
        date_str = audit_date.isoformat()

        # Org A uploads 2 GB
        StorageService.record_file_upload(
            organization=self.org_a,
            source_object_id="audit-att-1",
            original_filename="audit_file.dat",
            size_bytes=2_000_000_000,
            file_path="project_attachments/2026/09/audit_file.dat",
            uploaded_at=datetime(2026, 9, 7, 8, 0, 0, tzinfo=dt_timezone.utc)
        )

        initial_invoice_count = MonthlyInvoice.objects.count()
        initial_wallet_count = Wallet.objects.count()

        # Run the audit task synchronously
        result = audit_workspace_storage(date_str=date_str)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertGreaterEqual(result["orgs_count"], 2)

        # Verify usage was computed for Org A
        usage_a = StorageDailyUsage.objects.filter(organization=self.org_a, usage_date=audit_date).first()
        self.assertIsNotNone(usage_a)
        self.assertEqual(usage_a.billable_bytes, 2_000_000_000)
        self.assertEqual(usage_a.storage_credits, 2)

        # Invariant checks: zero invoices created or touched
        self.assertEqual(MonthlyInvoice.objects.count(), initial_invoice_count)
        self.assertEqual(Wallet.objects.count(), initial_wallet_count)

        # Re-running task is idempotent
        result2 = audit_workspace_storage(date_str=date_str)
        self.assertEqual(result2["status"], "SUCCESS")
        self.assertEqual(
            StorageDailyUsage.objects.filter(organization=self.org_a, usage_date=audit_date).count(),
            1
        )
