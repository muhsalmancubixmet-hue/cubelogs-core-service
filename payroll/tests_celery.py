# --------------------------------------------------------------------------------
#       Payroll & Subscribers Celery Async Task Tests
# --------------------------------------------------------------------------------

from datetime import date
from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings
from users.models import Employee, PermissionFlag, OrganizationMembership
from attendance.models import AttendancePeriod, AttendancePeriodEmployeeSnapshot
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    PayrollPeriod,
    Payslip,
)
from payroll.services import (
    assign_or_revise_salary_structure,
    calculate_payroll_period,
    finalize_payroll_period,
)
from payroll.tasks import (
    calculate_payroll_period_task,
    generate_bulk_payslip_zip_task,
    dispatch_task_safely as payroll_dispatch_safely,
)
from subscribers.models import SubscriptionPackage, SubscriberAccount, Wallet, WalletTransaction
from subscribers.tasks import (
    cascade_package_update_task,
    process_bulk_wallet_adjustments_task,
    dispatch_task_safely as subscribers_dispatch_safely,
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_STORE_EAGER_RESULT=True)
class CeleryBatchOperationsTests(TestCase):
    def setUp(self):
        self.perm_salary_manage, _ = PermissionFlag.objects.get_or_create(
            key='salary:manage', defaults={'name': 'Manage Salary', 'module': 'Salary'}
        )
        self.perm_payroll_view, _ = PermissionFlag.objects.get_or_create(
            key='payroll:view', defaults={'name': 'View Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_process, _ = PermissionFlag.objects.get_or_create(
            key='payroll:process', defaults={'name': 'Process Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_manage, _ = PermissionFlag.objects.get_or_create(
            key='payroll:manage', defaults={'name': 'Manage Payroll', 'module': 'Payroll'}
        )

        self.settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=True,
            payroll_currency='USD'
        )
        self.org = Organization.objects.create(
            name="Celery Test Org",
            subdomain="celerytest",
            settings=self.settings
        )

        self.admin = Employee.objects.create_user(
            email="celery_admin@test.com",
            username="celery_admin",
            password="testpassword123",
            first_name="Celery",
            last_name="Admin",
            organization=self.org,
            isSuperAdmin=True
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org,
            is_active_in_org=True
        )

        self.employee = Employee.objects.create_user(
            email="emp_celery@test.com",
            username="emp_celery",
            password="testpassword123",
            first_name="John",
            last_name="Doe",
            employee_code="EMP-0099",
            organization=self.org
        )
        OrganizationMembership.objects.create(
            user=self.employee,
            organization=self.org,
            is_active_in_org=True
        )

        self.comp_basic = SalaryComponent.objects.create(
            organization=self.org,
            name="Basic Salary",
            component_type="Earning",
            is_taxable=True
        )

        assign_or_revise_salary_structure(
            employee=self.employee,
            organization=self.org,
            effective_from=date(2026, 1, 1),
            components_data=[
                {'salary_component_id': self.comp_basic.id, 'amount': Decimal('5000.00')}
            ],
            created_by=self.admin
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _create_finalized_attendance(self, year, month):
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=year,
            month=month,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            employee=self.employee,
            employee_name="John Doe",
            revision=1,
            is_current=True,
            working_days=20,
            present_days=20.0,
            absent_days=0,
            payable_attendance_units=20.0
        )
        return att_period

    def test_payroll_period_calculate_task_direct(self):
        """Verify Celery task calculates payroll period directly."""
        self._create_finalized_attendance(2026, 5)
        result = calculate_payroll_period_task.delay(
            organization_id=self.org.id,
            year=2026,
            month=5,
            user_id=self.admin.id
        )
        self.assertEqual(result.status, 'SUCCESS')
        res_data = result.result
        self.assertEqual(res_data['status'], 'SUCCESS')
        self.assertEqual(res_data['total_employees'], 1)
        self.assertTrue(PayrollPeriod.objects.filter(organization=self.org, year=2026, month=5).exists())

    def test_payroll_period_calculate_view_async_dispatch(self):
        """Verify PayrollPeriodCalculateView returns 202 Accepted when ?async=true."""
        self._create_finalized_attendance(2026, 6)
        response = self.client.post(
            f'/api/v1/payroll/periods/2026/6/calculate/?async=true',
            HTTP_X_ACTIVE_ORGANIZATION=str(self.org.id)
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data)
        self.assertEqual(response.data['status'], 'SUCCESS')  # In eager mode it completes immediately

        task_id = response.data['task_id']
        status_res = self.client.get(f'/api/v1/payroll/tasks/{task_id}/')
        self.assertEqual(status_res.status_code, status.HTTP_200_OK)
        self.assertEqual(status_res.data['task_id'], task_id)
        self.assertEqual(status_res.data['status'], 'SUCCESS')

    def test_bulk_payslip_zip_task_async(self):
        """Verify asynchronous bulk payslip zip generation."""
        self._create_finalized_attendance(2026, 7)
        period = calculate_payroll_period(organization=self.org, year=2026, month=7, user=self.admin)
        finalize_payroll_period(organization=self.org, year=2026, month=7, user=self.admin)

        response = self.client.get(
            f'/api/v1/payroll/periods/2026/7/payslips/export-zip/?async=true',
            HTTP_X_ACTIVE_ORGANIZATION=str(self.org.id)
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data)

        task_id = response.data['task_id']
        status_res = self.client.get(f'/api/v1/payroll/tasks/{task_id}/')
        self.assertEqual(status_res.status_code, status.HTTP_200_OK)
        self.assertEqual(status_res.data['status'], 'SUCCESS')
        self.assertIn('download_url', status_res.data['result'])

    def test_package_cascade_update_task(self):
        """Verify package update cascades to SubscriberAccount and OrgSettings."""
        package = SubscriptionPackage.objects.create(
            name="Enterprise Max",
            price=1999,
            isActive=True,
            employeeLimit=250
        )
        sub_account = SubscriberAccount.objects.create(
            email=self.admin.email,
            packageName=package.name,
            isActive=True
        )
        self.settings.max_employees_allowed = 50
        self.settings.save()

        # Update package to inactive with new limit
        package.isActive = False
        package.employeeLimit = 300
        package.save()

        res = cascade_package_update_task.delay(package_id=package.id)
        self.assertEqual(res.status, 'SUCCESS')

        sub_account.refresh_from_db()
        self.assertFalse(sub_account.isActive)

        self.settings.refresh_from_db()
        self.assertEqual(self.settings.max_employees_allowed, 300)

    def test_bulk_wallet_adjustments_task(self):
        """Verify asynchronous bulk wallet credit/debit processing."""
        wallet = Wallet.objects.create(
            organization=self.org,
            employee=self.employee,
            balance=Decimal('100.00')
        )

        adjustments = [
            {
                'organization_id': self.org.id,
                'amount': '50.00',
                'type': 'Credit',
                'details': 'Loyalty Bonus'
            },
            {
                'organization_id': self.org.id,
                'amount': '25.00',
                'type': 'Debit',
                'details': 'Module Maintenance'
            }
        ]

        response = self.client.post(
            '/api/v1/wallet/bulk-adjust/',
            data={'adjustments': adjustments},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data)

        wallet.refresh_from_db()
        # 100 + 50 - 25 = 125.00
        self.assertEqual(wallet.balance, Decimal('125.00'))
        self.assertEqual(WalletTransaction.objects.filter(wallet=wallet).count(), 2)

    def test_dispatch_safely_fallback_on_broker_failure(self):
        """Verify dispatch_task_safely falls back to eager execution when delay() raises."""
        class MockTask:
            __name__ = "MockTask"
            def delay(self, *args, **kwargs):
                raise ConnectionError("Broker unreachable")
            def apply(self, args=None, kwargs=None):
                class EagerResult:
                    id = "fallback-eager-id"
                    status = "SUCCESS"
                return EagerResult()

        mock_task = MockTask()
        res = payroll_dispatch_safely(mock_task, 1, 2, key='val')
        self.assertEqual(res.id, "fallback-eager-id")
        self.assertEqual(res.status, "SUCCESS")
