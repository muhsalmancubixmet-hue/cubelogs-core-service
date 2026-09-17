from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageDailyUsage
from storage_billing.services import StorageService
from storage_billing.tasks import audit_workspace_storage
from subscribers.models import GlobalBillingSettings, MonthlyInvoice, Wallet, WalletTransaction


class StorageAuditTaskTestCase(TestCase):
    def setUp(self):
        self.org_settings1 = OrgSettings.objects.create()
        self.org1 = Organization.objects.create(name="Alpha Org", subdomain="alpha-org", settings=self.org_settings1)
        self.org_settings2 = OrgSettings.objects.create()
        self.org2 = Organization.objects.create(name="Beta Org", subdomain="beta-org", settings=self.org_settings2)

        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_credit_size_bytes = 1_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal("20.00")
        self.g_settings.storage_billing_enabled = False  # Billing disabled
        self.g_settings.save()

    def test_today_created_provisional(self):
        """
        1. Today's usage is created as provisional (is_finalized=False).
        """
        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-today-1",
            original_filename="today.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/today.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        row = StorageDailyUsage.objects.filter(organization=self.org1, usage_date=date(2026, 9, 9)).first()
        self.assertIsNotNone(row)
        self.assertFalse(row.is_finalized)
        self.assertEqual(row.billable_bytes, 1_000_000_000)

    def test_yesterday_provisional_created_during_yesterday_is_finalized(self):
        """
        2. Yesterday provisional created DURING yesterday (created_at < next_day_start)
           is finalized on today's run.
        """
        # Day 1: 2026-09-09 12:05 UTC (provisional creation)
        time_day1 = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-fin-1",
            original_filename="day1.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/day1.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        with patch('django.utils.timezone.now', return_value=time_day1):
            audit_workspace_storage(target_org_id=self.org1.id)

        row_day1 = StorageDailyUsage.objects.get(organization=self.org1, usage_date=date(2026, 9, 9))
        self.assertFalse(row_day1.is_finalized)

        # Day 2: 2026-09-10 00:05 UTC (audit runs next day)
        time_day2 = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=time_day2):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        self.assertGreaterEqual(res['rows_finalized'], 1)

        row_finalized = StorageDailyUsage.objects.get(organization=self.org1, usage_date=date(2026, 9, 9))
        self.assertTrue(row_finalized.is_finalized)

    def test_yesterday_missing_row_reconstructed_after_closure_remains_unfinalized(self):
        """
        3. A closed historical day reconstructed when worker comes online remains unfinalized
           (is_finalized=False).
        """
        # File existed on Sep 9
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-recon-1",
            original_filename="missed.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/missed.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        # Worker was down on Sep 9; first runs on Sep 10 00:05 UTC
        time_day2 = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=time_day2):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        self.assertGreaterEqual(res['historical_provisional_created'], 1)

        row_reconstructed = StorageDailyUsage.objects.get(organization=self.org1, usage_date=date(2026, 9, 9))
        self.assertFalse(row_reconstructed.is_finalized)

    def test_reconstructed_historical_provisional_is_not_autofinalized_on_next_run(self):
        """
        4. A reconstructed historical provisional row is NOT auto-finalized on subsequent task runs.
        """
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-recon-2",
            original_filename="missed2.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/missed2.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        # Run 1 on Sep 10: reconstructs Sep 9 as provisional
        time_run1 = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=time_run1):
            audit_workspace_storage(target_org_id=self.org1.id)

        row_sep9 = StorageDailyUsage.objects.get(organization=self.org1, usage_date=date(2026, 9, 9))
        self.assertFalse(row_sep9.is_finalized)

        # Run 2 on Sep 10 12:05 UTC: must NOT auto-finalize Sep 9
        time_run2 = datetime(2026, 9, 10, 12, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=time_run2):
            audit_workspace_storage(target_org_id=self.org1.id)

        row_sep9_after = StorageDailyUsage.objects.get(organization=self.org1, usage_date=date(2026, 9, 9))
        self.assertFalse(row_sep9_after.is_finalized)

    def test_finalized_yesterday_skipped_safely(self):
        """
        5. Already finalized rows are safely skipped and counted in finalized_skipped.
        """
        # Create already finalized row
        StorageDailyUsage.objects.create(
            organization=self.org1,
            usage_date=date(2026, 9, 9),
            billable_bytes=1_000_000_000,
            billable_gb=Decimal("1.0000"),
            storage_credits=1,
            storage_credit_size_bytes_snapshot=1_000_000_000,
            monthly_credit_price_snapshot=Decimal("20.00"),
            daily_credit_rate_snapshot=Decimal("0.666666666667"),
            storage_charge=Decimal("0.666666666667"),
            days_in_month=30,
            is_finalized=True
        )

        time_day2 = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=time_day2):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertGreaterEqual(res['finalized_skipped'], 1)

    def test_golive_lower_bound_enforced(self):
        """
        6. Explicit date_str before 2026-09-09 is safely skipped.
        """
        res = audit_workspace_storage(target_org_id=self.org1.id, date_str="2026-09-08")
        self.assertEqual(res['status'], 'SUCCESS')
        self.assertEqual(res['pre_golive_skipped'], 1)
        self.assertEqual(StorageDailyUsage.objects.filter(usage_date=date(2026, 9, 8)).count(), 0)

    def test_no_august_pre_golive_rows_created_during_catchup(self):
        """
        7. Historical StorageFile from August 11 exists. Scheduled catch-up must NOT
           create any rows prior to 2026-09-09.
        """
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-aug-file",
            original_filename="august.pdf",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/08/august.pdf",
            uploaded_at=datetime(2026, 8, 11, 12, 0, 0, tzinfo=dt_timezone.utc)
        )

        mock_now = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        # Check no pre-go-live rows exist in DB
        pre_golive_count = StorageDailyUsage.objects.filter(usage_date__lt=date(2026, 9, 9)).count()
        self.assertEqual(pre_golive_count, 0)

    def test_catchup_fills_recent_missing_usage_dates_provisionally(self):
        """
        8. Worker was down for 3 days (Sep 9, Sep 10, Sep 11). Run on Sep 12.
           Missing closed dates are reconstructed provisionally.
        """
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-catchup-file",
            original_filename="active_all.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/active_all.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        mock_now = datetime(2026, 9, 12, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        # Sep 9, Sep 10, Sep 11 reconstructed provisionally; Sep 12 created provisionally
        self.assertEqual(res['historical_provisional_created'], 3)
        self.assertEqual(res['rows_created'], 4)

        for d in [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]:
            row = StorageDailyUsage.objects.get(organization=self.org1, usage_date=d)
            self.assertFalse(row.is_finalized)

    def test_task_remains_idempotent(self):
        """
        9. Running the audit task twice produces the exact same DB state without duplicate rows.
        """
        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-idem-file",
            original_filename="idem.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/idem.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        with patch('django.utils.timezone.now', return_value=mock_now):
            res1 = audit_workspace_storage(target_org_id=self.org1.id)
            res2 = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res1['rows_created'], 1)
        self.assertEqual(res2['rows_created'], 0)
        self.assertEqual(res2['rows_updated'], 1)
        self.assertEqual(StorageDailyUsage.objects.filter(organization=self.org1).count(), 1)

    def test_failure_in_one_org_does_not_abort_next_org(self):
        """
        10. Failure on one organization/date is isolated; subsequent orgs still process.
        """
        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)

        original_reconcile = StorageService.reconcile_usage_date_range

        def faulty_reconcile(organization, start_date, end_date):
            if organization.id == self.org1.id:
                raise RuntimeError("Simulated DB timeout on Org 1")
            return original_reconcile(organization, start_date, end_date)

        with patch.object(StorageService, 'reconcile_usage_date_range', side_effect=faulty_reconcile):
            with patch('django.utils.timezone.now', return_value=mock_now):
                res = audit_workspace_storage()

        self.assertEqual(res['status'], 'PARTIAL_SUCCESS')
        self.assertEqual(res['errors_count'], 1)
        self.assertEqual(res['organizations_scanned'], 2)

    def test_telemetry_counters_correct(self):
        """
        11. Telemetry dictionary contains all expected structured counters.
        """
        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage()

        for key in [
            'organizations_scanned',
            'dates_considered',
            'rows_created',
            'rows_updated',
            'rows_finalized',
            'finalized_skipped',
            'historical_provisional_created',
            'pre_golive_skipped',
            'errors_count',
        ]:
            self.assertIn(key, res)

    def test_billing_disabled_does_not_stop_task(self):
        """
        12. storage_billing_enabled = False does NOT prevent daily usage calculation.
        """
        self.g_settings.storage_billing_enabled = False
        self.g_settings.save()

        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-bill-off",
            original_filename="off.pdf",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/off.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['status'], 'SUCCESS')
        self.assertEqual(res['rows_created'], 1)
        row = StorageDailyUsage.objects.filter(organization=self.org1, usage_date=date(2026, 9, 9)).first()
        self.assertIsNotNone(row)

    def test_task_does_not_create_invoice_or_wallet_transactions(self):
        """
        13. Strictly zero financial side effects: no MonthlyInvoice, Wallet, or WalletTransaction written.
        """
        invoice_count_before = MonthlyInvoice.objects.count()
        wallet_count_before = Wallet.objects.count()
        wallet_tx_count_before = WalletTransaction.objects.count()

        mock_now = datetime(2026, 9, 9, 12, 5, 0, tzinfo=dt_timezone.utc)
        StorageService.record_file_upload(
            organization=self.org1,
            source_object_id="task-no-fin",
            original_filename="nofin.pdf",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/nofin.pdf",
            uploaded_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        with patch('django.utils.timezone.now', return_value=mock_now):
            audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(MonthlyInvoice.objects.count(), invoice_count_before)
        self.assertEqual(Wallet.objects.count(), wallet_count_before)
        self.assertEqual(WalletTransaction.objects.count(), wallet_tx_count_before)

    def test_scheduled_task_never_calls_force_recompute(self):
        """
        14. Finalized rows remain unchanged even if global settings change.
        """
        # Create finalized historical row
        usage = StorageDailyUsage.objects.create(
            organization=self.org1,
            usage_date=date(2026, 9, 9),
            billable_bytes=1_000_000_000,
            billable_gb=Decimal("1.0000"),
            storage_credits=1,
            storage_credit_size_bytes_snapshot=1_000_000_000,
            monthly_credit_price_snapshot=Decimal("20.00"),
            daily_credit_rate_snapshot=Decimal("0.666666666667"),
            storage_charge=Decimal("0.666666666667"),
            days_in_month=30,
            is_finalized=True
        )

        # Change settings to 5 GB / ₹50.00
        self.g_settings.storage_credit_size_bytes = 5_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal("50.00")
        self.g_settings.save()

        mock_now = datetime(2026, 9, 10, 0, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=mock_now):
            res = audit_workspace_storage(target_org_id=self.org1.id)

        self.assertEqual(res['finalized_skipped'], 1)
        usage.refresh_from_db()
        self.assertEqual(usage.storage_credit_size_bytes_snapshot, 1_000_000_000)
        self.assertEqual(usage.monthly_credit_price_snapshot, Decimal("20.00"))

    def test_duplicate_concurrent_calculation_protection(self):
        """
        15. Service-level concurrency safety: re-running for the same organization and date
        deterministically returns the canonical row.
        """
        target = date(2026, 9, 9)
        u1, created1 = StorageService.calculate_or_update_daily_usage(self.org1, target)
        self.assertTrue(created1)

        u2, created2 = StorageService.calculate_or_update_daily_usage(self.org1, target)
        self.assertFalse(created2)
        self.assertEqual(u1.id, u2.id)
