# --------------------------------------------------------------------------------
#       Focused Production Billing & Email Lifecycle Test Suite
# --------------------------------------------------------------------------------

from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError
from django.core import mail

from core.models import Organization, OrgSettings, EmailLog
from subscribers.models import MonthlyInvoice, Wallet, WalletTransaction, GlobalBillingSettings
from users.models import Employee, OrganizationMembership, Role
from company.api.v1.services import BillingService
from company.tasks import sweep_workspace_subscriptions
from core.tasks import send_transactional_email_task, send_email_task, sanitize_email_log_content


class ProductionBillingWorkflowTestCase(TestCase):
    def setUp(self):
        import uuid
        uid = uuid.uuid4().hex[:6]
        # Create Org A
        self.settings_a = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org_a = Organization.objects.create(name=f"Org Alpha {uid}", subdomain=f"alpha-{uid}", settings=self.settings_a)

        # Create Org B
        self.settings_b = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org_b = Organization.objects.create(name=f"Org Beta {uid}", subdomain=f"beta-{uid}", settings=self.settings_b)

        # Role
        self.role_admin = Role.objects.create(name="Company Admin", slug="company-admin", organization=self.org_a)

        # Global Admin User
        self.user_admin = Employee.objects.create(
            username="admin_alpha",
            email="admin@alpha.com",
            first_name="Alpha",
            last_name="Admin",
            isSuperAdmin=True,
            is_active=True
        )

        # Membership in Org A
        self.mem_admin_a = OrganizationMembership.objects.create(
            user=self.user_admin,
            organization=self.org_a,
            role=self.role_admin,
            employment_status='Active',
            is_active_in_org=True
        )

        # Global Admin User B
        self.user_admin_b = Employee.objects.create(
            username="admin_beta",
            email="admin@beta.com",
            first_name="Beta",
            last_name="Admin",
            isSuperAdmin=True,
            is_active=True
        )

        # Wallet for Org A
        self.wallet_a = Wallet.objects.create(
            organization=self.org_a,
            employee=self.user_admin,
            balance=Decimal('0.00')
        )

        # Wallet for Org B (pre-funded so org_b does not trigger unexpected unpaid sweeps)
        self.wallet_b = Wallet.objects.create(
            organization=self.org_b,
            employee=self.user_admin_b,
            balance=Decimal('1000.00')
        )

        # Ensure Global Settings exist
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.employee_seat_price = Decimal('100.00')
        self.g_settings.monthly_subscription_price = Decimal('100.00')
        self.g_settings.tax_percentage = Decimal('0.00')
        self.g_settings.save()

    def test_01_organization_membership_seat_count_used(self):
        """TEST 1: OrganizationMembership seat count used instead of Employee.organization."""
        # Legacy Employee.organization set to None
        emp1 = Employee.objects.create(username="e1", email="e1@alpha.com", is_active=True)
        OrganizationMembership.objects.create(
            user=emp1, organization=self.org_a, employment_status='Active', is_active_in_org=True
        )

        count = BillingService.get_billable_memberships_qs(self.org_a).count()
        self.assertEqual(count, 1)

    def test_02_multi_org_user_counted_independently(self):
        """TEST 2: Multi-org user counted independently in each active organization membership."""
        user_multi = Employee.objects.create(username="multi", email="multi@example.com", is_active=True)
        OrganizationMembership.objects.create(user=user_multi, organization=self.org_a, employment_status='Active', is_active_in_org=True)
        OrganizationMembership.objects.create(user=user_multi, organization=self.org_b, employment_status='Active', is_active_in_org=True)

        self.assertEqual(BillingService.get_billable_memberships_qs(self.org_a).count(), 1)
        self.assertEqual(BillingService.get_billable_memberships_qs(self.org_b).count(), 1)

    def test_03_inactive_deleted_exited_membership_not_billed(self):
        """TEST 3: Inactive/deleted/exited membership not billed."""
        emp_inactive = Employee.objects.create(username="e_in", email="e_in@alpha.com", is_active=True)
        OrganizationMembership.objects.create(
            user=emp_inactive, organization=self.org_a, employment_status='Resigned', last_working_date=date.today() - timedelta(days=1), is_active_in_org=True
        )

        emp_deleted = Employee.objects.create(username="e_del", email="e_del@alpha.com", is_active=True)
        OrganizationMembership.objects.create(
            user=emp_deleted, organization=self.org_a, employment_status='Active', is_active_in_org=True, is_deleted=True
        )

        self.assertEqual(BillingService.get_billable_memberships_qs(self.org_a).count(), 0)

    def test_04_employee_added_mid_month_does_not_modify_existing_invoice(self):
        """TEST 4: Employee added mid-month does not modify existing invoice."""
        inv = MonthlyInvoice.objects.create(
            organization=self.org_a,
            billing_month=date(2026, 9, 1),
            amount=Decimal('200.00'),
            employee_count_snapshot=1
        )

        emp_new = Employee.objects.create(username="e_new", email="e_new@alpha.com", is_active=True)
        OrganizationMembership.objects.create(user=emp_new, organization=self.org_a, employment_status='Active', is_active_in_org=True)

        inv.refresh_from_db()
        self.assertEqual(inv.amount, Decimal('200.00'))
        self.assertEqual(inv.employee_count_snapshot, 1)

    def test_06_current_month_missing_invoice_recovered_after_day_1(self):
        """TEST 6: Current-month missing invoice is recovered after Day 1."""
        with patch('company.tasks.EmailService.send_transactional_email'):
            sweep_workspace_subscriptions()

        billing_m = timezone.now().date().replace(day=1)
        inv_exists = MonthlyInvoice.objects.filter(organization=self.org_a, billing_month=billing_m).exists()
        self.assertTrue(inv_exists)

    def test_07_second_invoice_for_same_org_month_type_rejected(self):
        """TEST 7: Second invoice for same organization/month/type cannot be created."""
        MonthlyInvoice.objects.create(
            organization=self.org_a,
            billing_month=date(2026, 9, 1),
            invoice_type='SUBSCRIPTION',
            amount=Decimal('100.00')
        )
        with self.assertRaises(IntegrityError):
            MonthlyInvoice.objects.create(
                organization=self.org_a,
                billing_month=date(2026, 9, 1),
                invoice_type='SUBSCRIPTION',
                amount=Decimal('100.00')
            )

    def test_08_invoice_snapshot_remains_unchanged(self):
        """TEST 8: Invoice snapshot remains unchanged after global pricing changes."""
        inv = MonthlyInvoice.objects.create(
            organization=self.org_a,
            billing_month=date(2026, 9, 1),
            amount=Decimal('200.00'),
            employee_unit_price_snapshot=Decimal('100.00')
        )
        self.g_settings.employee_seat_price = Decimal('500.00')
        self.g_settings.save()

        inv.refresh_from_db()
        self.assertEqual(inv.employee_unit_price_snapshot, Decimal('100.00'))

    def test_10_invoice_email_uses_correct_billing_contact(self):
        """TEST 10: Invoice email uses correct billing contact if set."""
        self.settings_a.billing_email = "accounts@alpha.com"
        self.settings_a.save()

        recipient = BillingService.get_billing_recipient_email(self.org_a)
        self.assertEqual(recipient, "accounts@alpha.com")

    def test_11_billing_email_fallback_uses_correct_admin_membership(self):
        """TEST 11: Billing email fallback uses correct organization membership admin."""
        self.settings_a.billing_email = ""
        self.settings_a.save()

        recipient = BillingService.get_billing_recipient_email(self.org_a)
        self.assertEqual(recipient, "admin@alpha.com")

    def test_13_wallet_top_up_immediately_processes_dues(self):
        """TEST 13: Wallet top-up immediately processes dues."""
        inv = MonthlyInvoice.objects.create(
            organization=self.org_a, billing_month=date(2026, 8, 1), amount=Decimal('200.00'), is_paid=False
        )

        BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('500.00'), details="Test credit")

        inv.refresh_from_db()
        self.wallet_a.refresh_from_db()
        self.assertTrue(inv.is_paid)
        self.assertEqual(self.wallet_a.balance, Decimal('300.00'))

    def test_14_oldest_complete_invoice_first(self):
        """TEST 14: Oldest complete invoice first (Jan 800, Feb 900, Wallet 1000 -> Jan paid, Feb unpaid, 200 remaining)."""
        inv_jan = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('800.00'), is_paid=False)
        inv_feb = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 2, 1), amount=Decimal('900.00'), is_paid=False)

        BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('1000.00'), details="Test credit")

        inv_jan.refresh_from_db()
        inv_feb.refresh_from_db()
        self.wallet_a.refresh_from_db()

        self.assertTrue(inv_jan.is_paid)
        self.assertFalse(inv_feb.is_paid)
        self.assertEqual(self.wallet_a.balance, Decimal('200.00'))

    def test_15_no_partial_invoice(self):
        """TEST 15: No partial invoice (invoice 800, wallet 300 -> 0 deducted)."""
        inv = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('800.00'), is_paid=False)

        BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('300.00'), details="Test credit")

        inv.refresh_from_db()
        self.wallet_a.refresh_from_db()

        self.assertFalse(inv.is_paid)
        self.assertEqual(self.wallet_a.balance, Decimal('300.00'))

    def test_16_mock_topup_idempotent_and_tenant_safe(self):
        """TEST 16: Mock topup is idempotent and rejects tenant mismatch."""
        session_id = "mock_test_12345"

        # First credit
        w1, tx1, created1 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=Decimal('500.00'),
            session_id=session_id,
            target_org_id=self.org_a.id
        )
        self.assertTrue(created1)
        self.assertEqual(w1.balance, Decimal('500.00'))

        # Duplicate credit attempt
        w2, tx2, created2 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=Decimal('500.00'),
            session_id=session_id,
            target_org_id=self.org_a.id
        )
        self.assertFalse(created2)
        self.assertEqual(w2.balance, Decimal('500.00'))  # Balance remains 500, not 1000

        # Tenant mismatch fails
        with self.assertRaises(ValueError):
            BillingService.credit_wallet_from_payment(
                wallet_id=self.wallet_a.id,
                amount_dec=Decimal('500.00'),
                session_id="mock_other",
                target_org_id=self.org_b.id
            )

    def test_17_arbitrary_wallet_save_does_not_trigger_dues(self):
        """TEST 17: Metadata-only Wallet.save() does NOT process dues automatically."""
        inv = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('100.00'), is_paid=False)
        self.wallet_a.balance = Decimal('500.00')
        self.wallet_a.save()

        inv.refresh_from_db()
        self.assertFalse(inv.is_paid)  # Save alone did not pay invoice

        BillingService.process_outstanding_dues(self.wallet_a)
        inv.refresh_from_db()
        self.assertTrue(inv.is_paid)

    def test_17_remaining_unpaid_invoice_prevents_active_restoration(self):
        """TEST 17: Remaining unpaid invoice prevents incorrect Active restoration."""
        inv_jan = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('800.00'), is_paid=False)
        inv_feb = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 2, 1), amount=Decimal('900.00'), is_paid=False)

        self.settings_a.subscriptionStatus = 'Restricted'
        self.settings_a.save()

        self.wallet_a.balance = Decimal('1000.00')
        BillingService.process_outstanding_dues(self.wallet_a)

        self.settings_a.refresh_from_db()
        self.assertEqual(self.settings_a.subscriptionStatus, 'Restricted')

    def test_18_all_overdue_dues_cleared_restores_workspace(self):
        """TEST 18: All overdue dues cleared restores workspace to Active."""
        inv_jan = MonthlyInvoice.objects.create(organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('800.00'), is_paid=False)

        self.settings_a.subscriptionStatus = 'Restricted'
        self.settings_a.save()

        BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('1000.00'))

        updated_settings = OrgSettings.objects.get(id=self.settings_a.id)
        self.assertEqual(updated_settings.subscriptionStatus, 'Active')

    def test_21_emaillog_does_not_persist_raw_password_or_token(self):
        """TEST 21: EmailLog does not persist raw password/token."""
        raw_body = "Hello! Your temporary Password: Secret123. Verification token=abc.123.xyz"
        sanitized = sanitize_email_log_content(raw_body)

        self.assertNotIn("Secret123", sanitized)
        self.assertNotIn("abc.123.xyz", sanitized)
        self.assertIn("[PROTECTED]", sanitized)
        self.assertIn("[PROTECTED_TOKEN]", sanitized)

    def test_celery_broker_payload_does_not_contain_secrets(self):
        """ASSERT: Credential-bearing onboarding email is NOT serialized into Celery task args."""
        with patch('core.tasks.send_transactional_email_task.delay') as mock_delay:
            with patch('core.tasks.send_mail'):
                from core.tasks import EmailService
                EmailService.send_transactional_email(
                    recipient="onboard@alpha.com",
                    subject="Welcome",
                    html_content="<h1>Your Password: SuperSecret123</h1>",
                    template_type='WELCOME',
                    password="SuperSecret123"
                )
                # Ensure Celery delay was NOT called with raw secret payload
                mock_delay.assert_not_called()

    def test_payment_failure_email_idempotency_and_next_month_support(self):
        """ASSERT: Failure email queued once per billing cycle; next month can send its own email."""
        inv_jan = MonthlyInvoice.objects.create(
            organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('500.00'), is_paid=False
        )

        with patch('core.tasks.EmailService.queue_and_send_email') as mock_email:
            # Sweep 1: triggers payment failure
            with patch('company.tasks.timezone.now') as mock_now:
                mock_now.return_value = timezone.make_aware(timezone.datetime(2026, 1, 10, 12, 0))
                sweep_workspace_subscriptions()
                first_call_count = mock_email.call_count

            inv_jan.refresh_from_db()
            self.assertTrue(inv_jan.payment_failed_email_sent)

            # Sweep 2 (Simulate worker restart / cache clear):
            with patch('company.tasks.timezone.now') as mock_now:
                mock_now.return_value = timezone.make_aware(timezone.datetime(2026, 1, 10, 18, 0))
                sweep_workspace_subscriptions()
                self.assertEqual(mock_email.call_count, first_call_count)

            # Next month (Feb) invoice failure:
            inv_feb = MonthlyInvoice.objects.create(
                organization=self.org_a, billing_month=date(2026, 2, 1), amount=Decimal('500.00'), is_paid=False
            )
            with patch('company.tasks.timezone.now') as mock_now:
                mock_now.return_value = timezone.make_aware(timezone.datetime(2026, 2, 10, 12, 0))
                sweep_workspace_subscriptions()
                self.assertGreater(mock_email.call_count, first_call_count)

    def test_restriction_and_reactivation_email_idempotency(self):
        """ASSERT: Restriction and reactivation emails send exactly once per billing event."""
        inv = MonthlyInvoice.objects.create(
            organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('500.00'), is_paid=False
        )

        # Restriction twice
        self.settings_a.subscriptionStatus = 'Unpaid'
        self.settings_a.save()

        with patch('company.tasks.Organization.objects.all', return_value=[self.org_a]):
            with patch('company.tasks.timezone.now') as mock_now:
                mock_now.return_value = timezone.make_aware(timezone.datetime(2026, 1, 20, 12, 0))
                with patch('core.tasks.EmailService.queue_and_send_email') as mock_email:
                    sweep_workspace_subscriptions()
                    c1 = mock_email.call_count
                    sweep_workspace_subscriptions()
                    self.assertEqual(mock_email.call_count, c1)

        inv.refresh_from_db()
        self.assertTrue(inv.restriction_email_sent)

        # Reactivation twice
        with patch('core.tasks.EmailService.queue_and_send_email') as mock_email:
            BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('1000.00'), details="Test credit")
            c2 = mock_email.call_count
            BillingService.process_outstanding_dues(self.wallet_a)
            self.assertEqual(mock_email.call_count, c2)

        inv.refresh_from_db()
        self.assertTrue(inv.reactivation_email_sent)

    def test_email_retries_no_duplicate_wallet_deduction(self):
        """ASSERT: Retrying dues processing or email delivery never duplicates wallet deduction."""
        inv = MonthlyInvoice.objects.create(
            organization=self.org_a, billing_month=date(2026, 1, 1), amount=Decimal('500.00'), is_paid=False
        )
        BillingService.credit_wallet_from_payment(self.wallet_a.id, Decimal('800.00'), details="Test credit")

        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('300.00'))

        # Retry dues processing
        BillingService.process_outstanding_dues(self.wallet_a)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('300.00'))

    def test_razorpay_credit_wallet_and_idempotency(self):
        """ASSERT: Razorpay wallet crediting works, records razorpay fields, and enforces idempotency."""
        order_id = "order_rzp_test_12345"
        payment_id = "pay_rzp_test_67890"
        signature = "dummy_signature_hash"

        # First credit
        w1, tx1, created1 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=Decimal('500.00'),
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
            target_org_id=self.org_a.id
        )
        self.assertTrue(created1)
        self.assertEqual(w1.balance, Decimal('500.00'))
        self.assertEqual(tx1.razorpay_order_id, order_id)
        self.assertEqual(tx1.razorpay_payment_id, payment_id)

        # Duplicate credit attempt with same razorpay_order_id
        w2, tx2, created2 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=Decimal('500.00'),
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
            target_org_id=self.org_a.id
        )
        self.assertFalse(created2)
        self.assertEqual(w2.balance, Decimal('500.00'))

    def test_razorpay_verify_payment_view_mock(self):
        """ASSERT: VerifyPaymentView correctly processes mock Razorpay payment verification."""
        from django.test import RequestFactory
        from subscribers.api.v1.views import VerifyPaymentView

        rf = RequestFactory()
        order_id = "mock_rzp_order_test_999"

        # Create pending transaction
        WalletTransaction.objects.create(
            wallet=self.wallet_a,
            amount=Decimal('250.00'),
            transactionType='Credit',
            success=False,
            razorpay_order_id=order_id,
            status='Pending',
            details='Pending topup'
        )

        request = rf.post(
            '/api/v1/subscribers/payment/verify/',
            data={
                'razorpay_order_id': order_id,
                'razorpay_payment_id': 'pay_mock_123',
                'payment_type': 'wallet'
            },
            content_type='application/json'
        )
        request.user = self.user_admin
        request.active_organization = self.org_a

        view = VerifyPaymentView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('250.00'))

