"""
api/views/attendance.py — Attendance management views
"""
from datetime import datetime, timedelta
from datetime import date as datetime_date

from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import (
    Employee, AttendanceLog, AuditLog, Leave, Schedule, OrgSettings
)
from api.serializers import AttendanceLogSerializer


class AttendanceLogViewSet(viewsets.ModelViewSet):
    queryset = AttendanceLog.objects.all().order_by('-date', '-id')
    serializer_class = AttendanceLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(employee__organization=self.request.user.organization)
        employee_id = self.request.query_params.get('employee_id')
        date = self.request.query_params.get('date')
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        if date:
            qs = qs.filter(date=date)
        return qs

    @action(detail=False, methods=['post'], url_path='clock-in')
    def clock_in(self, request):
        employee_id = request.data.get('employeeId') or request.user.id
        verification_data = request.data.get('verificationData')

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        today = datetime_date.today()

        # Check if already clocked in today (and not clocked out)
        active_log = AttendanceLog.objects.filter(employee=employee, date=today, clockOut__isnull=True).first()
        if active_log:
            return Response({'error': 'Already clocked in today'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()

        coords = {}
        photo = None
        if verification_data:
            coords = verification_data.get('coords', {})
            photo = verification_data.get('photo')

        org = employee.organization
        auto_approve = False
        if org and org.settings:
            auto_approve = getattr(org.settings, 'auto_approve_attendance', False)

        initial_status = 'Approved' if auto_approve else 'Pending Approval'

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

        # Log clock-in event
        AuditLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Clocked In",
            details=f"Employee clocked in at {now.strftime('%H:%M:%S')}."
        )

        serializer = self.get_serializer(log)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='clock-out')
    def clock_out(self, request):
        employee_id = request.data.get('employeeId') or request.user.id

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        # Find active clock-in log (where clockOut is null)
        log = AttendanceLog.objects.filter(employee=employee, clockOut__isnull=True).order_by('-date', '-id').first()
        if not log:
            return Response({'error': 'No active clock-in session found'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        log.clockOut = now

        # Calculate duration in seconds
        duration_seconds = max(0, int((now - log.clockIn).total_seconds()))
        log.totalDuration = str(duration_seconds)
        log.save()

        # Log clock-out event
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

        serializer = self.get_serializer(log)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AttendanceApprovalView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    ALLOWED_STATUSES = ['Approved', 'Late', 'Half Day', 'Absent', 'Pending Approval']

    def patch(self, request, pk):
        try:
            log = AttendanceLog.objects.get(pk=pk, employee__organization=request.user.organization)
        except AttendanceLog.DoesNotExist:
            return Response({'error': 'Attendance log not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get('status')
        if new_status not in self.ALLOWED_STATUSES:
            return Response(
                {'error': f"Invalid status. Choose from: {', '.join(self.ALLOWED_STATUSES)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_status = log.status
        log.status = new_status
        log.save()

        AuditLog.objects.create(
            employee=request.user,
            employeeName=f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email,
            action="Attendance Status Updated",
            details=f"Log #{pk} status changed from '{old_status}' to '{new_status}'."
        )

        return Response({
            'id': log.id,
            'status': log.status,
            'employeeName': log.employeeName,
            'message': f"Status updated to '{new_status}'.",
        }, status=status.HTTP_200_OK)


class HRAttendanceDashboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        today = datetime_date.today()
        org = request.user.organization

        org_settings = OrgSettings.objects.filter(organization=org).first()
        grace_minutes = getattr(org_settings, 'grace_period_minutes', 15) if org_settings else 15

        all_employees = Employee.objects.filter(organization=org, is_active=True)

        today_logs = AttendanceLog.objects.filter(
            employee__organization=org, date=today
        ).select_related('employee')
        logged_employee_ids = set(log.employee_id for log in today_logs)

        on_leave_today = Leave.objects.filter(
            employee__organization=org,
            startDate__lte=today,
            endDate__gte=today,
            status='Approved'
        ).select_related('employee')
        on_leave_employee_ids = set(lv.employee_id for lv in on_leave_today)

        pending_list = []
        late_list = []

        for log in today_logs:
            emp = log.employee
            entry = {
                'id': log.id,
                'employeeName': log.employeeName or f"{emp.first_name} {emp.last_name}".strip(),
                'employeeDesignation': emp.designation or '',
                'clockIn': log.clockIn.isoformat() if log.clockIn else None,
                'status': log.status,
            }

            minutes_late = 0
            schedule = Schedule.objects.filter(designation=emp.designation).first()
            if schedule and log.clockIn:
                try:
                    shift_h, shift_m = map(int, schedule.shiftStart.split(':'))
                    shift_start = log.clockIn.replace(hour=shift_h, minute=shift_m, second=0, microsecond=0)
                    grace_end = shift_start + timedelta(minutes=grace_minutes)
                    if log.clockIn > grace_end:
                        diff = log.clockIn - shift_start
                        minutes_late = int(diff.total_seconds() // 60)
                except Exception:
                    pass

            if minutes_late > 0:
                entry['minutesLate'] = minutes_late
                entry['shiftStart'] = schedule.shiftStart if schedule else None
                late_list.append(entry)

            if log.status == 'Pending Approval':
                pending_list.append(entry)

        on_leave_list = []
        for lv in on_leave_today:
            on_leave_list.append({
                'id': lv.id,
                'employeeName': lv.employeeName or f"{lv.employee.first_name} {lv.employee.last_name}".strip(),
                'employeeDesignation': lv.employee.designation or '',
                'leaveTypeName': lv.leaveTypeName or '',
                'dayType': lv.dayType or 'Full Day',
            })

        absent_list = []
        for emp in all_employees:
            if emp.id not in logged_employee_ids and emp.id not in on_leave_employee_ids:
                absent_list.append({
                    'id': emp.id,
                    'employeeName': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                    'employeeDesignation': emp.designation or '',
                })

        return Response({
            'date': today.isoformat(),
            'grace_period_minutes': grace_minutes,
            'pending': pending_list,
            'late': late_list,
            'on_leave': on_leave_list,
            'absent': absent_list,
            'summary': {
                'pendingCount': len(pending_list),
                'lateCount': len(late_list),
                'onLeaveCount': len(on_leave_list),
                'absentCount': len(absent_list),
            }
        }, status=status.HTTP_200_OK)
