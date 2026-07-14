# THIRD PARTY
from django.db import models
import django_filters as filters

# APPLICATION SPECIFIC
from users.models import Employee, Template


class EmployeeFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('email', 'email'),
            ('first_name', 'first_name'),
            ('last_name', 'last_name'),
            ('designation', 'designation'),
        )
    )

    class Meta:
        model = Employee
        fields = [
            'id',
            'email',
            'phone',
            'designation',
            'isSuperAdmin',
            'organization',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(first_name__icontains=value) |
            models.Q(last_name__icontains=value) |
            models.Q(email__icontains=value) |
            models.Q(phone__icontains=value) |
            models.Q(designation__icontains=value) |
            models.Q(organization__name__icontains=value)
        )


class TemplateFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
        )
    )

    class Meta:
        model = Template
        fields = [
            'id',
            'name',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value)
        )
