"""
api/views/organization.py — Organization, OrgSettings, Schedule, Template, OfficeLocation, AuditLog views
"""
from datetime import datetime

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import (
    Employee, AuditLog, Template, OfficeLocation, Schedule,
    OrgSettings, SubscriptionPackage, SubscriberAccount,
    Holiday, Organization, PERMISSION_FLAGS, Task
)
from api.serializers import (
    TemplateSerializer, OfficeLocationSerializer, ScheduleSerializer,
    OrgSettingsSerializer, AuditLogSerializer, HolidaySerializer, TaskSerializer
)


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

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all().order_by('-id')
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(assignedTo__organization=self.request.user.organization)
        assigned_to = self.request.query_params.get('assigned_to')
        task_status = self.request.query_params.get('status')
        if assigned_to:
            qs = qs.filter(assignedTo_id=assigned_to)
        if task_status:
            qs = qs.filter(status=task_status)
        return qs

    def perform_create(self, serializer):
        task = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Task Created",
            details=f"Created task '{task.title}' assigned to {task.assignedName}."
        )

    def perform_update(self, serializer):
        task = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Task Updated",
            details=f"Updated task '{task.title}' (Status: {task.status})."
        )

    def perform_destroy(self, instance):
        title = instance.title
        assigned = instance.assignedName
        instance.delete()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Task Deleted",
            details=f"Deleted task '{title}' assigned to {assigned}."
        )


class HolidayViewSet(viewsets.ModelViewSet):
    queryset = Holiday.objects.all().order_by('date')
    serializer_class = HolidaySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        qs = super().get_queryset()
        if self.request.user.is_authenticated and self.request.user.organization:
            qs = qs.filter(Q(organization=self.request.user.organization) | Q(organization__isnull=True))
        return qs

    def perform_create(self, serializer):
        if self.request.user.is_authenticated and self.request.user.organization:
            serializer.save(organization=self.request.user.organization)
        else:
            serializer.save()

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


class TemplateViewSet(viewsets.ModelViewSet):
    queryset = Template.objects.all().order_by('name')
    serializer_class = TemplateSerializer
    permission_classes = [permissions.IsAuthenticated]


class OfficeLocationViewSet(viewsets.ModelViewSet):
    queryset = OfficeLocation.objects.all().order_by('id')
    serializer_class = OfficeLocationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated and self.request.user.organization:
            qs = qs.filter(organization=self.request.user.organization)
        return qs

    def perform_create(self, serializer):
        if self.request.user.is_authenticated and self.request.user.organization:
            serializer.save(organization=self.request.user.organization)
        else:
            serializer.save()


class ScheduleViewSet(viewsets.ModelViewSet):
    queryset = Schedule.objects.all().order_by('designation')
    serializer_class = ScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]


class OrgSettingsViewSet(viewsets.ModelViewSet):
    queryset = OrgSettings.objects.all()
    serializer_class = OrgSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]

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


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all().order_by('-createdAt', '-id')
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and user.organization is not None:
            qs = qs.filter(employee__organization=user.organization)
        if not user.isSuperAdmin:
            qs = qs.filter(employee=user)
        else:
            employee_id = self.request.query_params.get('employee_id')
            action_type = self.request.query_params.get('action')
            date_str = self.request.query_params.get('date')
            org_id = self.request.query_params.get('organization_id')

            if org_id and user.organization is None:
                qs = qs.filter(employee__organization_id=org_id)
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


class PermissionsConfigView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        import os
        import json
        from django.conf import settings
        file_path = os.path.join(str(settings.BASE_DIR), 'api', 'permissions.json')
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
            return Response(data, status=status.HTTP_200_OK)
        return Response({"error": "Config not found"}, status=status.HTTP_404_NOT_FOUND)
