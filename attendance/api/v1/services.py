# --------------------------------------------------------------------------------
#       Attendance Services
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
from datetime import date as datetime_date

# DJANGO
from django.utils import timezone

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import AuditLog
from attendance.models import AttendanceLog

class AttendanceService:
    @staticmethod
    def clock_in(employee, verification_data=None):
        today = datetime_date.today()
        active_log = AttendanceLog.objects.filter(employee=employee, date=today, clockOut__isnull=True).first()
        if active_log:
            raise ValueError('Already clocked in today')

        coords = {}
        photo = None
        if verification_data:
            coords = verification_data.get('coords', {})
            photo = verification_data.get('photo')

        org = employee.organization
        auto_approve = getattr(org.settings, 'auto_approve_attendance', False) if org and org.settings else False
        initial_status = 'Approved' if auto_approve else 'Pending Approval'

        now = timezone.now()
        log = AttendanceLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            date=today,
            clockIn=now,
            clockOut=None,
            totalDuration="0",
            verificationPhoto=photo,
            verificationLocation=coords,
            status=initial_status
        )

        AuditLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Clocked In",
            details=f"Employee clocked in at {now.strftime('%H:%M:%S')}."
        )
        return log

    @staticmethod
    def clock_out(employee):
        log = AttendanceLog.objects.filter(employee=employee, clockOut__isnull=True).order_by('-date', '-id').first()
        if not log:
            raise ValueError('No active clock-in session found')

        now = timezone.now()
        log.clockOut = now
        duration_seconds = max(0, int((now - log.clockIn).total_seconds()))
        log.totalDuration = str(duration_seconds)
        log.save()

        hours = duration_seconds // 3600
        minutes = (duration_seconds % 3600) // 60
        seconds = duration_seconds % 60
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        AuditLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Clocked Out",
            details=f"Employee clocked out at {now.strftime('%H:%M:%S')}. Duration: {duration_str}."
        )
        return log
