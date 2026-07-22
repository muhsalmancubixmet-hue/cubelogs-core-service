from rest_framework import permissions

def check_backoffice_staff_management(view, request):
    """
    Validates if a backoffice staff operator has the 'staff' permission flag.
    Preserves existing DRF permissions check sequence.
    """
    user = request.user
    if user.is_authenticated and getattr(user, 'isSuperAdmin', False) and user.organization is None:
        if user.is_superuser:
            return
        user_perms = getattr(user, 'permissions', [])
        if not isinstance(user_perms, list) or 'staff' not in user_perms:
            view.permission_denied(
                request,
                message="You do not have permission to manage backoffice staff."
            )
