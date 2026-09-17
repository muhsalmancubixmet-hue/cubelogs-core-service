# --------------------------------------------------------------------------------
#       Payroll Multi-Tenant Phase 2B Tests
# --------------------------------------------------------------------------------

from datetime import date, datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied

from core.models import Organization, OrgSettings, OrganizationModule, AuditLog
from users.models import Employee, Role, OrganizationMembership, PermissionFlag
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
    get_employee_salary_structure,
    assign_or_revise_salary_structure,
    calculate_employee_payroll,
    calculate_payroll_period,
    finalize_payroll_period,
)


class PayrollMultiTenantPhase2BTests(TestCase):
    def setUp(self):
        # 1. Setup Permissions
        self.perm_salary_view, _ = PermissionFlag.objects.get_or_create(
            key='salary:view', defaults={'name': 'View Salary', 'module': 'Salary'}
        )
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
        self.perm_att_admin, _ = PermissionFlag.objects.get_or_create(
            key='attendance:admin', defaults={'name': 'Admin Attendance', 'module': 'Attendance'}
        )

        # 2. Setup Organizations
        self.settings_a = OrgSettings.objects.create(
            payroll_currency='INR',
            hourly_wage_paid_leave_eligible=True,
            daily_wage_paid_leave_eligible=True,
        )
        self.org_a = Organization.objects.create(name="Org Alpha", subdomain="alpha", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='attendance', enabled=True)
        OrganizationModule.objects.create(organization=self.org_a, module_id='payroll', enabled=True)

        self.settings_b = OrgSettings.objects.create(
            payroll_currency='USD',
            hourly_wage_paid_leave_eligible=False,
            daily_wage_paid_leave_eligible=False,
        )
        self.org_b = Organization.objects.create(name="Org Beta", subdomain="beta", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='attendance', enabled=True)
        OrganizationModule.objects.create(organization=self.org_b, module_id='payroll', enabled=True)

        # 3. Setup Roles
        self.admin_role = Role.objects.filter(slug='admin').first()
        if not self.admin_role:
            self.admin_role = Role.objects.create(name='Admin', slug='admin', is_system_role=True)
        self.admin_role.permissions.add(
            self.perm_salary_view, self.perm_salary_manage,
            self.perm_payroll_view, self.perm_payroll_process, self.perm_payroll_manage,
            self.perm_att_admin
        )

        self.emp_role = Role.objects.filter(slug='employee').first()
        if not self.emp_role:
            self.emp_role = Role.objects.create(name='Employee', slug='employee', is_system_role=True)

        # 4. Setup Admins with Memberships
        self.admin_a = Employee.objects.create_user(
            email="admin_a@alpha.com",
            password="password123",
            organization=self.org_a,
            role=self.admin_role,
            is_staff=True,
            isSuperAdmin=True
        )
        self.mem_admin_a = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_a,
            role=self.admin_role,
            is_active_in_org=True
        )

        self.admin_b = Employee.objects.create_user(
            email="admin_b@beta.com",
            password="password123",
            organization=self.org_b,
            role=self.admin_role,
            is_staff=True,
            isSuperAdmin=True
        )
        self.mem_admin_b = OrganizationMembership.objects.create(
            user=self.admin_b,
            organization=self.org_b,
            role=self.admin_role,
            is_active_in_org=True
        )

        # 5. Multi-Org Employee: member of both Org A and Org B
        self.emp_multi = Employee.objects.create_user(
            email="multi_emp@shared.com",
            password="password123",
            first_name="Multi",
            last_name="Worker",
            organization=self.org_a,  # legacy pointer points to Org A
            role=self.emp_role
        )
        self.mem_multi_a = OrganizationMembership.objects.create(
            user=self.emp_multi,
            organization=self.org_a,
            role=self.emp_role,
            employee_code="ALPHA-101",
            designation="Dev in Alpha",
            is_active_in_org=True
        )
        self.mem_multi_b = OrganizationMembership.objects.create(
            user=self.emp_multi,
            organization=self.org_b,
            role=self.emp_role,
            employee_code="BETA-202",
            designation="Dev in Beta",
            is_active_in_org=True
        )

        # 6. Single-Org Employee: only in Org A
        self.emp_single_a = Employee.objects.create_user(
            email="alice@alpha.com",
            password="password123",
            first_name="Alice",
            last_name="Alpha",
            organization=self.org_a,
            role=self.emp_role
        )
        self.mem_single_a = OrganizationMembership.objects.create(
            user=self.emp_single_a,
            organization=self.org_a,
            role=self.emp_role,
            employee_code="ALPHA-001",
            is_active_in_org=True
        )

        # 7. Salary Components
        self.comp_base_a = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Base Salary",
            code="BASE_A",
            component_type="Earning",
            is_taxable=True,
            is_proratable=True
        )
        self.comp_base_b = SalaryComponent.objects.create(
            organization=self.org_b,
            name="Base Salary USD",
            code="BASE_B",
            component_type="Earning",
            is_taxable=True,
            is_proratable=True
        )

        self.client = APIClient()

    def test_01_multi_org_employee_salary_assignment_in_active_org(self):
        """
        A multi-tenant employee can be assigned independent salary structures
        in each organization they belong to via OrganizationMembership.
        """
        # Assign in Org A
        struct_a = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{"component_id": self.comp_base_a.id, "amount": "50000.00"}],
            notes="Alpha contract",
            created_by=self.admin_a,
            compensation_type='MONTHLY'
        )
        self.assertEqual(struct_a.organization, self.org_a)
        self.assertEqual(struct_a.base_net_salary, Decimal('50000.00'))

        # Assign in Org B
        struct_b = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_b,
            effective_from=date(2026, 1, 1),
            components_data=[{"component_id": self.comp_base_b.id, "amount": "4000.00"}],
            notes="Beta contract",
            created_by=self.admin_b,
            compensation_type='MONTHLY'
        )
        self.assertEqual(struct_b.organization, self.org_b)
        self.assertEqual(struct_b.base_net_salary, Decimal('4000.00'))

        # Both structures coexist independently
        self.assertEqual(EmployeeSalaryStructure.objects.filter(employee=self.emp_multi, is_active=True).count(), 2)

    def test_02_cross_org_salary_assignment_rejected(self):
        """
        Attempting to assign a salary structure in an organization where the employee
        has NO active membership raises PermissionDenied (services) and 404/403 (views).
        """
        # emp_single_a belongs only to Org A. Attempting to assign in Org B must fail.
        with self.assertRaises(PermissionDenied):
            assign_or_revise_salary_structure(
                employee=self.emp_single_a,
                organization=self.org_b,
                effective_from=date(2026, 1, 1),
                components_data=[{"component_id": self.comp_base_b.id, "amount": "3000.00"}],
                created_by=self.admin_b,
                compensation_type='MONTHLY'
            )

        # Via API: Admin B tries to assign salary for emp_single_a
        self.client.force_authenticate(user=self.admin_b)
        # Emulate request.active_organization = org_b
        url = f"/api/payroll/employees/{self.emp_single_a.id}/salary/"
        res = self.client.post(
            url,
            {
                "effective_from": "2026-01-01",
                "compensation_type": "MONTHLY",
                "components": [{"component_id": self.comp_base_b.id, "amount": "3000.00"}]
            },
            format='json',
            HTTP_HOST='beta.cubelogs.test'
        )
        # Since employee is not in Org B, endpoint returns 404 Not Found
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_03_switching_active_organization_returns_correct_salary_structure(self):
        """
        When querying salary structure for a multi-org employee, the result
        is strictly scoped to the active organization.
        """
        # Setup structures in Org A and Org B
        struct_a = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{"component_id": self.comp_base_a.id, "amount": "60000.00"}],
            created_by=self.admin_a,
            compensation_type='MONTHLY'
        )
        struct_b = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_b,
            effective_from=date(2026, 1, 1),
            components_data=[{"component_id": self.comp_base_b.id, "amount": "5000.00"}],
            created_by=self.admin_b,
            compensation_type='MONTHLY'
        )

        target_date = date(2026, 6, 1)

        # Service lookup:
        resolved_a = get_employee_salary_structure(self.emp_multi, self.org_a, target_date)
        resolved_b = get_employee_salary_structure(self.emp_multi, self.org_b, target_date)

        self.assertEqual(resolved_a.id, struct_a.id)
        self.assertEqual(resolved_a.base_net_salary, Decimal('60000.00'))
        self.assertEqual(resolved_b.id, struct_b.id)
        self.assertEqual(resolved_b.base_net_salary, Decimal('5000.00'))

        # API lookup with Org A active:
        self.client.force_authenticate(user=self.admin_a)
        res_a = self.client.get(f"/api/payroll/employees/{self.emp_multi.id}/salary/")
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        self.assertEqual(res_a.data['active_structure']['id'], struct_a.id)
        self.assertEqual(Decimal(res_a.data['active_structure']['base_net_salary']), Decimal('60000.00'))

        # API lookup with Org B active:
        self.client.force_authenticate(user=self.admin_b)
        res_b = self.client.get(f"/api/payroll/employees/{self.emp_multi.id}/salary/")
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        self.assertEqual(res_b.data['active_structure']['id'], struct_b.id)
        self.assertEqual(Decimal(res_b.data['active_structure']['base_net_salary']), Decimal('5000.00'))

    def test_04_payroll_calculation_uses_active_payroll_organization_settings(self):
        """
        Payroll calculation uses active payroll organization settings,
        NOT employee.organization.settings.
        """
        # Create HOURLY salary structures for emp_multi in Org A and Org B
        struct_a = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            compensation_type='HOURLY',
            hourly_rate=Decimal('200.00'),
            created_by=self.admin_a
        )
        struct_b = assign_or_revise_salary_structure(
            employee=self.emp_multi,
            organization=self.org_b,
            effective_from=date(2026, 1, 1),
            compensation_type='HOURLY',
            hourly_rate=Decimal('50.00'),
            created_by=self.admin_b
        )

        # Mock attendance snapshot with worked hours and paid leave hours
        class DummyAttendanceSnapshot:
            def __init__(self, worked_minutes, payable_work_hours):
                self.total_worked_minutes = worked_minutes
                self.payable_work_hours = payable_work_hours
                self.working_days = 20
                self.payable_attendance_units = Decimal('20.00')
                self.paid_leave_days = Decimal('2.00')
                self.unpaid_leave_days = Decimal('0.00')
                self.absent_days = 0

        # Worked: 160 hrs (9600 min), Total payable: 176 hrs (includes 16 hrs paid leave)
        att_snap = DummyAttendanceSnapshot(worked_minutes=9600, payable_work_hours=Decimal('176.00'))

        # In Org A: hourly_wage_paid_leave_eligible is True
        calc_a = calculate_employee_payroll(
            employee=self.emp_multi,
            attendance_snapshot=att_snap,
            salary_structure=struct_a,
            organization=self.org_a
        )
        self.assertTrue(calc_a['calculation_breakdown']['paid_leave_eligible'])

        # In Org B: hourly_wage_paid_leave_eligible is False
        # Notice self.emp_multi.organization is still self.org_a!
        # But calculation for Org B must reflect Org B settings:
        calc_b = calculate_employee_payroll(
            employee=self.emp_multi,
            attendance_snapshot=att_snap,
            salary_structure=struct_b,
            organization=self.org_b
        )
        self.assertFalse(calc_b['calculation_breakdown']['paid_leave_eligible'])

    def test_05_cross_tenant_payslip_access_blocked(self):
        """
        Payslips issued in Org A cannot be accessed or downloaded by users in Org B.
        """
        # Create Attendance Period and Payroll Period for Org A
        att_period = AttendancePeriod.objects.create(
            organization=self.org_a,
            year=2026,
            month=7,
            status='Finalized',
            current_revision=1
        )
        payroll_period = PayrollPeriod.objects.create(
            organization=self.org_a,
            year=2026,
            month=7,
            status='Finalized',
            attendance_period=att_period,
            current_revision=1,
            total_net_payable=Decimal('50000.00'),
            total_employees=1
        )
        snap = PayrollEmployeeSnapshot.objects.create(
            payroll_period=payroll_period,
            revision=1,
            is_current=True,
            employee=self.emp_single_a,
            employee_name="Alice Alpha",
            net_payable=Decimal('50000.00')
        )
        payslip = Payslip.objects.create(
            organization=self.org_a,
            payroll_period=payroll_period,
            payroll_snapshot=snap,
            employee=self.emp_single_a,
            revision=1,
            payslip_number="PAY-ALPHA-202607-001",
            company_name_snapshot="Org Alpha",
            status='Issued'
        )

        # Admin A can view the payslip
        self.client.force_authenticate(user=self.admin_a)
        res_a = self.client.get(f"/api/payroll/payslips/{payslip.id}/")
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)

        # Admin B in Org B cannot view Org A's payslip
        self.client.force_authenticate(user=self.admin_b)
        res_b = self.client.get(f"/api/payroll/payslips/{payslip.id}/")
        self.assertEqual(res_b.status_code, status.HTTP_404_NOT_FOUND)

        # Admin B cannot download Org A's payslip PDF
        res_pdf = self.client.get(f"/api/payroll/payslips/{payslip.id}/pdf/")
        self.assertEqual(res_pdf.status_code, status.HTTP_404_NOT_FOUND)

    def test_06_existing_single_org_behavior_preserved(self):
        """
        A legacy employee with only employee.organization set (no explicit OrganizationMembership)
        still operates smoothly in their organization.
        """
        emp_legacy = Employee.objects.create_user(
            email="legacy@alpha.com",
            password="password123",
            first_name="Legacy",
            last_name="Worker",
            organization=self.org_a,
            role=self.emp_role
        )
        # Ensure no OrganizationMembership records exist for emp_legacy
        self.assertFalse(OrganizationMembership.objects.filter(user=emp_legacy).exists())

        # Assign salary structure in Org A -> succeeds
        struct = assign_or_revise_salary_structure(
            employee=emp_legacy,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{"component_id": self.comp_base_a.id, "amount": "45000.00"}],
            created_by=self.admin_a,
            compensation_type='MONTHLY'
        )
        self.assertEqual(struct.base_net_salary, Decimal('45000.00'))

        # Lookup in Org A -> succeeds
        resolved = get_employee_salary_structure(emp_legacy, self.org_a, date(2026, 5, 1))
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, struct.id)

        # Assign in Org B -> blocked
        with self.assertRaises(PermissionDenied):
            assign_or_revise_salary_structure(
                employee=emp_legacy,
                organization=self.org_b,
                effective_from=date(2026, 1, 1),
                components_data=[{"component_id": self.comp_base_b.id, "amount": "45000.00"}],
                created_by=self.admin_b,
                compensation_type='MONTHLY'
            )
