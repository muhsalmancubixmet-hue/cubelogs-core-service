# --------------------------------------------------------------------------------
#       Attendance Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
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

    def __str__(self):
        return f"{self.employeeName} - {self.date} ({self.status})"

# --------------------------------------------------------------------------------
# Schedule Model: Defines office shift start/end timings mapped to employee designations
# --------------------------------------------------------------------------------
class Schedule(BaseModel):
    designation = models.CharField(max_length=100, unique=True)
    shiftStart = models.CharField(max_length=5, default="09:00")
    shiftEnd = models.CharField(max_length=5, default="17:00")

    class Meta:
        db_table = 'api_schedule'

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


