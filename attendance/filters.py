# THIRD PARTY
from django.db import models
import django_filters as filters

# APPLICATION SPECIFIC
from attendance.models import AttendanceLog, Schedule, LeaveType, Leave, Holiday, OfficeLocation


class AttendanceLogFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    month = filters.NumberFilter(field_name='date__month')
    year = filters.NumberFilter(field_name='date__year')
    start_date = filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='date', lookup_expr='lte')
    employee = filters.NumberFilter(field_name='employee_id')
    employee_id = filters.NumberFilter(field_name='employee_id')
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('date', 'date'),
            ('status', 'status'),
        )
    )

    class Meta:
        model = AttendanceLog
        fields = [
            'id',
            'employee',
            'employee_id',
            'date',
            'status',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(employeeName__icontains=value) |
            models.Q(employee__first_name__icontains=value) |
            models.Q(employee__last_name__icontains=value) |
            models.Q(employee__email__icontains=value)
        )


class ScheduleFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('designation', 'designation'),
        )
    )

    class Meta:
        model = Schedule
        fields = [
            'id',
            'designation',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(designation__icontains=value)
        )


class LeaveTypeFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
        )
    )

    class Meta:
        model = LeaveType
        fields = [
            'id',
            'organization',
            'status',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value) |
            models.Q(description__icontains=value)
        )


class LeaveFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    date = filters.DateFilter(method="filter_single_date")
    start_date = filters.DateFilter(field_name='startDate', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='endDate', lookup_expr='lte')
    year = filters.NumberFilter(field_name='startDate__year')
    month = filters.NumberFilter(field_name='startDate__month')
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('startDate', 'startDate'),
            ('endDate', 'endDate'),
            ('duration', 'duration'),
            ('status', 'status'),
        )
    )

    class Meta:
        model = Leave
        fields = [
            'id',
            'employee',
            'leaveType',
            'status',
            'dayType',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(employeeName__icontains=value) |
            models.Q(leaveTypeName__icontains=value) |
            models.Q(reason__icontains=value) |
            models.Q(employee__first_name__icontains=value) |
            models.Q(employee__last_name__icontains=value) |
            models.Q(employee__email__icontains=value)
        )

    def filter_single_date(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(startDate__lte=value, endDate__gte=value)



class HolidayFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
            ('date', 'date'),
        )
    )

    class Meta:
        model = Holiday
        fields = [
            'id',
            'organization',
            'date',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value) |
            models.Q(description__icontains=value)
        )


class OfficeLocationFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
        )
    )

    class Meta:
        model = OfficeLocation
        fields = [
            'id',
            'organization',
            'isPrimary',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value)
        )
