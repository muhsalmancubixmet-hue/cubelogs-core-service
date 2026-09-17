# --------------------------------------------------------------------------------
#       Attendance Views
# --------------------------------------------------------------------------------

"""
api/views/attendance.py — Attendance management views
"""
import os
import json
from decimal import Decimal

from datetime import datetime, timedelta
from datetime import date as datetime_date

from django.utils import timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.urls import reverse

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from django.db.models import Q
from core.mixins import FilterMixinNew, TenantScopedViewSetMixin
from core.permissions import HasRequiredPermission, ActionPermissionMixin, DRFCheckModePermission, DRFPlanPermissionRequired
from attendance.permissions import IsLeaveOwnerOrManager
from core.decorators import permission_required
from core.module_registry.loader import load_modules
from core.models import AuditLog, OrgSettings, Organization
from users.models import Employee, PERMISSION_FLAGS, Template
from attendance.models import AttendanceLog, Leave, Schedule, OfficeLocation, Holiday, LeaveType, AttendancePolicy, AttendancePeriod, AttendancePeriodEmployeeSnapshot
from subscribers.models import SubscriptionPackage, SubscriberAccount
from attendance.services import (
    get_attendance_policy, save_attendance_policy,
    get_daily_attendance_summary, get_monthly_attendance_summary,
    is_date_locked, is_date_range_locked,
    validate_attendance_period, finalize_attendance_period, reopen_attendance_period
)

from attendance.api.v1.serializers import (
    AttendanceLogSerializer, TemplateSerializer, OfficeLocationSerializer, ScheduleSerializer,
    OrgSettingsSerializer, AuditLogSerializer, HolidaySerializer, LeaveTypeSerializer, LeaveSerializer,
    AttendancePolicySerializer, AttendancePeriodSerializer, AttendancePeriodEmployeeSnapshotSerializer,
    FinalizePeriodSerializer, ReopenPeriodSerializer
)
from users.filters import TemplateFilter
from attendance.filters import (
    AttendanceLogFilter, ScheduleFilter, LeaveTypeFilter, LeaveFilter, HolidayFilter, OfficeLocationFilter
)




# --------------------------------------------------------------------------------
# AttendanceLogViewSet: ViewSet managing daily employee clock-in and clock-out logs.
# --------------------------------------------------------------------------------
class AttendanceLogViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = AttendanceLog.objects.all().order_by('-date', '-id')
    serializer_class = AttendanceLogSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = AttendanceLogFilter

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'clock_in': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'clock_out': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
    }

    def get_queryset(self):
        qs = super().get_queryset().filter(is_deleted=False)
        user = self.request.user
        if user.is_authenticated:
            if user.organization:
                qs = qs.filter(employee__organization=user.organization)
            from core.decorators import has_fine_grained_permission
            if not has_fine_grained_permission(user, ['attendance:admin', 'attendance:management_portal']):
                qs = qs.filter(employee=user)
        return qs

    def get_permissions(self):
        if self.action in ['clock_in', 'clock_out', 'list', 'retrieve']:
            self.required_permission = None
        else:
            self.required_permission = ['attendance:admin', 'attendance:management_portal']
        return super().get_permissions()

    @action(detail=False, methods=['post'], url_path='clock-in')
    def clock_in(self, request):
        employee_id = request.data.get('employeeId') or request.user.id
        # Prevent IDOR
        if int(employee_id) != request.user.id:
            from core.decorators import has_fine_grained_permission
            is_admin = has_fine_grained_permission(request.user, ['attendance:admin', 'attendance:management_portal'])
            if not is_admin:
                return Response({'error': 'You do not have permission to clock in on behalf of other employees'}, status=status.HTTP_403_FORBIDDEN)

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        # Verify organization matches
        if employee.organization != request.user.organization:
            return Response({'error': 'Employee not found in your organization'}, status=status.HTTP_403_FORBIDDEN)

        # Employment Eligibility Validation
        today = timezone.now().date()
        if not employee.is_active or getattr(employee, 'employment_status', 'Active') == 'Deactivated':
            return Response({'error': 'Clock In is not available because your employee account is inactive.'}, status=status.HTTP_400_BAD_REQUEST)

        if employee.joining_date and today < employee.joining_date:
            return Response({'error': 'Clock In is not available before your joining date.'}, status=status.HTTP_400_BAD_REQUEST)

        if employee.last_working_date and today > employee.last_working_date:
            return Response({'error': 'Clock In is not available after your last working date.'}, status=status.HTTP_400_BAD_REQUEST)

        verification_data = request.data.get('verificationData') or {}
        coords = verification_data.get('coords', {}) if isinstance(verification_data, dict) else {}
        photo = verification_data.get('photo') if isinstance(verification_data, dict) else None

        has_valid_photo = (
            isinstance(photo, str) and
            bool(photo.strip()) and
            photo.startswith('data:image/') and
            len(photo) >= 50
        )

        org = employee.organization
        locations = list(OfficeLocation.objects.filter(organization=org))

        verification_method = None
        is_geofence_verified = False

        if locations:
            # Validate office locations configuration
            for loc in locations:
                if (
                    loc.lat is None or not (-90.0 <= float(loc.lat) <= 90.0) or
                    loc.lon is None or not (-180.0 <= float(loc.lon) <= 180.0) or
                    loc.radius is None or float(loc.radius) <= 0
                ):
                    return Response(
                        {'error': f"Office location '{loc.name}' configuration needs attention: invalid coordinates ({loc.lat}, {loc.lon}) or radius ({loc.radius})."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            lat = coords.get('lat') if isinstance(coords, dict) else None
            lon = coords.get('lon') if isinstance(coords, dict) else None

            if lat is not None and lon is not None:
                try:
                    lat_val = float(lat)
                    lon_val = float(lon)
                    if -90.0 <= lat_val <= 90.0 and -180.0 <= lon_val <= 180.0:
                        import math
                        def calculate_haversine(lat1, lon1, lat2, lon2):
                            R = 6371000.0  # Earth radius in meters
                            dlat = math.radians(lat2 - lat1)
                            dlon = math.radians(lon2 - lon1)
                            a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
                            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                            return R * c

                        min_dist = float('inf')
                        nearest_loc = None
                        for loc in locations:
                            dist = calculate_haversine(lat_val, lon_val, loc.lat, loc.lon)
                            if dist < min_dist:
                                min_dist = dist
                                nearest_loc = loc
                            if dist <= loc.radius:
                                is_geofence_verified = True
                                break

                        if not is_geofence_verified and nearest_loc is not None:
                            # If within office vicinity (up to 50 km), strict geofence is enforced regardless of photo
                            if min_dist <= 50000:
                                dist_km = min_dist / 1000.0
                                return Response(
                                    {'error': f"Outside office geofence. You are outside the allowed Clock In area for {nearest_loc.name}. You are {dist_km:.1f} km from the office (allowed radius: {int(nearest_loc.radius)}m)."},
                                    status=status.HTTP_400_BAD_REQUEST
                                )
                except (ValueError, TypeError):
                    is_geofence_verified = False
        else:
            # Organization has no OfficeLocation configured -> Direct/Remote clock-in with valid GPS coordinates is verified by location
            lat = coords.get('lat') if isinstance(coords, dict) else None
            lon = coords.get('lon') if isinstance(coords, dict) else None
            if lat is not None and lon is not None:
                try:
                    lat_val = float(lat)
                    lon_val = float(lon)
                    if not (lat_val == 0.0 and lon_val == 0.0) and -90.0 <= lat_val <= 90.0 and -180.0 <= lon_val <= 180.0:
                        is_geofence_verified = True
                except (ValueError, TypeError):
                    is_geofence_verified = False

        # VERIFICATION SECURITY RULE: VALID LOCATION OR VALID CAMERA FALLBACK PHOTO
        if is_geofence_verified:
            verification_method = 'Location'
        elif has_valid_photo:
            verification_method = 'Camera Fallback'
        else:
            return Response(
                {'error': 'Clock in failed: Verification photo is required when outside office geofence or location is unavailable.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        today = timezone.now().date()

        from django.db import transaction
        with transaction.atomic():
            # Check if already clocked in today (and not clocked out)
            active_log = AttendanceLog.objects.select_for_update().filter(employee=employee, date=today, clockOut__isnull=True).first()
            if active_log:
                return Response({'error': 'Already clocked in today'}, status=status.HTTP_400_BAD_REQUEST)

            now = timezone.now()
            policy = get_attendance_policy(org, today) if org else None
            auto_approve = policy.auto_approve_attendance if policy else False

            initial_status = 'Approved' if auto_approve else 'Pending Approval'

            log_coords = dict(coords) if isinstance(coords, dict) else {}
            log_coords['verification_method'] = verification_method

            log = AttendanceLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                date=today,
                clockIn=now,
                clockOut=None,
                totalDuration="0",
                verificationPhoto=photo if has_valid_photo else None,
                verificationLocation=log_coords,
                status=initial_status
            )

            # Log clock-in event
            AuditLog.objects.create(
                organization=org,
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
            from core.decorators import has_fine_grained_permission
            is_admin = has_fine_grained_permission(request.user, ['attendance:admin', 'attendance:management_portal'])
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

        if is_date_locked(employee.organization, log.date):
            return Response({'error': f'Attendance for {log.date.strftime("%B %Y")} is finalized and locked. Reopen the period to make changes.'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        policy = get_attendance_policy(employee.organization, log.date)
        min_session_minutes = policy.minimum_session_minutes if policy else 5

        # Check minimum session duration before clocking out
        if log.clockIn:
            elapsed_seconds = max(0, int((now - log.clockIn).total_seconds()))
            required_seconds = min_session_minutes * 60
            if elapsed_seconds < required_seconds:
                remaining_seconds = required_seconds - elapsed_seconds
                earliest_clock_out = log.clockIn + timedelta(minutes=min_session_minutes)
                clock_in_local = timezone.localtime(log.clockIn)
                earliest_local = timezone.localtime(earliest_clock_out)

                clock_in_time_str = clock_in_local.strftime('%I:%M %p').lstrip('0')
                earliest_time_str = earliest_local.strftime('%I:%M %p').lstrip('0')

                # Log controlled audit event for rejected early attempt
                AuditLog.objects.create(
                    organization=employee.organization,
                    employee=request.user,
                    employeeName=f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email,
                    action="Clock-Out Attempt Rejected",
                    details=f"Clock-out rejected for {employee.first_name}: Session duration ({elapsed_seconds // 60}m {elapsed_seconds % 60}s) is below minimum required ({min_session_minutes}m)."
                )

                return Response({
                    'error': f"Minimum work session duration is {min_session_minutes} minutes. You clocked in at {clock_in_time_str}. You can clock out after {earliest_time_str}.",
                    'reason': 'minimum_session_not_reached',
                    'minimum_session_minutes': min_session_minutes,
                    'clock_in_time': log.clockIn.isoformat(),
                    'earliest_clock_out_time': earliest_clock_out.isoformat(),
                    'remaining_seconds': remaining_seconds,
                }, status=status.HTTP_400_BAD_REQUEST)

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
            organization=employee.organization,
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Clocked Out",
            details=f"Employee clocked out at {now.strftime('%H:%M:%S')}. Duration: {duration_str}."
        )

        serializer = self.get_serializer(log)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        target_date = serializer.validated_data.get('date')
        if is_date_locked(org, target_date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': f'Attendance for {target_date.strftime("%B %Y")} is finalized and locked. Reopen the period to make changes.'})
        super().perform_create(serializer)

    def perform_update(self, serializer):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        orig_date = serializer.instance.date
        new_date = serializer.validated_data.get('date', orig_date)
        if is_date_locked(org, orig_date) or is_date_locked(org, new_date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Attendance for this period is finalized and locked. Reopen the period to make changes.'})
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        if is_date_locked(org, instance.date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': f'Attendance for {instance.date.strftime("%B %Y")} is finalized and locked. Reopen the period to make changes.'})
        instance.is_deleted = True
        instance.save()


# --------------------------------------------------------------------------------
# AttendanceApprovalView: API endpoint for managers to approve or reject employee clock logs.
# --------------------------------------------------------------------------------
class AttendanceApprovalView(APIView):
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission]
    required_plan_feature = 'is_attendance_enabled'
    required_permission = ['attendance:admin', 'attendance:management_portal']

    ALLOWED_STATUSES = ['Approved', 'Late', 'Half Day', 'Absent', 'Pending Approval']

    def patch(self, request, pk):
        active_org = getattr(request, 'active_organization', None)
        if not active_org:
            return Response({'error': 'Attendance log not found.'}, status=status.HTTP_404_NOT_FOUND)
        from django.db.models import Q
        try:
            log = AttendanceLog.objects.filter(
                Q(pk=pk) & (
                    Q(employee__organization=active_org) |
                    Q(employee__memberships__organization=active_org, employee__memberships__is_active_in_org=True)
                )
            ).distinct().get()
        except AttendanceLog.DoesNotExist:
            return Response({'error': 'Attendance log not found.'}, status=status.HTTP_404_NOT_FOUND)

        if is_date_locked(active_org, log.date):
            return Response({'error': f'Attendance for {log.date.strftime("%B %Y")} is finalized and locked. Reopen the period to make changes.'}, status=status.HTTP_400_BAD_REQUEST)

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
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission]
    required_plan_feature = 'is_attendance_enabled'
    required_permission = ['attendance:admin', 'attendance:management_portal']

    def get(self, request):
        date_str = request.query_params.get('date')
        if date_str:
            try:
                today = datetime_date.fromisoformat(date_str)
            except ValueError:
                today = timezone.now().date()
        else:
            today = timezone.now().date()
        org = request.user.organization
        if not org:
            return Response({'error': 'User does not belong to an organization.'}, status=status.HTTP_400_BAD_REQUEST)

        policy = get_attendance_policy(org, today)
        grace_minutes = policy.grace_period_minutes if policy else 15

        all_employees = Employee.objects.filter(organization=org, is_active=True).order_by('first_name', 'last_name')

        pending_list = []
        late_list = []
        on_leave_list = []
        absent_list = []
        needs_review_list = []
        present_count = 0
        half_day_count = 0

        for emp in all_employees:
            summary = get_daily_attendance_summary(emp, today, policy=policy)

            if summary['daily_status'] == 'Present':
                present_count += 1
            elif summary['daily_status'] == 'Half Day':
                half_day_count += 1

            if summary['daily_status'] in ['Absent', 'Not Started']:
                absent_list.append({
                    'id': emp.id,
                    'employeeName': summary['employee_name'],
                    'employeeDesignation': emp.designation or '',
                })

            if summary['is_late']:
                late_list.append({
                    'id': emp.id,
                    'employeeName': summary['employee_name'],
                    'employeeDesignation': emp.designation or '',
                    'clockIn': summary['first_clock_in'],
                    'minutesLate': summary['minutes_late'],
                    'shiftStart': summary['shift_start'],
                    'status': summary['daily_status'],
                })

            if summary['has_pending_approval']:
                pending_logs = AttendanceLog.objects.filter(
                    employee=emp, date=today, status='Pending Approval', is_deleted=False
                )
                for pl in pending_logs:
                    pending_list.append({
                        'id': pl.id,
                        'employeeId': emp.id,
                        'employeeName': summary['employee_name'],
                        'employeeDesignation': emp.designation or '',
                        'clockIn': pl.clockIn.isoformat() if pl.clockIn else None,
                        'status': 'Pending Approval',
                    })

            if summary['leave_fraction'] > 0 or summary['daily_status'] == 'Leave':
                on_leave_list.append({
                    'id': emp.id,
                    'employeeName': summary['employee_name'],
                    'employeeDesignation': emp.designation or '',
                    'leaveTypeName': summary['leave_type'] or 'Leave',
                    'dayType': 'Half Day' if summary['leave_fraction'] == 0.5 else 'Full Day',
                })

            if not summary['is_payroll_ready']:
                needs_review_list.append({
                    'id': emp.id,
                    'employeeName': summary['employee_name'],
                    'employeeDesignation': emp.designation or '',
                    'conflictReason': summary['conflict_reason'],
                    'requiresAdminResolution': summary['requires_admin_resolution'],
                    'hasPendingApproval': summary['has_pending_approval'],
                    'dailyStatus': summary['daily_status'],
                })

        return Response({
            'date': today.isoformat(),
            'grace_period_minutes': grace_minutes,
            'pending': pending_list,
            'late': late_list,
            'on_leave': on_leave_list,
            'absent': absent_list,
            'needs_review': needs_review_list,
            'summary': {
                'pendingCount': len(pending_list),
                'lateCount': len(late_list),
                'onLeaveCount': len(on_leave_list),
                'absentCount': len(absent_list),
                'needsReviewCount': len(needs_review_list),
                'presentCount': present_count,
                'halfDayCount': half_day_count,
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
class HolidayViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Holiday.objects.all().order_by('date')
    serializer_class = HolidaySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = HolidayFilter

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['holidays:view', 'holidays:manage', 'attendance:staff']
        else:
            self.required_permission = ['holidays:manage']
        return super().get_permissions()


    def list(self, request, *args, **kwargs):
        import django.utils.timezone as dj_timezone

        # Get static holidays filtered by tenant and query params
        static_qs = self.filter_queryset(self.get_queryset())

        current_year = dj_timezone.now().year
        try:
            start_year = int(request.query_params.get('start_year', current_year - 1))
            end_year = int(request.query_params.get('end_year', current_year + 1))
        except ValueError:
            start_year = current_year - 1
            end_year = current_year + 1

        user = request.user
        active_org = getattr(request, 'active_organization', None) or (getattr(user, 'organization', None) if user and user.is_authenticated else None)
        dynamic_holidays = []
        if active_org:
            dynamic_holidays = calculate_recurring_holidays(active_org, start_year, end_year)

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

    def perform_create(self, serializer):
        user = self.request.user
        active_org = getattr(self.request, 'active_organization', None) or (getattr(user, 'organization', None) if user and user.is_authenticated else None)
        target_date = serializer.validated_data.get('date')
        if is_date_locked(active_org, target_date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Holidays cannot be created for a finalized attendance period.'})
        super().perform_create(serializer)

    def perform_update(self, serializer):
        user = self.request.user
        active_org = getattr(self.request, 'active_organization', None) or (getattr(user, 'organization', None) if user and user.is_authenticated else None)
        orig_date = serializer.instance.date
        new_date = serializer.validated_data.get('date', orig_date)
        if is_date_locked(active_org, orig_date) or is_date_locked(active_org, new_date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Holidays cannot be modified for a finalized attendance period.'})
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        user = self.request.user
        active_org = getattr(self.request, 'active_organization', None) or (getattr(user, 'organization', None) if user and user.is_authenticated else None)
        if is_date_locked(active_org, instance.date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Holidays cannot be deleted for a finalized attendance period.'})
        super().perform_destroy(instance)



# --------------------------------------------------------------------------------
# HolidaySettingsView: API view configuring recurring monthly and yearly holiday templates.
# --------------------------------------------------------------------------------
class HolidaySettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission]
    required_plan_feature = 'is_attendance_enabled'
    required_permission = 'holidays:manage'

    def _get_active_org(self, request):
        from core.tenant import TenantContext
        try:
            active_org = TenantContext.get_active_organization(request)
        except Exception:
            active_org = getattr(request, 'active_organization', None)
        if not active_org and request.user and request.user.is_authenticated:
            active_org = getattr(request.user, 'organization', None)
        return active_org

    def get(self, request):
        active_org = self._get_active_org(request)
        if not active_org:
            return Response({"error": "User does not belong to an organization."}, status=status.HTTP_400_BAD_REQUEST)

        settings = active_org.settings
        if not settings:
            settings = OrgSettings.objects.create()
            active_org.settings = settings
            active_org.save()

        return Response({
            "default_weekly_holidays": settings.default_weekly_holidays,
            "monthly_recurring_holidays": settings.monthly_recurring_holidays,
            "yearly_recurring_holidays": settings.yearly_recurring_holidays,
        }, status=status.HTTP_200_OK)

    @method_decorator(permission_required('holidays:manage'))
    def patch(self, request):
        active_org = self._get_active_org(request)
        if not active_org:
            return Response({"error": "User does not belong to an organization."}, status=status.HTTP_400_BAD_REQUEST)

        settings = active_org.settings
        if not settings:
            settings = OrgSettings.objects.create()
            active_org.settings = settings
            active_org.save()


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
class TemplateViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Template.objects.all().order_by('name')
    serializer_class = TemplateSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = TemplateFilter

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = None
        else:
            self.required_permission = 'admin:templates'
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user
        org = user.organization if (user.is_authenticated and hasattr(user, 'organization')) else None
        serializer.save(organization=org)


# --------------------------------------------------------------------------------
# OfficeLocationViewSet: ViewSet managing primary geofence latitude and longitude boundaries.
# --------------------------------------------------------------------------------
class OfficeLocationViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = OfficeLocation.objects.all().order_by('id')
    serializer_class = OfficeLocationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = OfficeLocationFilter

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = None
        else:
            self.required_permission = 'locations:manage'
        return super().get_permissions()




# --------------------------------------------------------------------------------
# ScheduleViewSet: ViewSet managing shift times mapped to specific roles.
# --------------------------------------------------------------------------------
class ScheduleViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Schedule.objects.all().order_by('designation')
    serializer_class = ScheduleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ScheduleFilter

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['attendance:staff', 'attendance:management_portal']
        else:
            self.required_permission = ['attendance:management_portal']
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user
        org = getattr(user, 'organization', None) if user.is_authenticated else None
        serializer.save(organization=org)


# --------------------------------------------------------------------------------
# AttendancePolicyViewSet: ViewSet managing versioned organization attendance policies.
# --------------------------------------------------------------------------------
class AttendancePolicyViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = AttendancePolicy.objects.all().order_by('-effective_from')
    serializer_class = AttendancePolicySerializer

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'current_policy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'current_policy'] and self.request.method == 'GET':
            self.required_permission = None
        else:
            self.required_permission = ['attendance:management_portal', 'attendance:admin']
        return super().get_permissions()

    @action(detail=False, methods=['get', 'post', 'put', 'patch'], url_path='current')
    def current_policy(self, request):
        org = getattr(request.user, 'organization', None)
        if not org:
            return Response({'error': 'User does not belong to an organization.'}, status=status.HTTP_400_BAD_REQUEST)

        if request.method in ['POST', 'PUT', 'PATCH']:
            policy, created = save_attendance_policy(org, request.data)
            serializer = self.get_serializer(policy)
            return Response({
                **serializer.data,
                'message': 'These attendance rules will apply from tomorrow.',
                'is_new_version': created
            }, status=status.HTTP_200_OK)

        policy = get_attendance_policy(org)
        serializer = self.get_serializer(policy)
        return Response(serializer.data, status=status.HTTP_200_OK)



# --------------------------------------------------------------------------------
# OrgSettingsViewSet: ViewSet managing branding logos and custom attendance validation tolerances.
# --------------------------------------------------------------------------------
class OrgSettingsViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    queryset = OrgSettings.objects.all()
    serializer_class = OrgSettingsSerializer

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission],
        'current_settings': [permissions.IsAuthenticated, DRFCheckModePermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'current_settings'] and self.request.method == 'GET':
            self.required_permission = None
        else:
            self.required_permission = ['settings:branding', 'settings:billing', 'attendance:management_portal']
        return super().get_permissions()

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
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission]
    required_permission = 'audit_logs:view'

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
            search_q = self.request.query_params.get('search')

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
            if search_q:
                qs = qs.filter(
                    Q(action__icontains=search_q) |
                    Q(details__icontains=search_q) |
                    Q(employeeName__icontains=search_q) |
                    Q(employee__email__icontains=search_q) |
                    Q(employee__first_name__icontains=search_q) |
                    Q(employee__last_name__icontains=search_q)
                )
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
class LeaveTypeViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = LeaveType.objects.all().order_by('-id')
    serializer_class = LeaveTypeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LeaveTypeFilter

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
    }

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['leaves:apply', 'leaves:manage']
        else:
            self.required_permission = ['leaves:manage']
        return super().get_permissions()

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
class LeaveViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Leave.objects.all().order_by('-id')
    serializer_class = LeaveSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LeaveFilter

    required_plan_feature = 'is_attendance_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
        'update_status': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, IsLeaveOwnerOrManager],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated:
            from core.decorators import has_fine_grained_permission
            if not has_fine_grained_permission(user, ['leaves:approve', 'leaves:manage']):
                qs = qs.filter(employee=user)
        return qs

    def get_permissions(self):
        return super().get_permissions()



    def perform_create(self, serializer):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        start_date = serializer.validated_data.get('startDate')
        end_date = serializer.validated_data.get('endDate', start_date)
        if is_date_range_locked(org, start_date, end_date):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Leave cannot be applied for a finalized attendance period.'})
        leave = serializer.save()
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Leave Applied",
            details=f"Applied for {leave.leaveTypeName} leave from {leave.startDate} to {leave.endDate} ({leave.duration} days)."
        )

    def perform_update(self, serializer):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        orig_start = serializer.instance.startDate
        orig_end = serializer.instance.endDate
        new_start = serializer.validated_data.get('startDate', orig_start)
        new_end = serializer.validated_data.get('endDate', orig_end)
        if is_date_range_locked(org, orig_start, orig_end) or is_date_range_locked(org, new_start, new_end):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Leave cannot be modified for a finalized attendance period.'})
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        if is_date_range_locked(org, instance.startDate, instance.endDate):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Leave cannot be deleted for a finalized attendance period.'})
        super().perform_destroy(instance)

    @action(detail=True, methods=['patch'], url_path='status')
    def update_status(self, request, pk=None):
        leave = self.get_object()
        user = self.request.user
        org = user.organization if user.is_authenticated else None
        if is_date_range_locked(org, leave.startDate, leave.endDate):
            return Response({'error': 'Leave status cannot be modified for a finalized attendance period.'}, status=status.HTTP_400_BAD_REQUEST)

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


# --------------------------------------------------------------------------------
# DailyAttendanceSummaryView: Authoritative Daily Attendance Calculation API
# --------------------------------------------------------------------------------
class DailyAttendanceSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired]
    required_plan_feature = 'is_attendance_enabled'

    def get(self, request):
        user = request.user
        emp_id = request.query_params.get('employee_id')
        date_str = request.query_params.get('date')
        month_str = request.query_params.get('month')

        if not user.organization:
            return Response({'error': 'User does not belong to an organization.'}, status=status.HTTP_400_BAD_REQUEST)

        # Tenant-safe employee resolution
        if emp_id and str(emp_id) != str(user.id):
            emp = Employee.objects.filter(id=emp_id, organization=user.organization, is_active=True).first()
            if not emp:
                return Response({'error': 'Employee not found in your organization.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            emp = user

        if month_str:
            try:
                y, m = map(int, month_str.split('-'))
                summaries = get_monthly_attendance_summary(emp, y, m)
                return Response(summaries, status=status.HTTP_200_OK)
            except Exception as e:
                return Response({'error': f'Invalid month format. Expected YYYY-MM.'}, status=status.HTTP_400_BAD_REQUEST)

        if not date_str:
            target_date = timezone.now().date()
        else:
            try:
                target_date = datetime_date.fromisoformat(date_str)
            except Exception:
                return Response({'error': 'Invalid date format. Expected YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        summary = get_daily_attendance_summary(emp, target_date)
        return Response(summary, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# AttendancePeriodViewSet: Monthly Attendance Finalization, Validation, & Snapshots
# --------------------------------------------------------------------------------
class AttendancePeriodViewSet(ActionPermissionMixin, FilterMixinNew, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = AttendancePeriod.objects.all().order_by('-year', '-month')
    serializer_class = AttendancePeriodSerializer
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired]
    required_plan_feature = 'is_attendance_enabled'

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'period_summary': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'snapshots': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'finalize': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
        'reopen': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired],
    }

    def get_queryset(self):
        qs = super().get_queryset().filter(is_deleted=False)
        user = self.request.user
        if user.is_authenticated and user.organization:
            qs = qs.filter(organization=user.organization)
        return qs

    @action(detail=False, methods=['get'], url_path='summary')
    def period_summary(self, request):
        """
        GET /api/attendance/periods/summary/?year=2026&month=8
        Returns period record, validation status, readiness counts, and blocking issues list.
        """
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if not year or not month:
            return Response({'error': 'year and month query parameters are required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year = int(year)
            month = int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month.'}, status=status.HTTP_400_BAD_REQUEST)

        org = request.user.organization
        period = AttendancePeriod.objects.filter(
            organization=org,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        validation = validate_attendance_period(org, year, month)

        period_data = AttendancePeriodSerializer(period).data if period else {
            'year': year,
            'month': month,
            'status': 'Draft',
            'current_revision': 0,
            'total_employees': validation['total_employees'],
            'payroll_ready_count': validation['payroll_ready_count'],
            'needs_review_count': validation['needs_review_count'],
            'finalized_at': None,
            'finalized_by': None,
            'finalized_by_name': None,
            'reopened_at': None,
            'reopened_by_name': None,
            'reopen_reason': None,
        }

        return Response({
            'period': period_data,
            'validation': {
                'is_clean': validation['is_clean'],
                'is_past_month': validation['is_past_month'],
                'total_employees': validation['total_employees'],
                'payroll_ready_count': validation['payroll_ready_count'],
                'needs_review_count': validation['needs_review_count'],
                'issues': validation['issues']
            }
        })

    @action(detail=False, methods=['post'], url_path='finalize')
    def finalize(self, request):
        """
        POST /api/attendance/periods/finalize/
        Body: { "year": 2026, "month": 7 }
        """
        from core.decorators import has_fine_grained_permission
        is_admin = has_fine_grained_permission(request.user, ['attendance:admin'])
        if not is_admin:
            return Response({'error': 'You do not have permission to finalize attendance periods.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = FinalizePeriodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        year = serializer.validated_data['year']
        month = serializer.validated_data['month']

        org = request.user.organization
        period = finalize_attendance_period(org, year, month, request.user)
        return Response(AttendancePeriodSerializer(period).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='reopen')
    def reopen(self, request):
        """
        POST /api/attendance/periods/reopen/
        Body: { "year": 2026, "month": 7, "reason": "Corrected Alice overtime punch" }
        """
        from core.decorators import has_fine_grained_permission
        is_admin = has_fine_grained_permission(request.user, ['attendance:admin'])
        if not is_admin:
            return Response({'error': 'You do not have permission to reopen attendance periods.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReopenPeriodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data['reason']

        year = request.data.get('year')
        month = request.data.get('month')

        if not year or not month:
            return Response({'error': 'year and month are required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year = int(year)
            month = int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month.'}, status=status.HTTP_400_BAD_REQUEST)

        org = request.user.organization
        period = reopen_attendance_period(org, year, month, request.user, reason)
        return Response(AttendancePeriodSerializer(period).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='snapshots')
    def snapshots(self, request):
        """
        GET /api/attendance/periods/snapshots/?year=2026&month=7&revision=1
        Returns employee snapshots.
        """
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        revision = request.query_params.get('revision')
        employee_id = request.query_params.get('employee_id')

        if not year or not month:
            return Response({'error': 'year and month query parameters are required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year = int(year)
            month = int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month.'}, status=status.HTTP_400_BAD_REQUEST)

        org = request.user.organization
        period = AttendancePeriod.objects.filter(
            organization=org,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period:
            return Response([], status=status.HTTP_200_OK)

        qs = AttendancePeriodEmployeeSnapshot.objects.filter(
            attendance_period=period,
            is_deleted=False
        )

        if revision:
            try:
                rev_num = int(revision)
                qs = qs.filter(revision=rev_num)
            except ValueError:
                pass
        else:
            qs = qs.filter(is_current=True)

        user = request.user
        from core.decorators import has_fine_grained_permission
        can_view_all = has_fine_grained_permission(user, ['attendance:admin', 'attendance:management_portal'])
        if not can_view_all:
            qs = qs.filter(employee=user)
        elif employee_id:
            qs = qs.filter(employee_id=employee_id)

        serializer = AttendancePeriodEmployeeSnapshotSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)




# Misc backoffice HTML views
"""
api/views/misc.py — Backoffice HTML views and Stripe webhook
"""



# Backoffice views and webhook have been migrated to users/views.py and billing/views.py
