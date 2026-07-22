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
from core.permissions import ActionPermissionMixin, DRFCheckModePermission, DRFPlanPermissionRequired
from tasks.models import Task
from tasks.filters import TaskFilter
from tasks.api.v1.serializers import TaskSerializer


# --------------------------------------------------------------------------------
# TaskViewSet: ViewSet managing assignment, details, and status updates of user tasks.
# --------------------------------------------------------------------------------
class TaskViewSet(ActionPermissionMixin, FilterMixinNew, viewsets.ModelViewSet):
    queryset = Task.objects.all().order_by('-id')
    serializer_class = TaskSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = TaskFilter

    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission], # placeholder will be replaced by IsTaskOwnerOrManager
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, permissions.BasePermission],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated:
            qs = qs.filter(assignedTo__organization=user.organization)
            if not (user.is_superuser or getattr(user, 'isSuperAdmin', False)):
                user_perms = getattr(user, 'permissions', [])
                if 'tasks:create' not in user_perms:
                    qs = qs.filter(assignedTo=user)
        return qs

    def get_permissions(self):
        from tasks.permissions import IsTaskOwnerOrManager
        # Replace the placeholder BasePermission with the actual IsTaskOwnerOrManager class
        self.permission_classes_by_action = {
            k: [IsTaskOwnerOrManager if p == permissions.BasePermission else p for p in v]
            for k, v in self.permission_classes_by_action.items()
        }
        return super().get_permissions()


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
