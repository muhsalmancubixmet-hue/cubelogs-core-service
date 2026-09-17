from decimal import Decimal
import datetime
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role
from attendance.models import AttendancePeriod, AttendancePeriodEmployeeSnapshot
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    PayrollPeriod,
    PayrollEmployeeSnapshot,
    Payslip,
    SalaryPayment,
)
from payroll.services import (
    calculate_payroll_period,
    finalize_payroll_period,
    reopen_payroll_period,
    record_salary_payment,
    bulk_record_salary_payments,
    void_salary_payment,
)


class SalaryPaymentSystemTests(APITestCase):
    def setUp(self):
        settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a", settings=settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='payroll', enabled=True)

        settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b", settings=settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='payroll', enabled=True)

        admin_role = Role.objects.filter(slug='admin').first()
        emp_role = Role.objects.filter(slug='employee').first()

        # Admin Org A
        self.admin_a = Employee.objects.create_user(
            email="admin_a@test.com", password="password123",
            organization=self.org_a, role=admin_role, is_staff=True, isSuperAdmin=True
        )
        # Employee 1 Org A
        self.emp1_a = Employee.objects.create_user(
            email="emp1_a@test.com", password="password123",
            organization=self.org_a, role=emp_role, first_name="Emp1", last_name="A"
        )
        # Employee 2 Org A
        self.emp2_a = Employee.objects.create_user(
            email="emp2_a@test.com", password="password123",
            organization=self.org_a, role=emp_role, first_name="Emp2", last_name="A"
        )

        # Admin Org B
        self.admin_b = Employee.objects.create_user(
            email="admin_b@test.com", password="password123",
            organization=self.org_b, role=admin_role, is_staff=True, isSuperAdmin=True
        )

        # Basic Salary Structure for Emp 1 & 2 in Org A
        basic = SalaryComponent.objects.create(
            organization=self.org_a, name="Basic", code="BASIC",
            component_type="Earning", is_proratable=True
        )

        for emp, gross in [(self.emp1_a, Decimal("50000.00")), (self.emp2_a, Decimal("60000.00"))]:
            struct = EmployeeSalaryStructure.objects.create(
                organization=self.org_a, employee=emp,
                effective_from=datetime.date(2026, 1, 1),
                compensation_type="MONTHLY", gross_salary=gross,
                base_net_salary=gross, is_active=True
            )
            EmployeeSalaryComponent.objects.create(salary_structure=struct, salary_component=basic, amount=gross)

        self.client.force_authenticate(user=self.admin_a)

    def _setup_finalized_payroll(self):
        # Create finalized attendance period for Org A
        att, _ = AttendancePeriod.objects.get_or_create(
            organization=self.org_a, year=2026, month=8,
            defaults={'status': 'Finalized', 'current_revision': 1, 'finalized_at': timezone.now(), 'finalized_by': self.admin_a}
        )
        if att.status != 'Finalized':
            att.status = 'Finalized'
            att.save()

        for emp in [self.emp1_a, self.emp2_a]:
            AttendancePeriodEmployeeSnapshot.objects.get_or_create(
                attendance_period=att, revision=1, employee=emp,
                defaults={
                    'is_current': True,
                    'employee_name': emp.get_full_name() or emp.email,
                    'working_days': 22,
                    'payable_attendance_units': Decimal('22.00'),
                    'present_days': Decimal('22.00'),
                    'paid_leave_days': Decimal('0.00'),
                    'unpaid_leave_days': Decimal('0.00'),
                    'absent_days': 0
                }
            )

        # Calculate & Finalize Aug 2026 for Org A
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        period = finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)
        return period

    def test_01_cannot_pay_draft_or_calculated_payroll(self):
        att, _ = AttendancePeriod.objects.get_or_create(
            organization=self.org_a, year=2026, month=8,
            defaults={'status': 'Finalized', 'current_revision': 1, 'finalized_at': timezone.now(), 'finalized_by': self.admin_a}
        )
        for emp in [self.emp1_a, self.emp2_a]:
            AttendancePeriodEmployeeSnapshot.objects.get_or_create(
                attendance_period=att, revision=1, employee=emp,
                defaults={
                    'is_current': True,
                    'employee_name': emp.get_full_name() or emp.email,
                    'working_days': 22,
                    'payable_attendance_units': Decimal('22.00'),
                    'present_days': Decimal('22.00'),
                    'paid_leave_days': Decimal('0.00'),
                    'unpaid_leave_days': Decimal('0.00'),
                    'absent_days': 0
                }
            )
        # Create draft period
        period = calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        # Reopen to draft
        period.status = 'Draft'
        period.save()

        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period).first()
        url = "/api/v1/payroll/payments/pay-employee/"
        res = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "BankTransfer"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Finalized", str(res.data))

    def test_02_can_pay_finalized_payroll(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()

        url = "/api/v1/payroll/payments/pay-employee/"
        res = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "BankTransfer", "transaction_reference": "UTR123456"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], "Paid")
        self.assertEqual(Decimal(str(res.data["paid_amount"])), snap.net_payable)
        self.assertEqual(res.data["transaction_reference"], "UTR123456")

    def test_03_paid_amount_equals_net_payable_and_ignores_fake_amount(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()

        url = "/api/v1/payroll/payments/pay-employee/"
        # Attempt to pass a fake lower amount ₹1.00
        res = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "Cash", "paid_amount": "1.00"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Amount must strictly equal snap.net_payable
        self.assertEqual(Decimal(str(res.data["paid_amount"])), snap.net_payable)

    def test_04_future_payment_date_rejected(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period).first()

        future_date = (timezone.now().date() + datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        url = "/api/v1/payroll/payments/pay-employee/"
        res = self.client.post(url, {"snapshot_id": snap.id, "paid_at": future_date, "payment_method": "BankTransfer"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("future", str(res.data))

    def test_05_duplicate_payment_rejected(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()

        url = "/api/v1/payroll/payments/pay-employee/"
        res1 = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "BankTransfer"})
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Second payment request must be rejected
        res2 = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "BankTransfer"})
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already", str(res2.data).lower())

    def test_06_void_requires_reason_and_preserves_history(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()

        # Pay
        payment = record_salary_payment(self.org_a, snap.id, "2026-08-28", "BankTransfer", "REF100", "Initial", self.admin_a)

        # Void without valid reason (min 5 chars)
        url_void = f"/api/v1/payroll/payments/{payment.id}/void/"
        res_fail = self.client.post(url_void, {"void_reason": "abc"})
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)

        # Void with valid reason
        res_ok = self.client.post(url_void, {"void_reason": "Wrong bank account number selected"})
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ok.data["status"], "Voided")

        # History check
        payment.refresh_from_db()
        self.assertEqual(payment.status, "Voided")
        self.assertEqual(payment.void_reason, "Wrong bank account number selected")
        self.assertIsNotNone(payment.voided_at)

    def test_07_re_payment_allowed_after_void(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()

        # 1. Pay
        payment1 = record_salary_payment(self.org_a, snap.id, "2026-08-28", "BankTransfer", "REF1", "", self.admin_a)
        # 2. Void
        void_salary_payment(self.org_a, payment1.id, "Entered incorrect reference code", self.admin_a)

        # 3. Pay again
        url = "/api/v1/payroll/payments/pay-employee/"
        res2 = self.client.post(url, {"snapshot_id": snap.id, "paid_at": "2026-08-28", "payment_method": "Cheque", "transaction_reference": "CHQ555"})
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.data["status"], "Paid")

    def test_08_normal_employee_cannot_record_or_void_payment(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()
        payment = record_salary_payment(self.org_a, snap.id, "2026-08-28", "BankTransfer", "REF100", "", self.admin_a)

        # Authenticate as normal employee
        self.client.force_authenticate(user=self.emp1_a)

        url_pay = "/api/v1/payroll/payments/pay-employee/"
        res_pay = self.client.post(url_pay, {"snapshot_id": snap.id, "paid_at": "2026-08-28"})
        self.assertEqual(res_pay.status_code, status.HTTP_403_FORBIDDEN)

        url_void = f"/api/v1/payroll/payments/{payment.id}/void/"
        res_void = self.client.post(url_void, {"void_reason": "Trying to void self payment"})
        self.assertEqual(res_void.status_code, status.HTTP_403_FORBIDDEN)

    def test_09_cross_tenant_payment_blocked(self):
        period = self._setup_finalized_payroll()
        snap_a = PayrollEmployeeSnapshot.objects.filter(payroll_period=period).first()

        # Admin B tries to pay Org A snapshot
        self.client.force_authenticate(user=self.admin_b)
        url = "/api/v1/payroll/payments/pay-employee/"
        res = self.client.post(url, {"snapshot_id": snap_a.id, "paid_at": "2026-08-28"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_10_bulk_payment_works(self):
        period = self._setup_finalized_payroll()
        url = "/api/v1/payroll/payments/bulk-pay/"
        res = self.client.post(url, {
            "year": 2026,
            "month": 8,
            "paid_at": "2026-08-28",
            "payment_method": "BankTransfer",
            "transaction_reference": "BATCH_AUG_2026"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["paid_count"], 2)

        # Verify payments created in DB
        paid_count = SalaryPayment.objects.filter(payroll_period=period, status='Paid').count()
        self.assertEqual(paid_count, 2)

    def test_11_payroll_reopen_blocked_when_active_payments_exist(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period).first()
        record_salary_payment(self.org_a, snap.id, "2026-08-28", "BankTransfer", "", "", self.admin_a)

        # Attempt to reopen period
        with self.assertRaises(Exception):
            reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Need to fix salary components")

    def test_12_payslip_salary_figures_unchanged(self):
        period = self._setup_finalized_payroll()
        snap = PayrollEmployeeSnapshot.objects.filter(payroll_period=period, employee=self.emp1_a).first()
        payslip = Payslip.objects.get(payroll_period=period, employee=self.emp1_a)

        net_before = snap.net_payable
        # Pay salary
        record_salary_payment(self.org_a, snap.id, "2026-08-28", "BankTransfer", "", "", self.admin_a)

        snap.refresh_from_db()
        payslip.refresh_from_db()

        self.assertEqual(snap.net_payable, net_before)
        self.assertEqual(payslip.payroll_snapshot.net_payable, net_before)

    def test_13_org_settings_payroll_schedule_fields_and_validation(self):
        self.client.force_authenticate(user=self.admin_a)
        res = self.client.put('/api/settings/', {
            "payroll_processing_day": 25,
            "salary_payment_day": 2,
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.org_a.settings.refresh_from_db()
        self.assertEqual(self.org_a.settings.payroll_processing_day, 25)
        self.assertEqual(self.org_a.settings.salary_payment_day, 2)

    def test_14_invalid_payroll_schedule_day_rejected(self):
        self.client.force_authenticate(user=self.admin_a)
        res = self.client.put('/api/settings/', {
            "payroll_processing_day": 32,
            "salary_payment_day": 0,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_15_payroll_payment_routes_resolve_both_api_and_v1(self):
        from django.urls import resolve
        from payroll.views import RecordSalaryPaymentView, BulkRecordSalaryPaymentView, VoidSalaryPaymentView

        # Verify /api/ routes (frontend consumer)
        self.assertEqual(resolve('/api/payroll/payments/pay-employee/').func.view_class, RecordSalaryPaymentView)
        self.assertEqual(resolve('/api/payroll/payments/bulk-pay/').func.view_class, BulkRecordSalaryPaymentView)
        self.assertEqual(resolve('/api/payroll/payments/1/void/').func.view_class, VoidSalaryPaymentView)

        # Verify /api/v1/ routes (v1 API / test consumer)
        self.assertEqual(resolve('/api/v1/payroll/payments/pay-employee/').func.view_class, RecordSalaryPaymentView)
        self.assertEqual(resolve('/api/v1/payroll/payments/bulk-pay/').func.view_class, BulkRecordSalaryPaymentView)
        self.assertEqual(resolve('/api/v1/payroll/payments/1/void/').func.view_class, VoidSalaryPaymentView)


class AttendanceControlsPayrollEntitlementTests(APITestCase):
    def setUp(self):
        settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Entitlement Org", subdomain="ent-org", settings=settings)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)

        admin_role = Role.objects.filter(slug='admin').first()
        emp_role = Role.objects.filter(slug='employee').first()

        self.admin = Employee.objects.create_user(
            email="ent_admin@test.com", password="password123",
            organization=self.org, role=admin_role, is_staff=True, isSuperAdmin=True
        )
        self.emp = Employee.objects.create_user(
            email="ent_emp@test.com", password="password123",
            organization=self.org, role=emp_role
        )

    def test_01_attendance_off_blocks_attendance_and_payroll(self):
        """Attendance OFF -> Attendance and Payroll endpoints return 403."""
        OrganizationModule.objects.update_or_create(
            organization=self.org, module_id='attendance',
            defaults={'enabled': False}
        )
        self.client.force_authenticate(user=self.admin)

        res_att = self.client.get('/api/v1/attendance/')
        self.assertEqual(res_att.status_code, status.HTTP_403_FORBIDDEN)

        res_pay = self.client.get('/api/v1/payroll/periods/')
        self.assertEqual(res_pay.status_code, status.HTTP_403_FORBIDDEN)

    def test_02_attendance_off_leaves_project_entitlement_independent(self):
        """Attendance OFF -> Projects endpoint remains accessible."""
        OrganizationModule.objects.update_or_create(
            organization=self.org, module_id='attendance',
            defaults={'enabled': False}
        )
        self.client.force_authenticate(user=self.admin)

        res_proj = self.client.get('/api/v1/projects/')
        self.assertEqual(res_proj.status_code, status.HTTP_200_OK)

    def test_03_attendance_on_with_permission_allows_payroll(self):
        """Attendance ON + permission -> Payroll accessible (200)."""
        OrganizationModule.objects.update_or_create(
            organization=self.org, module_id='attendance',
            defaults={'enabled': True}
        )
        self.client.force_authenticate(user=self.admin)

        res_pay = self.client.get('/api/v1/payroll/periods/')
        self.assertEqual(res_pay.status_code, status.HTTP_200_OK)

    def test_04_attendance_on_without_payroll_permission_blocks_with_403(self):
        """Attendance ON without payroll permission -> 403 Forbidden."""
        OrganizationModule.objects.update_or_create(
            organization=self.org, module_id='attendance',
            defaults={'enabled': True}
        )
        self.client.force_authenticate(user=self.emp)

        res_pay = self.client.get('/api/v1/payroll/periods/')
        self.assertEqual(res_pay.status_code, status.HTTP_403_FORBIDDEN)



