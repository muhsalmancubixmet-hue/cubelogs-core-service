from rest_framework import permissions

class HasRequiredPermission(permissions.BasePermission):
    """
    Custom permission class to validate user permissions in DRF views.
    Expects `required_permission` attribute on the ViewSet/APIView.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        required_permission = getattr(view, 'required_permission', None)
        if not required_permission:
            return True

        user_perms = getattr(user, 'permissions', [])
        if not isinstance(user_perms, list):
            user_perms = []

        if isinstance(required_permission, (list, tuple)):
            return any(p in user_perms for p in required_permission)
        return required_permission in user_perms


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


class IsLeaveOwnerOrManager(permissions.BasePermission):
    """
    Custom permission class for Leave management.
    - Owners can create leaves and cancel (destroy) their own leaves if still pending.
    - Approvers/Admins can list, retrieve, and update (approve/reject).
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        if view.action in ['list', 'retrieve']:
            return True

        if view.action == 'create':
            user_perms = getattr(user, 'permissions', [])
            return 'leaves:apply' in user_perms

        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        user_perms = getattr(user, 'permissions', [])

        # Managing/Approving leaves
        if view.action in ['update', 'partial_update']:
            return 'leaves:approve' in user_perms or 'leaves:manage' in user_perms

        # Cancelling leaves (deleting)
        if view.action == 'destroy':
            # Owners can cancel their own pending leaves
            if obj.employee == user and obj.status == 'Pending':
                return True
            return 'leaves:approve' in user_perms or 'leaves:manage' in user_perms

        return False
