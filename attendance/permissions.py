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
