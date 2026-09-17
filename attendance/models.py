# --------------------------------------------------------------------------------
#       Attendance Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

from decimal import Decimal
from django.db import models

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import BaseModel, Organization

def default_weekly_holidays_default():
    return []

# --------------------------------------------------------------------------------
# AttendanceLog Model: Represents clock-in/out timestamps, status, and verification
# --------------------------------------------------------------------------------
class AttendanceLog(BaseModel):
    ATTENDANCE_STATUS_CHOICES = [
        ('Pending Approval', 'Pending Approval'),
        ('Approved', 'Approved'),
        ('Late', 'Late'),
        ('Half Day', 'Half Day'),
        ('Absent', 'Absent'),
    ]
    employee = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='attendance_logs')
    employee_profile = models.ForeignKey(
        'users.EmployeeProfile',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='attendance_logs'
    )
    employeeName = models.CharField(max_length=255)
    date = models.DateField()
    clockIn = models.DateTimeField(null=True, blank=True)
    clockOut = models.DateTimeField(null=True, blank=True)
    totalDuration = models.CharField(max_length=50, blank=True, null=True)
    verificationPhoto = models.TextField(blank=True, null=True)
    verificationLocation = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=50, choices=ATTENDANCE_STATUS_CHOICES, default='Pending Approval')

    class Meta:
        db_table = 'api_attendancelog'
        indexes = [
            models.Index(fields=['employee', 'date', 'is_deleted'], name='att_log_emp_date_del_idx'),
            models.Index(fields=['employee', 'clockIn'], name='attlog_emp_clockin_idx'),
            models.Index(fields=['employee_profile', 'date'], name='attlog_prof_date_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.employee_id and not self.employee_profile_id:
            try:
                from users.models import EmployeeProfile
                prof = EmployeeProfile.objects.filter(user_id=self.employee_id).first()
                if prof:
                    self.employee_profile_id = prof.id
            except Exception:
                pass
        elif self.employee_profile_id and not self.employee_id:
            try:
                self.employee_id = self.employee_profile.user_id
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employeeName} - {self.date} ({self.status})"

# --------------------------------------------------------------------------------
# AttendancePolicy Model: Versioned organization attendance & compliance policy
# --------------------------------------------------------------------------------
def default_weekly_holidays_policy():
    return ["Saturday", "Sunday"]


class AttendancePolicy(BaseModel):
    BREAK_TYPE_CHOICES = [
        ('Paid', 'Paid'),
        ('Unpaid', 'Unpaid'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='attendance_policies',
    )
    effective_from = models.DateField(db_index=True)

    grace_period_minutes = models.IntegerField(default=15)
    full_day_minimum_minutes = models.IntegerField(default=480)
    half_day_minimum_minutes = models.IntegerField(default=240)
    minimum_session_minutes = models.IntegerField(default=5)

    break_duration_minutes = models.IntegerField(default=60)
    break_type = models.CharField(max_length=20, choices=BREAK_TYPE_CHOICES, default='Unpaid')

    default_weekly_holidays = models.JSONField(default=default_weekly_holidays_policy, blank=True)
    auto_approve_attendance = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_attendancepolicy'
        ordering = ['-effective_from']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'effective_from'],
                name='unique_org_effective_from_policy'
            )
        ]

    def __str__(self):
        return f"{self.organization.name if self.organization else 'Org'} Policy (From {self.effective_from})"


# --------------------------------------------------------------------------------
# Schedule Model: Defines office shift start/end timings mapped to employee designations
# --------------------------------------------------------------------------------
class Schedule(BaseModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='schedules',
    )
    designation = models.CharField(max_length=100)
    shiftStart = models.CharField(max_length=5, default="09:00")
    shiftEnd = models.CharField(max_length=5, default="17:00")

    class Meta:
        db_table = 'api_schedule'
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'designation'],
                name='unique_org_designation_schedule'
            )
        ]

    def __str__(self):
        return f"{self.designation} ({self.shiftStart} - {self.shiftEnd})"

# --------------------------------------------------------------------------------
# LeaveType Model: Defines leave categories, yearly allowances, and validation limits
# --------------------------------------------------------------------------------
class LeaveType(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    limitPeriod = models.CharField(max_length=50, default='Yearly')
    maxLimit = models.IntegerField(default=10)
    restrictedDates = models.JSONField(default=list, blank=True)
    carryForward = models.BooleanField(default=False)
    maxCarryForward = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default='Active')
    minAdvanceDays = models.IntegerField(default=0)
    is_paid = models.BooleanField(default=True)
    organization = models.ForeignKey(
        Organization, null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='leave_types',
    )

    class Meta:
        db_table = 'api_leavetype'

    def __str__(self):
        return self.name

# --------------------------------------------------------------------------------
# Leave Model: Manages employee leave applications, dates, durations, and approvals
# --------------------------------------------------------------------------------
class Leave(BaseModel):
    employee = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='leaves')
    employeeName = models.CharField(max_length=255)
    leaveType = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='leaves')
    leaveTypeName = models.CharField(max_length=255)
    startDate = models.DateField()
    endDate = models.DateField()
    duration = models.FloatField(default=1.0)
    dayType = models.CharField(max_length=50, default='Full')
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, default='Pending')

    class Meta:
        db_table = 'api_leave'
        indexes = [
            models.Index(fields=['employee', 'startDate', 'endDate', 'is_deleted'], name='leave_emp_dates_del_idx'),
        ]

    def __str__(self):
        return f"{self.employeeName} - {self.leaveTypeName} ({self.startDate} to {self.endDate})"

# --------------------------------------------------------------------------------
# Holiday Model: Stores organization specific holidays and holiday display banners
# --------------------------------------------------------------------------------
class Holiday(BaseModel):
    organization = models.ForeignKey(
        Organization, null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='holidays',
    )
    name = models.CharField(max_length=255)
    date = models.DateField()
    description = models.TextField(blank=True, null=True)
    banner = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'api_holiday'

    def __str__(self):
        return f"{self.name} - {self.date}"



# --------------------------------------------------------------------------------
# OfficeLocation Model: Configures office geographic coordinates and geofence radii
# --------------------------------------------------------------------------------
class OfficeLocation(BaseModel):
    organization = models.ForeignKey(
        Organization, null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='locations',
    )
    name = models.CharField(max_length=255)
    lat = models.FloatField()
    lon = models.FloatField()
    radius = models.FloatField(default=100.0)
    isPrimary = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_officelocation'

    def __str__(self):
        return self.name


# --------------------------------------------------------------------------------
# AttendancePeriod Model: Represents monthly organization attendance container & lock status
# --------------------------------------------------------------------------------
class AttendancePeriod(BaseModel):
    STATUS_CHOICES = [
        ('Draft', 'Draft'),
        ('Finalized', 'Finalized'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='attendance_periods',
    )
    year = models.PositiveIntegerField(db_index=True)
    month = models.PositiveSmallIntegerField(db_index=True)  # 1 - 12
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Draft', db_index=True)
    current_revision = models.PositiveIntegerField(default=0)

    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        'users.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='finalized_attendance_periods',
    )

    total_employees = models.IntegerField(default=0)
    payroll_ready_count = models.IntegerField(default=0)
    needs_review_count = models.IntegerField(default=0)

    reopen_reason = models.TextField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        'users.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reopened_attendance_periods',
    )

    class Meta:
        db_table = 'api_attendanceperiod'
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'year', 'month'],
                name='unique_org_year_month_attendance_period'
            )
        ]

    def __str__(self):
        return f"{self.organization.name if self.organization else 'Org'} - {self.year}/{self.month:02d} ({self.status} Rev {self.current_revision})"


# --------------------------------------------------------------------------------
# AttendancePeriodEmployeeSnapshot Model: Revision-tracked monthly snapshot per employee
# --------------------------------------------------------------------------------
class AttendancePeriodEmployeeSnapshot(BaseModel):
    attendance_period = models.ForeignKey(
        AttendancePeriod,
        on_delete=models.CASCADE,
        related_name='employee_snapshots',
    )
    revision = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)

    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='attendance_snapshots',
    )
    employee_name = models.CharField(max_length=255)
    designation = models.CharField(max_length=100, blank=True, default='')

    # Aggregated monthly totals for Payroll
    working_days = models.IntegerField(default=0)
    present_days = models.FloatField(default=0.0)
    half_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)
    leave_days = models.FloatField(default=0.0)
    paid_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    unpaid_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    payable_attendance_units = models.FloatField(default=0.0)
    late_count = models.IntegerField(default=0)
    total_late_minutes = models.IntegerField(default=0)
    total_worked_minutes = models.IntegerField(default=0)
    payable_work_hours = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Finalized payable work hours for hourly compensation calculation")
    weekly_off_days = models.IntegerField(default=0)
    holiday_days = models.IntegerField(default=0)
    worked_on_off_days = models.IntegerField(default=0)

    # Detailed daily calculations JSON list for complete auditability
    daily_summaries = models.JSONField(default=list)

    class Meta:
        db_table = 'api_attendanceperiodemployeesnapshot'
        ordering = ['employee_name']
        constraints = [
            models.UniqueConstraint(
                fields=['attendance_period', 'employee', 'revision'],
                name='unique_period_emp_revision_snapshot'
            )
        ]

    def __str__(self):
        return f"{self.employee_name} - {self.attendance_period.year}/{self.attendance_period.month:02d} (Rev {self.revision})"



