# --------------------------------------------------------------------------------
#       Attendance Services
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
import calendar
from datetime import date as datetime_date, timedelta
from decimal import Decimal, ROUND_HALF_UP

# DJANGO
from django.utils import timezone

# APPLICATION SPECIFIC
from core.models import AuditLog, OrgSettings
from attendance.models import AttendanceLog, AttendancePolicy, Schedule, Holiday, Leave, AttendancePeriod, AttendancePeriodEmployeeSnapshot


def get_nth_weekday_of_month(year, month, weekday_name, n):
    """
    Calculates the calendar date for the Nth weekday of a given month.
    (e.g., 2nd Saturday, Last Sunday).
    """
    WEEKDAYS = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    target_weekday = WEEKDAYS.get(str(weekday_name).lower())
    if target_weekday is None:
        return None

    cal = calendar.Calendar()
    try:
        month_days = [d for d in cal.itermonthdates(year, month) if d.month == month]
    except Exception:
        return None
    matching_dates = [d for d in month_days if d.weekday() == target_weekday]

    if not matching_dates:
        return None

    if n == -1 or str(n).lower() == 'last':
        return matching_dates[-1]

    try:
        idx = int(n) - 1
        if 0 <= idx < len(matching_dates):
            return matching_dates[idx]
    except (ValueError, TypeError):
        pass
    return None


UNSET = object()


def is_holiday_or_weekly_off(organization, target_date, policy=None, holiday_record=UNSET):
    """
    Checks if target_date is a weekly off or holiday for the organization.
    Returns: (is_weekly_off: bool, is_holiday: bool, holiday_name: str | None)
    """
    if not organization:
        return False, False, None

    if policy is None:
        policy = get_attendance_policy(organization, target_date)

    day_name = target_date.strftime('%A')
    weekly_offs = policy.default_weekly_holidays if policy and policy.default_weekly_holidays else ["Saturday", "Sunday"]
    is_weekly_off = day_name in weekly_offs

    # 1. Check explicit Holiday records
    if holiday_record is UNSET and organization:
        holiday_record = Holiday.objects.filter(
            organization=organization,
            date=target_date,
            is_deleted=False
        ).first()

    if holiday_record:
        return is_weekly_off, True, holiday_record.name

    # 2. Check recurring holiday rules on OrgSettings
    org_settings = getattr(organization, 'settings', None)
    if org_settings:
        # Yearly recurring rules
        yearly_rules = getattr(org_settings, 'yearly_recurring_holidays', []) or []
        for rule in yearly_rules:
            if rule.get('month') == target_date.month and rule.get('day') == target_date.day:
                return is_weekly_off, True, rule.get('name', 'Yearly Recurring Holiday')

        # Monthly recurring rules
        monthly_rules = getattr(org_settings, 'monthly_recurring_holidays', []) or []
        for rule in monthly_rules:
            week_num = rule.get('week_number')
            r_day_name = rule.get('day')
            if week_num is not None and r_day_name:
                d = get_nth_weekday_of_month(target_date.year, target_date.month, r_day_name, week_num)
                if d == target_date:
                    return is_weekly_off, True, f"{r_day_name} recurring holiday"

    return is_weekly_off, False, None


def calculate_punctuality(first_clock_in, shift_start_str, grace_period_minutes):
    """
    Calculates if the first clock-in of the day was late based on shift start + grace period.
    Returns: (is_late: bool, minutes_late: int)
    """
    if not first_clock_in or not shift_start_str:
        return False, 0

    try:
        shift_h, shift_m = map(int, shift_start_str.split(':'))
        local_in = timezone.localtime(first_clock_in) if timezone.is_aware(first_clock_in) else first_clock_in
        clock_in_minutes = local_in.hour * 60 + local_in.minute
        shift_start_minutes = shift_h * 60 + shift_m
        grace_limit_minutes = shift_start_minutes + grace_period_minutes

        if clock_in_minutes > grace_limit_minutes:
            minutes_late = clock_in_minutes - shift_start_minutes
            return True, minutes_late
        return False, 0
    except Exception:
        return False, 0


def get_attendance_policy(organization, date=None):
    """
    Returns the authoritative AttendancePolicy for a given organization and date.
    Resolves using: effective_from <= date, ordered by -effective_from.
    If no policy is found (e.g. date before initial effective_from or newly created org),
    falls back to the earliest existing policy, or lazily creates a baseline policy.
    """
    if not organization:
        return None

    if date is None:
        date = timezone.now().date()
    elif isinstance(date, str):
        date = datetime_date.fromisoformat(date)

    policy = AttendancePolicy.objects.filter(
        organization=organization,
        effective_from__lte=date
    ).order_by('-effective_from').first()

    if not policy:
        policy = AttendancePolicy.objects.filter(
            organization=organization
        ).order_by('effective_from').first()

    if not policy:
        org_settings = getattr(organization, 'settings', None)
        grace = getattr(org_settings, 'grace_period_minutes', 15) or 15
        half_day = getattr(org_settings, 'half_day_threshold_minutes', 240) or 240
        weekly_offs = getattr(org_settings, 'default_weekly_holidays', None) or ["Saturday", "Sunday"]
        auto_approve = getattr(org_settings, 'auto_approve_attendance', False) or False

        policy = AttendancePolicy.objects.create(
            organization=organization,
            effective_from=date,
            grace_period_minutes=grace,
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=half_day,
            minimum_session_minutes=5,
            break_duration_minutes=60,
            break_type='Unpaid',
            default_weekly_holidays=weekly_offs,
            auto_approve_attendance=auto_approve,
        )

    return policy


def save_attendance_policy(organization, policy_data, save_date=None):
    """
    Future-safe policy update rule:
    When an admin saves policy changes on date D, the new rules take effect from tomorrow (D + 1).
    - If tomorrow's policy already exists: updates that future row in place.
    - Otherwise: creates a new AttendancePolicy row with effective_from = tomorrow.
    - All past / active policies (effective_from <= D) remain immutable.
    - Synchronizes legacy OrgSettings fields as cache for backwards compatibility.
    """
    if not organization:
        raise ValueError("Organization is required to save an AttendancePolicy.")

    if save_date is None:
        save_date = timezone.now().date()

    target_effective_date = save_date + timedelta(days=1)

    # Clean & normalize fields
    try:
        grace_period = int(policy_data.get('grace_period_minutes', 15))
    except (ValueError, TypeError):
        raise ValueError("Grace period minutes must be a valid whole number.")

    try:
        min_session = int(policy_data.get('minimum_session_minutes', 5))
    except (ValueError, TypeError):
        raise ValueError("Minimum session minutes must be a valid whole number.")

    try:
        full_day_min = int(policy_data.get('full_day_minimum_minutes', 480))
        half_day_min = int(policy_data.get('half_day_minimum_minutes', policy_data.get('half_day_threshold_minutes', 240)))
    except (ValueError, TypeError):
        raise ValueError("Work duration minimum minutes must be valid whole numbers.")

    if grace_period < 0 or grace_period > 180:
        raise ValueError("Grace period minutes must be between 0 and 180.")

    if min_session < 0 or min_session > 180:
        raise ValueError("Minimum session duration minutes must be between 0 and 180.")

    if full_day_min < 1 or full_day_min > 1440:
        raise ValueError("Full day minimum minutes must be between 1 and 1440.")

    if half_day_min < 1 or half_day_min > 1440:
        raise ValueError("Half day minimum minutes must be between 1 and 1440.")

    break_duration = int(policy_data.get('break_duration_minutes', 60))
    break_type = policy_data.get('break_type', 'Unpaid')
    if break_type not in ['Paid', 'Unpaid']:
        break_type = 'Unpaid'
    weekly_offs = policy_data.get('default_weekly_holidays', ["Saturday", "Sunday"])
    if not isinstance(weekly_offs, list):
        weekly_offs = ["Saturday", "Sunday"]
    auto_approve = bool(policy_data.get('auto_approve_attendance', False))

    policy, created = AttendancePolicy.objects.get_or_create(
        organization=organization,
        effective_from=target_effective_date,
        defaults={
            'grace_period_minutes': grace_period,
            'full_day_minimum_minutes': full_day_min,
            'half_day_minimum_minutes': half_day_min,
            'minimum_session_minutes': min_session,
            'break_duration_minutes': break_duration,
            'break_type': break_type,
            'default_weekly_holidays': weekly_offs,
            'auto_approve_attendance': auto_approve,
        }
    )

    if not created:
        policy.grace_period_minutes = grace_period
        policy.full_day_minimum_minutes = full_day_min
        policy.half_day_minimum_minutes = half_day_min
        policy.minimum_session_minutes = min_session
        policy.break_duration_minutes = break_duration
        policy.break_type = break_type
        policy.default_weekly_holidays = weekly_offs
        policy.auto_approve_attendance = auto_approve
        policy.save()

    # Synchronize legacy OrgSettings as compatibility cache
    if hasattr(organization, 'settings') and organization.settings:
        org_settings = organization.settings
        org_settings.grace_period_minutes = grace_period
        org_settings.half_day_threshold_minutes = half_day_min
        org_settings.default_weekly_holidays = weekly_offs
        org_settings.auto_approve_attendance = auto_approve
        org_settings.save(update_fields=[
            'grace_period_minutes',
            'half_day_threshold_minutes',
            'default_weekly_holidays',
            'auto_approve_attendance'
        ])

    return policy, created


def get_daily_attendance_summary(employee, target_date, policy=None, schedule=UNSET, month_context=None):
    """
    Phase 2 Core Daily Attendance Engine:
    Evaluates all raw logs, active versioned policy, tenant schedule,
    approved leaves, and holidays for (employee, date) and produces ONE
    authoritative daily attendance summary.
    """
    if isinstance(target_date, str):
        target_date = datetime_date.fromisoformat(target_date)

    today = timezone.localdate() if hasattr(timezone, 'localdate') else timezone.now().date()
    is_today = (target_date == today)
    is_past = (target_date < today)
    is_future = (target_date > today)
    org = employee.organization

    # 1. Resolve Policy & Thresholds for this date
    if policy is None:
        policy = get_attendance_policy(org, target_date)
    policy_eff = policy.effective_from.isoformat() if policy and policy.effective_from else target_date.isoformat()
    grace_period = policy.grace_period_minutes if policy else 15
    full_day_min = policy.full_day_minimum_minutes if policy else 480
    half_day_min = policy.half_day_minimum_minutes if policy else 240
    min_session = policy.minimum_session_minutes if policy else 5

    # 2. Resolve Schedule for Employee Designation
    if schedule is UNSET:
        schedule = Schedule.objects.filter(
            organization=org,
            designation=employee.designation,
            is_deleted=False
        ).first() if (org and getattr(employee, 'designation', None)) else None

    shift_start = schedule.shiftStart if schedule else "09:00"
    shift_end = schedule.shiftEnd if schedule else "17:00"

    try:
        sh_h, sh_m = map(int, shift_start.split(':'))
        eh_h, eh_m = map(int, shift_end.split(':'))
        scheduled_minutes = (eh_h * 60 + eh_m) - (sh_h * 60 + sh_m)
        if scheduled_minutes < 0:
            scheduled_minutes += 24 * 60
    except Exception:
        scheduled_minutes = 480

    # 2.5 Check Employment Window (Pre-joining or Post-termination)
    joining_date = getattr(employee, 'joining_date', None)
    last_working_date = getattr(employee, 'last_working_date', None)
    is_pre_joining = bool(joining_date and target_date < joining_date)
    is_post_termination = bool(last_working_date and target_date > last_working_date)

    if is_pre_joining or is_post_termination:
        return {
            "employee_id": employee.id,
            "employee_name": f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            "date": target_date.isoformat(),
            "day_of_week": target_date.strftime("%A"),

            "shift_start": shift_start,
            "shift_end": shift_end,
            "scheduled_minutes": scheduled_minutes,

            "policy_effective_from": policy_eff,
            "grace_period_minutes": grace_period,
            "full_day_minimum_minutes": full_day_min,
            "half_day_minimum_minutes": half_day_min,
            "minimum_session_minutes": min_session,

            "first_clock_in": None,
            "last_clock_out": None,
            "session_count": 0,
            "completed_session_count": 0,
            "worked_minutes": 0,
            "raw_statuses": [],
            "is_open_session": False,

            "is_late": False,
            "minutes_late": 0,

            "is_working_day": False,
            "is_weekly_off": False,
            "is_holiday": False,
            "holiday_name": None,
            "worked_on_off_day": False,

            "leave_type": None,
            "leave_fraction": 0.0,
            "leave_is_paid": True,
            "paid_leave_unit": 0.0,
            "unpaid_leave_unit": 0.0,

            "attendance_unit": 0.0,
            "leave_unit": 0.0,
            "payable_attendance_unit": 0.0,

            "daily_status": "Not Employed",
            "is_payroll_ready": True,
            "has_pending_approval": False,
            "requires_admin_resolution": False,
            "has_conflict": False,
            "conflict_reason": None,
        }

    # 3. Calendar Modifiers (Weekly Off & Holiday)
    holiday_rec = UNSET
    if month_context and 'holidays_map' in month_context:
        holiday_rec = month_context['holidays_map'].get(target_date)

    is_weekly_off, is_holiday, holiday_name = is_holiday_or_weekly_off(
        org, target_date, policy, holiday_record=holiday_rec
    )

    # 4. Check Approved Leave
    if month_context and 'leaves_list' in month_context:
        leave = next((l for l in month_context['leaves_list'] if l.startDate <= target_date <= l.endDate), None)
    else:
        leave = Leave.objects.filter(
            employee=employee,
            startDate__lte=target_date,
            endDate__gte=target_date,
            status='Approved',
            is_deleted=False
        ).first()

    leave_type = None
    leave_fraction = 0.0
    leave_is_paid = True
    if leave:
        leave_type = leave.leaveTypeName or (leave.leaveType.name if leave.leaveType else 'Leave')
        leave_is_paid = getattr(leave.leaveType, 'is_paid', True) if leave.leaveType else True
        if str(leave.dayType).lower() in ['half', 'half day', 'first half', 'second half'] or leave.duration == 0.5:
            leave_fraction = 0.5
        else:
            leave_fraction = 1.0

    paid_leave_unit = leave_fraction if leave_is_paid else 0.0
    unpaid_leave_unit = leave_fraction if not leave_is_paid else 0.0

    # 5. Session Aggregation (Exclude soft-deleted logs)
    if month_context and 'logs_map' in month_context:
        logs = month_context['logs_map'].get(target_date, [])
        session_count = len(logs)
    else:
        logs = AttendanceLog.objects.filter(
            employee=employee,
            date=target_date,
            is_deleted=False
        ).order_by('clockIn', 'id')
        session_count = logs.count()
    completed_session_count = 0
    worked_minutes = 0
    is_open_session = False
    has_pending_approval = False
    raw_statuses = []
    valid_clock_ins = []
    valid_clock_outs = []

    for log in logs:
        raw_statuses.append(log.status)
        if log.status == 'Pending Approval':
            has_pending_approval = True

        if log.clockIn:
            valid_clock_ins.append(log.clockIn)

        if log.clockIn and log.clockOut:
            completed_session_count += 1
            valid_clock_outs.append(log.clockOut)
            duration_sec = (log.clockOut - log.clockIn).total_seconds()
            worked_minutes += max(0, int(duration_sec // 60))
        elif log.clockIn and log.clockOut is None:
            is_open_session = True

    first_clock_in_dt = min(valid_clock_ins) if valid_clock_ins else None
    last_clock_out_dt = max(valid_clock_outs) if valid_clock_outs else None

    first_clock_in = first_clock_in_dt.isoformat() if first_clock_in_dt else None
    last_clock_out = last_clock_out_dt.isoformat() if last_clock_out_dt else None

    # 6. Punctuality (Late)
    is_late, minutes_late = calculate_punctuality(first_clock_in_dt, shift_start, grace_period)

    # 7. Raw Attendance Threshold Calculation
    if worked_minutes >= full_day_min:
        attendance_unit = 1.0
    elif worked_minutes >= half_day_min:
        attendance_unit = 0.5
    else:
        attendance_unit = 0.0

    # 8. Business Rule Consolidation
    has_conflict = False
    conflict_reason = None
    requires_admin_resolution = False
    worked_on_off_day = False
    leave_unit = 0.0
    payable_attendance_unit = 0.0
    daily_status = "Absent"

    # CASE A: Incomplete past session
    if is_open_session and is_past:
        daily_status = "Incomplete"
        requires_admin_resolution = True
        is_payroll_ready = False
        attendance_unit = 0.0
        payable_attendance_unit = 0.0

    # CASE B: Open session today (In Progress)
    elif is_open_session and is_today:
        daily_status = "In Progress"
        payable_attendance_unit = attendance_unit
        is_payroll_ready = not has_pending_approval

    # CASE C: Full-Day Leave + Actual Attendance (Conflict)
    elif leave_fraction == 1.0 and (worked_minutes >= half_day_min or session_count > 0):
        has_conflict = True
        conflict_reason = "attendance_on_full_day_leave"
        requires_admin_resolution = True
        is_payroll_ready = False
        leave_unit = 1.0
        payable_attendance_unit = min(1.0, attendance_unit + paid_leave_unit)
        daily_status = "Present" if attendance_unit == 1.0 else ("Half Day" if attendance_unit == 0.5 else "Leave")

    # CASE D: Full-Day Leave without Attendance
    elif leave_fraction == 1.0:
        leave_unit = 1.0
        attendance_unit = 0.0
        payable_attendance_unit = paid_leave_unit
        daily_status = "Leave"
        is_payroll_ready = True

    # CASE E: Half-Day Leave
    elif leave_fraction == 0.5:
        leave_unit = 0.5
        payable_attendance_unit = min(1.0, attendance_unit + paid_leave_unit)
        if attendance_unit >= 0.5:
            daily_status = "Present"
        else:
            daily_status = "Half Day"
        is_payroll_ready = not has_pending_approval

    # CASE F: Off-Day (Weekly Off / Holiday)
    elif is_weekly_off or is_holiday:
        if worked_minutes > 0 or session_count > 0:
            worked_on_off_day = True
            payable_attendance_unit = attendance_unit
            daily_status = "Present" if attendance_unit == 1.0 else ("Half Day" if attendance_unit == 0.5 else ("Weekly Off" if is_weekly_off else "Holiday"))
        else:
            attendance_unit = 0.0
            leave_unit = 0.0
            payable_attendance_unit = 0.0
            daily_status = "Weekly Off" if is_weekly_off else "Holiday"
        is_payroll_ready = not has_pending_approval

    # CASE G: Normal Working Day
    else:
        payable_attendance_unit = attendance_unit
        if attendance_unit == 1.0:
            daily_status = "Present"
            is_payroll_ready = not has_pending_approval
        elif attendance_unit == 0.5:
            daily_status = "Half Day"
            is_payroll_ready = not has_pending_approval
        elif is_future:
            daily_status = "Upcoming"
            is_payroll_ready = False
        elif is_today and session_count == 0:
            daily_status = "Not Started"
            is_payroll_ready = False
        else:
            daily_status = "Absent"
            is_payroll_ready = not has_pending_approval

    # Final readiness checks
    if has_pending_approval or requires_admin_resolution or has_conflict:
        is_payroll_ready = False

    return {
        "employee_id": employee.id,
        "employee_name": f"{employee.first_name} {employee.last_name}".strip() or employee.email,
        "date": target_date.isoformat(),
        "day_of_week": target_date.strftime("%A"),

        "shift_start": shift_start,
        "shift_end": shift_end,
        "scheduled_minutes": scheduled_minutes,

        "policy_effective_from": policy_eff,
        "grace_period_minutes": grace_period,
        "full_day_minimum_minutes": full_day_min,
        "half_day_minimum_minutes": half_day_min,
        "minimum_session_minutes": min_session,

        "session_count": session_count,
        "completed_session_count": completed_session_count,
        "first_clock_in": first_clock_in,
        "last_clock_out": last_clock_out,
        "worked_minutes": worked_minutes,
        "is_open_session": is_open_session,

        "is_late": is_late,
        "minutes_late": minutes_late,

        "is_weekly_off": is_weekly_off,
        "is_holiday": is_holiday,
        "is_working_day": not (is_weekly_off or is_holiday),
        "holiday_name": holiday_name,
        "worked_on_off_day": worked_on_off_day,

        "leave_type": leave_type,
        "leave_fraction": leave_fraction,
        "leave_is_paid": leave_is_paid,
        "paid_leave_unit": paid_leave_unit,
        "unpaid_leave_unit": unpaid_leave_unit,

        "attendance_unit": attendance_unit,
        "leave_unit": leave_unit,
        "payable_attendance_unit": payable_attendance_unit,

        "daily_status": daily_status,

        "has_pending_approval": has_pending_approval,
        "has_conflict": has_conflict,
        "conflict_reason": conflict_reason,
        "requires_admin_resolution": requires_admin_resolution,
        "is_payroll_ready": is_payroll_ready,

        "raw_statuses": raw_statuses,
    }


def get_monthly_attendance_summary(employee, year, month):
    """
    Iterates through all calendar days of the month and returns a list of daily summary dicts.
    Optimized to pre-fetch month-level attendance data (logs, leaves, holidays, policy, schedule)
    in bulk to eliminate N+1 queries while maintaining 100% authoritative calculation precision.
    """
    _, num_days = calendar.monthrange(year, month)
    summaries = []
    org = employee.organization
    start_date = datetime_date(year, month, 1)
    end_date = datetime_date(year, month, num_days)

    schedule = Schedule.objects.filter(
        organization=org,
        designation=employee.designation,
        is_deleted=False
    ).first() if (org and getattr(employee, 'designation', None)) else None

    policy = get_attendance_policy(org, start_date) if org else None

    # Bulk pre-fetch holidays for the month
    holidays_map = {}
    if org:
        h_qs = Holiday.objects.filter(
            organization=org,
            date__gte=start_date,
            date__lte=end_date,
            is_deleted=False
        )
        for h in h_qs:
            holidays_map[h.date] = h

    # Bulk pre-fetch approved leaves for the employee overlapping the month
    leaves_list = list(
        Leave.objects.filter(
            employee=employee,
            startDate__lte=end_date,
            endDate__gte=start_date,
            status='Approved',
            is_deleted=False
        ).select_related('leaveType')
    ) if employee else []

    # Bulk pre-fetch raw attendance logs for the employee for the month
    logs_map = {}
    if employee:
        logs_qs = AttendanceLog.objects.filter(
            employee=employee,
            date__gte=start_date,
            date__lte=end_date,
            is_deleted=False
        ).order_by('clockIn', 'id')
        for l in logs_qs:
            logs_map.setdefault(l.date, []).append(l)

    month_context = {
        'holidays_map': holidays_map,
        'leaves_list': leaves_list,
        'logs_map': logs_map
    }

    for day in range(1, num_days + 1):
        target_date = datetime_date(year, month, day)
        summaries.append(
            get_daily_attendance_summary(
                employee,
                target_date,
                policy=policy,
                schedule=schedule,
                month_context=month_context
            )
        )
    return summaries


def is_date_locked(organization, target_date):
    """
    Checks if a target date falls within a finalized AttendancePeriod for the organization.
    """
    if not organization or not target_date:
        return False
    return AttendancePeriod.objects.filter(
        organization=organization,
        year=target_date.year,
        month=target_date.month,
        status='Finalized',
        is_deleted=False
    ).exists()


def is_date_range_locked(organization, start_date, end_date):
    """
    Checks if any date within [start_date, end_date] falls in a finalized AttendancePeriod.
    """
    if not organization or not start_date or not end_date:
        return False
    current = start_date
    while current <= end_date:
        if is_date_locked(organization, current):
            return True
        _, num_days = calendar.monthrange(current.year, current.month)
        next_month_start = datetime_date(current.year, current.month, 1) + timedelta(days=num_days)
        current = next_month_start
    return False


def validate_attendance_period(organization, year, month):
    """
    Validates all active employees for the given month.
    Checks:
    - Month must be a completed past month (calendar month end < today).
    - Unresolved issues for any active employee (pending approvals, incomplete sessions, conflicts).
    """
    from users.models import Employee

    _, num_days = calendar.monthrange(year, month)
    month_end_date = datetime_date(year, month, num_days)
    today = datetime_date.today()

    is_past_month = month_end_date < today

    active_employees = Employee.objects.filter(
        organization=organization,
        is_active=True
    ).order_by('first_name', 'last_name', 'id')

    # Pre-fetch schedules for this tenant
    schedules_by_desig = {
        (s.designation or '').lower(): s
        for s in Schedule.objects.filter(organization=organization, is_deleted=False)
    }

    issues = []
    employee_data = []
    payroll_ready_count = 0
    needs_review_count = 0

    for emp in active_employees:
        emp_name = f"{emp.first_name} {emp.last_name}".strip() or emp.email
        emp_schedule = schedules_by_desig.get((emp.designation or '').lower())

        daily_summaries = []
        emp_has_issues = False

        totals = {
            "working_days": 0,
            "present_days": 0.0,
            "half_days": 0,
            "absent_days": 0,
            "leave_days": 0.0,
            "paid_leave_days": 0.0,
            "unpaid_leave_days": 0.0,
            "payable_attendance_units": 0.0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_worked_minutes": 0,
            "weekly_off_days": 0,
            "holiday_days": 0,
            "worked_on_off_days": 0,
        }

        for day in range(1, num_days + 1):
            target_date = datetime_date(year, month, day)
            summary = get_daily_attendance_summary(emp, target_date, schedule=emp_schedule)
            daily_summaries.append(summary)

            # Check blocking issues
            if not summary["is_payroll_ready"] or summary["requires_admin_resolution"] or summary["has_conflict"] or summary["has_pending_approval"] or summary["daily_status"] == 'Incomplete' or summary.get("is_open_session"):
                emp_has_issues = True
                reason = summary.get("conflict_reason")
                if not reason:
                    if summary.get("has_pending_approval"):
                        reason = "Pending attendance approval"
                    elif summary.get("daily_status") == 'Incomplete':
                        reason = "Missing clock-out (Incomplete session)"
                    elif summary.get("has_conflict"):
                        reason = "Attendance conflict with approved leave"
                    elif summary.get("is_open_session"):
                        reason = "Unclosed active clock session"
                    else:
                        reason = "Requires admin resolution"
                issues.append({
                    "employee_id": emp.id,
                    "employee_name": emp_name,
                    "date": str(target_date),
                    "reason": reason
                })

            # Accumulate totals
            if summary["is_working_day"]:
                totals["working_days"] += 1
            if summary["daily_status"] in ["Present", "Half Day"]:
                totals["present_days"] += summary["attendance_unit"]
            if summary["daily_status"] == "Half Day":
                totals["half_days"] += 1
            if summary["daily_status"] == "Absent":
                totals["absent_days"] += 1
            totals["paid_leave_days"] += summary.get("paid_leave_unit", 0.0)
            totals["unpaid_leave_days"] += summary.get("unpaid_leave_unit", 0.0)
            totals["leave_days"] += summary["leave_unit"]
            totals["payable_attendance_units"] += summary["payable_attendance_unit"]

            if summary["is_late"]:
                totals["late_count"] += 1
                totals["total_late_minutes"] += summary["minutes_late"]

            totals["total_worked_minutes"] += summary["worked_minutes"]

            if summary["is_weekly_off"]:
                totals["weekly_off_days"] += 1
            if summary["is_holiday"]:
                totals["holiday_days"] += 1
            if summary["worked_on_off_day"]:
                totals["worked_on_off_days"] += 1

        if emp_has_issues:
            needs_review_count += 1
        else:
            payroll_ready_count += 1

        employee_data.append({
            "employee": emp,
            "employee_id": emp.id,
            "employee_name": emp_name,
            "designation": emp.designation or '',
            "totals": totals,
            "daily_summaries": daily_summaries,
            "has_issues": emp_has_issues
        })

    is_clean = len(issues) == 0 and is_past_month

    return {
        "is_clean": is_clean,
        "is_past_month": is_past_month,
        "total_employees": active_employees.count(),
        "payroll_ready_count": payroll_ready_count,
        "needs_review_count": needs_review_count,
        "issues": issues,
        "employee_data": employee_data
    }


def finalize_attendance_period(organization, year, month, user):
    """
    Finalizes an attendance period for an organization.
    1. Validates month: must be past completed month, zero unresolved issues.
    2. Atomic transaction:
       - Increments current_revision (from 0 to 1 for first finalization; or +1 for re-finalization).
       - Updates old snapshots for this period to is_current = False.
       - Creates new AttendancePeriodEmployeeSnapshot rows with revision = period.current_revision and is_current = True.
       - Updates AttendancePeriod to status='Finalized', finalized_at=now, finalized_by=user.
       - Writes AuditLog.
    """
    from django.db import transaction
    from rest_framework.exceptions import ValidationError

    validation = validate_attendance_period(organization, year, month)

    if not validation["is_past_month"]:
        raise ValidationError({"detail": "Cannot finalize the current or future month. Only completed past months can be finalized."})

    if not validation["is_clean"]:
        raise ValidationError({
            "detail": f"Cannot finalize attendance period: {len(validation['issues'])} unresolved issues detected.",
            "issues": validation["issues"]
        })

    with transaction.atomic():
        period, created = AttendancePeriod.objects.select_for_update().get_or_create(
            organization=organization,
            year=year,
            month=month,
            defaults={'status': 'Draft', 'current_revision': 0}
        )

        if period.status == 'Finalized':
            raise ValidationError({"detail": "This attendance period is already finalized."})

        # Revision handling: increment revision
        new_revision = period.current_revision + 1
        period.current_revision = new_revision

        # Mark all prior snapshots for this period as not current
        AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=period
        ).update(is_current=False)

        # Create new snapshot records
        hourly_paid_leave_eligible = True
        if hasattr(organization, 'settings') and organization.settings is not None:
            hourly_paid_leave_eligible = getattr(organization.settings, 'hourly_wage_paid_leave_eligible', True)

        snapshot_objs = []
        for item in validation["employee_data"]:
            totals = item["totals"]
            worked_mins = totals.get("total_worked_minutes", 0)
            worked_hours = (Decimal(str(worked_mins)) / Decimal('60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            paid_leave_hours = Decimal('0.00')
            if hourly_paid_leave_eligible:
                for d_sum in item.get("daily_summaries", []):
                    paid_unit = Decimal(str(d_sum.get("paid_leave_unit", 0.0)))
                    if paid_unit > Decimal('0.00'):
                        sched_mins = Decimal(str(d_sum.get("scheduled_minutes", 480)))
                        paid_leave_hours += (sched_mins / Decimal('60')) * paid_unit
                paid_leave_hours = paid_leave_hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            payable_work_hours = (worked_hours + paid_leave_hours).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            snapshot_objs.append(
                AttendancePeriodEmployeeSnapshot(
                    attendance_period=period,
                    revision=new_revision,
                    is_current=True,
                    employee=item["employee"],
                    employee_name=item["employee_name"],
                    designation=item["designation"],
                    working_days=totals["working_days"],
                    present_days=totals["present_days"],
                    half_days=totals["half_days"],
                    absent_days=totals["absent_days"],
                    leave_days=totals["leave_days"],
                    paid_leave_days=Decimal(str(round(totals.get("paid_leave_days", 0.0), 2))),
                    unpaid_leave_days=Decimal(str(round(totals.get("unpaid_leave_days", 0.0), 2))),
                    payable_attendance_units=totals["payable_attendance_units"],
                    late_count=totals["late_count"],
                    total_late_minutes=totals["total_late_minutes"],
                    total_worked_minutes=totals["total_worked_minutes"],
                    payable_work_hours=payable_work_hours,
                    weekly_off_days=totals["weekly_off_days"],
                    holiday_days=totals["holiday_days"],
                    worked_on_off_days=totals["worked_on_off_days"],
                    daily_summaries=item["daily_summaries"],
                )
            )

        AttendancePeriodEmployeeSnapshot.objects.bulk_create(snapshot_objs)

        period.status = 'Finalized'
        period.finalized_at = timezone.now()
        period.finalized_by = user
        period.total_employees = validation["total_employees"]
        period.payroll_ready_count = validation["payroll_ready_count"]
        period.needs_review_count = 0
        period.save()

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Attendance Period Finalized",
            details=f"Finalized attendance period {year}-{month:02d} (Revision {new_revision}) for {len(snapshot_objs)} employees."
        )

    return period


def reopen_attendance_period(organization, year, month, user, reason):
    """
    Reopens a finalized attendance period.
    - Reason string is mandatory (minimum 5 characters).
    - Sets status to 'Draft'.
    - Preserves existing snapshot rows for revision history.
    - Writes AuditLog.
    """
    from django.db import transaction
    from rest_framework.exceptions import ValidationError, PermissionDenied

    if not reason or not str(reason).strip() or len(str(reason).strip()) < 5:
        raise ValidationError({"reason": "A valid reason (minimum 5 characters) is required to reopen a finalized attendance period."})

    # Permission check
    from core.decorators import has_fine_grained_permission
    if not has_fine_grained_permission(user, ['attendance:admin']):
        raise PermissionDenied("You do not have permission to reopen a finalized attendance period.")

    with transaction.atomic():
        period = AttendancePeriod.objects.select_for_update().filter(
            organization=organization,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period or period.status != 'Finalized':
            raise ValidationError({"detail": "Attendance period is not finalized and cannot be reopened."})

        # Lock Hierarchy Check: Cannot reopen attendance if payroll is finalized
        try:
            from payroll.models import PayrollPeriod
            finalized_payroll = PayrollPeriod.objects.filter(
                organization=organization,
                year=year,
                month=month,
                status='Finalized'
            ).exists()
            if finalized_payroll:
                raise ValidationError({
                    "detail": "Cannot reopen attendance period: Payroll for this month is Finalized. Please reopen Payroll first."
                })
        except ImportError:
            pass

        period.status = 'Draft'
        period.reopened_at = timezone.now()
        period.reopened_by = user
        period.reopen_reason = str(reason).strip()
        period.save()

        user_name = f"{user.first_name} {user.last_name}".strip() or user.email if user else "Admin"
        AuditLog.objects.create(
            organization=organization,
            employee=user,
            employeeName=user_name,
            action="Attendance Period Reopened",
            details=f"Reopened attendance period {year}-{month:02d} (Current Rev {period.current_revision}). Reason: {str(reason).strip()}"
        )

    return period


