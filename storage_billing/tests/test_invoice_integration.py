from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.conf import settings

from core.models import Organization, OrgSettings
from users.models import Employee, OrganizationMembership
from subscribers.models import GlobalBillingSettings, MonthlyInvoice, Wallet, WalletTransaction
from storage_billing.models import StorageDailyUsage
from company.api.v1.services import BillingService
from company.tasks import sweep_workspace_subscriptions


class StorageInvoiceIntegrationTestCase(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=True
        )
        self.org = Organization.objects.create(name="Apex Enterprise", settings=self.org_settings)

        # Create superadmin & employee
        self.superadmin = Employee.objects.create(
            username="admin@apex.com",
            email="admin@apex.com",
            first_name="Alice",
            last_name="Super",
            isSuperAdmin=True,
            organization=self.org
        )
        self.emp1 = Employee.objects.create(
            username="worker1@apex.com",
            email="worker1@apex.com",
            first_name="Bob",
            last_name="Worker",
            isSuperAdmin=False,
            organization=self.org,
            is_active=True
        )
        self.emp2 = Employee.objects.create(
            username="worker2@apex.com",
            email="worker2@apex.com",
            first_name="Charlie",
            last_name="Worker",
            isSuperAdmin=False,
            organization=self.org,
            is_active=True
        )

        # Create active memberships for employees (billable seats = 2)
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.emp1,
            is_active_in_org=True,
            is_deleted=False,
            employment_status='Active'
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.emp2,
            is_active_in_org=True,
            is_deleted=False,
            employment_status='Active'
        )

        # Global settings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.employee_seat_price = Decimal("50.00")
        self.g_settings.attendance_module_price = Decimal("99.00")
        self.g_settings.tasks_module_price = Decimal("56.00")
        self.g_settings.storage_credit_size_bytes = 1_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal("20.00")
        self.g_settings.storage_billing_enabled = True
        self.g_settings.save()

        # Wallet
        self.wallet, _ = Wallet.objects.get_or_create(
            employee=self.superadmin,
            defaults={'organization': self.org, 'balance': Decimal('5000.00')}
        )

    def _create_finalized_month_usage(self, year, month, days, daily_charge, billable_bytes=1000000000, credits=1):
        for d in range(1, days + 1):
            u_date = date(year, month, d)
            StorageDailyUsage.objects.create(
                organization=self.org,
                usage_date=u_date,
                billable_bytes=billable_bytes,
                billable_gb=Decimal("1.00"),
                storage_credits=credits,
                storage_credit_size_bytes_snapshot=1_000_000_000,
                monthly_credit_price_snapshot=Decimal("20.00"),
                daily_credit_rate_snapshot=daily_charge,
                storage_charge=daily_charge,
                days_in_month=days,
                is_finalized=True
            )

    def test_1_and_2_and_3_previous_month_30_days_summed_and_quantized_once(self):
        """
        Tests 1, 2, 3:
        Previous month (November 30 days) with 30 daily charges of 0.666666666667
        Summed raw 12-decimal: 30 * 0.666666666667 = 20.000000000010 => quantize once => 20.00
        """
        # December 2026 invoice uses November 2026 storage (30 days)
        daily_rate = Decimal("0.666666666667")
        self._create_finalized_month_usage(2026, 11, 30, daily_rate)

        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 12, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("20.00"))
        self.assertEqual(billing_res['storage_finalized_days'], 30)
        self.assertEqual(billing_res['storage_credit_days'], 30)
        self.assertEqual(billing_res['storage_billable_bytes_days'], 30 * 1_000_000_000)
        self.assertTrue(billing_res['is_complete'])
        self.assertTrue(billing_res['is_billable'])

    def test_4_zero_storage_complete_month(self):
        """
        Test 4: Zero storage complete month yields 0.00 charge.
        """
        self._create_finalized_month_usage(2026, 11, 30, Decimal("0.00"), billable_bytes=0, credits=0)
        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 12, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("0.00"))
        self.assertEqual(billing_res['storage_finalized_days'], 30)
        self.assertEqual(billing_res['storage_credit_days'], 0)
        self.assertTrue(billing_res['is_complete'])

    def test_5_incomplete_previous_month_charge_zero_invoice_created(self):
        """
        Test 5: Incomplete previous month (e.g. only 29 of 30 days finalized)
        => storage_charge = 0.00, storage_finalized_days = 29, seat/module invoice created normally.
        """
        # Create only 29 days for November (missing Nov 30)
        daily_rate = Decimal("0.666666666667")
        for d in range(1, 30):
            StorageDailyUsage.objects.create(
                organization=self.org,
                usage_date=date(2026, 11, d),
                billable_bytes=1_000_000_000,
                billable_gb=Decimal("1.00"),
                storage_credits=1,
                storage_credit_size_bytes_snapshot=1_000_000_000,
                monthly_credit_price_snapshot=Decimal("20.00"),
                daily_credit_rate_snapshot=daily_rate,
                storage_charge=daily_rate,
                days_in_month=30,
                is_finalized=True
            )

        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 12, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("0.00"))
        self.assertEqual(billing_res['storage_finalized_days'], 29)
        self.assertFalse(billing_res['is_complete'])

        # Run sweep on Dec 1 => invoice created with seat/modules, storage charge 0.00
        fake_now = datetime(2026, 12, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 12, 1)).first()
        self.assertIsNotNone(inv)
        # Seats (2 * 50 = 100) + Att (2 * 99 = 198) + Proj (2 * 56 = 112) = 410.00
        self.assertEqual(inv.amount, Decimal("410.00"))
        self.assertEqual(inv.storage_charge_snapshot, Decimal("0.00"))
        self.assertEqual(inv.storage_finalized_days_snapshot, 29)

    def test_6_september_grace_period_october_invoice_zero_charge(self):
        """
        Test 6: September usage is grace period (free). October 2026 invoice has storage charge 0.00.
        """
        # Even if 30 days exist in September
        self._create_finalized_month_usage(2026, 9, 30, Decimal("0.666666666667"))

        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 10, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("0.00"))
        self.assertFalse(billing_res['is_billable'])

        fake_now = datetime(2026, 10, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 10, 1)).first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.amount, Decimal("410.00"))
        self.assertEqual(inv.storage_charge_snapshot, Decimal("0.00"))

    def test_7_october_full_month_november_invoice_charges_storage(self):
        """
        Test 7: October is the first billable usage month. November 2026 invoice charges storage.
        """
        # 31 days in October: daily rate = 20 / 31 = 0.645161290323
        daily_rate = Decimal("20.00") / Decimal("31")
        self._create_finalized_month_usage(2026, 10, 31, daily_rate)

        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 11, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("20.00"))
        self.assertTrue(billing_res['is_billable'])
        self.assertTrue(billing_res['is_complete'])

        fake_now = datetime(2026, 11, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertIsNotNone(inv)
        # Seats/modules = 410.00 + storage = 20.00 => 430.00
        self.assertEqual(inv.amount, Decimal("430.00"))
        self.assertEqual(inv.storage_charge_snapshot, Decimal("20.00"))
        self.assertEqual(inv.storage_finalized_days_snapshot, 31)

    def test_8_billing_flag_false_yields_zero_charge(self):
        """
        Test 8: When storage_billing_enabled = False, storage charge is 0.00.
        """
        self.g_settings.storage_billing_enabled = False
        self.g_settings.save()

        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))
        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 11, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("0.00"))

    def test_9_billing_flag_true_adds_charge(self):
        """
        Test 9: When storage_billing_enabled = True, storage charge is added to total.
        """
        self.g_settings.storage_billing_enabled = True
        self.g_settings.save()

        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))
        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2026, 11, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("20.00"))

    def test_10_and_11_invoice_rerun_no_double_add_and_immutable(self):
        """
        Tests 10 & 11: Task rerun does not double add storage; existing issued invoice remains immutable.
        """
        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))

        fake_now = datetime(2026, 11, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv1 = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertEqual(inv1.amount, Decimal("430.00"))

        # Rerun sweep later that day
        fake_now_noon = datetime(2026, 11, 1, 12, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now_noon):
            sweep_workspace_subscriptions()

        inv2 = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertEqual(inv1.id, inv2.id)
        self.assertEqual(inv2.amount, Decimal("430.00"))
        self.assertEqual(inv2.storage_charge_snapshot, Decimal("20.00"))

    def test_12_and_13_wallet_debit_uses_combined_invoice_total_and_no_separate_transaction(self):
        """
        Tests 12 & 13: Wallet settlement debits combined invoice total (430.00) in single transaction.
        No separate storage wallet transaction is created.
        """
        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))

        fake_now = datetime(2026, 11, 5, 12, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertTrue(inv.is_paid)

        # Check wallet
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5000.00") - Decimal("430.00"))

        # Only one debit transaction exists
        txs = WalletTransaction.objects.filter(wallet=self.wallet, transactionType='Debit')
        self.assertEqual(txs.count(), 1)
        self.assertEqual(txs.first().amount, Decimal("430.00"))

    def test_14_oldest_complete_invoice_no_partial_debit(self):
        """
        Test 14: If wallet balance is less than combined invoice total, no partial debit occurs.
        """
        self.wallet.balance = Decimal("400.00")  # Less than 430.00
        self.wallet.save()

        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))

        fake_now = datetime(2026, 11, 5, 12, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertFalse(inv.is_paid)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400.00"))  # Untouched

    def test_15_january_invoice_uses_december_storage(self):
        """
        Test 15: January 2027 invoice correctly aggregates December 2026 storage (31 days).
        """
        daily_rate = Decimal("20.00") / Decimal("31")
        self._create_finalized_month_usage(2026, 12, 31, daily_rate)

        billing_res = BillingService.get_previous_month_storage_billing(self.org, date(2027, 1, 1))
        self.assertEqual(billing_res['storage_charge'], Decimal("20.00"))
        self.assertEqual(billing_res['storage_usage_month'], date(2026, 12, 1))
        self.assertEqual(billing_res['storage_finalized_days'], 31)

    def test_16_data_retention_invoice_untouched(self):
        """
        Test 16: DATA_RETENTION invoice (for Restricted workspace) does not charge storage.
        """
        self.org_settings.subscriptionStatus = 'Restricted'
        self.org_settings.save()

        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))

        fake_now = datetime(2026, 11, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertEqual(inv.invoice_type, 'DATA_RETENTION')
        self.assertEqual(inv.amount, Decimal(str(self.g_settings.monthly_data_rent)))
        self.assertEqual(inv.storage_charge_snapshot, Decimal("0.00"))

    def test_17_tax_remains_zero(self):
        """
        Test 17: Storage charge adds to subtotal and amount without adding tax.
        """
        self._create_finalized_month_usage(2026, 10, 31, Decimal("20.00") / Decimal("31"))

        fake_now = datetime(2026, 11, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertEqual(inv.tax_percentage_snapshot, Decimal("0.00"))
        self.assertEqual(inv.tax_amount_snapshot, Decimal("0.00"))
        self.assertEqual(inv.amount, inv.subtotal_snapshot)

    def test_18_snapshot_fields_saved_correctly(self):
        """
        Test 18: All 5 storage snapshot fields are correctly stored on the invoice.
        """
        self._create_finalized_month_usage(2026, 10, 31, Decimal("40.00") / Decimal("31"), billable_bytes=2_000_000_000, credits=2)

        fake_now = datetime(2026, 11, 1, 0, 15, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            sweep_workspace_subscriptions()

        inv = MonthlyInvoice.objects.filter(organization=self.org, billing_month=date(2026, 11, 1)).first()
        self.assertEqual(inv.storage_usage_month_snapshot, date(2026, 10, 1))
        self.assertEqual(inv.storage_charge_snapshot, Decimal("40.00"))
        self.assertEqual(inv.storage_finalized_days_snapshot, 31)
        self.assertEqual(inv.storage_billable_bytes_days_snapshot, 31 * 2_000_000_000)
        self.assertEqual(inv.storage_credit_days_snapshot, 31 * 2)

    def test_19_invoice_schedule_ordering_verification(self):
        """
        Test 19: Verify subscription sweep runs at minute 15, after storage audit at minute 5.
        """
        from cubelogs.settings import CELERY_BEAT_SCHEDULE
        sweep_schedule = CELERY_BEAT_SCHEDULE['sweep-workspace-subscriptions-daily']['schedule']
        storage_schedule = CELERY_BEAT_SCHEDULE['audit-workspace-storage-daily']['schedule']

        # Celery crontab minute attribute is a set of minutes
        self.assertEqual(sweep_schedule.minute, {15})
        self.assertEqual(storage_schedule.minute, {5})
