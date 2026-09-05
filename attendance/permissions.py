from rest_framework import permissions

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
            from core.decorators import has_fine_grained_permission
            return has_fine_grained_permission(user, ['leaves:apply', 'leaves:manage'])

        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        from core.decorators import has_fine_grained_permission

        # Managing/Approving leaves
        if view.action in ['update', 'partial_update']:
            return has_fine_grained_permission(user, ['leaves:approve', 'leaves:manage'])

        # Cancelling leaves (deleting)
        if view.action == 'destroy':
            # Owners can cancel their own pending leaves
            if obj.employee == user and obj.status == 'Pending':
                return True
            return has_fine_grained_permission(user, ['leaves:approve', 'leaves:manage'])

        return False
