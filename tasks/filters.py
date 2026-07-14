# THIRD PARTY
from django.db import models
import django_filters as filters

# APPLICATION SPECIFIC
from tasks.models import Task


class TaskFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('dueDate', 'dueDate'),
            ('status', 'status'),
        )
    )

    class Meta:
        model = Task
        fields = [
            'id',
            'assignedTo',
            'status',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(title__icontains=value) |
            models.Q(description__icontains=value) |
            models.Q(assignedName__icontains=value) |
            models.Q(assignedTo__first_name__icontains=value) |
            models.Q(assignedTo__last_name__icontains=value) |
            models.Q(assignedTo__email__icontains=value)
        )
