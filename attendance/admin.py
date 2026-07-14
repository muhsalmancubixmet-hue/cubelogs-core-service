# --------------------------------------------------------------------------------
#       Attendance Admin
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin

# THIRD PARTY

# APPLICATION SPECIFIC
from attendance.models import (
    AttendanceLog,
    Schedule,
    LeaveType,
    Leave,
    Holiday,
    OfficeLocation
)


@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = ('employeeName', 'date', 'clockIn', 'clockOut', 'totalDuration', 'status')
    list_filter = ('status', 'date')
    search_fields = ('employeeName', 'employee__email')


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ('designation', 'shiftStart', 'shiftEnd')
    search_fields = ('designation',)


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'limitPeriod', 'maxLimit', 'status', 'organization')
    list_filter = ('status', 'limitPeriod', 'organization')
    search_fields = ('name',)


@admin.register(Leave)
class LeaveAdmin(admin.ModelAdmin):
    list_display = ('employeeName', 'leaveTypeName', 'startDate', 'endDate', 'duration', 'status')
    list_filter = ('status', 'startDate')
    search_fields = ('employeeName', 'leaveTypeName')


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'organization')
    list_filter = ('date', 'organization')
    search_fields = ('name',)


@admin.register(OfficeLocation)
class OfficeLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'lat', 'lon', 'radius', 'isPrimary')
    list_filter = ('organization', 'isPrimary')
    search_fields = ('name',)
