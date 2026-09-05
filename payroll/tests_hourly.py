import datetime
from datetime import date, datetime as dt, time
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied

from core.models import Organization, OrgSettings
from users.models import Employee, Role
from attendance.models import (
    AttendanceLog, AttendancePolicy, AttendancePeriod, AttendancePeriodEmployeeSnapshot, Leave, LeaveType
)
from attendance.services import finalize_attendance_period
from attendance.api.v1.serializers import OrgSettingsSerializer
from payroll.models import (
    SalaryComponent, EmployeeSalaryStructure, EmployeeSalaryComponent,
    PayrollPeriod, PayrollEmployeeSnapshot, PayrollAdjustment, Payslip
)
from payroll.services import (
    assign_or_revise_salary_structure,
    calculate_employee_payroll,
    calculate_payroll_period,
    finalize_payroll_period,
    reopen_payroll_period,
    get_employee_salary_structure,
)
from payroll.serializers import (
    EmployeeSalaryStructureSerializer,
    SalaryStructureCreateSerializer,
    PayrollEmployeeSnapshotSerializer,
    PayslipSerializer,
    PayslipDetailSerializer,
)
from payroll.pdf import generate_payslip_pdf
from payroll.demo_seed import seed_demo_dataset, remove_demo_dataset, is_demo_dataset_seeded


class HourlyCompensationTestCase(TestCase):
    def setUp(self):
        self.settings = OrgSettings.objects.create(
            payroll_currency="INR",
            hourly_wage_paid_leave_eligible=True,
            daily_wage_paid_leave_eligible=True
        )
        self.org = Organization.objects.create(name="Test Corp", subdomain="test-corp", settings=self.settings)
        self.role, _ = Role.objects.get_or_create(name="Employee")
        self.admin_user = Employee.objects.create_user(
            username="admin@testcorp.com",
            email="admin@testcorp.com",
            first_name="Admin",
            last_name="User",
            organization=self.org,
            role=self.role,
            is_staff=True,
            is_superuser=True
        )
        self.emp = Employee.objects.create(
            username="hourly.worker@testcorp.com",
            email="hourly.worker@testcorp.com",
            first_name="Kavita",
            last_name="Reddy",
            designation="Support Specialist",
            organization=self.org,
            role=self.role
        )
        self.allowance, _ = SalaryComponent.objects.get_or_create(
            organization=self.org,
            code="TRANS_ALLOW",
            defaults={
                "name": "Transport Allowance",
                "component_type": "Earning",
                "is_taxable": False,
                "is_proratable": False,
                "is_active": True,
            }
        )
        self.deduction, _ = SalaryComponent.objects.get_or_create(
            organization=self.org,
            code="UNIFORM_DED",
            defaults={
                "name": "Uniform Fee",
                "component_type": "Deduction",
                "is_taxable": False,
                "is_proratable": False,
                "is_active": True,
            }
        )
        self.paid_leave_type = LeaveType.objects.create(
            organization=self.org,
            name="Paid Leave",
            is_paid=True
        )
        # Baseline salary structure for admin user so payroll finalization succeeds
        assign_or_revise_salary_structure(
            employee=self.admin_user,
            organization=self.org,
            effective_from=date(2026, 1, 1),
            compensation_type="MONTHLY",
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("50000.00")}],
            created_by=self.admin_user
        )

    # 1. Hourly salary structure creation with positive hourly_rate
    def test_01_assign_hourly_salary_structure_success(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("1500.00")}],
            created_by=self.admin_user
        )
        self.assertEqual(struct.compensation_type, "HOURLY")
        self.assertEqual(struct.hourly_rate, Decimal("250.00"))
        self.assertEqual(struct.gross_salary, Decimal("1500.00"))
        self.assertEqual(struct.currency, "INR")
        self.assertTrue(struct.is_active)

    # 2. Hourly salary structure rejection when hourly_rate is missing, 0, or negative
    def test_02_assign_hourly_rate_validation(self):
        with self.assertRaises(ValidationError):
            assign_or_revise_salary_structure(
                employee=self.emp,
                organization=self.org,
                effective_from=date(2026, 7, 1),
                compensation_type="HOURLY",
                hourly_rate=None,
                created_by=self.admin_user
            )
        with self.assertRaises(ValidationError):
            assign_or_revise_salary_structure(
                employee=self.emp,
                organization=self.org,
                effective_from=date(2026, 7, 1),
                compensation_type="HOURLY",
                hourly_rate=Decimal("0.00"),
                created_by=self.admin_user
            )
        with self.assertRaises(ValidationError):
            assign_or_revise_salary_structure(
                employee=self.emp,
                organization=self.org,
                effective_from=date(2026, 7, 1),
                compensation_type="HOURLY",
                hourly_rate=Decimal("-50.00"),
                created_by=self.admin_user
            )

    # 3. Hourly salary structure rejection when effective_from.day != 1
    def test_03_effective_date_first_of_month(self):
        with self.assertRaises(ValidationError):
            assign_or_revise_salary_structure(
                employee=self.emp,
                organization=self.org,
                effective_from=date(2026, 7, 15),
                compensation_type="HOURLY",
                hourly_rate=Decimal("200.00"),
                created_by=self.admin_user
            )

    # 4. Period lock check when organization payroll is finalized
    def test_04_period_lock_check(self):
        att_period = AttendancePeriod.objects.create(
            organization=self.org, year=2026, month=7, status="Finalized", current_revision=1
        )
        PayrollPeriod.objects.create(
            organization=self.org, year=2026, month=7, status="Finalized", attendance_period=att_period
        )
        with self.assertRaises(ValidationError):
            assign_or_revise_salary_structure(
                employee=self.emp,
                organization=self.org,
                effective_from=date(2026, 7, 1),
                compensation_type="HOURLY",
                hourly_rate=Decimal("200.00"),
                created_by=self.admin_user
            )

    # 5. Immutable revisions: new structure with distinct date
    def test_05_immutable_historical_revision(self):
        struct1 = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 1, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            created_by=self.admin_user
        )
        struct2 = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            created_by=self.admin_user
        )
        self.assertEqual(EmployeeSalaryStructure.objects.filter(employee=self.emp).count(), 2)
        resolved_june = get_employee_salary_structure(self.emp, date(2026, 6, 1))
        resolved_july = get_employee_salary_structure(self.emp, date(2026, 7, 1))
        self.assertEqual(resolved_june.hourly_rate, Decimal("200.00"))
        self.assertEqual(resolved_july.hourly_rate, Decimal("250.00"))

    # 6. Multi-session attendance log aggregation to payable_work_hours
    def test_06_multi_session_attendance_aggregation(self):
        log_date = date(2026, 7, 6)
        AttendanceLog.objects.create(
            employee=self.emp,
            date=log_date,
            clockIn=timezone.make_aware(dt.combine(log_date, time(9, 0))),
            clockOut=timezone.make_aware(dt.combine(log_date, time(13, 0))),
            totalDuration="4h 00m",
            status="Approved"
        )
        AttendanceLog.objects.create(
            employee=self.emp,
            date=log_date,
            clockIn=timezone.make_aware(dt.combine(log_date, time(14, 0))),
            clockOut=timezone.make_aware(dt.combine(log_date, time(17, 30))),
            totalDuration="3h 30m",
            status="Approved"
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        snap = AttendancePeriodEmployeeSnapshot.objects.get(
            attendance_period=att_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.total_worked_minutes, 450)
        self.assertEqual(snap.payable_work_hours, Decimal("7.50"))

    # 7. Paid leave eligible: True includes scheduled hours
    def test_07_hourly_paid_leave_eligible_true(self):
        self.settings.hourly_wage_paid_leave_eligible = True
        self.settings.save()
        leave_date = date(2026, 7, 10)
        Leave.objects.create(
            employee=self.emp,
            employeeName=f"{self.emp.first_name} {self.emp.last_name}",
            leaveType=self.paid_leave_type,
            leaveTypeName=self.paid_leave_type.name,
            startDate=leave_date,
            endDate=leave_date,
            duration=1.0,
            status="Approved"
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        snap = AttendancePeriodEmployeeSnapshot.objects.get(
            attendance_period=att_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.paid_leave_days, Decimal("1.00"))
        self.assertEqual(snap.payable_work_hours, Decimal("8.00"))

    # 8. Paid leave eligible: False excludes paid leave hours
    def test_08_hourly_paid_leave_eligible_false(self):
        self.settings.hourly_wage_paid_leave_eligible = False
        self.settings.save()
        leave_date = date(2026, 7, 10)
        Leave.objects.create(
            employee=self.emp,
            employeeName=f"{self.emp.first_name} {self.emp.last_name}",
            leaveType=self.paid_leave_type,
            leaveTypeName=self.paid_leave_type.name,
            startDate=leave_date,
            endDate=leave_date,
            duration=1.0,
            status="Approved"
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        snap = AttendancePeriodEmployeeSnapshot.objects.get(
            attendance_period=att_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.paid_leave_days, Decimal("1.00"))
        self.assertEqual(snap.payable_work_hours, Decimal("0.00"))

    # 9. Gross wage formula: hourly_rate * payable_hours
    def test_09_wage_formula_calculation(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=21,
            payable_attendance_units=10.0,
            payable_work_hours=Decimal("160.00"),
            total_worked_minutes=9600
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        self.assertEqual(res["earned_gross"], Decimal("40000.00"))
        self.assertEqual(res["net_payable"], Decimal("40000.00"))
        self.assertEqual(res["calculation_breakdown"]["wage_earnings"], "40000.00")

    # 10. Fixed allowance earned gross: wage_earnings + fixed allowances
    def test_10_wage_with_fixed_allowance(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("2000.00")}],
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=21,
            payable_work_hours=Decimal("100.00"),
            total_worked_minutes=6000
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        self.assertEqual(res["earned_gross"], Decimal("27000.00"))
        self.assertEqual(res["net_payable"], Decimal("27000.00"))

    # 11. Attendance deduction for hourly is always 0.00
    def test_11_attendance_deduction_zero(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("300.00"),
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=22,
            payable_work_hours=Decimal("40.00"),
            total_worked_minutes=2400
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        self.assertEqual(res["attendance_deduction"], Decimal("0.00"))
        self.assertEqual(res["calculation_breakdown"]["attendance_deduction"], "0.00")

    # 12. Proratable gross is 0.00, non_proratable_gross is fixed allowances
    def test_12_proration_metrics_zero(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("1000.00")}],
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=20,
            payable_work_hours=Decimal("50.00")
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        self.assertEqual(res["proratable_gross"], Decimal("0.00"))
        self.assertEqual(res["non_proratable_gross"], Decimal("1000.00"))

    # 13. Net payable formula with adjustments
    def test_13_net_payable_with_adjustments(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            components_data=[
                {"salary_component_id": self.allowance.id, "amount": Decimal("1000.00")},
                {"salary_component_id": self.deduction.id, "amount": Decimal("500.00")}
            ],
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=20,
            payable_work_hours=Decimal("100.00")
        )
        adj_earn = PayrollAdjustment(adjustment_type="Earning", amount=Decimal("2500.00"))
        adj_ded = PayrollAdjustment(adjustment_type="Deduction", amount=Decimal("300.00"))
        res = calculate_employee_payroll(self.emp, att_snap, struct, adjustments=[adj_earn, adj_ded])
        # Earned gross = (200 * 100) + 1000 = 21000
        # Additional earnings = 2500
        # Base deductions = 500
        # Additional deductions = 300
        # Net = 21000 + 2500 - 500 - 300 = 22700.00
        self.assertEqual(res["earned_gross"], Decimal("21000.00"))
        self.assertEqual(res["net_payable"], Decimal("22700.00"))

    # 14. Negative net handling
    def test_14_negative_net_handling(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("100.00"),
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=20,
            payable_work_hours=Decimal("10.00")
        )
        adj_ded = PayrollAdjustment(adjustment_type="Deduction", amount=Decimal("5000.00"))
        res = calculate_employee_payroll(self.emp, att_snap, struct, adjustments=[adj_ded])
        self.assertEqual(res["status"], "NegativeNet")
        self.assertTrue(res["net_payable"] < Decimal("0.00"))

    # 15. Zero payable hours results in 0 wage earnings but preserves fixed allowances
    def test_15_zero_payable_hours(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("1200.00")}],
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=20,
            payable_work_hours=Decimal("0.00")
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        self.assertEqual(res["earned_gross"], Decimal("1200.00"))
        self.assertEqual(res["net_payable"], Decimal("1200.00"))
        self.assertEqual(res["calculation_breakdown"]["wage_earnings"], "0.00")

    # 16. Attendance period finalization freezes payable_work_hours
    def test_16_attendance_finalization_freezes_snapshot(self):
        log_date = date(2026, 7, 1)
        AttendanceLog.objects.create(
            employee=self.emp,
            date=log_date,
            clockIn=timezone.make_aware(dt.combine(log_date, time(9, 0))),
            clockOut=timezone.make_aware(dt.combine(log_date, time(17, 0))),
            totalDuration="8h 00m",
            status="Approved"
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        snap = AttendancePeriodEmployeeSnapshot.objects.get(
            attendance_period=att_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.payable_work_hours, Decimal("8.00"))
        # Adding more logs after finalization does not alter snapshot
        AttendanceLog.objects.create(
            employee=self.emp,
            date=date(2026, 7, 2),
            clockIn=timezone.make_aware(dt.combine(date(2026, 7, 2), time(9, 0))),
            clockOut=timezone.make_aware(dt.combine(date(2026, 7, 2), time(17, 0))),
            totalDuration="8h 00m",
            status="Approved"
        )
        snap.refresh_from_db()
        self.assertEqual(snap.payable_work_hours, Decimal("8.00"))

    # 17. Payroll period calculation reads snapshot payable_work_hours
    def test_17_payroll_calculation_reads_snapshot_only(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            created_by=self.admin_user
        )
        log_date = date(2026, 7, 1)
        AttendanceLog.objects.create(
            employee=self.emp,
            date=log_date,
            clockIn=timezone.make_aware(dt.combine(log_date, time(9, 0))),
            clockOut=timezone.make_aware(dt.combine(log_date, time(17, 0))),
            totalDuration="8h 00m",
            status="Approved"
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        pr_period = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        snap = PayrollEmployeeSnapshot.objects.get(
            payroll_period=pr_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.payable_hours, Decimal("8.00"))
        self.assertEqual(snap.hourly_rate, Decimal("200.00"))
        self.assertEqual(snap.earned_gross, Decimal("1600.00"))

    # 18. Historical payroll snapshots retain values even when structure changes later
    def test_18_historical_immutability(self):
        assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            created_by=self.admin_user
        )
        att_period = finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        pr_period = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        finalize_payroll_period(self.org, 2026, 7, self.admin_user)

        # Later in August, revise rate to 300/hr
        assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 8, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("300.00"),
            created_by=self.admin_user
        )
        snap = PayrollEmployeeSnapshot.objects.get(
            payroll_period=pr_period, employee=self.emp, is_current=True
        )
        self.assertEqual(snap.hourly_rate, Decimal("200.00"))

    # 19. Reopening and recalculating updates snapshot revision
    def test_19_reopen_and_recalculate(self):
        assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("200.00"),
            created_by=self.admin_user
        )
        finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        pr = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        finalize_payroll_period(self.org, 2026, 7, self.admin_user)
        reopen_payroll_period(self.org, 2026, 7, self.admin_user, "Correction")
        pr_recalc = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        self.assertEqual(pr_recalc.current_revision, 2)

    # 20. Payslip generation produces HOURLY fields
    def test_20_payslip_generation(self):
        assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            created_by=self.admin_user
        )
        finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        pr = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        finalize_payroll_period(self.org, 2026, 7, self.admin_user)
        payslip = Payslip.objects.get(payroll_period=pr, employee=self.emp)
        self.assertEqual(payslip.payroll_snapshot.compensation_type, "HOURLY")
        self.assertEqual(payslip.payroll_snapshot.hourly_rate, Decimal("250.00"))

    # 21. ReportLab PDF generation executes without errors for hourly employee
    def test_21_pdf_generation(self):
        assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            components_data=[{"salary_component_id": self.allowance.id, "amount": Decimal("1000.00")}],
            created_by=self.admin_user
        )
        finalize_attendance_period(self.org, 2026, 7, self.admin_user)
        pr = calculate_payroll_period(self.org, 2026, 7, self.admin_user)
        finalize_payroll_period(self.org, 2026, 7, self.admin_user)
        payslip = Payslip.objects.get(payroll_period=pr, employee=self.emp)
        pdf_buffer = generate_payslip_pdf(payslip)
        pdf_bytes = pdf_buffer.getvalue() if hasattr(pdf_buffer, 'getvalue') else pdf_buffer
        self.assertTrue(len(pdf_bytes) > 1000)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    # 22. Serializers expose hourly_rate and payable_hours
    def test_22_serializers(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("250.00"),
            created_by=self.admin_user
        )
        ser_struct = EmployeeSalaryStructureSerializer(struct).data
        self.assertEqual(ser_struct["compensation_type"], "HOURLY")
        self.assertEqual(ser_struct["hourly_rate"], "250.00")

        create_ser = SalaryStructureCreateSerializer(data={
            "effective_from": "2026-08-01",
            "compensation_type": "HOURLY",
            "hourly_rate": "300.00"
        })
        self.assertTrue(create_ser.is_valid())

    # 23. Demo dataset seeding creates DEMO-EMP-006 with INR 250/hr and 176.50 hours
    def test_23_demo_dataset_seeding(self):
        remove_demo_dataset(self.org.id)
        res = seed_demo_dataset(self.org.id)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["payslip_count"], 6)
        emp6 = Employee.objects.get(organization=self.org, email__startswith="demo.emp06@")
        snap = PayrollEmployeeSnapshot.objects.get(
            payroll_period=res["payroll_period"], employee=emp6, is_current=True
        )
        self.assertEqual(snap.compensation_type, "HOURLY")
        self.assertEqual(snap.hourly_rate, Decimal("250.00"))
        self.assertEqual(snap.payable_hours, Decimal("185.00"))
        # 185.00 * 250 = 46,250 + 2000 transport = 48,250.00
        self.assertEqual(snap.earned_gross, Decimal("48250.00"))
        self.assertEqual(snap.net_payable, Decimal("48250.00"))
        self.assertTrue(is_demo_dataset_seeded(self.org))

    # 24. Decimal precision: no floating point rounding errors
    def test_24_decimal_precision(self):
        struct = assign_or_revise_salary_structure(
            employee=self.emp,
            organization=self.org,
            effective_from=date(2026, 7, 1),
            compensation_type="HOURLY",
            hourly_rate=Decimal("333.33"),
            created_by=self.admin_user
        )
        att_snap = AttendancePeriodEmployeeSnapshot(
            employee=self.emp,
            working_days=20,
            payable_work_hours=Decimal("17.33")
        )
        res = calculate_employee_payroll(self.emp, att_snap, struct)
        # 333.33 * 17.33 = 5776.6089 -> 5776.61
        self.assertEqual(res["earned_gross"], Decimal("5776.61"))
        self.assertEqual(res["net_payable"], Decimal("5776.61"))

    # 25. OrgSettings serializer includes hourly_wage_paid_leave_eligible
    def test_25_org_settings_serializer(self):
        ser = OrgSettingsSerializer(self.settings).data
        self.assertIn("hourly_wage_paid_leave_eligible", ser)
        self.assertTrue(ser["hourly_wage_paid_leave_eligible"])
