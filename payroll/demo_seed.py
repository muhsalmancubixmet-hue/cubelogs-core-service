# --------------------------------------------------------------------------------
#       Payroll & Attendance Demo Dataset Seeder & Cleanup Engine
# --------------------------------------------------------------------------------

import calendar
from datetime import date as datetime_date, datetime, time
from decimal import Decimal
from django.db import transaction, models
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.models import Organization
from users.models import Employee, Role
from attendance.models import (
    AttendanceLog,
    AttendancePolicy,
    Schedule,
    LeaveType,
    Leave,
    Holiday,
    AttendancePeriod,
    AttendancePeriodEmployeeSnapshot,
)
from attendance.services import finalize_attendance_period
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
    assign_or_revise_salary_structure,
    calculate_payroll_period,
    finalize_payroll_period,
    calculate_salary_totals,
)

# Demo Metadata Constants
DEMO_MARKER = "[DEMO-DATASET-V1]"
DEMO_EMAIL_DOMAIN = "demo.cubelogs.internal"
DEMO_YEAR = 2026
DEMO_MONTH = 7  # July 2026

DEMO_EMPLOYEES_CONFIG = [
    {
        "code": "DEMO-EMP-001",
        "email": f"demo.emp01@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Aarav",
        "last_name": "Sharma",
        "designation": "Senior Backend Engineer",
        "department": "Engineering",
        "scenario": "Perfect Attendance + Multi-Session on July 6",
        "compensation_type": "MONTHLY",
        "daily_rate": None,
        "salary": {
            "basic": Decimal("60000.00"),
            "hra": Decimal("24000.00"),
            "transport": Decimal("5000.00"),
            "pf": Decimal("3600.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-002",
        "email": f"demo.emp02@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Priya",
        "last_name": "Nair",
        "designation": "UI/UX Designer",
        "department": "Design",
        "scenario": "One Approved Paid Leave (Casual Leave on July 10)",
        "compensation_type": "MONTHLY",
        "daily_rate": None,
        "salary": {
            "basic": Decimal("50000.00"),
            "hra": Decimal("20000.00"),
            "transport": Decimal("5000.00"),
            "pf": Decimal("3000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-003",
        "email": f"demo.emp03@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Vikram",
        "last_name": "Patel",
        "designation": "Full Stack Developer",
        "department": "Engineering",
        "scenario": "One Approved Unpaid Leave / LWP (July 22)",
        "compensation_type": "MONTHLY",
        "daily_rate": None,
        "salary": {
            "basic": Decimal("55000.00"),
            "hra": Decimal("22000.00"),
            "transport": Decimal("5000.00"),
            "pf": Decimal("3200.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-004",
        "email": f"demo.emp04@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Sneha",
        "last_name": "Kulkarni",
        "designation": "QA Engineer",
        "department": "Quality Assurance",
        "scenario": "Daily Wage (₹3,000/day) + Half-Day (July 8) + Late Day (July 17)",
        "compensation_type": "DAILY",
        "daily_rate": Decimal("3000.00"),
        "salary": {},
    },
    {
        "code": "DEMO-EMP-005",
        "email": f"demo.emp05@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Rohan",
        "last_name": "Mehta",
        "designation": "Product Operations",
        "department": "Operations",
        "scenario": "Daily Wage (₹3,500/day) + One Absent Day (July 28) + ₹5,000 Bonus Adjustment",
        "compensation_type": "DAILY",
        "daily_rate": Decimal("3500.00"),
        "salary": {},
    },
    {
        "code": "DEMO-EMP-006",
        "email": f"demo.emp06@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Kavita",
        "last_name": "Reddy",
        "designation": "Technical Support Specialist",
        "department": "Customer Support",
        "scenario": "Hourly Wage (₹250/hr) + Multi-Session July 13 & 20 + ₹2,000 Transport Allowance",
        "compensation_type": "HOURLY",
        "hourly_rate": Decimal("250.00"),
        "daily_rate": None,
        "salary": {
            "transport": Decimal("2000.00"),
        },
    },
]


def resolve_target_organization(org_id=None):
    """
    Deterministically resolves the target development organization.
    Aborts if ambiguous or invalid.
    """
    if org_id:
        org = Organization.objects.filter(id=org_id).first()
        if not org:
            raise ValueError(f"Organization with ID '{org_id}' was not found.")
        return org

    # Default lookup: prefer organization with active employees/admin
    org_with_admin = Organization.objects.filter(employees__is_superuser=True).first()
    if org_with_admin:
        return org_with_admin

    org_first = Organization.objects.first()
    if org_first:
        return org_first

    raise ValueError("No organization exists in the database. Please create an organization first.")


def is_demo_dataset_seeded(org):
    """
    Checks whether the full July 2026 demo dataset is already present.
    """
    demo_emp_count = Employee.objects.filter(
        organization=org,
        email__endswith=f"@{DEMO_EMAIL_DOMAIN}"
    ).count()

    att_finalized = AttendancePeriod.objects.filter(
        organization=org,
        year=DEMO_YEAR,
        month=DEMO_MONTH,
        status='Finalized'
    ).exists()

    payroll_finalized = PayrollPeriod.objects.filter(
        organization=org,
        year=DEMO_YEAR,
        month=DEMO_MONTH,
        status='Finalized'
    ).exists()

    payslip_count = Payslip.objects.filter(
        organization=org,
        payroll_period__year=DEMO_YEAR,
        payroll_period__month=DEMO_MONTH,
        employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}"
    ).count()

    demo_structures_count = EmployeeSalaryStructure.objects.filter(
        organization=org,
        employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}",
        effective_from=datetime_date(2026, 1, 1),
        is_active=True
    ).count()

    return (
        demo_emp_count == 6 and
        att_finalized and
        payroll_finalized and
        payslip_count == 6 and
        demo_structures_count == 6
    )


def assign_or_reconcile_demo_salary_structure(employee, organization, effective_from, components_data=None, notes=None, created_by=None, compensation_type='MONTHLY', daily_rate=None, hourly_rate=None):
    """
    Assigns or reconciles a salary structure for a demo or unconfigured employee at effective_from.
    - If no structure exists for (employee, effective_from): creates via assign_or_revise_salary_structure.
    - If a demo structure already exists: reconciles totals, currency, components, and ensures is_active=True without violating UniqueConstraint.
    - If a non-demo structure exists for a demo employee: raises ValidationError to protect data.
    - Never modifies real salary structures of non-demo employees.
    """
    if components_data is None:
        components_data = []

    existing = EmployeeSalaryStructure.objects.filter(
        employee=employee,
        organization=organization,
        effective_from=effective_from
    ).first()

    is_demo_emp = employee.email.endswith(f"@{DEMO_EMAIL_DOMAIN}")

    if existing:
        # Check if existing structure is a demo structure
        is_demo_struct = bool(existing.notes and DEMO_MARKER in existing.notes)
        if not is_demo_struct:
            if is_demo_emp:
                raise ValidationError(
                    f"A non-demo salary structure already exists for demo employee {employee.email} effective from {effective_from}. "
                    "Aborting to avoid overwriting non-demo data."
                )
            else:
                # Real employee with real existing structure -> DO NOT TOUCH
                return existing

        # Existing structure is a demo structure -> reconcile it safely in place
        if compensation_type == 'DAILY':
            gross_salary, base_deductions, base_net_salary, parsed_lines = (
                calculate_salary_totals(components_data, organization) if components_data else (Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), [])
            )
            existing.daily_rate = daily_rate
            existing.hourly_rate = None
        elif compensation_type == 'HOURLY':
            gross_salary, base_deductions, base_net_salary, parsed_lines = (
                calculate_salary_totals(components_data, organization) if components_data else (Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), [])
            )
            existing.daily_rate = None
            existing.hourly_rate = hourly_rate
        else:
            gross_salary, base_deductions, base_net_salary, parsed_lines = calculate_salary_totals(components_data, organization)
            existing.daily_rate = None
            existing.hourly_rate = None

        currency = 'INR'
        if hasattr(organization, 'settings') and organization.settings and organization.settings.payroll_currency:
            currency = organization.settings.payroll_currency

        existing.compensation_type = compensation_type
        existing.gross_salary = gross_salary
        existing.base_deductions = base_deductions
        existing.base_net_salary = base_net_salary
        existing.currency = currency
        existing.is_active = True
        if notes:
            existing.notes = notes
        existing.save()

        # Reconcile component lines
        existing.components.all().delete()
        if parsed_lines:
            component_objs = [
                EmployeeSalaryComponent(
                    salary_structure=existing,
                    salary_component=line['component'],
                    amount=line['amount']
                )
                for line in parsed_lines
            ]
            EmployeeSalaryComponent.objects.bulk_create(component_objs)
        return existing
    else:
        return assign_or_revise_salary_structure(
            employee=employee,
            organization=organization,
            effective_from=effective_from,
            components_data=components_data,
            notes=notes,
            created_by=created_by,
            compensation_type=compensation_type,
            daily_rate=daily_rate,
            hourly_rate=hourly_rate
        )


def seed_demo_dataset(org_id=None, dry_run=False):
    """
    Main entry point for seeding the realistic July 2026 demo dataset.
    Idempotent and recoverable from partial runs.
    """
    org = resolve_target_organization(org_id)

    # 1. Idempotency Check (Check first if complete demo dataset is already present)
    if is_demo_dataset_seeded(org):
        return {
            "status": "already_seeded",
            "organization": org,
            "message": f"Demo dataset for July {DEMO_YEAR} already exists in organization '{org.name}' (ID: {org.id}).",
        }

    # 2. Check for conflicting real non-demo attendance logs for July 2026
    has_real_logs = AttendanceLog.objects.filter(
        employee__organization=org,
        date__year=DEMO_YEAR,
        date__month=DEMO_MONTH
    ).exclude(employee__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").exists()

    if has_real_logs:
        raise ValidationError(
            f"Organization '{org.name}' already contains real non-demo attendance logs for July 2026. "
            "Aborting to protect production data. Please specify a different organization with --org-id."
        )

    # 3. Dry-Run Check (returns without writing any database records)
    if dry_run:
        return {
            "status": "dry_run",
            "organization": org,
            "message": f"[DRY RUN] Would seed July {DEMO_YEAR} demo dataset (5 employees, attendance logs, finalized periods, payslips) into organization '{org.name}' (ID: {org.id}).",
        }

    # 4. Atomic Execution Wrapper
    with transaction.atomic():
        # A. Resolve Admin Actor for Auditing
        admin_user = Employee.objects.filter(organization=org, isSuperAdmin=True).first() or \
                     Employee.objects.filter(organization=org, is_staff=True).first() or \
                     Employee.objects.filter(organization=org).first()

        # B. Ensure Organization Settings & Attendance Policy
        if hasattr(org, 'settings') and org.settings:
            org.settings.payroll_currency = "INR"
            org.settings.payroll_proration_basis = "WORKING_DAYS"
            org.settings.is_attendance_enabled = True
            org.settings.save()

        policy, _ = AttendancePolicy.objects.get_or_create(
            organization=org,
            effective_from=datetime_date(2026, 1, 1),
            defaults={
                "grace_period_minutes": 15,
                "full_day_minimum_minutes": 480,
                "half_day_minimum_minutes": 240,
                "break_duration_minutes": 60,
                "break_type": "Unpaid",
                "default_weekly_holidays": ["Saturday", "Sunday"],
                "auto_approve_attendance": True,
            }
        )

        # C. Ensure Organization-Scoped Schedules
        for cfg in DEMO_EMPLOYEES_CONFIG:
            Schedule.objects.update_or_create(
                organization=org,
                designation=cfg["designation"],
                defaults={
                    "shiftStart": "09:00",
                    "shiftEnd": "18:00",
                }
            )

        # D. Organization Holiday (15 July 2026)
        Holiday.objects.update_or_create(
            organization=org,
            date=datetime_date(2026, 7, 15),
            defaults={
                "name": "Mid-Year Organization Day",
                "description": f"{DEMO_MARKER} Mid-Year Organization Celebration Day",
                "banner": "",
            }
        )

        # E. Organization Leave Types
        lt_casual, _ = LeaveType.objects.get_or_create(
            organization=org,
            name="Casual Leave",
            defaults={
                "description": f"{DEMO_MARKER} Paid Casual Leave",
                "limitPeriod": "Yearly",
                "maxLimit": 12,
                "is_paid": True,
                "status": "Active",
            }
        )
        lt_unpaid, _ = LeaveType.objects.get_or_create(
            organization=org,
            name="Leave Without Pay",
            defaults={
                "description": f"{DEMO_MARKER} Unpaid Leave / LWP",
                "limitPeriod": "Yearly",
                "maxLimit": 30,
                "is_paid": False,
                "status": "Active",
            }
        )

        # F. Ensure Role and 5 Demo Employees
        role_employee, _ = Role.objects.get_or_create(
            name="Employee",
            defaults={"description": "Standard Employee Role"}
        )

        created_employees = {}
        for cfg in DEMO_EMPLOYEES_CONFIG:
            base_email = cfg["email"]
            existing_global = Employee.objects.filter(email=base_email).first()
            if existing_global and existing_global.organization_id != org.id:
                email_user = base_email.split('@')[0]
                emp_email = f"{email_user}.org{org.id}@{DEMO_EMAIL_DOMAIN}"
            else:
                emp_email = base_email

            emp_obj = Employee.objects.filter(
                organization=org,
                employee_code=cfg["code"]
            ).first() or Employee.objects.filter(
                organization=org,
                email=emp_email
            ).first()

            if not emp_obj:
                emp_obj = Employee.objects.create(
                    organization=org,
                    email=emp_email,
                    username=emp_email.split('@')[0],
                    first_name=cfg["first_name"],
                    last_name=cfg["last_name"],
                    designation=cfg["designation"],
                    employee_code=cfg["code"],
                    joining_date=datetime_date(2026, 1, 1),
                    employment_status="Active",
                    is_active=True,
                    role=role_employee,
                )
            else:
                emp_obj.first_name = cfg["first_name"]
                emp_obj.last_name = cfg["last_name"]
                emp_obj.designation = cfg["designation"]
                emp_obj.employee_code = cfg["code"]
                emp_obj.joining_date = datetime_date(2026, 1, 1)
                emp_obj.employment_status = "Active"
                emp_obj.is_active = True
                emp_obj.role = role_employee
                emp_obj.save()
            created_employees[cfg["code"]] = emp_obj

        # G. Create Deterministic July 2026 Attendance Logs (Clean existing demo logs first)
        AttendanceLog.objects.filter(
            employee__in=created_employees.values(),
            date__year=DEMO_YEAR,
            date__month=DEMO_MONTH
        ).delete()

        # Clean existing demo leaves for July 2026 first
        Leave.objects.filter(
            employee__in=created_employees.values(),
            startDate__year=DEMO_YEAR,
            startDate__month=DEMO_MONTH
        ).delete()

        # Calculate all weekdays in July 2026 excluding the Holiday (July 15)
        cal = calendar.Calendar()
        july_days = [d for d in cal.itermonthdates(DEMO_YEAR, DEMO_MONTH) if d.month == DEMO_MONTH]
        working_days = [d for d in july_days if d.weekday() < 5 and d != datetime_date(2026, 7, 15)]

        emp1 = created_employees["DEMO-EMP-001"]
        emp2 = created_employees["DEMO-EMP-002"]
        emp3 = created_employees["DEMO-EMP-003"]
        emp4 = created_employees["DEMO-EMP-004"]
        emp5 = created_employees["DEMO-EMP-005"]
        emp6 = created_employees["DEMO-EMP-006"]

        # 1. Employee 1 (Aarav Sharma) - Perfect Attendance + Multi-Session on July 6
        for dt in working_days:
            if dt == datetime_date(2026, 7, 6):
                # Session 1: 09:00 - 13:00 (4h)
                AttendanceLog.objects.create(
                    employee=emp1,
                    employeeName=f"{emp1.first_name} {emp1.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(13, 0))),
                    totalDuration="4h 00m",
                    status="Approved",
                )
                # Session 2: 14:00 - 18:00 (4h)
                AttendanceLog.objects.create(
                    employee=emp1,
                    employeeName=f"{emp1.first_name} {emp1.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(14, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="4h 00m",
                    status="Approved",
                )
            else:
                AttendanceLog.objects.create(
                    employee=emp1,
                    employeeName=f"{emp1.first_name} {emp1.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="8h 00m",
                    status="Approved",
                )

        # 2. Employee 2 (Priya Nair) - Paid Casual Leave on July 10
        Leave.objects.create(
            employee=emp2,
            employeeName=f"{emp2.first_name} {emp2.last_name}",
            leaveType=lt_casual,
            leaveTypeName=lt_casual.name,
            startDate=datetime_date(2026, 7, 10),
            endDate=datetime_date(2026, 7, 10),
            duration=1.0,
            dayType="Full",
            reason=f"{DEMO_MARKER} Family function",
            status="Approved",
        )
        for dt in working_days:
            if dt != datetime_date(2026, 7, 10):
                AttendanceLog.objects.create(
                    employee=emp2,
                    employeeName=f"{emp2.first_name} {emp2.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="8h 00m",
                    status="Approved",
                )

        # 3. Employee 3 (Vikram Patel) - Unpaid Leave (LWP) on July 22
        Leave.objects.create(
            employee=emp3,
            employeeName=f"{emp3.first_name} {emp3.last_name}",
            leaveType=lt_unpaid,
            leaveTypeName=lt_unpaid.name,
            startDate=datetime_date(2026, 7, 22),
            endDate=datetime_date(2026, 7, 22),
            duration=1.0,
            dayType="Full",
            reason=f"{DEMO_MARKER} Personal travel",
            status="Approved",
        )
        for dt in working_days:
            if dt != datetime_date(2026, 7, 22):
                AttendanceLog.objects.create(
                    employee=emp3,
                    employeeName=f"{emp3.first_name} {emp3.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="8h 00m",
                    status="Approved",
                )

        # 4. Employee 4 (Sneha Kulkarni) - Half-Day July 8 + Late July 17
        for dt in working_days:
            if dt == datetime_date(2026, 7, 8):
                # Half-day: 09:00 - 13:00 (4 hours)
                AttendanceLog.objects.create(
                    employee=emp4,
                    employeeName=f"{emp4.first_name} {emp4.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(13, 0))),
                    totalDuration="4h 00m",
                    status="Approved",
                )
            elif dt == datetime_date(2026, 7, 17):
                # Late arrival: 09:45 - 18:00 (Grace period exceeded)
                AttendanceLog.objects.create(
                    employee=emp4,
                    employeeName=f"{emp4.first_name} {emp4.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 45))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="7h 15m",
                    status="Approved",
                )
            else:
                AttendanceLog.objects.create(
                    employee=emp4,
                    employeeName=f"{emp4.first_name} {emp4.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="8h 00m",
                    status="Approved",
                )

        # 5. Employee 5 (Rohan Mehta) - Unexcused Absent on July 28
        for dt in working_days:
            if dt != datetime_date(2026, 7, 28):
                AttendanceLog.objects.create(
                    employee=emp5,
                    employeeName=f"{emp5.first_name} {emp5.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(18, 0))),
                    totalDuration="8h 00m",
                    status="Approved",
                )

        # 6. Employee 6 (Kavita Reddy) - Hourly Wage (176.50 hrs total across July)
        # 19 days @ 8.5 hrs (09:00 - 17:30 = 510 mins) = 161.5 hrs
        # July 13 multi-session: 09:00 - 13:00 (4.0h) & 14:00 - 17:30 (3.5h) -> 7.5 hrs (450 mins)
        # July 20 multi-session: 09:00 - 12:30 (3.5h) & 13:30 - 17:30 (4.0h) -> 7.5 hrs (450 mins)
        for dt in working_days:
            if dt == datetime_date(2026, 7, 13):
                AttendanceLog.objects.create(
                    employee=emp6,
                    employeeName=f"{emp6.first_name} {emp6.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(13, 0))),
                    totalDuration="4h 00m",
                    status="Approved",
                )
                AttendanceLog.objects.create(
                    employee=emp6,
                    employeeName=f"{emp6.first_name} {emp6.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(14, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(17, 30))),
                    totalDuration="3h 30m",
                    status="Approved",
                )
            elif dt == datetime_date(2026, 7, 20):
                AttendanceLog.objects.create(
                    employee=emp6,
                    employeeName=f"{emp6.first_name} {emp6.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(12, 30))),
                    totalDuration="3h 30m",
                    status="Approved",
                )
                AttendanceLog.objects.create(
                    employee=emp6,
                    employeeName=f"{emp6.first_name} {emp6.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(13, 30))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(17, 30))),
                    totalDuration="4h 00m",
                    status="Approved",
                )
            else:
                AttendanceLog.objects.create(
                    employee=emp6,
                    employeeName=f"{emp6.first_name} {emp6.last_name}",
                    date=dt,
                    clockIn=timezone.make_aware(datetime.combine(dt, time(9, 0))),
                    clockOut=timezone.make_aware(datetime.combine(dt, time(17, 30))),
                    totalDuration="8h 30m",
                    status="Approved",
                )

        # I. Finalize Attendance Period using core service
        existing_att = AttendancePeriod.objects.filter(organization=org, year=DEMO_YEAR, month=DEMO_MONTH).first()
        if existing_att and existing_att.status == 'Finalized':
            existing_att.status = 'Draft'
            existing_att.save()

        att_period = finalize_attendance_period(org, DEMO_YEAR, DEMO_MONTH, admin_user)

        # J. Organization Salary Components
        comp_basic, _ = SalaryComponent.objects.get_or_create(
            organization=org,
            code="DEMO_BASIC",
            defaults={
                "name": "Basic Salary",
                "component_type": "Earning",
                "is_taxable": True,
                "is_proratable": True,
                "is_active": True,
                "description": f"{DEMO_MARKER} Base contractual monthly wage",
            }
        )
        comp_hra, _ = SalaryComponent.objects.get_or_create(
            organization=org,
            code="DEMO_HRA",
            defaults={
                "name": "House Rent Allowance",
                "component_type": "Earning",
                "is_taxable": True,
                "is_proratable": False,
                "is_active": True,
                "description": f"{DEMO_MARKER} Monthly housing allowance",
            }
        )
        comp_trans, _ = SalaryComponent.objects.get_or_create(
            organization=org,
            code="DEMO_TRANSPORT",
            defaults={
                "name": "Transport Allowance",
                "component_type": "Earning",
                "is_taxable": False,
                "is_proratable": True,
                "is_active": True,
                "description": f"{DEMO_MARKER} Monthly travel subsidy",
            }
        )
        comp_pf, _ = SalaryComponent.objects.get_or_create(
            organization=org,
            code="DEMO_PF",
            defaults={
                "name": "Provident Fund",
                "component_type": "Deduction",
                "is_taxable": False,
                "is_proratable": False,
                "is_active": True,
                "description": f"{DEMO_MARKER} Statutory employee PF deduction",
            }
        )
        comp_ptax, _ = SalaryComponent.objects.get_or_create(
            organization=org,
            code="DEMO_PTAX",
            defaults={
                "name": "Professional Tax",
                "component_type": "Deduction",
                "is_taxable": False,
                "is_proratable": False,
                "is_active": True,
                "description": f"{DEMO_MARKER} Statutory state professional tax",
            }
        )

        # K. Assign/Reconcile Salary Structures for All Employees
        effective_date = datetime_date(2026, 1, 1)
        for cfg in DEMO_EMPLOYEES_CONFIG:
            emp = created_employees[cfg["code"]]
            sal = cfg.get("salary", {})
            comp_type = cfg.get("compensation_type", "MONTHLY")
            daily_rate = cfg.get("daily_rate")
            hourly_rate = cfg.get("hourly_rate")

            if comp_type == 'DAILY':
                components_payload = []
            elif comp_type == 'HOURLY':
                components_payload = [
                    {"salary_component_id": comp_trans.id, "amount": sal["transport"]}
                ] if "transport" in sal else []
            else:
                components_payload = [
                    {"salary_component_id": comp_basic.id, "amount": sal["basic"]},
                    {"salary_component_id": comp_hra.id, "amount": sal["hra"]},
                    {"salary_component_id": comp_trans.id, "amount": sal["transport"]},
                    {"salary_component_id": comp_pf.id, "amount": sal["pf"]},
                    {"salary_component_id": comp_ptax.id, "amount": sal["ptax"]},
                ]

            assign_or_reconcile_demo_salary_structure(
                employee=emp,
                organization=org,
                effective_from=effective_date,
                components_data=components_payload,
                notes=f"{DEMO_MARKER} Standard compensation structure ({comp_type})",
                created_by=admin_user,
                compensation_type=comp_type,
                daily_rate=daily_rate,
                hourly_rate=hourly_rate
            )

        # Ensure any non-demo active employee in the target org has a baseline salary structure so payroll finalization succeeds
        for non_demo_emp in Employee.objects.filter(organization=org, is_active=True).exclude(email__endswith=f"@{DEMO_EMAIL_DOMAIN}"):
            if not EmployeeSalaryStructure.objects.filter(employee=non_demo_emp, is_active=True).exists():
                assign_or_reconcile_demo_salary_structure(
                    employee=non_demo_emp,
                    organization=org,
                    effective_from=effective_date,
                    components_data=[{"salary_component_id": comp_basic.id, "amount": Decimal("50000.00")}],
                    notes=f"{DEMO_MARKER} Baseline salary structure",
                    created_by=admin_user
                )

        # L. Calculate Payroll Period (First Pass)
        existing_pr = PayrollPeriod.objects.filter(organization=org, year=DEMO_YEAR, month=DEMO_MONTH).first()
        if existing_pr and existing_pr.status == 'Finalized':
            existing_pr.status = 'Draft'
            existing_pr.save()

        payroll_period = calculate_payroll_period(org, DEMO_YEAR, DEMO_MONTH, admin_user)

        # M. Add Performance Bonus Adjustment for Employee 5 (Rohan Mehta)
        PayrollAdjustment.objects.filter(payroll_period=payroll_period, employee=emp5).delete()
        PayrollAdjustment.objects.create(
            organization=org,
            payroll_period=payroll_period,
            employee=emp5,
            adjustment_type="Earning",
            category="Bonus",
            amount=Decimal("5000.00"),
            description=f"{DEMO_MARKER} Performance Bonus",
            created_by=admin_user,
        )

        # N. Recalculate Payroll with the Adjustment
        payroll_period = calculate_payroll_period(org, DEMO_YEAR, DEMO_MONTH, admin_user)

        # O. Finalize Payroll Period (Automatically Issues Payslips)
        finalized_payroll = finalize_payroll_period(org, DEMO_YEAR, DEMO_MONTH, admin_user)

        payslip_count = Payslip.objects.filter(
            payroll_period=finalized_payroll,
            status='Issued',
            employee__in=created_employees.values()
        ).count()

        return {
            "status": "success",
            "organization": org,
            "attendance_period": att_period,
            "payroll_period": finalized_payroll,
            "payslip_count": payslip_count,
            "employees": created_employees,
        }


def remove_demo_dataset(org_id=None, dry_run=False):
    """
    Cleans up all demo-specific records in strict reverse-dependency order.
    Preserves all real non-demo organization data.
    Idempotent and safe to run multiple times.
    """
    org = resolve_target_organization(org_id)

    demo_employees = Employee.objects.filter(
        organization=org,
        email__endswith=f"@{DEMO_EMAIL_DOMAIN}"
    )

    # Check if ANY demo artifacts exist
    has_demo_artifacts = (
        demo_employees.exists() or
        SalaryComponent.objects.filter(organization=org, code__startswith="DEMO_").exists() or
        EmployeeSalaryStructure.objects.filter(organization=org, notes__contains=DEMO_MARKER).exists() or
        Holiday.objects.filter(organization=org, description__contains=DEMO_MARKER).exists()
    )

    if not has_demo_artifacts:
        return {
            "status": "success",
            "organization": org,
            "counts": {},
            "message": f"No demo records found in organization '{org.name}' (ID: {org.id}). Nothing to remove.",
        }

    if dry_run:
        return {
            "status": "dry_run",
            "organization": org,
            "message": f"[DRY RUN] Would delete demo records (employees, logs, leaves, snapshots, salary structures, payroll adjustments, payslips) from organization '{org.name}'.",
        }

    with transaction.atomic():
        counts = {}

        # 1. Demo Payslips (strictly scoped to this org)
        payslips_del, _ = Payslip.objects.filter(
            organization=org,
            employee__in=demo_employees
        ).delete()
        counts["payslips"] = payslips_del

        # 2. Demo Payroll Adjustments
        adj_del, _ = PayrollAdjustment.objects.filter(
            organization=org,
            employee__in=demo_employees
        ).delete()
        counts["payroll_adjustments"] = adj_del

        # 3. Demo Payroll Employee Snapshots
        p_snaps_del, _ = PayrollEmployeeSnapshot.objects.filter(
            payroll_period__organization=org,
            employee__in=demo_employees
        ).delete()
        counts["payroll_employee_snapshots"] = p_snaps_del

        # 4. Payroll Period (only if period contains no other non-demo employees)
        payroll_period = PayrollPeriod.objects.filter(organization=org, year=DEMO_YEAR, month=DEMO_MONTH).first()
        if payroll_period:
            remaining_p_snaps = PayrollEmployeeSnapshot.objects.filter(payroll_period=payroll_period).count()
            if remaining_p_snaps == 0:
                payroll_period.delete()
                counts["payroll_period"] = 1
            else:
                payroll_period.status = 'Draft'
                payroll_period.save()
                counts["payroll_period"] = 0

        # 5. Demo Attendance Period Employee Snapshots
        a_snaps_del, _ = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period__organization=org,
            employee__in=demo_employees
        ).delete()
        counts["attendance_employee_snapshots"] = a_snaps_del

        # 6. Attendance Period (only if period contains no other non-demo employees)
        att_period = AttendancePeriod.objects.filter(organization=org, year=DEMO_YEAR, month=DEMO_MONTH).first()
        if att_period:
            remaining_a_snaps = AttendancePeriodEmployeeSnapshot.objects.filter(attendance_period=att_period).count()
            if remaining_a_snaps == 0:
                att_period.delete()
                counts["attendance_period"] = 1
            else:
                att_period.status = 'Draft'
                att_period.save()
                counts["attendance_period"] = 0

        # 7. Demo Attendance Logs
        logs_del, _ = AttendanceLog.objects.filter(
            employee__organization=org,
            employee__in=demo_employees
        ).delete()
        counts["attendance_logs"] = logs_del

        # 8. Demo Leaves
        leaves_del, _ = Leave.objects.filter(
            employee__organization=org,
            employee__in=demo_employees
        ).delete()
        counts["leaves"] = leaves_del

        # 9. Demo Salary Components assignment
        emp_sal_comp_del, _ = EmployeeSalaryComponent.objects.filter(
            salary_structure__organization=org
        ).filter(
            models.Q(salary_structure__employee__in=demo_employees) |
            models.Q(salary_component__code__startswith="DEMO_")
        ).delete()
        counts["employee_salary_components"] = emp_sal_comp_del

        # 10. Demo Employee Salary Structures + baseline demo structures
        structs_del, _ = EmployeeSalaryStructure.objects.filter(
            organization=org
        ).filter(
            models.Q(employee__in=demo_employees) |
            models.Q(notes__contains=DEMO_MARKER)
        ).delete()
        counts["employee_salary_structures"] = structs_del

        # 11. Demo Salary Components
        sal_comp_del, _ = SalaryComponent.objects.filter(
            organization=org,
            code__startswith="DEMO_"
        ).delete()
        counts["salary_components"] = sal_comp_del

        # 12. Demo Holiday
        hol_del, _ = Holiday.objects.filter(
            organization=org,
            description__contains=DEMO_MARKER
        ).delete()
        counts["holidays"] = hol_del

        # 13. Demo Employees
        emp_del, _ = demo_employees.delete()
        counts["employees"] = emp_del

        return {
            "status": "success",
            "organization": org,
            "counts": counts,
        }
