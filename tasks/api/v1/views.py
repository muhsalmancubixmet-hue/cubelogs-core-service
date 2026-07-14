# --------------------------------------------------------------------------------
#       Tasks Views
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import viewsets, permissions
from django_filters.rest_framework import DjangoFilterBackend

# APPLICATION SPECIFIC
from core.models import AuditLog
from core.mixins import FilterMixinNew
from tasks.models import Task
from tasks.filters import TaskFilter
from tasks.api.v1.serializers import TaskSerializer


# --------------------------------------------------------------------------------
# TaskViewSet: ViewSet managing assignment, details, and status updates of user tasks.
# --------------------------------------------------------------------------------
class TaskViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = Task.objects.all().order_by('-id')
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = TaskFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(assignedTo__organization=self.request.user.organization)
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
