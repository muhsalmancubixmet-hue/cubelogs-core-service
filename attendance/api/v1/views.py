# --------------------------------------------------------------------------------
#       Attendance Views
# --------------------------------------------------------------------------------

"""
api/views/attendance.py — Attendance management views
"""
import os
import json
import stripe
from decimal import Decimal
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import stripe.error

from datetime import datetime, timedelta
from datetime import date as datetime_date

from django.utils import timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.urls import reverse

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from core.mixins import FilterMixinNew, TenantScopedViewSetMixin
from core.permissions import HasRequiredPermission
from core.module_registry.loader import load_modules
from users.models import Employee, PERMISSION_FLAGS, Template
from core.models import AuditLog, OrgSettings, Organization
from attendance.models import AttendanceLog, Leave, Schedule, OfficeLocation, Holiday, LeaveType
from subscribers.models import SubscriptionPackage, SubscriberAccount

from attendance.api.v1.serializers import (
    AttendanceLogSerializer, TemplateSerializer, OfficeLocationSerializer, ScheduleSerializer,
    OrgSettingsSerializer, AuditLogSerializer, HolidaySerializer, LeaveTypeSerializer, LeaveSerializer
)
from users.filters import TemplateFilter
from attendance.filters import (
    AttendanceLogFilter, ScheduleFilter, LeaveTypeFilter, LeaveFilter, HolidayFilter, OfficeLocationFilter
)




# --------------------------------------------------------------------------------
# AttendanceLogViewSet: ViewSet managing daily employee clock-in and clock-out logs.
# --------------------------------------------------------------------------------
class AttendanceLogViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = AttendanceLog.objects.all().order_by('-date', '-id')
    serializer_class = AttendanceLogSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = AttendanceLogFilter

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and not (user.is_superuser or getattr(user, 'isSuperAdmin', False)):
            user_perms = getattr(user, 'permissions', [])
            if 'attendance:admin' not in user_perms and 'attendance:management_portal' not in user_perms:
                qs = qs.filter(employee=user)
        return qs

    def get_permissions(self):
        if self.action in ['clock_in', 'clock_out']:
            return [permissions.IsAuthenticated()]
        if self.action in ['list', 'retrieve']:
            return [permissions.IsAuthenticated()]
        self.required_permission = ['attendance:admin', 'attendance:management_portal']
        return [permissions.IsAuthenticated(), HasRequiredPermission()]

    @action(detail=False, methods=['post'], url_path='clock-in')
    def clock_in(self, request):
        employee_id = request.data.get('employeeId') or request.user.id
        # Prevent IDOR
        if int(employee_id) != request.user.id:
            user_perms = getattr(request.user, 'permissions', [])
            is_admin = request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or 'attendance:admin' in user_perms or 'attendance:management_portal' in user_perms
            if not is_admin:
                return Response({'error': 'You do not have permission to clock in on behalf of other employees'}, status=status.HTTP_403_FORBIDDEN)

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        # Verify organization matches
        if employee.organization != request.user.organization:
            return Response({'error': 'Employee not found in your organization'}, status=status.HTTP_403_FORBIDDEN)

        today = datetime_date.today()

        # Check if already clocked in today (and not clocked out)
        active_log = AttendanceLog.objects.filter(employee=employee, date=today, clockOut__isnull=True).first()
        if active_log:
            return Response({'error': 'Already clocked in today'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()

        coords = {}
        photo = None
        if verification_data := request.data.get('verificationData'):
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
        # Prevent IDOR
        if int(employee_id) != request.user.id:
            user_perms = getattr(request.user, 'permissions', [])
            is_admin = request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or 'attendance:admin' in user_perms or 'attendance:management_portal' in user_perms
            if not is_admin:
                return Response({'error': 'You do not have permission to clock out on behalf of other employees'}, status=status.HTTP_403_FORBIDDEN)

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        # Verify organization matches
        if employee.organization != request.user.organization:
            return Response({'error': 'Employee not found in your organization'}, status=status.HTTP_403_FORBIDDEN)

        # Find active clock-in log (where clockOut is null)
        log = AttendanceLog.objects.filter(employee=employee, clockOut__isnull=True).order_by('-date', '-id').first()
        if not log:
            return Response({'error': 'No active clock-in session found'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        log.clockOut = now

        # Calculate duration in seconds
        if log.clockIn:
            duration_seconds = max(0, int((now - log.clockIn).total_seconds()))
        else:
            duration_seconds = 0
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


# --------------------------------------------------------------------------------
# AttendanceApprovalView: API endpoint for managers to approve or reject employee clock logs.
# --------------------------------------------------------------------------------
class AttendanceApprovalView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = ['attendance:admin', 'attendance:management_portal']

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


# --------------------------------------------------------------------------------
# HRAttendanceDashboardView: API view presenting global analytics of daily logs, leaves, and absents.
# --------------------------------------------------------------------------------
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








# ─── Helper functions ──────────────────────────────────────────────────────────

def get_nth_weekday_of_month(year, month, weekday_name, n):
    import calendar
    WEEKDAYS = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    target_weekday = WEEKDAYS.get(weekday_name.lower())
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

    if n == -1 or n == 'last' or str(n).lower() == 'last':
        return matching_dates[-1]

    try:
        idx = int(n) - 1
        if 0 <= idx < len(matching_dates):
            return matching_dates[idx]
    except (ValueError, TypeError):
        pass
    return None


def calculate_recurring_holidays(organization, start_year, end_year):
    import datetime as dt
    settings = organization.settings
    if not settings:
        return []

    weekly_offs = settings.default_weekly_holidays or []
    monthly_rules = settings.monthly_recurring_holidays or []
    yearly_rules = settings.yearly_recurring_holidays or []

    holidays = []
    mock_id = -1

    for year in range(start_year, end_year + 1):
        # 1. Weekly Holidays
        if weekly_offs:
            curr_date = dt.date(year, 1, 1)
            end_date = dt.date(year, 12, 31)
            while curr_date <= end_date:
                day_name = curr_date.strftime('%A')
                if day_name in weekly_offs:
                    holidays.append(Holiday(
                        id=mock_id,
                        organization=organization,
                        name=f"Weekly Off ({day_name})",
                        date=curr_date,
                        description=f"Standard weekly recurring off-day.",
                        banner=None
                    ))
                    mock_id -= 1
                curr_date += dt.timedelta(days=1)

        # 2. Monthly Recurring Holidays
        for rule in monthly_rules:
            week_num = rule.get('week_number')
            day_name = rule.get('day')
            if week_num is not None and day_name:
                for month in range(1, 13):
                    d = get_nth_weekday_of_month(year, month, day_name, week_num)
                    if d:
                        suffix = "th"
                        if week_num == 1: suffix = "st"
                        elif week_num == 2: suffix = "nd"
                        elif week_num == 3: suffix = "rd"
                        elif str(week_num).lower() == 'last' or week_num == -1: suffix = " Last"

                        rule_desc = f"{week_num}{suffix} {day_name} of Month" if isinstance(week_num, int) and week_num > 0 else f"Last {day_name} of Month"
                        holidays.append(Holiday(
                            id=mock_id,
                            organization=organization,
                            name=f"{rule_desc}",
                            date=d,
                            description=f"Monthly recurring holiday: {rule_desc}.",
                            banner=None
                        ))
                        mock_id -= 1

        # 3. Yearly Recurring Holidays
        for rule in yearly_rules:
            month = rule.get('month')
            day = rule.get('day')
            name = rule.get('name', 'Yearly Holiday')
            if month and day:
                try:
                    d = dt.date(year, int(month), int(day))
                    holidays.append(Holiday(
                        id=mock_id,
                        organization=organization,
                        name=name,
                        date=d,
                        description=f"Yearly recurring holiday: {name}.",
                        banner=None
                    ))
                    mock_id -= 1
                except ValueError:
                    pass

    return holidays


# ─── ViewSets ──────────────────────────────────────────────────────────────────



# --------------------------------------------------------------------------------
# HolidayViewSet: ViewSet managing public holiday calendars for tenant organizations.
# --------------------------------------------------------------------------------
class HolidayViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Holiday.objects.all().order_by('date')
    serializer_class = HolidaySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = HolidayFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['holidays:view', 'holidays:manage', 'attendance:staff']
        else:
            self.required_permission = 'holidays:manage'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]


    def list(self, request, *args, **kwargs):
        import django.utils.timezone as dj_timezone

        # Get static holidays
        static_qs = self.get_queryset()

        current_year = dj_timezone.now().year
        try:
            start_year = int(request.query_params.get('start_year', current_year - 1))
            end_year = int(request.query_params.get('end_year', current_year + 1))
        except ValueError:
            start_year = current_year - 1
            end_year = current_year + 1

        user = request.user
        dynamic_holidays = []
        if user.is_authenticated and user.organization:
            dynamic_holidays = calculate_recurring_holidays(user.organization, start_year, end_year)

        # Merge: static holidays take precedence over dynamic ones on the same date
        merged = {}
        for h in dynamic_holidays:
            merged[h.date] = h

        for h in static_qs:
            merged[h.date] = h

        # Convert back to list and sort by date
        merged_list = sorted(merged.values(), key=lambda x: x.date)

        serializer = self.get_serializer(merged_list, many=True)
        return Response(serializer.data)


# --------------------------------------------------------------------------------
# HolidaySettingsView: API view configuring recurring monthly and yearly holiday templates.
# --------------------------------------------------------------------------------
class HolidaySettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user.organization:
            return Response({"error": "User does not belong to an organization."}, status=status.HTTP_400_BAD_REQUEST)

        settings = user.organization.settings
        if not settings:
            settings = OrgSettings.objects.create()
            user.organization.settings = settings
            user.organization.save()

        return Response({
            "default_weekly_holidays": settings.default_weekly_holidays,
            "monthly_recurring_holidays": settings.monthly_recurring_holidays,
            "yearly_recurring_holidays": settings.yearly_recurring_holidays,
        }, status=status.HTTP_200_OK)

    def patch(self, request):
        user = request.user
        if not user.organization:
            return Response({"error": "User does not belong to an organization."}, status=status.HTTP_400_BAD_REQUEST)

        settings = user.organization.settings
        if not settings:
            settings = OrgSettings.objects.create()
            user.organization.settings = settings
            user.organization.save()

        data = request.data
        if 'default_weekly_holidays' in data:
            settings.default_weekly_holidays = data['default_weekly_holidays']
        if 'monthly_recurring_holidays' in data:
            settings.monthly_recurring_holidays = data['monthly_recurring_holidays']
        if 'yearly_recurring_holidays' in data:
            settings.yearly_recurring_holidays = data['yearly_recurring_holidays']

        settings.save()
        return Response({
            "default_weekly_holidays": settings.default_weekly_holidays,
            "monthly_recurring_holidays": settings.monthly_recurring_holidays,
            "yearly_recurring_holidays": settings.yearly_recurring_holidays,
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# TemplateViewSet: ViewSet managing security role template profiles.
# --------------------------------------------------------------------------------
class TemplateViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Template.objects.all().order_by('name')
    serializer_class = TemplateSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = TemplateFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            # Any authenticated user can read templates (needed for settings page + employee onboarding)
            return [permissions.IsAuthenticated()]
        # Create / update / delete requires admin:templates
        self.required_permission = 'admin:templates'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]

    def perform_create(self, serializer):
        user = self.request.user
        org = user.organization if (user.is_authenticated and hasattr(user, 'organization')) else None
        serializer.save(organization=org)


# --------------------------------------------------------------------------------
# OfficeLocationViewSet: ViewSet managing primary geofence latitude and longitude boundaries.
# --------------------------------------------------------------------------------
class OfficeLocationViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = OfficeLocation.objects.all().order_by('id')
    serializer_class = OfficeLocationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = OfficeLocationFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            # Any authenticated user can read locations (needed on settings page + clock-in)
            return [permissions.IsAuthenticated()]
        self.required_permission = 'locations:manage'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]




# --------------------------------------------------------------------------------
# ScheduleViewSet: ViewSet managing shift times mapped to specific roles.
# --------------------------------------------------------------------------------
class ScheduleViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Schedule.objects.all().order_by('designation')
    serializer_class = ScheduleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ScheduleFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['attendance:staff', 'attendance:management_portal']
        else:
            self.required_permission = 'attendance:management_portal'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]



# --------------------------------------------------------------------------------
# OrgSettingsViewSet: ViewSet managing branding logos and custom attendance validation tolerances.
# --------------------------------------------------------------------------------
class OrgSettingsViewSet(viewsets.ModelViewSet):
    queryset = OrgSettings.objects.all()
    serializer_class = OrgSettingsSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'current_settings'] and self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        self.required_permission = ['settings:branding', 'settings:billing', 'attendance:management_portal']
        return [permissions.IsAuthenticated(), HasRequiredPermission()]

    def get_object(self):
        user = self.request.user
        if user.is_authenticated and user.organization:
            org = user.organization
            if not org.settings:
                settings_obj = OrgSettings.objects.create()
                org.settings = settings_obj
                org.save()
            return org.settings
        # Always return the single OrgSettings object (create if not exists)
        obj, created = OrgSettings.objects.get_or_create(id=1)
        return obj

    def list(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=False, methods=['get', 'put', 'patch'], url_path='current')
    def current_settings(self, request):
        from django.utils import timezone

        instance = self.get_object()

        superadmin = Employee.objects.filter(isSuperAdmin=True).first()
        if superadmin:
            sub = SubscriberAccount.objects.filter(email=superadmin.email, isActive=True).first()
            if sub and sub.expiresAt:
                delta = sub.expiresAt - timezone.now()
                instance.subscriptionDays = max(0, delta.days)
                instance.save()

        if request.method in ['PUT', 'PATCH']:
            new_days = request.data.get('subscriptionDays')
            package_name = request.data.get('packageName')
            if (new_days is not None or package_name is not None) and superadmin:
                sub, created = SubscriberAccount.objects.get_or_create(
                    email=superadmin.email,
                    defaults={'packageName': 'Professional', 'isActive': True}
                )
                sub.isActive = True
                if new_days is not None:
                    sub.expiresAt = timezone.now() + timezone.timedelta(days=int(new_days))
                if package_name:
                    if SubscriptionPackage.objects.filter(name=package_name).exists():
                        sub.packageName = package_name
                sub.save()

            partial = request.method == 'PATCH'
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


# --------------------------------------------------------------------------------
# AuditLogViewSet: ViewSet managing read-only access to employee operation audit trails.
# --------------------------------------------------------------------------------
class AuditLogViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all().order_by('-createdAt', '-id')
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.isSuperAdmin:
            qs = qs.filter(employee=user)
        else:
            employee_id = self.request.query_params.get('employee_id')
            action_type = self.request.query_params.get('action')
            date_str = self.request.query_params.get('date')
            org_id = self.request.query_params.get('organization_id')

            if org_id and user.organization is None:
                qs = qs.filter(organization_id=org_id)
            if employee_id:
                qs = qs.filter(employee_id=employee_id)
            if action_type:
                qs = qs.filter(action=action_type)
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    qs = qs.filter(createdAt__date=target_date)
                except ValueError:
                    pass
        return qs


# --------------------------------------------------------------------------------
# PermissionsConfigView: View to retrieve system authorization flag configuration registry.
# --------------------------------------------------------------------------------
class PermissionsConfigView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        try:
            data = load_modules()
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": f"Config not found: {e}"}, status=status.HTTP_404_NOT_FOUND)


# Leaves and LeaveTypes Views



# --------------------------------------------------------------------------------
# LeaveTypeViewSet: ViewSet managing leave type configurations and restrictions.
# --------------------------------------------------------------------------------
class LeaveTypeViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = LeaveType.objects.all().order_by('-id')
    serializer_class = LeaveTypeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LeaveTypeFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['leaves:apply', 'leaves:manage']
        else:
            self.required_permission = 'leaves:manage'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and user.organization:
            org_qs = qs.filter(organization=user.organization)
            if not org_qs.exists():
                # Clone the global leave types for this organization
                global_types = qs.filter(organization__isnull=True)
                cloned_map = {}
                for gt in global_types:
                    new_lt = LeaveType.objects.create(
                        name=gt.name,
                        description=gt.description,
                        limitPeriod=gt.limitPeriod,
                        maxLimit=gt.maxLimit,
                        restrictedDates=gt.restrictedDates,
                        carryForward=gt.carryForward,
                        maxCarryForward=gt.maxCarryForward,
                        status=gt.status,
                        minAdvanceDays=gt.minAdvanceDays,
                        organization=user.organization
                    )
                    cloned_map[gt.id] = new_lt

                # Update existing Leaves of this organization to point to the cloned LeaveTypes
                leaves_to_update = Leave.objects.filter(employee__organization=user.organization)
                for leave in leaves_to_update:
                    if leave.leaveType_id in cloned_map:
                        leave.leaveType = cloned_map[leave.leaveType_id]
                        leave.save()

                org_qs = qs.filter(organization=user.organization)
            return org_qs.order_by('-id')
        return qs



# --------------------------------------------------------------------------------
# LeaveViewSet: ViewSet managing employee leave application requests.
# --------------------------------------------------------------------------------
class LeaveViewSet(FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Leave.objects.all().order_by('-id')
    serializer_class = LeaveSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LeaveFilter

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and not (user.is_superuser or getattr(user, 'isSuperAdmin', False)):
            user_perms = getattr(user, 'permissions', [])
            if 'leaves:approve' not in user_perms and 'leaves:manage' not in user_perms:
                qs = qs.filter(employee=user)
        return qs

    def get_permissions(self):
        from core.permissions import IsLeaveOwnerOrManager
        return [permissions.IsAuthenticated(), IsLeaveOwnerOrManager()]



    def perform_create(self, serializer):
        leave = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Leave Applied",
            details=f"Applied for {leave.leaveTypeName} leave from {leave.startDate} to {leave.endDate} ({leave.duration} days)."
        )

        # Leave notification email code path has been removed per instructions.
        pass

    @action(detail=True, methods=['patch'], url_path='status')
    def update_status(self, request, pk=None):
        leave = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['Approved', 'Rejected', 'Pending']:
            return Response({'error': 'Invalid status value'}, status=status.HTTP_400_BAD_REQUEST)
        leave.status = new_status
        leave.save()

        # Log status update
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Leave Status Updated",
            details=f"Updated leave request status for {leave.employeeName} to '{new_status}'."
        )

        # Send leave status update email to employee
        if leave.employee and leave.employee.email:
            try:
                from core.tasks import queue_and_send_email
                subject = f"Leave Request {new_status}: {leave.leaveTypeName}"
                body = (
                    f"Hi {leave.employee.first_name or 'there'},\n\n"
                    f"Your leave request for {leave.leaveTypeName} has been {new_status.lower()}.\n"
                    f"Details:\n"
                    f"Duration: {leave.startDate} to {leave.endDate} ({leave.duration} days)\n"
                    f"Reason: {leave.reason or 'No reason provided'}\n\n"
                    f"CubeLogs Portal"
                )
                queue_and_send_email(leave.employee.email, subject, body)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to send leave status email to employee: {e}")

        serializer = self.get_serializer(leave)
        return Response(serializer.data, status=status.HTTP_200_OK)


# Misc backoffice HTML views
"""
api/views/misc.py — Backoffice HTML views and Stripe webhook
"""



# Backoffice views and webhook have been migrated to users/views.py and billing/views.py
