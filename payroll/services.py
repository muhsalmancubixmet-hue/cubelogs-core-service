# --------------------------------------------------------------------------------
#       Payroll Services - Salary Calculation, Version Resolution & History
# --------------------------------------------------------------------------------

from datetime import date as datetime_date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied

from core.models import AuditLog
from attendance.models import AttendancePeriod, AttendancePeriodEmployeeSnapshot
from users.models import OrganizationMembership
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    PayrollPeriod,
    PayrollAdjustment,
    PayrollEmployeeSnapshot,
    Payslip,
)


def is_payroll_locked(organization, year, month):
    """
    Returns True if the payroll period for the given organization/year/month is finalized.
    """
    return PayrollPeriod.objects.filter(
        organization=organization,
        year=year,
        month=month,
        status='Finalized'
    ).exists()


def get_employee_salary_structure(employee, organization, target_date=None):
    """
    Resolves the active salary structure in effect for an employee on a target date.
    Uses the latest structure where effective_from <= target_date within the explicit organization.
    """
    if target_date is None and isinstance(organization, (datetime_date, datetime)):
        # Fallback for transition / legacy calls where organization was omitted
        target_date = organization
        organization = getattr(employee, 'organization', None)

    if not employee or not organization or not target_date:
        return None

    return EmployeeSalaryStructure.objects.filter(
        employee=employee,
        organization=organization,
        is_active=True,
        effective_from__lte=target_date
    ).order_by('-effective_from', '-created_at').first()


def get_bulk_employee_salary_structures(organization, target_date):
    """
    Bulk resolves active salary structures for all employees in an organization
    as of a target date in 1 query.
    Returns dict: employee_id -> EmployeeSalaryStructure instance.
    Uses exact effective_from logic matching get_employee_salary_structure:
    latest structure where effective_from <= target_date and is_active=True.
    """
    if not organization or not target_date:
        return {}

    structures = EmployeeSalaryStructure.objects.filter(
        organization=organization,
        is_active=True,
        effective_from__lte=target_date
    ).prefetch_related('components__salary_component').order_by('employee_id', '-effective_from', '-created_at')

    structures_by_emp = {}
    for struct in structures:
        if struct.employee_id not in structures_by_emp:
            structures_by_emp[struct.employee_id] = struct

    return structures_by_emp


def calculate_salary_totals(components_payload, organization):
    """
    Authoritative backend calculator for salary structure totals.
    Validates components against tenant catalog and computes Gross, Deductions, Net.
    """
    gross_salary = Decimal('0.00')
    base_deductions = Decimal('0.00')
    parsed_lines = []

    for item in components_payload:
        comp_id = item.get('salary_component_id') or item.get('salary_component')
        raw_amount = item.get('amount', '0.00')

        try:
            amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"components": f"Invalid monetary amount: '{raw_amount}'."})

        if amount < Decimal('0.00'):
            raise ValidationError({"components": "Salary component amounts cannot be negative."})

        comp = SalaryComponent.objects.filter(
            id=comp_id,
            organization=organization,
            is_active=True
        ).first()

        if not comp:
            raise ValidationError({"components": f"Salary component ID {comp_id} was not found for this organization."})

        if comp.component_type == 'Earning':
            gross_salary += amount
        elif comp.component_type == 'Deduction':
            base_deductions += amount

        parsed_lines.append({
            'component': comp,
            'amount': amount
        })

    base_net_salary = gross_salary - base_deductions
    return gross_salary, base_deductions, base_net_salary, parsed_lines


def assign_or_revise_salary_structure(employee, organization, effective_from, components_data=None, notes=None, created_by=None, compensation_type='MONTHLY', daily_rate=None, hourly_rate=None):
    """
    Assigns an initial salary structure or creates a new historical revision.
    Enforces:
    - effective_from.day == 1
    - Finalized payroll lock check (cannot revise for a month with Finalized Payroll)
    - Immutable history (creates new record)
    - Backend calculation authority
    - Currency snapshotting
    - Support for MONTHLY, DAILY, and HOURLY compensation types
    - Non-leaking AuditLog creation
    """
    if components_data is None:
        components_data = []

    if effective_from.day != 1:
        raise ValidationError({"effective_from": "Salary effective date must be the first day of a month."})

    has_membership = OrganizationMembership.objects.filter(
        user=employee,
        organization=organization,
        is_active_in_org=True,
        is_deleted=False
    ).exists()
    if not has_membership:
        if OrganizationMembership.objects.filter(user=employee).exists() or getattr(employee, 'organization', None) != organization:
            raise PermissionDenied("Cannot assign salary structure to an employee outside your organization.")

    if compensation_type not in ['MONTHLY', 'DAILY', 'HOURLY']:
        raise ValidationError({"compensation_type": "Invalid compensation type. Must be 'MONTHLY', 'DAILY', or 'HOURLY'."})

    # Check if a finalized payroll period exists for this month
    if is_payroll_locked(organization, effective_from.year, effective_from.month):
        raise ValidationError({
            "effective_from": f"Cannot assign or revise salary structure for {effective_from.strftime('%B %Y')}: Payroll for this month is Finalized. Please reopen Payroll first."
        })

    # Check for existing version on the exact same date
    existing_on_date = EmployeeSalaryStructure.objects.filter(
        employee=employee,
        organization=organization,
        effective_from=effective_from,
        is_active=True
    ).exists()
    if existing_on_date:
        raise ValidationError({
            "effective_from": f"A salary structure already exists for {effective_from.strftime('%B %Y')}. Revisions must have a distinct effective date."
        })

    is_initial = not EmployeeSalaryStructure.objects.filter(
        employee=employee,
        organization=organization,
        is_active=True
    ).exists()

    daily_rate_val = None
    hourly_rate_val = None

    if compensation_type == 'DAILY':
        if daily_rate is None or str(daily_rate).strip() == '':
            raise ValidationError({"daily_rate": "Daily rate is required for Daily Wage compensation."})
        try:
            daily_rate_val = Decimal(str(daily_rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"daily_rate": f"Invalid daily rate amount: '{daily_rate}'."})
        if daily_rate_val <= Decimal('0.00'):
            raise ValidationError({"daily_rate": "Daily rate must be greater than zero."})

        if components_data:
            gross_salary, base_deductions, base_net_salary, parsed_lines = calculate_salary_totals(components_data, organization)
        else:
            gross_salary, base_deductions, base_net_salary, parsed_lines = Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), []

    elif compensation_type == 'HOURLY':
        if hourly_rate is None or str(hourly_rate).strip() == '':
            raise ValidationError({"hourly_rate": "Hourly rate is required for Hourly Wage compensation."})
        try:
            hourly_rate_val = Decimal(str(hourly_rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"hourly_rate": f"Invalid hourly rate amount: '{hourly_rate}'."})
        if hourly_rate_val <= Decimal('0.00'):
            raise ValidationError({"hourly_rate": "Hourly rate must be greater than zero."})

        if components_data:
            gross_salary, base_deductions, base_net_salary, parsed_lines = calculate_salary_totals(components_data, organization)
        else:
            gross_salary, base_deductions, base_net_salary, parsed_lines = Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), []

    else:
        # MONTHLY
        if not components_data:
            raise ValidationError({"components": "At least one salary component is required for Monthly Salary."})
        gross_salary, base_deductions, base_net_salary, parsed_lines = calculate_salary_totals(components_data, organization)

    # Snapshot organization payroll currency
    currency = 'INR'
    if hasattr(organization, 'settings') and organization.settings and organization.settings.payroll_currency:
        currency = organization.settings.payroll_currency

    with transaction.atomic():
        structure = EmployeeSalaryStructure.objects.create(
            organization=organization,
            employee=employee,
            effective_from=effective_from,
            currency=currency,
            compensation_type=compensation_type,
            daily_rate=daily_rate_val,
            hourly_rate=hourly_rate_val,
            gross_salary=gross_salary,
            base_deductions=base_deductions,
            base_net_salary=base_net_salary,
            notes=notes,
            created_by=created_by,
        )

        if parsed_lines:
            component_objs = [
                EmployeeSalaryComponent(
                    salary_structure=structure,
                    salary_component=line['component'],
                    amount=line['amount']
                )
                for line in parsed_lines
            ]
            EmployeeSalaryComponent.objects.bulk_create(component_objs)

        # AuditLog entry without leaking raw monetary figures
        actor_name = f"{created_by.first_name} {created_by.last_name}".strip() or created_by.email if created_by else "Admin"
        emp_name = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        action_name = "Salary Structure Assigned" if is_initial else "Salary Structure Revised"

        rate_info = ""
        if compensation_type == 'DAILY':
            rate_info = f", Rate: {currency} {daily_rate_val}/day"
        elif compensation_type == 'HOURLY':
            rate_info = f", Rate: {currency} {hourly_rate_val}/hr"

        AuditLog.objects.create(
            organization=organization,
            employee=created_by,
            employeeName=actor_name,
            action=action_name,
            details=f"{action_name} for {emp_name} ({compensation_type}{rate_info}, Effective: {effective_from.isoformat()}, Currency: {currency})."
        )

    return structure


def calculate_employee_payroll(employee, attendance_snapshot, salary_structure, adjustments=None, proration_basis='WORKING_DAYS', organization=None):
    """
    Computes individual monthly payroll calculation for one employee based strictly on
    their finalized AttendancePeriodEmployeeSnapshot, active EmployeeSalaryStructure,
    and optional PayrollAdjustments.
    Supports both MONTHLY and DAILY compensation types.
    """
    if adjustments is None:
        adjustments = []

    # 1. Handle Missing Salary Structure
    if not salary_structure:
        return {
            "employee": employee,
            "employee_name": f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            "designation": employee.designation or '',
            "salary_structure": None,
            "salary_effective_from": None,
            "compensation_type": "MONTHLY",
            "currency": "USD",
            "working_days": attendance_snapshot.working_days if attendance_snapshot else 0,
            "payable_attendance_units": Decimal(str(attendance_snapshot.payable_attendance_units)) if attendance_snapshot else Decimal('0.00'),
            "paid_leave_days": attendance_snapshot.paid_leave_days if attendance_snapshot else Decimal('0.00'),
            "unpaid_leave_days": attendance_snapshot.unpaid_leave_days if attendance_snapshot else Decimal('0.00'),
            "absent_days": attendance_snapshot.absent_days if attendance_snapshot else 0,
            "base_gross_salary": Decimal('0.00'),
            "base_fixed_deductions": Decimal('0.00'),
            "base_net_salary": Decimal('0.00'),
            "proratable_gross": Decimal('0.00'),
            "non_proratable_gross": Decimal('0.00'),
            "daily_rate": Decimal('0.0000'),
            "attendance_deduction": Decimal('0.00'),
            "earned_gross": Decimal('0.00'),
            "additional_earnings": Decimal('0.00'),
            "additional_deductions": Decimal('0.00'),
            "total_deductions": Decimal('0.00'),
            "net_payable": Decimal('0.00'),
            "salary_components_snapshot": [],
            "attendance_summary_snapshot": {},
            "calculation_breakdown": {"error": "Missing active salary structure for period."},
            "status": "MissingSalaryStructure",
            "notes": "No salary structure assigned effective for this period."
        }

    comp_type = getattr(salary_structure, 'compensation_type', 'MONTHLY') or 'MONTHLY'

    # 2. Extract Base Salary Components
    component_lines = salary_structure.components.select_related('salary_component').all()

    # 3. Attendance Extraction
    working_days = attendance_snapshot.working_days if attendance_snapshot else 0
    payable_units = Decimal(str(attendance_snapshot.payable_attendance_units)) if attendance_snapshot else Decimal('0.00')
    paid_leave_days = attendance_snapshot.paid_leave_days if attendance_snapshot else Decimal('0.00')
    unpaid_leave_days = attendance_snapshot.unpaid_leave_days if attendance_snapshot else Decimal('0.00')
    absent_days = attendance_snapshot.absent_days if attendance_snapshot else 0

    attendance_summary_snapshot = {
        "working_days": working_days,
        "present_days": attendance_snapshot.present_days if attendance_snapshot else 0.0,
        "half_days": attendance_snapshot.half_days if attendance_snapshot else 0,
        "absent_days": absent_days,
        "leave_days": attendance_snapshot.leave_days if attendance_snapshot else 0.0,
        "paid_leave_days": str(paid_leave_days),
        "unpaid_leave_days": str(unpaid_leave_days),
        "payable_attendance_units": str(payable_units),
        "late_count": attendance_snapshot.late_count if attendance_snapshot else 0,
        "total_late_minutes": attendance_snapshot.total_late_minutes if attendance_snapshot else 0,
        "total_worked_minutes": attendance_snapshot.total_worked_minutes if attendance_snapshot else 0,
        "weekly_off_days": attendance_snapshot.weekly_off_days if attendance_snapshot else 0,
        "holiday_days": attendance_snapshot.holiday_days if attendance_snapshot else 0,
    }

    # Variable Adjustments
    additional_earnings = Decimal('0.00')
    additional_deductions = Decimal('0.00')
    for adj in adjustments:
        if adj.adjustment_type == 'Earning':
            additional_earnings += adj.amount
        elif adj.adjustment_type == 'Deduction':
            additional_deductions += adj.amount

    daily_rate = Decimal('0.0000')
    hourly_rate = None
    payable_hours = Decimal('0.00')

    if comp_type == 'HOURLY':
        # ---------------------------------------------------------------------
        # HOURLY WAGE CALCULATION ENGINE
        # ---------------------------------------------------------------------
        contractual_hourly_rate = Decimal(str(salary_structure.hourly_rate or '0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        hourly_rate = contractual_hourly_rate

        # Finalized payable work hours from AttendancePeriodEmployeeSnapshot only
        if attendance_snapshot and hasattr(attendance_snapshot, 'payable_work_hours') and attendance_snapshot.payable_work_hours is not None:
            payable_hours = Decimal(str(attendance_snapshot.payable_work_hours)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif attendance_snapshot:
            payable_hours = (Decimal(str(attendance_snapshot.total_worked_minutes)) / Decimal('60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            payable_hours = Decimal('0.00')

        worked_hours = (Decimal(str(attendance_snapshot.total_worked_minutes if attendance_snapshot else 0)) / Decimal('60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        paid_leave_hours = (payable_hours - worked_hours) if payable_hours > worked_hours else Decimal('0.00')

        org = organization
        if not org and salary_structure and getattr(salary_structure, 'organization', None):
            org = salary_structure.organization
        if not org and attendance_snapshot and getattr(attendance_snapshot, 'attendance_period', None):
            org = attendance_snapshot.attendance_period.organization

        paid_leave_eligible = True
        if org and hasattr(org, 'settings') and org.settings is not None:
            paid_leave_eligible = getattr(org.settings, 'hourly_wage_paid_leave_eligible', True)

        wage_earnings = (contractual_hourly_rate * payable_hours).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        fixed_earnings = Decimal('0.00')
        base_fixed_deductions = Decimal('0.00')
        components_snapshot = []

        for item in component_lines:
            comp = item.salary_component
            amount = item.amount
            if comp.component_type == 'Earning':
                fixed_earnings += amount
            elif comp.component_type == 'Deduction':
                base_fixed_deductions += amount

            components_snapshot.append({
                "component_id": comp.id,
                "code": comp.code,
                "name": comp.name,
                "component_type": comp.component_type,
                "amount": str(amount),
                "is_taxable": comp.is_taxable,
                "is_proratable": False,
            })

        proratable_gross = Decimal('0.00')
        non_proratable_gross = fixed_earnings
        base_gross_salary = fixed_earnings
        base_net_salary = base_gross_salary - base_fixed_deductions

        attendance_deduction = Decimal('0.00')
        earned_gross = (wage_earnings + fixed_earnings).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        total_deductions = (base_fixed_deductions + additional_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        net_payable = (earned_gross + additional_earnings - total_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        daily_rate = Decimal('0.0000')
        status = 'OK'
        notes = ''
        if net_payable < Decimal('0.00'):
            status = 'NegativeNet'
            notes = f"Total deductions ({total_deductions}) exceed earned wage and additions ({earned_gross + additional_earnings})."

        calculation_breakdown = {
            "compensation_type": "HOURLY",
            "contractual_hourly_rate": str(contractual_hourly_rate),
            "payable_hours": str(payable_hours),
            "worked_hours": str(worked_hours),
            "paid_leave_hours": str(paid_leave_hours),
            "paid_leave_eligible": paid_leave_eligible,
            "wage_earnings": str(wage_earnings),
            "proration_basis": "N/A (Hourly Wage)",
            "working_days": working_days,
            "payable_attendance_units": str(payable_units),
            "unpaid_units": "0.00",
            "proratable_gross": "0.00",
            "non_proratable_gross": str(non_proratable_gross),
            "daily_rate": "0.0000",
            "attendance_deduction": "0.00",
            "earned_gross": str(earned_gross),
            "base_fixed_deductions": str(base_fixed_deductions),
            "additional_earnings": str(additional_earnings),
            "additional_deductions": str(additional_deductions),
            "total_deductions": str(total_deductions),
            "net_payable": str(net_payable),
        }

    elif comp_type == 'DAILY':
        # ---------------------------------------------------------------------
        # DAILY WAGE CALCULATION ENGINE
        # ---------------------------------------------------------------------
        contractual_daily_rate = Decimal(str(salary_structure.daily_rate or '0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        daily_rate = contractual_daily_rate.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

        org = organization
        if not org and salary_structure and getattr(salary_structure, 'organization', None):
            org = salary_structure.organization
        if not org and attendance_snapshot and getattr(attendance_snapshot, 'attendance_period', None):
            org = attendance_snapshot.attendance_period.organization

        paid_leave_eligible = True
        if org and hasattr(org, 'settings') and org.settings is not None:
            paid_leave_eligible = getattr(org.settings, 'daily_wage_paid_leave_eligible', True)

        if paid_leave_eligible:
            payable_days = payable_units
        else:
            # When paid leave is ineligible for daily wage, only actual worked attendance units are payable
            payable_days = Decimal(str(attendance_snapshot.present_days if attendance_snapshot else 0.0))

        wage_earnings = (contractual_daily_rate * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        fixed_earnings = Decimal('0.00')
        base_fixed_deductions = Decimal('0.00')
        components_snapshot = []

        for item in component_lines:
            comp = item.salary_component
            amount = item.amount
            if comp.component_type == 'Earning':
                fixed_earnings += amount
            elif comp.component_type == 'Deduction':
                base_fixed_deductions += amount

            components_snapshot.append({
                "component_id": comp.id,
                "code": comp.code,
                "name": comp.name,
                "component_type": comp.component_type,
                "amount": str(amount),
                "is_taxable": comp.is_taxable,
                "is_proratable": False,
            })

        proratable_gross = Decimal('0.00')
        non_proratable_gross = fixed_earnings
        base_gross_salary = fixed_earnings
        base_net_salary = base_gross_salary - base_fixed_deductions

        attendance_deduction = Decimal('0.00')
        earned_gross = (wage_earnings + fixed_earnings).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        total_deductions = (base_fixed_deductions + additional_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        net_payable = (earned_gross + additional_earnings - total_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        status = 'OK'
        notes = ''
        if net_payable < Decimal('0.00'):
            status = 'NegativeNet'
            notes = f"Total deductions ({total_deductions}) exceed earned wage and additions ({earned_gross + additional_earnings})."

        calculation_breakdown = {
            "compensation_type": "DAILY",
            "contractual_daily_rate": str(contractual_daily_rate),
            "payable_days": str(payable_days),
            "paid_leave_eligible": paid_leave_eligible,
            "wage_earnings": str(wage_earnings),
            "proration_basis": "N/A (Daily Wage)",
            "working_days": working_days,
            "payable_attendance_units": str(payable_units),
            "unpaid_units": "0.00",
            "proratable_gross": "0.00",
            "non_proratable_gross": str(non_proratable_gross),
            "daily_rate": str(daily_rate),
            "attendance_deduction": "0.00",
            "earned_gross": str(earned_gross),
            "base_fixed_deductions": str(base_fixed_deductions),
            "additional_earnings": str(additional_earnings),
            "additional_deductions": str(additional_deductions),
            "total_deductions": str(total_deductions),
            "net_payable": str(net_payable),
        }

    else:
        # ---------------------------------------------------------------------
        # MONTHLY SALARY CALCULATION ENGINE (Preserved Exactly)
        # ---------------------------------------------------------------------
        proratable_gross = Decimal('0.00')
        non_proratable_gross = Decimal('0.00')
        base_fixed_deductions = Decimal('0.00')
        components_snapshot = []

        for item in component_lines:
            comp = item.salary_component
            amount = item.amount
            is_proratable = getattr(comp, 'is_proratable', True)
            if comp.component_type == 'Earning':
                if is_proratable:
                    proratable_gross += amount
                else:
                    non_proratable_gross += amount
            elif comp.component_type == 'Deduction':
                base_fixed_deductions += amount

            components_snapshot.append({
                "component_id": comp.id,
                "code": comp.code,
                "name": comp.name,
                "component_type": comp.component_type,
                "amount": str(amount),
                "is_taxable": comp.is_taxable,
                "is_proratable": is_proratable,
            })

        base_gross_salary = proratable_gross + non_proratable_gross
        base_net_salary = base_gross_salary - base_fixed_deductions

        unpaid_units = max(Decimal('0.00'), Decimal(str(working_days)) - payable_units)
        daily_rate = Decimal('0.0000')
        attendance_deduction = Decimal('0.00')
        status = 'OK'
        notes = ''

        if working_days == 0:
            daily_rate = Decimal('0.0000')
            attendance_deduction = Decimal('0.00')
            earned_gross = non_proratable_gross
            status = 'NotEligible' if base_gross_salary == Decimal('0.00') else 'OK'
        else:
            daily_rate = (proratable_gross / Decimal(str(working_days))).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            attendance_deduction = (unpaid_units * daily_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            earned_gross = proratable_gross - attendance_deduction + non_proratable_gross

        total_deductions = (base_fixed_deductions + attendance_deduction + additional_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        net_payable = (earned_gross + additional_earnings - base_fixed_deductions - additional_deductions).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if net_payable < Decimal('0.00'):
            status = 'NegativeNet'
            notes = f"Total deductions ({total_deductions}) exceed earned salary and additions ({earned_gross + additional_earnings})."

        calculation_breakdown = {
            "compensation_type": "MONTHLY",
            "proration_basis": proration_basis,
            "working_days": working_days,
            "payable_attendance_units": str(payable_units),
            "unpaid_units": str(unpaid_units),
            "proratable_gross": str(proratable_gross),
            "non_proratable_gross": str(non_proratable_gross),
            "daily_rate": str(daily_rate),
            "attendance_deduction": str(attendance_deduction),
            "earned_gross": str(earned_gross),
            "base_fixed_deductions": str(base_fixed_deductions),
            "additional_earnings": str(additional_earnings),
            "additional_deductions": str(additional_deductions),
            "total_deductions": str(total_deductions),
            "net_payable": str(net_payable),
        }

    return {
        "employee": employee,
        "employee_name": f"{employee.first_name} {employee.last_name}".strip() or employee.email,
        "designation": employee.designation or '',
        "salary_structure": salary_structure,
        "salary_effective_from": salary_structure.effective_from,
        "compensation_type": comp_type,
        "hourly_rate": hourly_rate,
        "payable_hours": payable_hours,
        "currency": salary_structure.currency,
        "working_days": working_days,
        "payable_attendance_units": payable_units,
        "paid_leave_days": paid_leave_days,
        "unpaid_leave_days": unpaid_leave_days,
        "absent_days": absent_days,
        "base_gross_salary": base_gross_salary,
        "base_fixed_deductions": base_fixed_deductions,
        "base_net_salary": base_net_salary,
        "proratable_gross": proratable_gross,
        "non_proratable_gross": non_proratable_gross,
        "daily_rate": daily_rate,
        "attendance_deduction": attendance_deduction,
        "earned_gross": earned_gross,
        "additional_earnings": additional_earnings,
        "additional_deductions": additional_deductions,
        "total_deductions": total_deductions,
        "net_payable": net_payable,
        "salary_components_snapshot": components_snapshot,
        "attendance_summary_snapshot": attendance_summary_snapshot,
        "calculation_breakdown": calculation_breakdown,
        "status": status,
        "notes": notes,
    }


def detect_stale_payroll_sources(payroll_period):
    """
    Checks if source attendance revision or employee salary revisions have changed
    since this payroll period was calculated.
    Returns: (is_stale: bool, reasons: list[str])
    """
    if not payroll_period or payroll_period.status == 'Draft':
        return False, []

    reasons = []
    att_period = payroll_period.attendance_period
    if att_period and att_period.current_revision != payroll_period.attendance_revision:
        reasons.append(
            f"Attendance revision changed from Rev {payroll_period.attendance_revision} to Rev {att_period.current_revision}."
        )

    # Check if any employee salary structures have been updated/created after calculation
    target_date = datetime_date(payroll_period.year, payroll_period.month, 1)
    if payroll_period.calculated_at:
        new_salaries = EmployeeSalaryStructure.objects.filter(
            organization=payroll_period.organization,
            effective_from__lte=target_date,
            created_at__gt=payroll_period.calculated_at
        ).exists()
        if new_salaries:
            reasons.append("Salary structures were modified or added after payroll calculation.")

    return bool(reasons), reasons


def calculate_payroll_period(organization, year, month, user):
    """
    Calculates or recalculates monthly payroll for an organization.
    Prerequisites:
    - AttendancePeriod for (organization, year, month) must be Finalized.
    - Consumes AttendancePeriodEmployeeSnapshot.is_current=True.
    - Resolves active salary structure for each employee as of the 1st of the month.
    """
    att_period = AttendancePeriod.objects.filter(
        organization=organization,
        year=year,
        month=month,
        is_deleted=False
    ).first()

    if not att_period or att_period.status != 'Finalized':
        raise ValidationError({
            "detail": f"Cannot calculate payroll: Attendance period {year}-{month:02d} is not Finalized."
        })

    with transaction.atomic():
        period, created = PayrollPeriod.objects.select_for_update().get_or_create(
            organization=organization,
            year=year,
            month=month,
            defaults={
                'attendance_period': att_period,
                'attendance_revision': att_period.current_revision,
                'currency': organization.settings.payroll_currency if hasattr(organization, 'settings') and organization.settings and organization.settings.payroll_currency else 'INR',
                'proration_basis': getattr(organization.settings, 'payroll_proration_basis', 'WORKING_DAYS') if hasattr(organization, 'settings') and organization.settings else 'WORKING_DAYS',
                'status': 'Draft',
                'current_revision': 0,
            }
        )

        if period.status == 'Finalized':
            raise ValidationError({
                "detail": f"Payroll period {year}-{month:02d} is Finalized and cannot be recalculated. Please reopen it first."
            })

        period.attendance_period = att_period
        period.attendance_revision = att_period.current_revision
        if hasattr(organization, 'settings') and organization.settings:
            period.currency = organization.settings.payroll_currency
            period.proration_basis = getattr(organization.settings, 'payroll_proration_basis', 'WORKING_DAYS')

        target_date = datetime_date(year, month, 1)

        # 1. Fetch all current finalized attendance snapshots for this period
        att_snapshots = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=att_period,
            revision=att_period.current_revision,
            is_current=True
        ).select_related('employee')

        # 2. Fetch all existing adjustments for this payroll period
        adjustments_by_emp = {}
        for adj in PayrollAdjustment.objects.filter(payroll_period=period, is_deleted=False):
            adjustments_by_emp.setdefault(adj.employee_id, []).append(adj)

        # Determine revision numbering:
        # If period.current_revision == 0 (first calculation), revision = 1
        # If period was reopened, new calculation uses revision = period.current_revision + 1
        calc_revision = 1 if period.current_revision == 0 else (period.current_revision + 1)

        # Mark any previous snapshots as not current
        PayrollEmployeeSnapshot.objects.filter(
            payroll_period=period,
            is_current=True
        ).update(is_current=False)

        calculated_snapshots = []
        total_base_gross = Decimal('0.00')
        total_attendance_deductions = Decimal('0.00')
        total_earned_gross = Decimal('0.00')
        total_base_deductions = Decimal('0.00')
        total_adjustments_net = Decimal('0.00')
        total_net_payable = Decimal('0.00')

        # 3. Bulk-fetch all active salary structures for organization as of target_date (1 query)
        salary_structures_map = get_bulk_employee_salary_structures(organization, target_date)

        for att_snap in att_snapshots:
            emp = att_snap.employee
            salary_struct = salary_structures_map.get(emp.id) or get_employee_salary_structure(emp, organization, target_date)
            emp_adjustments = adjustments_by_emp.get(emp.id, [])

            calc_res = calculate_employee_payroll(
                employee=emp,
                attendance_snapshot=att_snap,
                salary_structure=salary_struct,
                adjustments=emp_adjustments,
                proration_basis=period.proration_basis,
                organization=organization,
            )

            snap_obj = PayrollEmployeeSnapshot(
                payroll_period=period,
                revision=calc_revision,
                is_current=True,
                employee=emp,
                employee_name=calc_res["employee_name"],
                designation=calc_res["designation"],
                salary_structure=calc_res["salary_structure"],
                salary_effective_from=calc_res["salary_effective_from"],
                compensation_type=calc_res.get("compensation_type", "MONTHLY"),
                hourly_rate=calc_res.get("hourly_rate"),
                currency=calc_res["currency"],
                working_days=calc_res["working_days"],
                payable_attendance_units=calc_res["payable_attendance_units"],
                payable_hours=calc_res.get("payable_hours", Decimal('0.00')),
                paid_leave_days=calc_res["paid_leave_days"],
                unpaid_leave_days=calc_res["unpaid_leave_days"],
                absent_days=calc_res["absent_days"],
                base_gross_salary=calc_res["base_gross_salary"],
                base_fixed_deductions=calc_res["base_fixed_deductions"],
                base_net_salary=calc_res["base_net_salary"],
                proratable_gross=calc_res["proratable_gross"],
                non_proratable_gross=calc_res["non_proratable_gross"],
                daily_rate=calc_res["daily_rate"],
                attendance_deduction=calc_res["attendance_deduction"],
                earned_gross=calc_res["earned_gross"],
                additional_earnings=calc_res["additional_earnings"],
                additional_deductions=calc_res["additional_deductions"],
                total_deductions=calc_res["total_deductions"],
                net_payable=calc_res["net_payable"],
                salary_components_snapshot=calc_res["salary_components_snapshot"],
                attendance_summary_snapshot=calc_res["attendance_summary_snapshot"],
                calculation_breakdown=calc_res["calculation_breakdown"],
                status=calc_res["status"],
                notes=calc_res["notes"],
            )
            calculated_snapshots.append(snap_obj)

            total_base_gross += calc_res["base_gross_salary"]
            total_attendance_deductions += calc_res["attendance_deduction"]
            total_earned_gross += calc_res["earned_gross"]
            total_base_deductions += calc_res["base_fixed_deductions"]
            total_adjustments_net += (calc_res["additional_earnings"] - calc_res["additional_deductions"])
            total_net_payable += calc_res["net_payable"]

        PayrollEmployeeSnapshot.objects.bulk_create(calculated_snapshots)

        period.current_revision = calc_revision
        period.status = 'Calculated'
        period.total_employees = len(calculated_snapshots)
        period.total_base_gross = total_base_gross
        period.total_attendance_deductions = total_attendance_deductions
        period.total_earned_gross = total_earned_gross
        period.total_base_deductions = total_base_deductions
        period.total_adjustments_net = total_adjustments_net
        period.total_net_payable = total_net_payable
        period.calculated_at = timezone.now()
        period.calculated_by = user
        period.save()

        # AuditLog
        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Payroll Calculated",
            details=f"Calculated payroll period {year}-{month:02d} (Revision {calc_revision}) for {len(calculated_snapshots)} employees."
        )

    return period


def generate_payslip_number(organization_id, year, month, employee_id, revision=1):
    """
    Deterministic format: PAY-YYYYMM-ORGID-EMPID[-R{revision}]
    Example:
    PAY-202608-001-0042 (Rev 1)
    PAY-202608-001-0042-R2 (Rev 2)
    """
    base = f"PAY-{year:04d}{month:02d}-{int(organization_id):03d}-{int(employee_id):04d}"
    if revision > 1:
        return f"{base}-R{revision}"
    return base


def issue_period_payslips(payroll_period, issued_by=None):
    """
    Generates and issues immutable Payslip records for all eligible employees
    in a finalized PayrollPeriod.
    Prerequisites:
    - PayrollPeriod.status == 'Finalized'
    - Consumes PayrollEmployeeSnapshot.is_current == True and status == 'OK'
    - Idempotent: Does not create duplicate payslips if run multiple times.
    - Freezes corporate branding and employee demographic details.
    - Emits AuditLog without exposing salary figures.
    """
    if payroll_period.status != 'Finalized':
        raise ValidationError({
            "detail": f"Cannot issue payslips: Payroll period {payroll_period.year}-{payroll_period.month:02d} is not Finalized."
        })

    org = payroll_period.organization
    org_settings = getattr(org, 'settings', None)

    # Freeze Corporate Snapshot
    company_name = org.name
    company_details = {
        "address": getattr(org_settings, 'company_address', '') or '',
        "phone": getattr(org_settings, 'company_phone', '') or '',
        "email": getattr(org_settings, 'company_email', '') or '',
        "tax_id": getattr(org_settings, 'company_tax_id', '') or '',
        "brandLogo": getattr(org_settings, 'brandLogo', '') or '',
    }

    # Fetch eligible snapshots for current revision
    snapshots = PayrollEmployeeSnapshot.objects.filter(
        payroll_period=payroll_period,
        revision=payroll_period.current_revision,
        is_current=True,
        status='OK'
    ).select_related('employee')

    issued_payslips = []
    with transaction.atomic():
        for snap in snapshots:
            emp = snap.employee
            emp_details = {
                "name": f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                "employee_code": getattr(emp, 'employee_code', None) or f"EMP-{emp.id:04d}",
                "designation": getattr(emp, 'designation', '') or snap.designation or '',
                "department": getattr(emp, 'department', '') or '',
                "joining_date": str(emp.joining_date) if getattr(emp, 'joining_date', None) else None,
            }

            payslip_num = generate_payslip_number(
                organization_id=org.id,
                year=payroll_period.year,
                month=payroll_period.month,
                employee_id=emp.id,
                revision=payroll_period.current_revision
            )

            payslip, created = Payslip.objects.get_or_create(
                organization=org,
                payroll_period=payroll_period,
                employee=emp,
                revision=payroll_period.current_revision,
                defaults={
                    'payroll_snapshot': snap,
                    'payslip_number': payslip_num,
                    'status': 'Issued',
                    'company_name_snapshot': company_name,
                    'company_details_snapshot': company_details,
                    'employee_details_snapshot': emp_details,
                    'issued_by': issued_by,
                }
            )
            if created:
                issued_payslips.append(payslip)

        if issued_payslips:
            actor_name = f"{issued_by.first_name} {issued_by.last_name}".strip() or issued_by.email if issued_by else "System"
            AuditLog.objects.create(
                organization=org,
                employee=issued_by,
                employeeName=actor_name,
                action="Payslips Issued",
                details=f"Issued {len(issued_payslips)} payslips for finalized period {payroll_period.year}-{payroll_period.month:02d} (Rev {payroll_period.current_revision})."
            )

    return issued_payslips


def finalize_payroll_period(organization, year, month, user):
    """
    Finalizes and locks a calculated PayrollPeriod.
    Checks:
    - Period must be in 'Calculated' status.
    - No employees with NegativeNet or MissingSalaryStructure.
    - Attendance revision has not changed since calculation.
    - Creates AuditLog, locks the period, and automatically issues payslips.
    """
    with transaction.atomic():
        period = PayrollPeriod.objects.select_for_update().filter(
            organization=organization,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period or period.status != 'Calculated':
            raise ValidationError({
                "detail": f"Payroll period {year}-{month:02d} must be Calculated before it can be Finalized."
            })

        # Check for stale source
        is_stale, reasons = detect_stale_payroll_sources(period)
        if is_stale:
            raise ValidationError({
                "detail": f"Payroll calculation is stale: {'; '.join(reasons)} Please recalculate payroll before finalizing."
            })

        # Check for blocking employee statuses
        problem_snaps = PayrollEmployeeSnapshot.objects.filter(
            payroll_period=period,
            revision=period.current_revision,
            is_current=True,
            status__in=['NegativeNet', 'MissingSalaryStructure']
        )
        if problem_snaps.exists():
            issues_summary = [f"{s.employee_name}: {s.get_status_display()}" for s in problem_snaps[:5]]
            raise ValidationError({
                "detail": f"Cannot finalize payroll: unresolved employee issues found: {', '.join(issues_summary)}. Please resolve all issues before finalizing."
            })

        period.status = 'Finalized'
        period.finalized_at = timezone.now()
        period.finalized_by = user
        period.save()

        # Automatic Payslip Issuance
        issue_period_payslips(period, issued_by=user)

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Payroll Finalized",
            details=f"Finalized payroll period {year}-{month:02d} (Revision {period.current_revision}) for {period.total_employees} employees."
        )

    return period


def reopen_payroll_period(organization, year, month, user, reason):
    """
    Reopens a Finalized PayrollPeriod.
    Requires:
    - User has payroll:manage permission.
    - Reason string >= 5 characters.
    - Sets status to 'Draft', marks previously issued payslips as 'Superseded',
      creates AuditLog, preserves snapshot rows for history.
    """
    if not reason or not str(reason).strip() or len(str(reason).strip()) < 5:
        raise ValidationError({
            "reason": "A valid reason (minimum 5 characters) is required to reopen a finalized payroll period."
        })

    with transaction.atomic():
        period = PayrollPeriod.objects.select_for_update().filter(
            organization=organization,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period or period.status != 'Finalized':
            raise ValidationError({
                "detail": f"Payroll period {year}-{month:02d} is not Finalized and cannot be reopened."
            })

        # Check if active salary payments exist for this period
        from payroll.models import SalaryPayment
        active_payments_count = SalaryPayment.objects.filter(
            payroll_period=period,
            status='Paid',
            is_deleted=False
        ).count()
        if active_payments_count > 0:
            raise ValidationError({
                "detail": "This payroll contains recorded salary payments. Void the payments before reopening payroll."
            })

        period.status = 'Draft'
        period.reopened_at = timezone.now()
        period.reopened_by = user
        period.reopen_reason = str(reason).strip()
        period.save()

        # Mark all issued payslips for this period as Superseded
        superseded_count = Payslip.objects.filter(
            payroll_period=period,
            status='Issued'
        ).update(status='Superseded')

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Payroll Period Reopened",
            details=f"Reopened payroll period {year}-{month:02d} (Rev {period.current_revision}). {superseded_count} payslips marked Superseded. Reason: {str(reason).strip()}"
        )

    return period


def record_salary_payment(organization, snapshot_id, paid_at, payment_method, transaction_reference, notes, user):
    """
    Records salary payment for a single finalized employee snapshot.
    Validations:
    - PayrollPeriod status MUST be 'Finalized'.
    - Snapshot must belong to organization.
    - Snapshot cannot already have an active 'Paid' record.
    - paid_at required, cannot be in future.
    - paid_amount is strictly derived from snapshot.net_payable.
    """
    import datetime
    if not paid_at:
        raise ValidationError({"paid_at": "Payment date is required."})

    paid_date = paid_at if isinstance(paid_at, datetime.date) else (datetime.datetime.strptime(str(paid_at), "%Y-%m-%d").date() if isinstance(paid_at, str) else paid_at)
    today = timezone.now().date()
    if paid_date > today:
        raise ValidationError({"paid_at": "Payment date cannot be in the future."})

    from payroll.models import SalaryPayment, PayrollEmployeeSnapshot

    with transaction.atomic():
        snapshot = PayrollEmployeeSnapshot.objects.select_for_update().filter(
            payroll_period__organization=organization,
            id=snapshot_id,
            is_deleted=False
        ).select_related('payroll_period', 'employee').first()

        if not snapshot:
            raise ValidationError({"detail": "Payroll employee snapshot not found."})

        period = snapshot.payroll_period
        if period.status != 'Finalized':
            raise ValidationError({"detail": "Salary payment can only be recorded for Finalized payroll periods."})

        # Check existing active Paid record
        if SalaryPayment.objects.filter(payroll_snapshot=snapshot, status='Paid', is_deleted=False).exists():
            raise ValidationError({"detail": f"Salary for {snapshot.employee_name} has already been marked as Paid."})

        valid_methods = [c[0] for c in SalaryPayment.PAYMENT_METHOD_CHOICES]
        method = str(payment_method).strip() if payment_method else 'BankTransfer'
        if method not in valid_methods:
            method = 'BankTransfer'

        payment = SalaryPayment.objects.create(
            organization=organization,
            payroll_period=period,
            payroll_snapshot=snapshot,
            employee=snapshot.employee,
            paid_amount=snapshot.net_payable,
            paid_at=paid_date,
            payment_method=method,
            transaction_reference=str(transaction_reference or '').strip(),
            notes=str(notes or '').strip(),
            status='Paid',
            recorded_by=user
        )

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        ref_str = f" (Ref: {payment.transaction_reference})" if payment.transaction_reference else ""
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Salary Payment Recorded",
            details=f"Marked salary of {snapshot.currency} {snapshot.net_payable} as Paid for {snapshot.employee_name} ({period.year}-{period.month:02d}) via {payment.get_payment_method_display()}{ref_str}."
        )

    return payment


def bulk_record_salary_payments(organization, year, month, snapshot_ids, paid_at, payment_method, transaction_reference, notes, user):
    """
    Bulk records salary payments for multiple finalized employee snapshots in a single atomic transaction.
    """
    import datetime
    if not paid_at:
        raise ValidationError({"paid_at": "Payment date is required."})

    paid_date = paid_at if isinstance(paid_at, datetime.date) else (datetime.datetime.strptime(str(paid_at), "%Y-%m-%d").date() if isinstance(paid_at, str) else paid_at)
    today = timezone.now().date()
    if paid_date > today:
        raise ValidationError({"paid_at": "Payment date cannot be in the future."})

    from payroll.models import SalaryPayment, PayrollEmployeeSnapshot, PayrollPeriod

    with transaction.atomic():
        period = PayrollPeriod.objects.filter(
            organization=organization,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period or period.status != 'Finalized':
            raise ValidationError({"detail": f"Payroll period {year}-{month:02d} must be Finalized before recording payments."})

        snaps_qs = PayrollEmployeeSnapshot.objects.select_for_update().filter(
            payroll_period=period,
            revision=period.current_revision,
            is_current=True,
            is_deleted=False
        )
        if snapshot_ids and len(snapshot_ids) > 0:
            snaps_qs = snaps_qs.filter(id__in=snapshot_ids)

        snapshots = list(snaps_qs)
        if not snapshots:
            raise ValidationError({"detail": "No eligible employee snapshots found for payment."})

        existing_paid_snapshot_ids = set(
            SalaryPayment.objects.filter(
                payroll_period=period,
                status='Paid',
                is_deleted=False
            ).values_list('payroll_snapshot_id', flat=True)
        )

        to_pay = [s for s in snapshots if s.id not in existing_paid_snapshot_ids]
        if not to_pay:
            raise ValidationError({"detail": "All selected employees are already marked as Paid."})

        valid_methods = [c[0] for c in SalaryPayment.PAYMENT_METHOD_CHOICES]
        method = str(payment_method).strip() if payment_method else 'BankTransfer'
        if method not in valid_methods:
            method = 'BankTransfer'

        payments_to_create = []
        total_amount = Decimal('0.00')
        for s in to_pay:
            total_amount += s.net_payable
            payments_to_create.append(SalaryPayment(
                organization=organization,
                payroll_period=period,
                payroll_snapshot=s,
                employee=s.employee,
                paid_amount=s.net_payable,
                paid_at=paid_date,
                payment_method=method,
                transaction_reference=str(transaction_reference or '').strip(),
                notes=str(notes or '').strip(),
                status='Paid',
                recorded_by=user
            ))

        SalaryPayment.objects.bulk_create(payments_to_create)

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        ref_str = f" (Ref: {transaction_reference})" if transaction_reference else ""
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Bulk Salary Payment Recorded",
            details=f"Bulk recorded salary payment for {len(to_pay)} employees for period {year}-{month:02d}. Total Paid: {period.currency} {total_amount}{ref_str}."
        )

    return len(to_pay)


def void_salary_payment(organization, payment_id, void_reason, user):
    """
    Voids an existing active Paid salary payment.
    """
    if not void_reason or not str(void_reason).strip() or len(str(void_reason).strip()) < 5:
        raise ValidationError({"void_reason": "A valid reason (minimum 5 characters) is required to void a salary payment."})

    from payroll.models import SalaryPayment

    with transaction.atomic():
        payment = SalaryPayment.objects.select_for_update().filter(
            organization=organization,
            id=payment_id,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee').first()

        if not payment or payment.status != 'Paid':
            raise ValidationError({"detail": "Active salary payment record not found or already voided."})

        payment.status = 'Voided'
        payment.voided_at = timezone.now()
        payment.voided_by = user
        payment.void_reason = str(void_reason).strip()
        payment.save()

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        emp_name = payment.employee.get_full_name() or payment.employee.email if payment.employee else "Employee"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Salary Payment Voided",
            details=f"Voided salary payment of {payment.paid_amount} for {emp_name} ({payment.payroll_period.year}-{payment.payroll_period.month:02d}). Reason: {payment.void_reason}"
        )

    return payment

