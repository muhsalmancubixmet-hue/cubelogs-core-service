from rest_framework import permissions

class CanViewPackagesOrSuperAdmin(permissions.BasePermission):
    """
    Custom permission class to allow users with settings:billing permission
    or SuperAdmin status to view packages.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if getattr(request.user, 'isSuperAdmin', False):
            return True
        user_perms = getattr(request.user, 'permissions', [])
        return 'settings:billing' in user_perms
