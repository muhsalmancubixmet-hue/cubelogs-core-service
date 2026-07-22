from rest_framework import permissions

class IsTaskOwnerOrManager(permissions.BasePermission):
    """
    Custom permission class for Task management.
    - Managers/Admins with tasks:create can perform all actions.
    - Regular employees can view/retrieve their own assigned tasks,
      and only edit the status of their assigned tasks.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        # For list/retrieve, the queryset filtering itself restricts access.
        if view.action in ['list', 'retrieve']:
            return True

        # Creating or deleting tasks requires task workspace creator permission
        if view.action in ['create', 'destroy']:
            user_perms = getattr(user, 'permissions', [])
            return 'tasks:create' in user_perms

        # For updates, we do instance-level checks (handled in has_object_permission)
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        user_perms = getattr(user, 'permissions', [])
        if 'tasks:create' in user_perms:
            return True

        # Non-managers can only edit status of their own assigned tasks
        if obj.assignedTo == user and view.action in ['update', 'partial_update']:
            # Inspect the request data. They should only change 'status'.
            updated_fields = set(request.data.keys())
            # Allow updates if 'status' is the only field or no fields are updated
            if updated_fields.issubset({'status'}):
                return True

        return False
