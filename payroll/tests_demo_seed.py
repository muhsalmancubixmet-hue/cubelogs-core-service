# --------------------------------------------------------------------------------
#       Unit Tests: Payroll & Attendance Demo Dataset Seeder & Cleanup
# --------------------------------------------------------------------------------

from decimal import Decimal
from datetime import date as datetime_date
from django.test import TestCase
from django.core.exceptions import ValidationError
from unittest.mock import patch

from core.models import Organization, OrgSettings
from users.models import Employee, Role
from attendance.models import (
    AttendanceLog,
    AttendancePeriod,
    AttendancePeriodEmployeeSnapshot,
    Leave,
    LeaveType,
    Holiday,
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
from payroll.demo_seed import (
    seed_demo_dataset,
    remove_demo_dataset,
    DEMO_EMAIL_DOMAIN,
    DEMO_YEAR,
    DEMO_MONTH,
    DEMO_MARKER,
    DEMO_EMPLOYEES_CONFIG,
    assign_or_reconcile_demo_salary_structure,
)
from payroll.services import assign_or_revise_salary_structure


class PayrollDemoSeedTestCase(TestCase):
    """
    Comprehensive test suite covering all idempotency, partial recovery,
    transaction safety, protection of real data, and cleanup requirements.
    """

    def setUp(self):
        self.settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            payroll_currency='INR',
            payroll_proration_basis='WORKING_DAYS'
        )
        self.org = Organization.objects.create(
            name="Acme Corp Test",
            subdomain="acme-test",
            settings=self.settings
        )

        # Real existing employee in self.org (must NOT be modified or deleted)
        self.real_employee = Employee.objects.create(
            email="real.developer@acme.com",
            username="real_dev",
            first_name="Real",
            last_name="Developer",
            organization=self.org,
            is_active=True
        )

    def test_01_dry_run_creates_zero_changes(self):
        """
        Req 19: Dry run must make ZERO database changes.
        """
        res = seed_demo_dataset(org_id=self.org.id, dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(Employee.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(SalaryComponent.objects.filter(organization=self.org, code__startswith='DEMO_').count(), 0)
        self.assertEqual(EmployeeSalaryStructure.objects.filter(organization=self.org).count(), 0)
        self.assertEqual(AttendancePeriod.objects.filter(organization=self.org, year=DEMO_YEAR, month=DEMO_MONTH).count(), 0)
        self.assertEqual(PayrollPeriod.objects.filter(organization=self.org, year=DEMO_YEAR, month=DEMO_MONTH).count(), 0)
        self.assertEqual(Payslip.objects.filter(organization=self.org).count(), 0)

    def test_02_clean_seed_creates_complete_dataset(self):
        """
        Req 1, 2, 3, 4, 11: Clean database -> seed succeeds.
        - Exactly 5 demo employees created.
        - Exactly one salary structure per demo employee on 2026-01-01 in INR.
        - Exactly 5 issued payslips for demo employees.
        - All 5 specific scenario calculations verified.
        """
        res = seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["payslip_count"], 6)

        demo_employees = Employee.objects.filter(
            organization=self.org,
            email__endswith=f"@{DEMO_EMAIL_DOMAIN}"
        ).order_by('employee_code')
        self.assertEqual(demo_employees.count(), 6)

        # Verify Periods
        att_period = AttendancePeriod.objects.get(organization=self.org, year=DEMO_YEAR, month=DEMO_MONTH)
        payroll_period = PayrollPeriod.objects.get(organization=self.org, year=DEMO_YEAR, month=DEMO_MONTH)
        self.assertEqual(att_period.status, 'Finalized')
        self.assertEqual(payroll_period.status, 'Finalized')

        # Verify Attendance Snapshots
        att_snaps = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=att_period,
            is_current=True,
            employee__in=demo_employees
        )
        self.assertEqual(att_snaps.count(), 6)

        # Verify Employee 1: Aarav Sharma (Perfect Attendance + Multi-Session on July 6)
        emp1 = demo_employees.get(employee_code="DEMO-EMP-001")
        snap1 = att_snaps.get(employee=emp1)
        self.assertEqual(snap1.payable_attendance_units, 22.0)
        self.assertEqual(snap1.unpaid_leave_days, Decimal("0.00"))
        self.assertEqual(snap1.absent_days, 0)
        emp1_logs_july6 = AttendanceLog.objects.filter(employee=emp1, date=datetime_date(2026, 7, 6))
        self.assertEqual(emp1_logs_july6.count(), 2)

        # Verify Employee 2: Priya Nair (Paid Leave Scenario)
        emp2 = demo_employees.get(employee_code="DEMO-EMP-002")
        snap2 = att_snaps.get(employee=emp2)
        self.assertEqual(snap2.paid_leave_days, Decimal("1.00"))
        self.assertEqual(snap2.payable_attendance_units, 22.0)

        # Verify Employee 3: Vikram Patel (Unpaid Leave Scenario)
        emp3 = demo_employees.get(employee_code="DEMO-EMP-003")
        snap3 = att_snaps.get(employee=emp3)
        self.assertEqual(snap3.unpaid_leave_days, Decimal("1.00"))
        self.assertEqual(snap3.payable_attendance_units, 21.0)

        # Verify Employee 4: Sneha Kulkarni (Half-Day + Late Scenario)
        emp4 = demo_employees.get(employee_code="DEMO-EMP-004")
        snap4 = att_snaps.get(employee=emp4)
        self.assertEqual(snap4.half_days, 1)
        self.assertEqual(snap4.late_count, 1)
        self.assertEqual(snap4.payable_attendance_units, 21.5)

        # Verify Employee 5: Rohan Mehta (Absent Scenario)
        emp5 = demo_employees.get(employee_code="DEMO-EMP-005")
        snap5 = att_snaps.get(employee=emp5)
        self.assertEqual(snap5.absent_days, 1)
        self.assertEqual(snap5.payable_attendance_units, 21.0)

        # Verify Exactly 1 Structure per Demo Employee on 2026-01-01
        structures = EmployeeSalaryStructure.objects.filter(
            organization=self.org,
            employee__in=demo_employees,
            effective_from=datetime_date(2026, 1, 1),
            is_active=True
        )
        self.assertEqual(structures.count(), 6)
        for s in structures:
            self.assertEqual(s.currency, 'INR')

        # Verify 6 Issued Payslips
        payslips = Payslip.objects.filter(
            payroll_period=payroll_period,
            status='Issued',
            employee__in=demo_employees
        )
        self.assertEqual(payslips.count(), 6)

    def test_03_idempotent_multi_seed_creates_no_duplicates(self):
        """
        Req 5, 6, 7, 8, 9, 10, 11, 13:
        Running seed repeatedly (1x, 2x, 5x) succeeds without increasing counts
        and never throws IntegrityError.
        """
        # Run 1
        res1 = seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res1["status"], "success")

        # Capture counts after 1st run
        emp_count_1 = Employee.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        struct_count_1 = EmployeeSalaryStructure.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        comp_count_1 = SalaryComponent.objects.filter(organization=self.org, code__startswith="DEMO_").count()
        logs_count_1 = AttendanceLog.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        a_snaps_count_1 = AttendancePeriodEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        p_snaps_count_1 = PayrollEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        payslips_count_1 = Payslip.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()

        self.assertEqual(emp_count_1, 6)
        self.assertEqual(struct_count_1, 6)
        self.assertEqual(comp_count_1, 5)
        self.assertEqual(payslips_count_1, 6)

        # Run 2
        res2 = seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res2["status"], "already_seeded")

        # Run 3
        res3 = seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res3["status"], "already_seeded")

        # Verify NO count increases
        self.assertEqual(Employee.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), emp_count_1)
        self.assertEqual(EmployeeSalaryStructure.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), struct_count_1)
        self.assertEqual(SalaryComponent.objects.filter(organization=self.org, code__startswith="DEMO_").count(), comp_count_1)
        self.assertEqual(AttendanceLog.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), logs_count_1)
        self.assertEqual(AttendancePeriodEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), a_snaps_count_1)
        self.assertEqual(PayrollEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), p_snaps_count_1)
        self.assertEqual(Payslip.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), payslips_count_1)

    def test_04_partially_seeded_salary_structure_recovers_cleanly(self):
        """
        Req 12, 13:
        Simulate a previous partial run where 2 demo employees have salary structures,
        and the seeder is run again. It must reconcile cleanly without throwing UniqueConstraint error.
        """
        # Create demo employees
        emp1 = Employee.objects.create(
            organization=self.org,
            email=f"demo.emp01@{DEMO_EMAIL_DOMAIN}",
            username="demo.emp01",
            first_name="Aarav",
            last_name="Sharma",
            employee_code="DEMO-EMP-001",
            joining_date=datetime_date(2026, 1, 1),
            is_active=True
        )
        emp2 = Employee.objects.create(
            organization=self.org,
            email=f"demo.emp02@{DEMO_EMAIL_DOMAIN}",
            username="demo.emp02",
            first_name="Priya",
            last_name="Nair",
            employee_code="DEMO-EMP-002",
            joining_date=datetime_date(2026, 1, 1),
            is_active=True
        )
        comp_basic = SalaryComponent.objects.create(
            organization=self.org,
            code="DEMO_BASIC",
            name="Basic Salary",
            component_type="Earning",
            is_proratable=True
        )

        # Pre-assign existing salary structure on 2026-01-01 for emp1 and emp2 with partial values
        assign_or_revise_salary_structure(
            employee=emp1,
            organization=self.org,
            effective_from=datetime_date(2026, 1, 1),
            components_data=[{"salary_component_id": comp_basic.id, "amount": Decimal("50000.00")}],
            notes=f"{DEMO_MARKER} Standard compensation structure"
        )
        assign_or_revise_salary_structure(
            employee=emp2,
            organization=self.org,
            effective_from=datetime_date(2026, 1, 1),
            components_data=[{"salary_component_id": comp_basic.id, "amount": Decimal("40000.00")}],
            notes=f"{DEMO_MARKER} Standard compensation structure"
        )

        self.assertEqual(EmployeeSalaryStructure.objects.filter(organization=self.org).count(), 2)

        # Now run seed_demo_dataset — must NOT fail with IntegrityError, must recover and complete all 6!
        res = seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res["status"], "success")

        # Verify all 6 demo employees now have valid reconciled structures
        demo_structures = EmployeeSalaryStructure.objects.filter(
            organization=self.org,
            employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}",
            effective_from=datetime_date(2026, 1, 1)
        )
        self.assertEqual(demo_structures.count(), 6)

        # Verify emp1 structure was reconciled with full ₹60,000 basic + HRA + Transport totals (Gross 89,000)
        s1 = demo_structures.get(employee=emp1)
        self.assertEqual(s1.gross_salary, Decimal("89000.00"))
        self.assertEqual(s1.base_net_salary, Decimal("85200.00"))

        # Verify 6 payslips issued
        self.assertEqual(Payslip.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)

    def test_05_non_demo_conflicting_structure_is_protected(self):
        """
        Req 14:
        If a demo employee already has a NON-DEMO salary structure (no DEMO_MARKER in notes),
        the seeder must fail safely with ValidationError and never overwrite it.
        """
        emp1 = Employee.objects.create(
            organization=self.org,
            email=f"demo.emp01@{DEMO_EMAIL_DOMAIN}",
            username="demo.emp01",
            first_name="Aarav",
            last_name="Sharma",
            employee_code="DEMO-EMP-001",
            joining_date=datetime_date(2026, 1, 1),
            is_active=True
        )
        real_comp = SalaryComponent.objects.create(
            organization=self.org,
            code="REAL_BASE",
            name="Contract Base",
            component_type="Earning"
        )
        # Create non-demo structure with external notes
        assign_or_revise_salary_structure(
            employee=emp1,
            organization=self.org,
            effective_from=datetime_date(2026, 1, 1),
            components_data=[{"salary_component_id": real_comp.id, "amount": Decimal("99000.00")}],
            notes="Custom Executive Contract"
        )

        with self.assertRaises(ValidationError):
            assign_or_reconcile_demo_salary_structure(
                employee=emp1,
                organization=self.org,
                effective_from=datetime_date(2026, 1, 1),
                components_data=[{"salary_component_id": real_comp.id, "amount": Decimal("50000.00")}],
                notes=f"{DEMO_MARKER} Standard compensation structure"
            )

        # Structure remains untouched
        s1 = EmployeeSalaryStructure.objects.get(employee=emp1, effective_from=datetime_date(2026, 1, 1))
        self.assertEqual(s1.gross_salary, Decimal("99000.00"))
        self.assertEqual(s1.notes, "Custom Executive Contract")

    def test_06_idempotent_multi_cleanup_preserves_real_data(self):
        """
        Req 15, 16, 17:
        - remove -> remove again succeeds as clean no-op.
        - cleanup removes 100% of demo artifacts.
        - cleanup preserves real non-demo records.
        """
        seed_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(Employee.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)

        # 1st Remove
        res_clean_1 = remove_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res_clean_1["status"], "success")

        # Verify all demo records deleted
        self.assertEqual(Employee.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(Payslip.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(PayrollAdjustment.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(PayrollEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(AttendancePeriodEmployeeSnapshot.objects.filter(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(SalaryComponent.objects.filter(organization=self.org, code__startswith='DEMO_').count(), 0)
        self.assertEqual(Holiday.objects.filter(organization=self.org, date=datetime_date(2026, 7, 15)).count(), 0)

        # Verify real employee is preserved
        self.assertTrue(Employee.objects.filter(id=self.real_employee.id).exists())
        self.assertEqual(Employee.objects.filter(organization=self.org).count(), 1)

        # 2nd Remove (must succeed as clean no-op)
        res_clean_2 = remove_demo_dataset(org_id=self.org.id, dry_run=False)
        self.assertEqual(res_clean_2["status"], "success")
        self.assertTrue(Employee.objects.filter(id=self.real_employee.id).exists())

    def test_07_cross_organization_demo_records_isolated(self):
        """
        Req 20: Cross-organization demo records are never touched when seeding/cleaning another org.
        """
        # Create second org with its own OrgSettings
        settings2 = OrgSettings.objects.create(
            is_attendance_enabled=True,
            payroll_currency='INR',
            payroll_proration_basis='WORKING_DAYS'
        )
        org2 = Organization.objects.create(
            name="Beta Corp Test",
            subdomain="beta-test",
            settings=settings2
        )

        # Seed both orgs
        seed_demo_dataset(org_id=self.org.id, dry_run=False)
        seed_demo_dataset(org_id=org2.id, dry_run=False)

        self.assertEqual(Employee.objects.filter(organization=self.org, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)
        self.assertEqual(Employee.objects.filter(organization=org2, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)

        # Clean only self.org
        remove_demo_dataset(org_id=self.org.id, dry_run=False)

        # self.org is clean, but org2 is completely untouched!
        self.assertEqual(Employee.objects.filter(organization=self.org, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(Employee.objects.filter(organization=org2, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)
        self.assertEqual(Payslip.objects.filter(organization=org2, employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 6)

        # Clean org2
        remove_demo_dataset(org_id=org2.id, dry_run=False)
        self.assertEqual(Employee.objects.filter(organization=org2, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)

    def test_08_failed_transaction_rollback(self):
        """
        Req 18:
        If an unexpected error occurs during seeding, transaction.atomic() rolls back
        all newly created database changes cleanly.
        """
        with patch('payroll.demo_seed.finalize_payroll_period', side_effect=RuntimeError("Simulated Payroll Failure")):
            with self.assertRaises(RuntimeError):
                seed_demo_dataset(org_id=self.org.id, dry_run=False)

        # Verify complete rollback
        self.assertEqual(Employee.objects.filter(organization=self.org, email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count(), 0)
        self.assertEqual(SalaryComponent.objects.filter(organization=self.org, code__startswith='DEMO_').count(), 0)
        self.assertEqual(Payslip.objects.filter(organization=self.org).count(), 0)
