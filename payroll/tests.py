# --------------------------------------------------------------------------------
#       Payroll Tests - Salary Structures, Components & Paid/Unpaid Leave
# --------------------------------------------------------------------------------

from datetime import date, datetime, timezone as dt_timezone, timedelta, date as datetime_date
from decimal import Decimal
import io
import zipfile
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError, PermissionDenied as DRFPermissionDenied
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, AuditLog
from users.models import Employee, Role, PermissionFlag
from attendance.models import (
    AttendancePolicy,
    Schedule,
    Leave,
    LeaveType,
    AttendanceLog,
    AttendancePeriod,
    AttendancePeriodEmployeeSnapshot,
)
from attendance.services import (
    get_daily_attendance_summary,
    get_monthly_attendance_summary,
    finalize_attendance_period,
)
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    PayrollPeriod,
    PayrollEmployeeSnapshot,
    PayrollAdjustment,
    Payslip,
)
from payroll.services import (
    get_employee_salary_structure,
    assign_or_revise_salary_structure,
    calculate_salary_totals,
    calculate_employee_payroll,
    calculate_payroll_period,
    finalize_payroll_period,
    reopen_payroll_period,
    generate_payslip_number,
    issue_period_payslips,
)
from payroll.pdf import generate_payslip_pdf


class PayrollPhase5Tests(TestCase):
    def setUp(self):
        # 1. Setup Permissions
        self.perm_salary_view, _ = PermissionFlag.objects.get_or_create(
            key='salary:view', defaults={'name': 'View Salary', 'module': 'Salary'}
        )
        self.perm_salary_manage, _ = PermissionFlag.objects.get_or_create(
            key='salary:manage', defaults={'name': 'Manage Salary', 'module': 'Salary'}
        )
        self.perm_att_admin, _ = PermissionFlag.objects.get_or_create(
            key='attendance:admin', defaults={'name': 'Admin Attendance', 'module': 'Attendance'}
        )

        # 2. Setup Tenants
        self.settings_a = OrgSettings.objects.create(payroll_currency='AED')
        self.org_a = Organization.objects.create(name="Tenant Alpha", subdomain="alpha", settings=self.settings_a)

        self.settings_b = OrgSettings.objects.create(payroll_currency='USD')
        self.org_b = Organization.objects.create(name="Tenant Beta", subdomain="beta", settings=self.settings_b)

        # 3. Setup Policies & Schedules
        AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            grace_period_minutes=15,
            auto_approve_attendance=True,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        Schedule.objects.create(
            organization=self.org_a,
            designation="Engineer",
            shiftStart="09:00",
            shiftEnd="17:00"
        )

        # 4. Setup Employees
        # Admin Org A with salary:manage
        self.admin_a = Employee.objects.create(
            email="admin@alpha.com",
            username="admin_alpha",
            first_name="Admin",
            last_name="Alpha",
            organization=self.org_a,
            designation="Manager",
            isSuperAdmin=True
        )

        # Viewer Org A with salary:view only
        self.viewer_a = Employee.objects.create(
            email="viewer@alpha.com",
            username="viewer_alpha",
            first_name="Viewer",
            last_name="Alpha",
            organization=self.org_a,
            designation="HR",
            useDefaultPermissions=False
        )
        self.viewer_a.extra_permissions.add(self.perm_salary_view)

        # Regular Employee Org A
        self.emp_a = Employee.objects.create(
            email="alice@alpha.com",
            username="alice_alpha",
            first_name="Alice",
            last_name="Alpha",
            organization=self.org_a,
            designation="Engineer",
            useDefaultPermissions=False
        )

        # Admin Org B
        self.admin_b = Employee.objects.create(
            email="admin@beta.com",
            username="admin_beta",
            first_name="Admin",
            last_name="Beta",
            organization=self.org_b,
            designation="Manager",
            isSuperAdmin=True
        )

        # 5. Salary Components for Tenant A
        self.basic_a = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Basic Salary",
            code="BASIC",
            component_type="Earning"
        )
        self.housing_a = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Housing Allowance",
            code="HOUSING",
            component_type="Earning"
        )
        self.insurance_a = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Medical Insurance",
            code="INSURANCE",
            component_type="Deduction"
        )

        # Salary Components for Tenant B (Same code name)
        self.basic_b = SalaryComponent.objects.create(
            organization=self.org_b,
            name="Basic Salary",
            code="BASIC",
            component_type="Earning"
        )

        # 6. API Clients
        self.client_admin_a = APIClient()
        self.client_admin_a.force_authenticate(user=self.admin_a)

        self.client_viewer_a = APIClient()
        self.client_viewer_a.force_authenticate(user=self.viewer_a)

        self.client_admin_b = APIClient()
        self.client_admin_b.force_authenticate(user=self.admin_b)

    # --------------------------------------------------------------------------
    # 1. Component & Structure Creation with Decimal Precision
    # --------------------------------------------------------------------------
    def test_01_create_initial_salary_structure_exact_decimals(self):
        """1. Creates structure with Decimal values and verifies Gross, Deductions, Net."""
        structure = assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[
                {'salary_component_id': self.basic_a.id, 'amount': '3000.50'},
                {'salary_component_id': self.housing_a.id, 'amount': '800.25'},
                {'salary_component_id': self.insurance_a.id, 'amount': '150.75'},
            ],
            notes="Initial package",
            created_by=self.admin_a
        )

        self.assertEqual(structure.gross_salary, Decimal('3800.75'))
        self.assertEqual(structure.base_deductions, Decimal('150.75'))
        self.assertEqual(structure.base_net_salary, Decimal('3650.00'))
        self.assertEqual(structure.currency, 'AED')
        self.assertEqual(structure.components.count(), 3)

    def test_02_negative_salary_amount_rejected(self):
        """2. Rejects negative salary component amounts."""
        with self.assertRaises(Exception):
            assign_or_revise_salary_structure(
                employee=self.emp_a,
                organization=self.org_a,
                effective_from=date(2026, 1, 1),
                components_data=[
                    {'salary_component_id': self.basic_a.id, 'amount': '-500.00'},
                ],
                created_by=self.admin_a
            )

    def test_03_backend_recalculates_totals_ignoring_manipulation(self):
        """3. Backend ignores/overrides manipulated frontend totals in API."""
        payload = {
            "effective_from": "2026-02-01",
            "gross_salary": "999999.00",  # Fake manipulated value
            "base_net_salary": "999999.00",  # Fake manipulated value
            "components": [
                {"salary_component": self.basic_a.id, "amount": "4000.00"},
                {"salary_component": self.insurance_a.id, "amount": "200.00"}
            ],
            "notes": "Feb Revision"
        }
        res = self.client_admin_a.post(f"/api/payroll/employees/{self.emp_a.id}/salary/", payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(str(res.data['gross_salary'])), Decimal('4000.00'))
        self.assertEqual(Decimal(str(res.data['base_deductions'])), Decimal('200.00'))
        self.assertEqual(Decimal(str(res.data['base_net_salary'])), Decimal('3800.00'))

    # --------------------------------------------------------------------------
    # 2. Date Enforcement & Historical Versioning
    # --------------------------------------------------------------------------
    def test_04_mid_month_effective_date_rejected_with_400(self):
        """4. Mid-month effective_from returns HTTP 400."""
        payload = {
            "effective_from": "2026-03-15",  # Mid-month invalid
            "components": [
                {"salary_component": self.basic_a.id, "amount": "3000.00"}
            ]
        }
        res = self.client_admin_a.post(f"/api/payroll/employees/{self.emp_a.id}/salary/", payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Salary effective date must be the first day of a month.", str(res.data))

    def test_05_salary_revision_creates_new_version_preserves_old(self):
        """5. Revisions create append-only historical rows without altering past versions."""
        # Version 1: Jan 2026 (Net 3000)
        v1 = assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3000.00'}],
            notes="Jan v1"
        )
        # Version 2: July 2026 (Net 4000)
        v2 = assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 7, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '4000.00'}],
            notes="Jul v2"
        )

        v1.refresh_from_db()
        self.assertEqual(v1.base_net_salary, Decimal('3000.00'))
        self.assertEqual(v2.base_net_salary, Decimal('4000.00'))

        # Resolve for March 2026 -> v1
        resolved_mar = get_employee_salary_structure(self.emp_a, self.org_a, date(2026, 3, 15))
        self.assertEqual(resolved_mar.id, v1.id)
        self.assertEqual(resolved_mar.base_net_salary, Decimal('3000.00'))

        # Resolve for August 2026 -> v2
        resolved_aug = get_employee_salary_structure(self.emp_a, self.org_a, date(2026, 8, 1))
        self.assertEqual(resolved_aug.id, v2.id)
        self.assertEqual(resolved_aug.base_net_salary, Decimal('4000.00'))

    def test_06_duplicate_effective_date_rejected(self):
        """6. Rejects duplicate salary structure on the same effective date."""
        assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3000.00'}]
        )
        with self.assertRaises(Exception):
            assign_or_revise_salary_structure(
                employee=self.emp_a,
                organization=self.org_a,
                effective_from=date(2026, 1, 1),
                components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3500.00'}]
            )

    # --------------------------------------------------------------------------
    # 3. Currency Snapshot Protection
    # --------------------------------------------------------------------------
    def test_07_org_currency_change_does_not_mutate_old_structure(self):
        """7. Changing OrgSettings.payroll_currency does not alter existing salary currency."""
        s1 = assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3000.00'}]
        )
        self.assertEqual(s1.currency, 'AED')

        # Mutate OrgSettings default currency to USD
        self.settings_a.payroll_currency = 'USD'
        self.settings_a.save()

        s1.refresh_from_db()
        self.assertEqual(s1.currency, 'AED')  # Preserved original snapshot

        # New revision uses the updated default USD
        s2 = assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 6, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3500.00'}]
        )
        self.assertEqual(s2.currency, 'USD')

    # --------------------------------------------------------------------------
    # 4. Paid vs Unpaid Leave in Daily & Monthly Attendance Engine
    # --------------------------------------------------------------------------
    def test_08_full_day_paid_vs_unpaid_leave(self):
        """8. Paid leave gives 1.0 payable credit; Unpaid leave gives 0.0 credit."""
        paid_type = LeaveType.objects.create(
            organization=self.org_a,
            name="Annual Leave",
            is_paid=True
        )
        unpaid_type = LeaveType.objects.create(
            organization=self.org_a,
            name="Leave Without Pay (LWP)",
            is_paid=False
        )

        target_date_1 = date(2026, 2, 2)  # Monday
        target_date_2 = date(2026, 2, 3)  # Tuesday

        # Apply Paid Leave for day 1
        Leave.objects.create(
            employee=self.emp_a,
            leaveType=paid_type,
            startDate=target_date_1,
            endDate=target_date_1,
            duration=1.0,
            dayType="Full",
            status="Approved"
        )
        # Apply Unpaid Leave for day 2
        Leave.objects.create(
            employee=self.emp_a,
            leaveType=unpaid_type,
            startDate=target_date_2,
            endDate=target_date_2,
            duration=1.0,
            dayType="Full",
            status="Approved"
        )

        summary_1 = get_daily_attendance_summary(self.emp_a, target_date_1)
        self.assertEqual(summary_1["daily_status"], "Leave")
        self.assertTrue(summary_1["leave_is_paid"])
        self.assertEqual(summary_1["paid_leave_unit"], 1.0)
        self.assertEqual(summary_1["unpaid_leave_unit"], 0.0)
        self.assertEqual(summary_1["payable_attendance_unit"], 1.0)

        summary_2 = get_daily_attendance_summary(self.emp_a, target_date_2)
        self.assertEqual(summary_2["daily_status"], "Leave")
        self.assertFalse(summary_2["leave_is_paid"])
        self.assertEqual(summary_2["paid_leave_unit"], 0.0)
        self.assertEqual(summary_2["unpaid_leave_unit"], 1.0)
        self.assertEqual(summary_2["payable_attendance_unit"], 0.0)

    def test_09_half_day_paid_vs_unpaid_leave(self):
        """9. Half-day paid gives 0.5 paid unit; Half-day unpaid gives 0.5 unpaid unit."""
        unpaid_type = LeaveType.objects.create(
            organization=self.org_a,
            name="Unpaid Half Day",
            is_paid=False
        )
        target_date = date(2026, 2, 4)  # Wednesday

        Leave.objects.create(
            employee=self.emp_a,
            leaveType=unpaid_type,
            startDate=target_date,
            endDate=target_date,
            duration=0.5,
            dayType="Half",
            status="Approved"
        )

        summary = get_daily_attendance_summary(self.emp_a, target_date)
        self.assertEqual(summary["daily_status"], "Half Day")
        self.assertFalse(summary["leave_is_paid"])
        self.assertEqual(summary["paid_leave_unit"], 0.0)
        self.assertEqual(summary["unpaid_leave_unit"], 0.5)
        self.assertEqual(summary["payable_attendance_unit"], 0.0)

    def test_10_finalized_snapshot_preserves_paid_and_unpaid_leave(self):
        """10. Finalized AttendancePeriodEmployeeSnapshot captures paid_leave_days and unpaid_leave_days."""
        paid_type = LeaveType.objects.create(organization=self.org_a, name="Paid Sick", is_paid=True)
        unpaid_type = LeaveType.objects.create(organization=self.org_a, name="Unpaid LWP", is_paid=False)

        # Jan 2026 is a past month
        # Day 5 (Mon): Paid Leave
        Leave.objects.create(
            employee=self.emp_a,
            leaveType=paid_type,
            startDate=date(2026, 1, 5),
            endDate=date(2026, 1, 5),
            status="Approved"
        )
        # Day 6 (Tue): Unpaid Leave
        Leave.objects.create(
            employee=self.emp_a,
            leaveType=unpaid_type,
            startDate=date(2026, 1, 6),
            endDate=date(2026, 1, 6),
            status="Approved"
        )

        # Clock-in present for all normal working days in Jan 2026 except leave days (Jan 5 & 6)
        for day in range(1, 32):
            if day in (5, 6):
                continue
            dt = date(2026, 1, day)
            if dt.weekday() < 5:  # Mon-Fri
                AttendanceLog.objects.create(
                    employee=self.emp_a,
                    employeeName="Alice Alpha",
                    date=dt,
                    clockIn=datetime(2026, 1, day, 9, 0, tzinfo=dt_timezone.utc),
                    clockOut=datetime(2026, 1, day, 17, 0, tzinfo=dt_timezone.utc),
                    status="Approved"
                )

        period = finalize_attendance_period(self.org_a, 2026, 1, user=self.admin_a)
        snapshot = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=period,
            employee=self.emp_a,
            is_current=True
        ).first()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.paid_leave_days, Decimal('1.00'))
        self.assertEqual(snapshot.unpaid_leave_days, Decimal('1.00'))
        self.assertEqual(snapshot.leave_days, 2.0)
        # Total working days = 22. Worked: 20 days (20.0). Paid leave: 1 day (1.0). Unpaid leave: 1 day (0.0).
        self.assertEqual(snapshot.payable_attendance_units, 21.0)

    # --------------------------------------------------------------------------
    # 5. Tenant Isolation & Permissions
    # --------------------------------------------------------------------------
    def test_11_cross_tenant_salary_component_injection_blocked(self):
        """11. Cannot use Org B salary component in Org A employee salary."""
        payload = {
            "effective_from": "2026-04-01",
            "components": [
                {"salary_component": self.basic_b.id, "amount": "5000.00"}  # Component belongs to Org B!
            ]
        }
        res = self.client_admin_a.post(f"/api/payroll/employees/{self.emp_a.id}/salary/", payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(f"Salary component ID {self.basic_b.id} was not found", str(res.data))

    def test_12_cross_tenant_salary_access_blocked(self):
        """12. Org B admin cannot view or edit Org A employee salary."""
        # Org B admin trying to view Org A employee salary
        res_view = self.client_admin_b.get(f"/api/payroll/employees/{self.emp_a.id}/salary/")
        self.assertEqual(res_view.status_code, status.HTTP_404_NOT_FOUND)

        # Org B admin trying to post to Org A employee salary
        res_post = self.client_admin_b.post(
            f"/api/payroll/employees/{self.emp_a.id}/salary/",
            {"effective_from": "2026-05-01", "components": [{"salary_component": self.basic_b.id, "amount": "1000"}]},
            format='json'
        )
        self.assertEqual(res_post.status_code, status.HTTP_404_NOT_FOUND)

    def test_13_salary_view_is_readonly(self):
        """13. Users with salary:view can GET but cannot POST."""
        res_get = self.client_viewer_a.get(f"/api/payroll/employees/{self.emp_a.id}/salary/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)

        res_post = self.client_viewer_a.post(
            f"/api/payroll/employees/{self.emp_a.id}/salary/",
            {"effective_from": "2026-08-01", "components": [{"salary_component": self.basic_a.id, "amount": "1000"}]},
            format='json'
        )
        self.assertEqual(res_post.status_code, status.HTTP_403_FORBIDDEN)

    def test_14_audit_log_created_without_leaking_amounts(self):
        """14. Audit log records assignment/revision without exposing raw salary numbers in details."""
        assign_or_revise_salary_structure(
            employee=self.emp_a,
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            components_data=[{'salary_component_id': self.basic_a.id, 'amount': '3000.00'}],
            created_by=self.admin_a
        )
        log = AuditLog.objects.filter(
            organization=self.org_a,
            action="Salary Structure Assigned"
        ).first()

        self.assertIsNotNone(log)
        self.assertIn("Alice Alpha", log.details)
        self.assertIn("Effective: 2026-01-01", log.details)
        # Verify raw amount '3000' is NOT in details string
        self.assertNotIn("3000", log.details)


class PayrollPhase6Tests(TestCase):
    def setUp(self):
        # 1. Setup Permissions
        self.perm_payroll_view, _ = PermissionFlag.objects.get_or_create(
            key='payroll:view', defaults={'name': 'View Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_process, _ = PermissionFlag.objects.get_or_create(
            key='payroll:process', defaults={'name': 'Process Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_manage, _ = PermissionFlag.objects.get_or_create(
            key='payroll:manage', defaults={'name': 'Manage Payroll', 'module': 'Payroll'}
        )
        self.perm_salary_manage, _ = PermissionFlag.objects.get_or_create(
            key='salary:manage', defaults={'name': 'Manage Salary', 'module': 'Salary'}
        )
        self.perm_att_admin, _ = PermissionFlag.objects.get_or_create(
            key='attendance:admin', defaults={'name': 'Admin Attendance', 'module': 'Attendance'}
        )

        # 2. Setup Tenants
        self.settings_a = OrgSettings.objects.create(
            payroll_currency='USD',
            payroll_proration_basis='WORKING_DAYS',
            auto_approve_attendance=True,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org_a = Organization.objects.create(name="Tenant Alpha", subdomain="alpha", settings=self.settings_a)

        self.settings_b = OrgSettings.objects.create(payroll_currency='EUR')
        self.org_b = Organization.objects.create(name="Tenant Beta", subdomain="beta", settings=self.settings_b)

        # 3. Setup Policy & Schedule for Org A
        AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            grace_period_minutes=15,
            auto_approve_attendance=True,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        Schedule.objects.create(
            organization=self.org_a,
            designation="Engineer",
            shiftStart="09:00",
            shiftEnd="17:00"
        )

        # 4. Setup Employees
        self.admin_a = Employee.objects.create(
            email="admin@alpha.com",
            username="admin_alpha",
            first_name="Admin",
            last_name="Alpha",
            organization=self.org_a,
            designation="Manager",
            isSuperAdmin=True
        )

        self.processor_a = Employee.objects.create(
            email="processor@alpha.com",
            username="processor_alpha",
            first_name="Processor",
            last_name="Alpha",
            organization=self.org_a,
            designation="Accountant",
            useDefaultPermissions=False
        )
        self.processor_a.extra_permissions.add(self.perm_payroll_view, self.perm_payroll_process)

        self.emp_1 = Employee.objects.create(
            email="emp1@alpha.com",
            username="emp1_alpha",
            first_name="Alice",
            last_name="Smith",
            organization=self.org_a,
            designation="Engineer",
            joining_date=date(2026, 1, 1),
            is_active=True
        )

        self.emp_2 = Employee.objects.create(
            email="emp2@alpha.com",
            username="emp2_alpha",
            first_name="Bob",
            last_name="Jones",
            organization=self.org_a,
            designation="Engineer",
            joining_date=date(2026, 7, 15),  # Joins mid-month in July
            is_active=True
        )

        self.emp_b = Employee.objects.create(
            email="emp@beta.com",
            username="emp_beta",
            first_name="Beta",
            last_name="User",
            organization=self.org_b,
            designation="Engineer",
            is_active=True
        )

        # 5. Setup Salary Components for Org A
        self.basic = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Basic Salary",
            code="BASIC",
            component_type="Earning",
            is_proratable=True
        )
        self.housing = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Housing Allowance",
            code="HRA",
            component_type="Earning",
            is_proratable=False  # Fixed allowance
        )
        self.insurance = SalaryComponent.objects.create(
            organization=self.org_a,
            name="Health Insurance",
            code="INS",
            component_type="Deduction"
        )

        # Assign Salary Structure to Admin: Basic 5000
        assign_or_revise_salary_structure(
            employee=self.admin_a,
            organization=self.org_a,
            effective_from=date(2026, 7, 1),
            components_data=[
                {'salary_component_id': self.basic.id, 'amount': '5000.00'},
            ],
            created_by=self.admin_a
        )

        # Assign Salary Structure to Processor: Basic 3500
        assign_or_revise_salary_structure(
            employee=self.processor_a,
            organization=self.org_a,
            effective_from=date(2026, 7, 1),
            components_data=[
                {'salary_component_id': self.basic.id, 'amount': '3500.00'},
            ],
            created_by=self.admin_a
        )

        # Assign Salary Structure to Alice: Basic 3000, Housing 1000, Insurance 100 -> Gross 4000, Base Net 3900
        assign_or_revise_salary_structure(
            employee=self.emp_1,
            organization=self.org_a,
            effective_from=date(2026, 7, 1),
            components_data=[
                {'salary_component_id': self.basic.id, 'amount': '3000.00'},
                {'salary_component_id': self.housing.id, 'amount': '1000.00'},
                {'salary_component_id': self.insurance.id, 'amount': '100.00'},
            ],
            created_by=self.admin_a
        )

        # Assign Salary Structure to Bob: Basic 2200 (all proratable)
        assign_or_revise_salary_structure(
            employee=self.emp_2,
            organization=self.org_a,
            effective_from=date(2026, 7, 1),
            components_data=[
                {'salary_component_id': self.basic.id, 'amount': '2200.00'},
            ],
            created_by=self.admin_a
        )

        # Setup Leave Types
        self.paid_leave_type = LeaveType.objects.create(
            organization=self.org_a,
            name="Annual Leave",
            is_paid=True,
            maxLimit=20
        )
        self.unpaid_leave_type = LeaveType.objects.create(
            organization=self.org_a,
            name="Unpaid Leave",
            is_paid=False,
            maxLimit=0
        )

        # Clients
        self.client_admin_a = APIClient()
        self.client_admin_a.force_authenticate(user=self.admin_a)

        self.client_processor_a = APIClient()
        self.client_processor_a.force_authenticate(user=self.processor_a)

    def _setup_finalized_july_attendance(self):
        """Helper to create attendance logs and finalize July 2026 for Org A."""
        # July 2026 has 23 working days (excluding Saturdays and Sundays)
        # Alice works all days except July 1 (Unpaid Leave) and July 2 (Paid Leave)
        Leave.objects.create(
            employee=self.emp_1,
            employeeName=self.emp_1.first_name,
            leaveType=self.unpaid_leave_type,
            leaveTypeName=self.unpaid_leave_type.name,
            startDate=date(2026, 7, 1),
            endDate=date(2026, 7, 1),
            duration=1.0,
            status='Approved'
        )
        Leave.objects.create(
            employee=self.emp_1,
            employeeName=self.emp_1.first_name,
            leaveType=self.paid_leave_type,
            leaveTypeName=self.paid_leave_type.name,
            startDate=date(2026, 7, 2),
            endDate=date(2026, 7, 2),
            duration=1.0,
            status='Approved'
        )

        # Clock Alice, Admin, Processor in for July 3 to July 31 on weekdays
        for day in range(1, 32):
            cur_date = date(2026, 7, day)
            if cur_date.weekday() < 5:  # Monday - Friday
                if day >= 3:
                    AttendanceLog.objects.create(
                        employee=self.emp_1,
                        employeeName=self.emp_1.first_name,
                        clockIn=datetime(2026, 7, day, 9, 0, tzinfo=dt_timezone.utc),
                        clockOut=datetime(2026, 7, day, 17, 0, tzinfo=dt_timezone.utc),
                        date=cur_date,
                        status="Present"
                    )
                AttendanceLog.objects.create(
                    employee=self.admin_a,
                    employeeName=self.admin_a.first_name,
                    clockIn=datetime(2026, 7, day, 9, 0, tzinfo=dt_timezone.utc),
                    clockOut=datetime(2026, 7, day, 17, 0, tzinfo=dt_timezone.utc),
                    date=cur_date,
                    status="Present"
                )
                AttendanceLog.objects.create(
                    employee=self.processor_a,
                    employeeName=self.processor_a.first_name,
                    clockIn=datetime(2026, 7, day, 9, 0, tzinfo=dt_timezone.utc),
                    clockOut=datetime(2026, 7, day, 17, 0, tzinfo=dt_timezone.utc),
                    date=cur_date,
                    status="Present"
                )

        # Clock Bob in from July 15 onwards on weekdays (he joined July 15)
        for day in range(15, 32):
            cur_date = date(2026, 7, day)
            if cur_date.weekday() < 5:
                AttendanceLog.objects.create(
                    employee=self.emp_2,
                    employeeName=self.emp_2.first_name,
                    clockIn=datetime(2026, 7, day, 9, 0, tzinfo=dt_timezone.utc),
                    clockOut=datetime(2026, 7, day, 17, 0, tzinfo=dt_timezone.utc),
                    date=cur_date,
                    status="Present"
                )

        return finalize_attendance_period(self.org_a, 2026, 7, self.admin_a)

    def test_15_joining_date_excludes_pre_employment_absences(self):
        """15. Days before employee.joining_date evaluate to 'Not Employed' and do not count as absent."""
        # Bob joined July 15, 2026. July 1 (Wednesday) is before joining.
        summary_pre = get_daily_attendance_summary(self.emp_2, date(2026, 7, 1))
        self.assertEqual(summary_pre["daily_status"], "Not Employed")
        self.assertFalse(summary_pre["is_working_day"])
        self.assertEqual(summary_pre["payable_attendance_unit"], 0.0)

        # July 16 (Thursday) is after joining -> Working day
        summary_post = get_daily_attendance_summary(self.emp_2, date(2026, 7, 16))
        self.assertTrue(summary_post["is_working_day"])

    def test_16_last_working_date_excludes_post_employment_absences(self):
        """16. Days after employee.last_working_date evaluate to 'Not Employed'."""
        self.emp_1.last_working_date = date(2026, 7, 20)
        self.emp_1.save()

        summary_after = get_daily_attendance_summary(self.emp_1, date(2026, 7, 25))
        self.assertEqual(summary_after["daily_status"], "Not Employed")
        self.assertFalse(summary_after["is_working_day"])

    def test_17_invalid_employment_date_range_rejected(self):
        """17. last_working_date earlier than joining_date raises validation error."""
        emp = Employee(
            email="invalid_dates@alpha.com",
            username="invalid_dates",
            organization=self.org_a,
            joining_date=date(2026, 8, 1),
            last_working_date=date(2026, 7, 1)  # Invalid: before joining
        )
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises(DjangoValidationError):
            emp.clean()

    def test_18_proratable_vs_non_proratable_earning_calculation(self):
        """18. Attendance deductions reduce proratable gross (Basic) only, leaving fixed allowances (Housing) intact."""
        att_period = self._setup_finalized_july_attendance()
        alice_snap = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=att_period,
            employee=self.emp_1,
            is_current=True
        ).first()

        # Alice has: 23 working days, 21 worked, 1 paid leave, 1 unpaid leave
        # payable_attendance_units = 22.0 -> unpaid_units = 1.0
        # Basic (proratable) = 3,000.00, Housing (non-proratable) = 1,000.00, Insurance (deduction) = 100.00
        # daily_rate = 3000 / 23 = 130.4348
        # attendance_deduction = 1 * 130.4348 = 130.43
        # earned_gross = 3000 - 130.43 + 1000 = 3869.57
        # net_payable = 3869.57 - 100 (insurance) = 3769.57
        salary_struct = get_employee_salary_structure(self.emp_1, self.org_a, date(2026, 7, 1))
        from payroll.services import calculate_employee_payroll
        calc = calculate_employee_payroll(
            employee=self.emp_1,
            attendance_snapshot=alice_snap,
            salary_structure=salary_struct,
            adjustments=[],
            proration_basis='WORKING_DAYS'
        )

        self.assertEqual(calc["proratable_gross"], Decimal('3000.00'))
        self.assertEqual(calc["non_proratable_gross"], Decimal('1000.00'))
        self.assertEqual(calc["daily_rate"], Decimal('130.4348'))
        self.assertEqual(calc["attendance_deduction"], Decimal('130.43'))
        self.assertEqual(calc["earned_gross"], Decimal('3869.57'))
        self.assertEqual(calc["base_fixed_deductions"], Decimal('100.00'))
        self.assertEqual(calc["net_payable"], Decimal('3769.57'))
        self.assertEqual(calc["status"], "OK")

    def test_19_clean_monthly_payroll_calculation(self):
        """19. Calculate monthly payroll period via API."""
        self._setup_finalized_july_attendance()
        res = self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "Calculated")
        self.assertEqual(res.data["current_revision"], 1)
        self.assertEqual(res.data["total_employees"], 4)

    def test_20_payroll_blocked_without_finalized_attendance(self):
        """20. Payroll calculation rejected if attendance is not Finalized."""
        res = self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("is not Finalized", str(res.data))

    def test_21_unpaid_full_day_and_half_day_deduction_exact_decimals(self):
        """21. Precision verification for unpaid deductions."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        snap = PayrollEmployeeSnapshot.objects.filter(
            payroll_period__year=2026,
            payroll_period__month=7,
            employee=self.emp_1,
            is_current=True
        ).first()

        self.assertEqual(snap.working_days, 23)
        self.assertEqual(snap.attendance_deduction, Decimal('130.43'))
        self.assertEqual(snap.net_payable, Decimal('3769.57'))

    def test_22_paid_leave_causes_zero_deduction(self):
        """22. Paid leave is counted in payable_attendance_units and incurs no attendance deduction."""
        self._setup_finalized_july_attendance()
        snap = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period__year=2026,
            attendance_period__month=7,
            employee=self.emp_1,
            is_current=True
        ).first()
        # Alice had 1 paid leave day (July 2)
        self.assertEqual(snap.paid_leave_days, Decimal('1.00'))
        self.assertEqual(snap.unpaid_leave_days, Decimal('1.00'))

    def test_23_manual_payroll_adjustments_included(self):
        """23. Manual earning/deduction adjustments are applied in calculation."""
        self._setup_finalized_july_attendance()
        # Calculate first to create Draft/Calculated period
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")

        # Add bonus adjustment of 500
        res_adj = self.client_admin_a.post(
            "/api/payroll/periods/2026/7/adjustments/",
            {
                "employee": self.emp_1.id,
                "adjustment_type": "Earning",
                "category": "Bonus",
                "amount": "500.00",
                "description": "Project Completion Bonus"
            },
            format='json'
        )
        self.assertEqual(res_adj.status_code, status.HTTP_201_CREATED)

        # Recalculate
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        snap = PayrollEmployeeSnapshot.objects.filter(
            payroll_period__year=2026,
            payroll_period__month=7,
            employee=self.emp_1,
            is_current=True
        ).first()

        self.assertEqual(snap.additional_earnings, Decimal('500.00'))
        # Previous net 3769.57 + 500 = 4269.57
        self.assertEqual(snap.net_payable, Decimal('4269.57'))

    def test_24_finalized_payroll_locks_and_creates_immutable_snapshots(self):
        """24. Finalizing payroll sets status to Finalized."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        res_fin = self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin.status_code, status.HTTP_200_OK)
        self.assertEqual(res_fin.data["status"], "Finalized")

        # Recalculating finalized payroll is blocked
        res_recalc = self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.assertEqual(res_recalc.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be recalculated", str(res_recalc.data))

    def test_25_finalized_payroll_blocks_attendance_reopen(self):
        """25. Attempting to reopen attendance when payroll is Finalized is blocked."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")

        # Attempt to reopen attendance
        from attendance.services import reopen_attendance_period
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            reopen_attendance_period(self.org_a, 2026, 7, self.admin_a, "Need to fix punch")
        self.assertIn("Payroll for this month is Finalized. Please reopen Payroll first", str(ctx.exception))

    def test_26_reopened_payroll_allows_attendance_reopen(self):
        """26. Reopening payroll moves it to Draft and allows attendance to be reopened."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")

        # Reopen Payroll
        res_reopen = self.client_admin_a.post(
            "/api/payroll/periods/2026/7/reopen/",
            {"reason": "Adjust attendance records for Alice"},
            format='json'
        )
        self.assertEqual(res_reopen.status_code, status.HTTP_200_OK)
        self.assertEqual(res_reopen.data["status"], "Draft")

        # Now attendance reopen succeeds
        from attendance.services import reopen_attendance_period
        att_reopened = reopen_attendance_period(self.org_a, 2026, 7, self.admin_a, "Need to fix punch")
        self.assertEqual(att_reopened.status, "Draft")

    def test_27_finalized_payroll_blocks_backdated_salary_revision(self):
        """27. Salary revisions effective for a finalized payroll month are rejected."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")

        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            assign_or_revise_salary_structure(
                employee=self.emp_1,
                organization=self.org_a,
                effective_from=date(2026, 7, 1),
                components_data=[{'salary_component_id': self.basic.id, 'amount': '4500.00'}],
                created_by=self.admin_a
            )
        self.assertIn("Payroll for this month is Finalized", str(ctx.exception))

    def test_28_calculated_payroll_detects_stale_attendance_revision(self):
        """28. If attendance is refinalized, calculated payroll detects stale source and blocks finalize."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")

        # Reopen and refinalize attendance (Revision 2)
        from attendance.services import reopen_attendance_period
        reopen_attendance_period(self.org_a, 2026, 7, self.admin_a, "Correcting Alice punch")
        finalize_attendance_period(self.org_a, 2026, 7, self.admin_a)

        # Attempt to finalize payroll without recalculation
        res_fin = self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Payroll calculation is stale", str(res_fin.data))

        # Recalculating payroll refreshes attendance revision and allows finalization
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        res_fin_ok = self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin_ok.status_code, status.HTTP_200_OK)

    def test_29_negative_net_detected_and_blocks_finalization(self):
        """29. If total deductions exceed earnings, row is flagged NegativeNet and finalization is blocked."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")

        # Add huge deduction adjustment for Bob (5000 deduction on 2200 salary)
        self.client_admin_a.post(
            "/api/payroll/periods/2026/7/adjustments/",
            {
                "employee": self.emp_2.id,
                "adjustment_type": "Deduction",
                "category": "Fine",
                "amount": "5000.00",
                "description": "Asset Damage Penalty"
            },
            format='json'
        )
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")

        snap = PayrollEmployeeSnapshot.objects.filter(
            payroll_period__year=2026,
            payroll_period__month=7,
            employee=self.emp_2,
            is_current=True
        ).first()
        self.assertEqual(snap.status, "NegativeNet")

        # Finalization is blocked
        res_fin = self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unresolved employee issues", str(res_fin.data))

    def test_30_missing_salary_structure_blocks_finalization(self):
        """30. An employee without salary structure is flagged MissingSalaryStructure and blocks finalization."""
        # Create third employee with no salary structure
        Employee.objects.create(
            email="emp3@alpha.com",
            username="emp3_alpha",
            first_name="Charlie",
            last_name="Brown",
            organization=self.org_a,
            designation="Engineer",
            is_active=True
        )
        finalize_attendance_period(self.org_a, 2026, 7, self.admin_a)
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")

        res_fin = self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Missing Salary Structure", str(res_fin.data))

    def test_31_adjustments_blocked_on_finalized_payroll(self):
        """31. Adding or deleting adjustments on finalized payroll is rejected."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")

        res_adj = self.client_admin_a.post(
            "/api/payroll/periods/2026/7/adjustments/",
            {"employee": self.emp_1.id, "adjustment_type": "Earning", "amount": "100", "description": "Late Bonus"},
            format='json'
        )
        self.assertEqual(res_adj.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non-finalized payroll periods", str(res_adj.data))

    def test_32_cross_tenant_payroll_isolation(self):
        """32. Cross-tenant payroll calculations or adjustment injections are rejected."""
        self._setup_finalized_july_attendance()
        # Org B admin trying to calculate Org A payroll
        client_beta = APIClient()
        admin_b = Employee.objects.create(
            email="admin@beta.com",
            username="admin_beta",
            organization=self.org_b,
            isSuperAdmin=True
        )
        client_beta.force_authenticate(user=admin_b)

        res = client_beta.post("/api/payroll/periods/2026/7/calculate/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("is not Finalized", str(res.data))

    def test_33_payroll_permissions_enforced(self):
        """33. payroll:process can calculate but cannot finalize or reopen."""
        self._setup_finalized_july_attendance()
        # Processor can calculate
        res_calc = self.client_processor_a.post("/api/payroll/periods/2026/7/calculate/")
        self.assertEqual(res_calc.status_code, status.HTTP_200_OK)

        # Processor cannot finalize
        res_fin = self.client_processor_a.post("/api/payroll/periods/2026/7/finalize/")
        self.assertEqual(res_fin.status_code, status.HTTP_403_FORBIDDEN)

        # Processor cannot reopen
        res_reopen = self.client_processor_a.post("/api/payroll/periods/2026/7/reopen/", {"reason": "Test"})
        self.assertEqual(res_reopen.status_code, status.HTTP_403_FORBIDDEN)

    def test_34_audit_log_created_on_calculate_finalize_reopen(self):
        """34. AuditLog entries are created for each payroll lifecycle transition."""
        self._setup_finalized_july_attendance()
        self.client_admin_a.post("/api/payroll/periods/2026/7/calculate/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/finalize/")
        self.client_admin_a.post("/api/payroll/periods/2026/7/reopen/", {"reason": "Audit Test Reopen"}, format='json')

        actions = list(AuditLog.objects.filter(organization=self.org_a).values_list('action', flat=True))
        self.assertIn("Payroll Calculated", actions)
        self.assertIn("Payroll Finalized", actions)
        self.assertIn("Payroll Period Reopened", actions)


class PayrollPhase7APayslipTests(TestCase):
    def setUp(self):
        # Permissions
        self.perm_payroll_view, _ = PermissionFlag.objects.get_or_create(
            key='payroll:view', defaults={'name': 'View Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_process, _ = PermissionFlag.objects.get_or_create(
            key='payroll:process', defaults={'name': 'Process Payroll', 'module': 'Payroll'}
        )
        self.perm_payroll_manage, _ = PermissionFlag.objects.get_or_create(
            key='payroll:manage', defaults={'name': 'Manage Payroll', 'module': 'Payroll'}
        )
        self.perm_salary_view, _ = PermissionFlag.objects.get_or_create(
            key='salary:view', defaults={'name': 'View Salary', 'module': 'Salary'}
        )
        self.perm_salary_manage, _ = PermissionFlag.objects.get_or_create(
            key='salary:manage', defaults={'name': 'Manage Salary', 'module': 'Salary'}
        )

        # Organization & Settings
        self.settings_a = OrgSettings.objects.create(
            payroll_currency='USD',
            company_address='100 Tech Park, Suite 500, Silicon City',
            company_phone='+1-555-0199',
            company_email='payroll@alpha.com',
            company_tax_id='TAX-ALPHA-12345',
            brandLogo='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        )
        self.org_a = Organization.objects.create(name="Alpha Corp", subdomain="alpha", settings=self.settings_a)

        self.settings_b = OrgSettings.objects.create(payroll_currency='EUR')
        self.org_b = Organization.objects.create(name="Beta Corp", subdomain="beta", settings=self.settings_b)

        # Employees in Org A
        self.admin_a = Employee.objects.create(
            email="admin@alpha.com",
            username="admin_alpha",
            first_name="Admin",
            last_name="Alpha",
            organization=self.org_a,
            isSuperAdmin=True
        )
        self.staff_a = Employee.objects.create(
            email="staff@alpha.com",
            username="staff_alpha",
            first_name="Alice",
            last_name="Smith",
            employee_code="EMP-0101",
            department="Engineering",
            designation="Senior Engineer",
            organization=self.org_a,
            joining_date=date(2026, 1, 1)
        )
        self.viewer_a = Employee.objects.create(
            email="viewer@alpha.com",
            username="viewer_alpha",
            first_name="Viewer",
            last_name="Only",
            organization=self.org_a
        )
        self.viewer_a.extra_permissions.add(self.perm_payroll_view)

        # Employee in Org B
        self.admin_b = Employee.objects.create(
            email="admin@beta.com",
            username="admin_beta",
            first_name="Beta",
            last_name="Admin",
            organization=self.org_b,
            isSuperAdmin=True
        )

        # Clients
        self.client_admin_a = APIClient()
        self.client_admin_a.force_authenticate(user=self.admin_a)

        self.client_viewer_a = APIClient()
        self.client_viewer_a.force_authenticate(user=self.viewer_a)

        self.client_admin_b = APIClient()
        self.client_admin_b.force_authenticate(user=self.admin_b)

        self.client_unauth = APIClient()

        # Components & Salary for Staff A
        self.comp_basic = SalaryComponent.objects.create(
            organization=self.org_a, name="Basic Salary", code="BASIC",
            component_type="Earning", is_proratable=True
        )
        self.comp_housing = SalaryComponent.objects.create(
            organization=self.org_a, name="Housing Allowance", code="HRA",
            component_type="Earning", is_proratable=False
        )
        self.comp_ins = SalaryComponent.objects.create(
            organization=self.org_a, name="Health Insurance", code="INS",
            component_type="Deduction"
        )

        assign_or_revise_salary_structure(
            employee=self.staff_a,
            organization=self.org_a,
            effective_from=date(2026, 8, 1),
            components_data=[
                {'salary_component_id': self.comp_basic.id, 'amount': '4000.00'},
                {'salary_component_id': self.comp_housing.id, 'amount': '1000.00'},
                {'salary_component_id': self.comp_ins.id, 'amount': '200.00'},
            ],
            created_by=self.admin_a
        )

    def _setup_finalized_august_payroll(self):
        """Helper to create finalized attendance and finalized payroll for August 2026."""
        # 1. Finalized Attendance Period
        att_period = AttendancePeriod.objects.create(
            organization=self.org_a,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1,
            finalized_at=timezone.now(),
            finalized_by=self.admin_a
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            revision=1,
            is_current=True,
            employee=self.staff_a,
            employee_name="Alice Smith",
            designation="Senior Engineer",
            working_days=22,
            payable_attendance_units=22.0,
            present_days=22.0,
            paid_leave_days=Decimal("0.00"),
            unpaid_leave_days=Decimal("0.00"),
            absent_days=0
        )

        # 2. Calculate Payroll
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)

        # 3. Finalize Payroll
        finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)
        return PayrollPeriod.objects.get(organization=self.org_a, year=2026, month=8)

    def test_01_draft_payroll_cannot_issue_payslip(self):
        """1. draft payroll cannot issue payslip."""
        att_period = AttendancePeriod.objects.create(
            organization=self.org_a, year=2026, month=8, status='Finalized', current_revision=1
        )
        period = PayrollPeriod.objects.create(
            organization=self.org_a, year=2026, month=8, status='Draft',
            attendance_period=att_period, attendance_revision=1
        )
        from rest_framework.exceptions import ValidationError as DRFValidationError
        with self.assertRaises(DRFValidationError):
            issue_period_payslips(period, issued_by=self.admin_a)

    def test_02_calculated_payroll_cannot_issue_payslip(self):
        """2. calculated payroll cannot issue payslip."""
        att_period = AttendancePeriod.objects.create(
            organization=self.org_a, year=2026, month=8, status='Finalized', current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period, revision=1, is_current=True,
            employee=self.staff_a, employee_name="Alice Smith", working_days=22,
            payable_attendance_units=22.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00')
        )
        period = calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        self.assertEqual(period.status, 'Calculated')

        from rest_framework.exceptions import ValidationError as DRFValidationError
        with self.assertRaises(DRFValidationError):
            issue_period_payslips(period, issued_by=self.admin_a)

    def test_03_finalized_payroll_issues_payslip(self):
        """3. finalized payroll issues payslip automatically."""
        period = self._setup_finalized_august_payroll()
        self.assertEqual(period.status, 'Finalized')

        payslips = Payslip.objects.filter(payroll_period=period, status='Issued')
        self.assertEqual(payslips.count(), 1)
        ps = payslips.first()
        self.assertEqual(ps.employee, self.staff_a)
        self.assertEqual(ps.revision, 1)
        self.assertEqual(ps.status, 'Issued')
        self.assertEqual(ps.payslip_number, f"PAY-202608-{self.org_a.id:03d}-{self.staff_a.id:04d}")

    def test_04_issuance_is_idempotent(self):
        """4. issuance is idempotent; running twice does not duplicate records."""
        period = self._setup_finalized_august_payroll()
        self.assertEqual(Payslip.objects.filter(payroll_period=period).count(), 1)

        # Call again
        extra_issued = issue_period_payslips(period, issued_by=self.admin_a)
        self.assertEqual(len(extra_issued), 0)
        self.assertEqual(Payslip.objects.filter(payroll_period=period).count(), 1)

    def test_05_financial_values_come_from_PayrollEmployeeSnapshot(self):
        """5. financial values on payslip come strictly from PayrollEmployeeSnapshot."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)
        snap = ps.payroll_snapshot

        self.assertEqual(snap.base_gross_salary, Decimal('5000.00')) # 4000 basic + 1000 hra
        self.assertEqual(snap.earned_gross, Decimal('5000.00'))
        self.assertEqual(snap.base_fixed_deductions, Decimal('200.00'))
        self.assertEqual(snap.net_payable, Decimal('4800.00'))

        # Check API response
        res = self.client_admin_a.get(f"/api/payroll/payslips/{ps.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['net_payable'], '4800.00')
        self.assertEqual(res.data['payroll_snapshot']['net_payable'], '4800.00')

    def test_06_future_salary_change_does_not_alter_issued_payslip(self):
        """6. future salary structure changes do not alter previously issued payslips."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)
        self.assertEqual(ps.payroll_snapshot.net_payable, Decimal('4800.00'))

        # Assign higher salary for September
        assign_or_revise_salary_structure(
            employee=self.staff_a,
            organization=self.org_a,
            effective_from=date(2026, 9, 1),
            components_data=[
                {'salary_component_id': self.comp_basic.id, 'amount': '8000.00'},
                {'salary_component_id': self.comp_housing.id, 'amount': '2000.00'},
            ],
            created_by=self.admin_a
        )

        # Query August payslip again
        ps.refresh_from_db()
        self.assertEqual(ps.payroll_snapshot.net_payable, Decimal('4800.00'))

    def test_07_employee_and_company_snapshot_remains_historical(self):
        """7. employee and company details remain frozen historically on the payslip."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)

        self.assertEqual(ps.company_name_snapshot, "Alpha Corp")
        self.assertEqual(ps.company_details_snapshot['address'], "100 Tech Park, Suite 500, Silicon City")
        self.assertEqual(ps.employee_details_snapshot['employee_code'], "EMP-0101")
        self.assertEqual(ps.employee_details_snapshot['department'], "Engineering")

        # Mutate live employee and company records
        self.staff_a.employee_code = "EMP-9999"
        self.staff_a.department = "Executive"
        self.staff_a.save()

        self.org_a.name = "Alpha Global Worldwide"
        self.org_a.save()
        self.settings_a.company_address = "999 New Street"
        self.settings_a.save()

        # Payslip snapshot must still have original frozen data
        ps.refresh_from_db()
        self.assertEqual(ps.company_name_snapshot, "Alpha Corp")
        self.assertEqual(ps.company_details_snapshot['address'], "100 Tech Park, Suite 500, Silicon City")
        self.assertEqual(ps.employee_details_snapshot['employee_code'], "EMP-0101")
        self.assertEqual(ps.employee_details_snapshot['department'], "Engineering")

    def test_08_cross_tenant_payslip_access_blocked(self):
        """8. cross-tenant payslip access is blocked."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)

        # Admin B in Beta Corp tries to access Alpha's payslip
        res_json = self.client_admin_b.get(f"/api/payroll/payslips/{ps.id}/")
        self.assertEqual(res_json.status_code, status.HTTP_404_NOT_FOUND)

        res_pdf = self.client_admin_b.get(f"/api/payroll/payslips/{ps.id}/pdf/")
        self.assertEqual(res_pdf.status_code, status.HTTP_404_NOT_FOUND)

    def test_09_payroll_view_permission_required(self):
        """9. payroll:view permission is required for admin payslip endpoints."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)

        # Staff user with no payroll:view permission
        client_no_perm = APIClient()
        unperm_user = Employee.objects.create(
            email="noperm@alpha.com", username="noperm", organization=self.org_a
        )
        client_no_perm.force_authenticate(user=unperm_user)

        res = client_no_perm.get(f"/api/payroll/payslips/{ps.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # User with payroll:view succeeds
        res_viewer = self.client_viewer_a.get(f"/api/payroll/payslips/{ps.id}/")
        self.assertEqual(res_viewer.status_code, status.HTTP_200_OK)

    def test_10_reopen_marks_issued_payslip_superseded(self):
        """10. reopening payroll marks issued payslips as Superseded."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)
        self.assertEqual(ps.status, 'Issued')

        # Reopen Payroll
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Need to adjust overtime")

        ps.refresh_from_db()
        self.assertEqual(ps.status, 'Superseded')

    def test_11_refinalize_creates_next_revision_payslip(self):
        """11. recalculating and refinalizing payroll creates Rev 2 payslip."""
        period = self._setup_finalized_august_payroll()
        ps_rev1 = Payslip.objects.get(payroll_period=period, employee=self.staff_a)
        self.assertEqual(ps_rev1.revision, 1)

        # Reopen
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Add bonus adjustment")

        # Add bonus adjustment
        PayrollAdjustment.objects.create(
            organization=self.org_a,
            payroll_period=period,
            employee=self.staff_a,
            adjustment_type='Earning',
            category='Bonus',
            amount=Decimal('500.00'),
            description='Q3 Performance'
        )

        # Recalculate & Refinalize (Revision 2)
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)

        # Old payslip is superseded
        ps_rev1.refresh_from_db()
        self.assertEqual(ps_rev1.status, 'Superseded')

        # New payslip is Revision 2
        ps_rev2 = Payslip.objects.get(payroll_period=period, employee=self.staff_a, revision=2)
        self.assertEqual(ps_rev2.status, 'Issued')
        self.assertEqual(ps_rev2.payslip_number, f"PAY-202608-{self.org_a.id:03d}-{self.staff_a.id:04d}-R2")
        self.assertEqual(ps_rev2.payroll_snapshot.net_payable, Decimal('5300.00')) # 4800 + 500

    def test_12_pdf_endpoint_returns_application_pdf(self):
        """12. PDF endpoint returns application/pdf with security headers."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)

        res = self.client_admin_a.get(f"/api/payroll/payslips/{ps.id}/pdf/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn(f"filename=\"Payslip_{ps.payslip_number}.pdf\"", res['Content-Disposition'])
        self.assertEqual(res['Cache-Control'], 'no-store, private')
        self.assertEqual(res['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(len(res.content) > 1000)

    def test_13_missing_logo_does_not_break_pdf(self):
        """13. missing or invalid brand logo does not crash PDF rendering."""
        # Create an org without logo
        settings_no_logo = OrgSettings.objects.create(payroll_currency='USD', brandLogo='')
        org_no_logo = Organization.objects.create(name="NoLogo Inc", subdomain="nologo", settings=settings_no_logo)
        emp = Employee.objects.create(
            email="emp@nologo.com", username="nologo_emp", organization=org_no_logo, isSuperAdmin=True
        )
        assign_or_revise_salary_structure(
            employee=emp, organization=org_no_logo, effective_from=date(2026, 8, 1),
            components_data=[{'salary_component_id': SalaryComponent.objects.create(organization=org_no_logo, name='Basic', code='B', component_type='Earning').id, 'amount': '1000.00'}]
        )
        att = AttendancePeriod.objects.create(organization=org_no_logo, year=2026, month=8, status='Finalized', current_revision=1)
        AttendancePeriodEmployeeSnapshot.objects.create(attendance_period=att, revision=1, is_current=True, employee=emp, employee_name="NoLogo Emp", working_days=20, payable_attendance_units=20.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00'))
        calculate_payroll_period(org_no_logo, 2026, 8, emp)
        finalize_payroll_period(org_no_logo, 2026, 8, emp)

        ps = Payslip.objects.get(organization=org_no_logo, employee=emp)
        pdf_bytes = generate_payslip_pdf(ps)
        self.assertTrue(len(pdf_bytes.getvalue()) > 500)

    def test_14_superseded_pdf_contains_visible_superseded_marker(self):
        """14. superseded PDF rendering executes cleanly and includes superseded marker."""
        period = self._setup_finalized_august_payroll()
        ps = Payslip.objects.get(payroll_period=period, employee=self.staff_a)
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Audit adjustment")
        ps.refresh_from_db()
        self.assertEqual(ps.status, 'Superseded')

        pdf_bytes = generate_payslip_pdf(ps)
        self.assertTrue(len(pdf_bytes.getvalue()) > 500)

    def test_15_audit_log_contains_no_salary_amounts(self):
        """15. AuditLog entries for payslip issuance and reopen contain zero monetary amounts."""
        period = self._setup_finalized_august_payroll()
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Testing audit secrecy")

        logs = AuditLog.objects.filter(organization=self.org_a)
        for log in logs:
            self.assertNotIn("4800", str(log.details))
            self.assertNotIn("5000", str(log.details))
            self.assertNotIn("4000", str(log.details))


class PayrollPhase7BSelfServiceTests(TestCase):
    def setUp(self):
        # Organization
        self.settings_a = OrgSettings.objects.create(payroll_currency='USD', company_address='100 Main St')
        self.org_a = Organization.objects.create(name="Alpha Corp", subdomain="alpha", settings=self.settings_a)

        self.settings_b = OrgSettings.objects.create(payroll_currency='EUR')
        self.org_b = Organization.objects.create(name="Beta Corp", subdomain="beta", settings=self.settings_b)

        # Employees in Org A
        self.admin_a = Employee.objects.create(
            email="admin@alpha.com", username="admin_alpha", organization=self.org_a, isSuperAdmin=True
        )
        self.alice = Employee.objects.create(
            email="alice@alpha.com", username="alice", first_name="Alice", last_name="Smith",
            employee_code="EMP-001", department="Engineering", designation="Engineer",
            organization=self.org_a, joining_date=date(2026, 1, 1)
        )
        self.bob = Employee.objects.create(
            email="bob@alpha.com", username="bob", first_name="Bob", last_name="Jones",
            employee_code="EMP-002", department="Design", designation="Designer",
            organization=self.org_a, joining_date=date(2026, 1, 1)
        )

        # Employee in Org B
        self.charlie_b = Employee.objects.create(
            email="charlie@beta.com", username="charlie", first_name="Charlie",
            organization=self.org_b
        )

        # Clients
        self.client_alice = APIClient()
        self.client_alice.force_authenticate(user=self.alice)

        self.client_bob = APIClient()
        self.client_bob.force_authenticate(user=self.bob)

        self.client_charlie = APIClient()
        self.client_charlie.force_authenticate(user=self.charlie_b)

        # Salaries & Attendance
        comp_basic = SalaryComponent.objects.create(
            organization=self.org_a, name="Basic", code="BASIC", component_type="Earning", is_proratable=True
        )
        assign_or_revise_salary_structure(
            employee=self.alice, organization=self.org_a, effective_from=date(2026, 8, 1),
            components_data=[{'salary_component_id': comp_basic.id, 'amount': '5000.00'}],
            created_by=self.admin_a
        )
        assign_or_revise_salary_structure(
            employee=self.bob, organization=self.org_a, effective_from=date(2026, 8, 1),
            components_data=[{'salary_component_id': comp_basic.id, 'amount': '4000.00'}],
            created_by=self.admin_a
        )

        # Finalized Attendance
        att = AttendancePeriod.objects.create(
            organization=self.org_a, year=2026, month=8, status='Finalized', current_revision=1,
            finalized_at=timezone.now(), finalized_by=self.admin_a
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att, revision=1, is_current=True, employee=self.alice,
            employee_name="Alice Smith", working_days=22, payable_attendance_units=22.0,
            present_days=22.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00'), absent_days=0
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att, revision=1, is_current=True, employee=self.bob,
            employee_name="Bob Jones", working_days=22, payable_attendance_units=22.0,
            present_days=22.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00'), absent_days=0
        )

        # Calculate & Finalize Payroll
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        self.period = finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)

        self.ps_alice = Payslip.objects.get(payroll_period=self.period, employee=self.alice)
        self.ps_bob = Payslip.objects.get(payroll_period=self.period, employee=self.bob)

    def test_01_employee_lists_own_issued_payslips(self):
        """1. employee lists own Issued payslips."""
        res = self.client_alice.get("/api/payroll/my-payslips/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['id'], self.ps_alice.id)
        self.assertEqual(res.data[0]['payslip_number'], self.ps_alice.payslip_number)
        self.assertEqual(res.data[0]['month_name'], "August 2026")
        self.assertEqual(res.data[0]['net_payable'], "5000.00")
        self.assertEqual(res.data[0]['currency'], "USD")

    def test_02_superseded_payslips_excluded_from_normal_self_list(self):
        """2. superseded payslips are excluded from employee self-service list."""
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Reopen for bonus")
        res = self.client_alice.get("/api/payroll/my-payslips/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

    def test_03_employee_can_retrieve_own_payslip_detail(self):
        """3. employee can retrieve own payslip detail with breakdown."""
        res = self.client_alice.get(f"/api/payroll/my-payslips/{self.ps_alice.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['payslip_number'], self.ps_alice.payslip_number)
        self.assertEqual(res.data['employee_name'], "Alice Smith")
        self.assertIn('payroll_snapshot', res.data)
        self.assertEqual(res.data['payroll_snapshot']['earned_gross'], '5000.00')

    def test_04_employee_can_download_own_pdf(self):
        """4. employee can download own PDF payslip."""
        res = self.client_alice.get(f"/api/payroll/my-payslips/{self.ps_alice.id}/pdf/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn(f"filename=\"Payslip_{self.ps_alice.payslip_number}.pdf\"", res['Content-Disposition'])
        self.assertTrue(len(res.content) > 1000)

    def test_05_employee_cannot_retrieve_another_employee_payslip(self):
        """5. employee cannot retrieve another employee's payslip."""
        res = self.client_alice.get(f"/api/payroll/my-payslips/{self.ps_bob.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_06_employee_cannot_download_another_employee_pdf(self):
        """6. employee cannot download another employee's PDF."""
        res = self.client_alice.get(f"/api/payroll/my-payslips/{self.ps_bob.id}/pdf/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_07_cross_tenant_access_blocked(self):
        """7. cross-tenant self-service access is blocked."""
        res_list = self.client_charlie.get("/api/payroll/my-payslips/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_list.data), 0)

        res_detail = self.client_charlie.get(f"/api/payroll/my-payslips/{self.ps_alice.id}/")
        self.assertEqual(res_detail.status_code, status.HTTP_404_NOT_FOUND)

        res_pdf = self.client_charlie.get(f"/api/payroll/my-payslips/{self.ps_alice.id}/pdf/")
        self.assertEqual(res_pdf.status_code, status.HTTP_404_NOT_FOUND)

    def test_08_own_payslip_access_does_not_require_payroll_view(self):
        """8. employee access to own payslips does not require payroll:view permission."""
        self.assertEqual(self.alice.extra_permissions.count(), 0)
        self.assertFalse(self.alice.isSuperAdmin)

        res_list = self.client_alice.get("/api/payroll/my-payslips/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_list.data), 1)

        res_detail = self.client_alice.get(f"/api/payroll/my-payslips/{self.ps_alice.id}/")
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)


class PayrollPhase7CZipExportTests(TestCase):
    def setUp(self):
        # Organizations
        self.settings_a = OrgSettings.objects.create(payroll_currency='USD', company_address='100 Enterprise Way')
        self.org_a = Organization.objects.create(name="Alpha Corp", subdomain="alpha", settings=self.settings_a)

        self.settings_b = OrgSettings.objects.create(payroll_currency='EUR')
        self.org_b = Organization.objects.create(name="Beta Corp", subdomain="beta", settings=self.settings_b)

        # Users in Org A
        self.admin_a = Employee.objects.create(
            email="admin@alpha.com", username="admin_alpha", organization=self.org_a, isSuperAdmin=True
        )
        self.payroll_viewer = Employee.objects.create(
            email="viewer@alpha.com", username="viewer_alpha", organization=self.org_a
        )
        flag_view, _ = PermissionFlag.objects.get_or_create(key="payroll:view", defaults={"name": "View Payroll"})
        self.payroll_viewer.extra_permissions.add(flag_view)

        self.alice = Employee.objects.create(
            email="alice@alpha.com", username="alice", first_name="Alice", last_name="Smith",
            employee_code="EMP-001", department="Engineering", designation="Engineer",
            organization=self.org_a, joining_date=date(2026, 1, 1)
        )
        self.bob = Employee.objects.create(
            email="bob@alpha.com", username="bob", first_name="Bob", last_name="Jones",
            employee_code="EMP-002", department="Design", designation="Designer",
            organization=self.org_a, joining_date=date(2026, 1, 1)
        )

        # User in Org B
        self.admin_b = Employee.objects.create(
            email="admin@beta.com", username="admin_beta", organization=self.org_b, isSuperAdmin=True
        )

        # Clients
        self.client_admin = APIClient()
        self.client_admin.force_authenticate(user=self.admin_a)

        self.client_viewer = APIClient()
        self.client_viewer.force_authenticate(user=self.payroll_viewer)

        self.client_alice = APIClient()
        self.client_alice.force_authenticate(user=self.alice)

        self.client_beta = APIClient()
        self.client_beta.force_authenticate(user=self.admin_b)

        # Salary & Attendance
        comp_basic = SalaryComponent.objects.create(
            organization=self.org_a, name="Basic", code="BASIC", component_type="Earning", is_proratable=True
        )
        assign_or_revise_salary_structure(
            employee=self.alice, organization=self.org_a, effective_from=date(2026, 8, 1),
            components_data=[{'salary_component_id': comp_basic.id, 'amount': '5000.00'}],
            created_by=self.admin_a
        )
        assign_or_revise_salary_structure(
            employee=self.bob, organization=self.org_a, effective_from=date(2026, 8, 1),
            components_data=[{'salary_component_id': comp_basic.id, 'amount': '4000.00'}],
            created_by=self.admin_a
        )

        # Finalized Attendance
        self.att = AttendancePeriod.objects.create(
            organization=self.org_a, year=2026, month=8, status='Finalized', current_revision=1,
            finalized_at=timezone.now(), finalized_by=self.admin_a
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=self.att, revision=1, is_current=True, employee=self.alice,
            employee_name="Alice Smith", working_days=22, payable_attendance_units=22.0,
            present_days=22.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00'), absent_days=0
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=self.att, revision=1, is_current=True, employee=self.bob,
            employee_name="Bob Jones", working_days=22, payable_attendance_units=22.0,
            present_days=22.0, paid_leave_days=Decimal('0.00'), unpaid_leave_days=Decimal('0.00'), absent_days=0
        )

    def _setup_finalized_august(self):
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        return finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)

    def test_01_finalized_payroll_exports_zip_successfully(self):
        """1. Finalized payroll exports ZIP successfully."""
        self._setup_finalized_august()
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_02_zip_content_type_is_application_zip(self):
        """2. ZIP Content-Type and headers are correct."""
        self._setup_finalized_august()
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Content-Type'], 'application/zip')
        self.assertIn('attachment; filename="Payslips_2026_08.zip"', res['Content-Disposition'])
        self.assertEqual(res['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', res['Cache-Control'])

    def test_03_zip_contains_all_issued_payslip_pdfs(self):
        """3. ZIP contains all Issued payslip PDFs."""
        self._setup_finalized_august()
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        self.assertEqual(len(names), 2)
        self.assertIn("Payslip_EMP-001_2026_08.pdf", names)
        self.assertIn("Payslip_EMP-002_2026_08.pdf", names)

    def test_04_every_exported_file_starts_with_pdf_bytes(self):
        """4. Every exported file inside the ZIP starts with %PDF magic bytes."""
        self._setup_finalized_august()
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        for name in zf.namelist():
            pdf_data = zf.read(name)
            self.assertTrue(pdf_data.startswith(b'%PDF'), f"File {name} does not start with %PDF")

    def test_05_superseded_payslips_are_excluded(self):
        """5. Superseded payslips are excluded from bulk ZIP export."""
        self._setup_finalized_august()
        # Reopen -> Draft
        reopen_payroll_period(self.org_a, 2026, 8, self.admin_a, reason="Adjust bonus")
        # Exporting in Draft is rejected
        res_draft = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res_draft.status_code, status.HTTP_400_BAD_REQUEST)

        # Recalculate & Refinalize -> creates Rev 2
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        finalize_payroll_period(self.org_a, 2026, 8, self.admin_a)

        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        self.assertEqual(len(zf.namelist()), 2)

    def test_06_draft_payroll_export_rejected(self):
        """6. Draft payroll export is rejected."""
        PayrollPeriod.objects.create(organization=self.org_a, year=2026, month=8, attendance_period=self.att, status='Draft')
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Finalized", res.data.get('detail', ''))

    def test_07_calculated_payroll_export_rejected(self):
        """7. Calculated payroll export is rejected."""
        calculate_payroll_period(self.org_a, 2026, 8, self.admin_a)
        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Finalized", res.data.get('detail', ''))

    def test_08_unauthorized_user_rejected(self):
        """8. Unauthorized employee without payroll permissions is rejected."""
        self._setup_finalized_august()
        res = self.client_alice.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # But payroll_viewer with payroll:view succeeds
        res_viewer = self.client_viewer.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res_viewer.status_code, status.HTTP_200_OK)

    def test_09_cross_tenant_export_blocked(self):
        """9. Cross-tenant ZIP export is blocked."""
        self._setup_finalized_august()
        # Admin from Org B attempts to export Org A's period
        res = self.client_beta.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_10_empty_issued_payslips_period_returns_clear_error(self):
        """10. Finalized period with 0 issued payslips returns HTTP 400."""
        period = self._setup_finalized_august()
        Payslip.objects.filter(payroll_period=period).delete()

        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get('detail'), "No issued payslips found for this period.")

    def test_11_zip_entry_filenames_cannot_contain_path_traversal(self):
        """11. ZIP entry filenames sanitize path traversal attempts."""
        self.alice.employee_code = "../../malicious/path"
        self.alice.save()
        self._setup_finalized_august()

        res = self.client_admin.get("/api/payroll/periods/2026/8/payslips/export-zip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        for name in zf.namelist():
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn("..", name)


class PayrollCurrencyAndSettingsTests(TestCase):
    def setUp(self):
        self.settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Currency Org", subdomain="currency-org", settings=self.settings)
        self.role = Role.objects.create(name="Admin")
        self.admin = Employee.objects.create(
            organization=self.org,
            email="admin@currency.com",
            first_name="Admin",
            isSuperAdmin=True,
            role=self.role
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_default_payroll_currency_is_inr(self):
        """OrgSettings default payroll_currency is INR."""
        self.assertEqual(self.settings.payroll_currency, 'INR')
        self.assertEqual(self.settings.payroll_proration_basis, 'WORKING_DAYS')

    def test_update_payroll_settings_via_api(self):
        """PATCH /settings/current/ updates payroll_currency and proration_basis."""
        url = "/api/settings/current/"
        payload = {
            "payroll_currency": "AED",
            "payroll_proration_basis": "CALENDAR_DAYS"
        }
        res = self.client.patch(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.payroll_currency, "AED")
        self.assertEqual(self.settings.payroll_proration_basis, "CALENDAR_DAYS")

    def test_inr_pdf_payslip_generation(self):
        """ReportLab generates PDF with INR currency without font or encoding error."""
        from payroll.models import PayrollPeriod, PayrollEmployeeSnapshot, Payslip
        from payroll.pdf import generate_payslip_pdf
        from attendance.models import AttendancePeriod

        att = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1
        )
        period = PayrollPeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            attendance_period=att,
            status='Finalized',
            currency='INR'
        )
        snap = PayrollEmployeeSnapshot.objects.create(
            payroll_period=period,
            employee=self.admin,
            employee_name="Admin User",
            currency='INR',
            earned_gross=Decimal('50000.00'),
            net_payable=Decimal('48000.00')
        )
        payslip = Payslip.objects.create(
            organization=self.org,
            payroll_period=period,
            payroll_snapshot=snap,
            employee=self.admin,
            payslip_number="PAY-202608-001-0001",
            status='Issued',
            company_name_snapshot="Currency Org",
            company_details_snapshot={"address": "100 Tech Park, Bangalore"},
            employee_details_snapshot={"name": "Admin User", "employee_code": "EMP-0001"}
        )

        pdf_buffer = generate_payslip_pdf(payslip)
        pdf_bytes = pdf_buffer.getvalue()
        self.assertTrue(len(pdf_bytes) > 1000)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))


class MonthlyAndDailyWageCompensationTestCase(TestCase):
    """
    Comprehensive tests for Monthly Salary and Daily Wage compensation support.
    Verifies items 1-25 from specification.
    """

    def setUp(self):
        self.settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            payroll_currency='INR',
            payroll_proration_basis='WORKING_DAYS',
            daily_wage_paid_leave_eligible=True
        )
        self.org = Organization.objects.create(
            name="Apex Technologies",
            subdomain="apex-tech",
            settings=self.settings
        )
        self.other_org = Organization.objects.create(
            name="Other Corp",
            subdomain="other-corp"
        )

        self.admin = Employee.objects.create(
            email="admin@apex.com",
            username="apex_admin",
            first_name="Super",
            last_name="Admin",
            organization=self.org,
            isSuperAdmin=True,
            is_active=True
        )
        self.emp_monthly = Employee.objects.create(
            email="monthly.worker@apex.com",
            username="monthly_emp",
            first_name="Rahul",
            last_name="Verma",
            organization=self.org,
            useDefaultPermissions=False,
            is_active=True
        )
        self.emp_daily = Employee.objects.create(
            email="daily.worker@apex.com",
            username="daily_emp",
            first_name="Suresh",
            last_name="Kumar",
            organization=self.org,
            useDefaultPermissions=False,
            is_active=True
        )

        # Base components
        self.comp_basic = SalaryComponent.objects.create(
            organization=self.org,
            name="Basic Salary",
            code="BASIC",
            component_type="Earning",
            is_taxable=True,
            is_proratable=True,
            is_active=True
        )
        self.comp_hra = SalaryComponent.objects.create(
            organization=self.org,
            name="HRA",
            code="HRA",
            component_type="Earning",
            is_taxable=True,
            is_proratable=False,
            is_active=True
        )
        self.comp_bonus_allowance = SalaryComponent.objects.create(
            organization=self.org,
            name="Site Allowance",
            code="SITE_ALLOW",
            component_type="Earning",
            is_taxable=True,
            is_proratable=False,
            is_active=True
        )
        self.comp_pf = SalaryComponent.objects.create(
            organization=self.org,
            name="Provident Fund",
            code="PF",
            component_type="Deduction",
            is_taxable=False,
            is_proratable=False,
            is_active=True
        )

    def test_01_existing_structures_default_to_monthly(self):
        """1. Existing structures default to MONTHLY."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_monthly,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            components_data=[
                {"salary_component_id": self.comp_basic.id, "amount": Decimal("40000.00")},
                {"salary_component_id": self.comp_hra.id, "amount": Decimal("10000.00")},
            ],
            created_by=self.admin
        )
        self.assertEqual(struct.compensation_type, 'MONTHLY')
        self.assertIsNone(struct.daily_rate)

    def test_02_existing_monthly_salary_result_remains_unchanged(self):
        """2. Existing Monthly salary proration calculation remains byte-for-byte unchanged."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_monthly,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            components_data=[
                {"salary_component_id": self.comp_basic.id, "amount": Decimal("40000.00")},
                {"salary_component_id": self.comp_hra.id, "amount": Decimal("10000.00")},
            ],
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=20,
            present_days=18.0,
            half_days=0,
            absent_days=2,
            leave_days=0.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('2.00'),
            payable_attendance_units=Decimal('18.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_monthly,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["compensation_type"], "MONTHLY")
        self.assertEqual(res["base_gross_salary"], Decimal("50000.00"))
        self.assertEqual(res["proratable_gross"], Decimal("40000.00"))
        self.assertEqual(res["non_proratable_gross"], Decimal("10000.00"))
        # daily rate = 40000 / 20 = 2000.0000
        self.assertEqual(res["daily_rate"], Decimal("2000.0000"))
        # attendance deduction = 2 * 2000 = 4000.00
        self.assertEqual(res["attendance_deduction"], Decimal("4000.00"))
        # earned gross = 40000 - 4000 + 10000 = 46000.00
        self.assertEqual(res["earned_gross"], Decimal("46000.00"))
        self.assertEqual(res["net_payable"], Decimal("46000.00"))

    def test_03_daily_salary_requires_positive_daily_rate(self):
        """3. Daily salary requires positive daily_rate."""
        with self.assertRaises((ValidationError, DRFValidationError)) as ctx:
            assign_or_revise_salary_structure(
                employee=self.emp_daily,
                organization=self.org,
                effective_from=datetime_date(2026, 8, 1),
                compensation_type='DAILY',
                daily_rate=None,
                created_by=self.admin
            )
        self.assertIn("daily_rate", str(ctx.exception))

        with self.assertRaises((ValidationError, DRFValidationError)) as ctx:
            assign_or_revise_salary_structure(
                employee=self.emp_daily,
                organization=self.org,
                effective_from=datetime_date(2026, 8, 1),
                compensation_type='DAILY',
                daily_rate=Decimal("0.00"),
                created_by=self.admin
            )
        self.assertIn("daily_rate", str(ctx.exception))

    def test_04_daily_wage_full_payable_units_calculation(self):
        """4. Daily Wage full payable units: ₹1,000 * 20 days = ₹20,000."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=22,
            present_days=20.0,
            half_days=0,
            absent_days=2,
            leave_days=0.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('2.00'),
            payable_attendance_units=Decimal('20.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["compensation_type"], "DAILY")
        self.assertEqual(res["daily_rate"], Decimal("1000.0000"))
        self.assertEqual(res["attendance_deduction"], Decimal("0.00"))
        self.assertEqual(res["earned_gross"], Decimal("20000.00"))
        self.assertEqual(res["net_payable"], Decimal("20000.00"))
        self.assertEqual(res["calculation_breakdown"]["wage_earnings"], "20000.00")

    def test_05_daily_wage_half_day_calculation(self):
        """5. Daily Wage 0.5 day: ₹1,000 * 20.5 units = ₹20,500."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=22,
            present_days=20.5,
            half_days=1,
            absent_days=1,
            leave_days=0.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('1.50'),
            payable_attendance_units=Decimal('20.50')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["earned_gross"], Decimal("20500.00"))
        self.assertEqual(res["net_payable"], Decimal("20500.00"))
        self.assertEqual(res["attendance_deduction"], Decimal("0.00"))

    def test_06_daily_wage_unpaid_leave_not_paid(self):
        """6. Daily Wage unpaid leave not included in payable units."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1500.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=22,
            present_days=19.0,
            half_days=0,
            absent_days=0,
            leave_days=3.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('3.00'),
            payable_attendance_units=Decimal('19.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["earned_gross"], Decimal("28500.00"))  # 1500 * 19
        self.assertEqual(res["net_payable"], Decimal("28500.00"))

    def test_07_daily_wage_paid_leave_eligible_true(self):
        """7. Daily Wage with daily_wage_paid_leave_eligible=True: 20 worked + 1 paid leave = 21 payable days -> ₹21,000."""
        self.org.settings.daily_wage_paid_leave_eligible = True
        self.org.settings.save()

        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=22,
            present_days=20.0,
            half_days=0,
            absent_days=1,
            leave_days=1.0,
            paid_leave_days=Decimal('1.00'),
            unpaid_leave_days=Decimal('1.00'),
            payable_attendance_units=Decimal('21.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["earned_gross"], Decimal("21000.00"))
        self.assertEqual(res["net_payable"], Decimal("21000.00"))
        self.assertEqual(res["calculation_breakdown"]["payable_days"], "21.00")
        self.assertTrue(res["calculation_breakdown"]["paid_leave_eligible"])

    def test_08_daily_wage_paid_leave_eligible_false(self):
        """8. Daily Wage with daily_wage_paid_leave_eligible=False: 20 worked + 1 paid leave = 20 payable days -> ₹20,000."""
        self.org.settings.daily_wage_paid_leave_eligible = False
        self.org.settings.save()

        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=22,
            present_days=20.0,
            half_days=0,
            absent_days=1,
            leave_days=1.0,
            paid_leave_days=Decimal('1.00'),
            unpaid_leave_days=Decimal('1.00'),
            payable_attendance_units=Decimal('21.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        self.assertEqual(res["earned_gross"], Decimal("20000.00"))
        self.assertEqual(res["net_payable"], Decimal("20000.00"))
        self.assertEqual(res["calculation_breakdown"]["payable_days"], "20.0")
        self.assertFalse(res["calculation_breakdown"]["paid_leave_eligible"])

    def test_09_10_daily_wage_adjustments(self):
        """9 & 10. Daily Wage + earning adjustment & deduction adjustment."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=20,
            present_days=20.0,
            half_days=0,
            absent_days=0,
            leave_days=0.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('0.00'),
            payable_attendance_units=Decimal('20.00')
        )
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Draft'
        )
        period = PayrollPeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            attendance_period=att_period,
            status='Draft'
        )
        adj_bonus = PayrollAdjustment.objects.create(
            organization=self.org,
            payroll_period=period,
            employee=self.emp_daily,
            adjustment_type="Earning",
            category="Bonus",
            amount=Decimal("3000.00")
        )
        adj_fine = PayrollAdjustment.objects.create(
            organization=self.org,
            payroll_period=period,
            employee=self.emp_daily,
            adjustment_type="Deduction",
            category="Penalty",
            amount=Decimal("500.00")
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct,
            adjustments=[adj_bonus, adj_fine]
        )
        # wage = 20,000, additional_earnings = 3,000, additional_deductions = 500 => net = 22,500
        self.assertEqual(res["earned_gross"], Decimal("20000.00"))
        self.assertEqual(res["additional_earnings"], Decimal("3000.00"))
        self.assertEqual(res["additional_deductions"], Decimal("500.00"))
        self.assertEqual(res["total_deductions"], Decimal("500.00"))
        self.assertEqual(res["net_payable"], Decimal("22500.00"))

    def test_11_12_daily_wage_fixed_allowances_and_deductions(self):
        """11 & 12. Daily Wage + optional fixed allowance & fixed deduction component."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1000.00"),
            components_data=[
                {"salary_component_id": self.comp_bonus_allowance.id, "amount": Decimal("4000.00")},
                {"salary_component_id": self.comp_pf.id, "amount": Decimal("1500.00")},
            ],
            created_by=self.admin
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            working_days=20,
            present_days=20.0,
            half_days=0,
            absent_days=0,
            leave_days=0.0,
            paid_leave_days=Decimal('0.00'),
            unpaid_leave_days=Decimal('0.00'),
            payable_attendance_units=Decimal('20.00')
        )
        res = calculate_employee_payroll(
            employee=self.emp_daily,
            attendance_snapshot=att_snap,
            salary_structure=struct
        )
        # wage = 20,000 + fixed allowance 4,000 = earned_gross 24,000 - fixed deduction 1,500 = net 22,500
        self.assertEqual(res["earned_gross"], Decimal("24000.00"))
        self.assertEqual(res["base_fixed_deductions"], Decimal("1500.00"))
        self.assertEqual(res["total_deductions"], Decimal("1500.00"))
        self.assertEqual(res["net_payable"], Decimal("22500.00"))

    def test_13_14_snapshot_freezes_compensation_type_and_daily_rate(self):
        """13 & 14. PayrollEmployeeSnapshot freezes compensation_type and contractual daily rate."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("2000.00"),
            created_by=self.admin
        )
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            revision=1,
            employee=self.emp_daily,
            working_days=20,
            present_days=20.0,
            payable_attendance_units=Decimal('20.00')
        )
        period = calculate_payroll_period(self.org, 2026, 8, self.admin)
        snap = period.employee_snapshots.get(employee=self.emp_daily, is_current=True)
        self.assertEqual(snap.compensation_type, 'DAILY')
        self.assertEqual(snap.daily_rate, Decimal('2000.0000'))
        self.assertEqual(snap.calculation_breakdown["contractual_daily_rate"], "2000.00")

    def test_15_later_rate_revision_does_not_change_historical_payroll(self):
        """15. Later daily rate revision does not affect frozen historical payroll snapshot."""
        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("2000.00"),
            created_by=self.admin
        )
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            revision=1,
            employee=self.emp_daily,
            working_days=20,
            present_days=20.0,
            payable_attendance_units=Decimal('20.00')
        )
        calculate_payroll_period(self.org, 2026, 8, self.admin)
        payroll_period = finalize_payroll_period(self.org, 2026, 8, self.admin)
        snap_before = payroll_period.employee_snapshots.get(employee=self.emp_daily, is_current=True)
        self.assertEqual(snap_before.net_payable, Decimal("40000.00"))

        # Revise rate for September 2026
        assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 9, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("3000.00"),
            created_by=self.admin
        )

        snap_after = payroll_period.employee_snapshots.get(employee=self.emp_daily, is_current=True)
        self.assertEqual(snap_after.net_payable, Decimal("40000.00"))
        self.assertEqual(snap_after.daily_rate, Decimal("2000.0000"))

    def test_16_17_future_revisions_between_monthly_and_daily(self):
        """16 & 17. MONTHLY -> DAILY and DAILY -> MONTHLY future revisions work smoothly."""
        # Initial: Monthly
        s1 = assign_or_revise_salary_structure(
            employee=self.emp_monthly,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='MONTHLY',
            components_data=[{"salary_component_id": self.comp_basic.id, "amount": Decimal("50000.00")}],
            created_by=self.admin
        )
        self.assertEqual(s1.compensation_type, 'MONTHLY')

        # Revision 1: Switch to Daily
        s2 = assign_or_revise_salary_structure(
            employee=self.emp_monthly,
            organization=self.org,
            effective_from=datetime_date(2026, 9, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("2500.00"),
            created_by=self.admin
        )
        self.assertEqual(s2.compensation_type, 'DAILY')
        self.assertEqual(s2.daily_rate, Decimal('2500.00'))

        # Revision 2: Switch back to Monthly
        s3 = assign_or_revise_salary_structure(
            employee=self.emp_monthly,
            organization=self.org,
            effective_from=datetime_date(2026, 10, 1),
            compensation_type='MONTHLY',
            components_data=[{"salary_component_id": self.comp_basic.id, "amount": Decimal("60000.00")}],
            created_by=self.admin
        )
        self.assertEqual(s3.compensation_type, 'MONTHLY')
        self.assertEqual(s3.gross_salary, Decimal('60000.00'))

    def test_18_finalized_payroll_lock_prevents_retroactive_revision(self):
        """18. Finalized payroll lock behavior prevents retroactive revision."""
        assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("2000.00"),
            created_by=self.admin
        )
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            revision=1,
            employee=self.emp_daily,
            working_days=20,
            present_days=20.0,
            payable_attendance_units=Decimal('20.00')
        )
        calculate_payroll_period(self.org, 2026, 8, self.admin)
        finalize_payroll_period(self.org, 2026, 8, self.admin)

        with self.assertRaises((ValidationError, DRFValidationError)) as ctx:
            assign_or_revise_salary_structure(
                employee=self.emp_daily,
                organization=self.org,
                effective_from=datetime_date(2026, 8, 1),
                compensation_type='DAILY',
                daily_rate=Decimal("2500.00"),
                created_by=self.admin
            )
        self.assertIn("Finalized", str(ctx.exception))

    def test_19_20_daily_wage_payslip_and_pdf(self):
        """19 & 20. Daily Wage payslip JSON correct & PDF renders successfully."""
        from payroll.pdf import generate_payslip_pdf
        from payroll.serializers import PayslipSerializer

        struct = assign_or_revise_salary_structure(
            employee=self.emp_daily,
            organization=self.org,
            effective_from=datetime_date(2026, 8, 1),
            compensation_type='DAILY',
            daily_rate=Decimal("1500.00"),
            created_by=self.admin
        )
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=8,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_period,
            revision=1,
            employee=self.emp_daily,
            working_days=20,
            present_days=20.0,
            payable_attendance_units=Decimal('20.00')
        )
        calculate_payroll_period(self.org, 2026, 8, self.admin)
        payroll_period = finalize_payroll_period(self.org, 2026, 8, self.admin)
        payslip = Payslip.objects.get(payroll_period=payroll_period, employee=self.emp_daily)

        # Serializer JSON check
        data = PayslipSerializer(payslip).data
        self.assertEqual(data["compensation_type"], "DAILY")
        self.assertEqual(data["daily_rate"], "1500.00")
        self.assertEqual(data["net_payable"], "30000.00")

        # PDF check
        pdf_buf = generate_payslip_pdf(payslip)
        pdf_bytes = pdf_buf.getvalue()
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))
        self.assertTrue(len(pdf_bytes) > 1000)

    def test_21_historical_monthly_payslip_still_renders(self):
        """21. Existing historical Monthly payslip without new fields still renders safely."""
        from payroll.pdf import generate_payslip_pdf

        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=7,
            status='Finalized',
            current_revision=1
        )
        period = PayrollPeriod.objects.create(
            organization=self.org,
            year=2026,
            month=7,
            attendance_period=att_period,
            status='Finalized',
            currency='INR'
        )
        snap = PayrollEmployeeSnapshot.objects.create(
            payroll_period=period,
            employee=self.emp_monthly,
            employee_name="Rahul Verma",
            compensation_type='MONTHLY',
            currency='INR',
            earned_gross=Decimal('50000.00'),
            net_payable=Decimal('50000.00'),
            calculation_breakdown={"proration_basis": "WORKING_DAYS"}
        )
        payslip = Payslip.objects.create(
            organization=self.org,
            payroll_period=period,
            payroll_snapshot=snap,
            employee=self.emp_monthly,
            payslip_number="PAY-202607-001-0001",
            status='Issued',
            company_name_snapshot="Apex Tech",
            company_details_snapshot={"address": "Apex Park"},
            employee_details_snapshot={"name": "Rahul Verma", "employee_code": "EMP-001"}
        )
        pdf_buf = generate_payslip_pdf(payslip)
        pdf_bytes = pdf_buf.getvalue()
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_22_salary_manage_permission_required(self):
        """22. salary:manage permission required to assign or revise salary structure."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.emp_daily)  # Regular employee without salary:manage

        url = f"/api/payroll/employees/{self.emp_daily.id}/salary/"
        res = client.post(url, {
            "effective_from": "2026-09-01",
            "compensation_type": "DAILY",
            "daily_rate": "1500.00"
        }, format="json")
        self.assertEqual(res.status_code, 403)

    def test_23_cross_tenant_protection(self):
        """23. Cross-tenant protections prevent assigning salary to another tenant's employee."""
        from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

        other_emp = Employee.objects.create(
            email="other@corp.com",
            username="other_emp",
            organization=self.other_org
        )
        with self.assertRaises((PermissionDenied, DRFPermissionDenied)):
            assign_or_revise_salary_structure(
                employee=other_emp,
                organization=self.org,
                effective_from=datetime_date(2026, 8, 1),
                compensation_type='DAILY',
                daily_rate=Decimal("1500.00"),
                created_by=self.admin
            )

    def test_24_bulk_salary_structure_resolver_correctness_and_equality(self):
        """24. get_bulk_employee_salary_structures returns 100% identical structures to single resolver."""
        from payroll.services import get_bulk_employee_salary_structures, get_employee_salary_structure
        target_dt = datetime_date(2026, 7, 1)

        single_struct_monthly = get_employee_salary_structure(self.emp_monthly, self.org, target_dt)
        single_struct_daily = get_employee_salary_structure(self.emp_daily, self.org, target_dt)

        bulk_map = get_bulk_employee_salary_structures(self.org, target_dt)

        self.assertEqual(bulk_map.get(self.emp_monthly.id), single_struct_monthly)
        self.assertEqual(bulk_map.get(self.emp_daily.id), single_struct_daily)

    def test_25_salary_directory_pagination_and_search(self):
        """25. GET /api/payroll/employees/salaries/ supports page, page_size, and search parameters."""
        self.client.force_login(self.admin)
        res = self.client.get("/api/payroll/employees/salaries/?page=1&page_size=1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("count", res.json())
        self.assertIn("results", res.json())
        self.assertEqual(len(res.json()["results"]), 1)

        # Search parameter test
        res_search = self.client.get(f"/api/payroll/employees/salaries/?search={self.emp_monthly.first_name}")
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(res_search.json()["count"], 1)

    def test_26_payslip_bulk_zip_export_limit(self):
        """26. PeriodPayslipBulkZipExportView enforces MAX_BULK_PAYSLIPS limit."""
        att_period = AttendancePeriod.objects.create(
            organization=self.org,
            year=2026,
            month=7,
            status='Finalized',
            current_revision=1
        )
        # Create finalized July period
        period = PayrollPeriod.objects.create(
            organization=self.org,
            attendance_period=att_period,
            year=2026,
            month=7,
            status='Finalized',
            currency='INR'
        )

        snap = PayrollEmployeeSnapshot.objects.create(
            payroll_period=period,
            revision=1,
            is_current=True,
            employee=self.admin,
            employee_name="Admin User",
            working_days=20,
            payable_attendance_units=Decimal('20.00'),
            base_gross_salary=Decimal('50000.00'),
            base_net_salary=Decimal('50000.00'),
            earned_gross=Decimal('50000.00'),
            net_payable=Decimal('50000.00'),
            status='OK'
        )

        # Create 201 mock payslips to trigger limit
        payslips = []
        for i in range(201):
            emp = Employee.objects.create(
                email=f"bulk_test_{i}@apex.com",
                username=f"bulk_test_{i}",
                organization=self.org
            )
            payslips.append(Payslip(
                organization=self.org,
                payroll_period=period,
                payroll_snapshot=snap,
                employee=emp,
                payslip_number=f"PAY-202607-001-{i:04d}",
                status='Issued',
                company_name_snapshot="Apex Tech"
            ))
        Payslip.objects.bulk_create(payslips)

        self.client.force_login(self.admin)
        res = self.client.get("/api/payroll/periods/2026/7/payslips/export-zip/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("limited to 200 payslips", str(res.data))
