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


class IsSuperAdminUser(permissions.BasePermission):
    """
    Common permission class for SuperAdmin and Backoffice operators.
    Applies shared permission verification rules for Subscribers and Company.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and getattr(request.user, 'isSuperAdmin', False)):
            return False

        # Client superadmin — belongs to an organisation
        if request.user.organization is not None:
            return True

        # Root admin
        if request.user.is_superuser:
            return True

        # Backoffice operators — enforce page-level permissions
        user_perms = getattr(request.user, 'permissions', [])
        if not isinstance(user_perms, list):
            user_perms = []

        all_backoffice_perms = [
            'packages', 'subscribers', 'leads', 'cms', 'faqs',
            'testimonials', 'coupons', 'staff', 'audit_logs', 'billing_settings',
        ]
        if not any(p in user_perms for p in all_backoffice_perms):
            user_perms = all_backoffice_perms

        path = request.path
        if 'packages' in path:
            return 'packages' in user_perms
        elif 'subscribers' in path:
            return 'subscribers' in user_perms
        elif 'leads' in path:
            return 'leads' in user_perms
        elif 'cms' in path:
            # Allow reading CMS/FAQs if they have either cms or faqs permission
            if request.method == 'GET':
                return 'cms' in user_perms or 'faqs' in user_perms
            
            # For CMS writes, determine if they are updating the FAQ copy block
            try:
                if isinstance(request.data, dict) and request.data.get('key') == 'faqs':
                    return 'faqs' in user_perms
            except Exception:
                pass
            return 'cms' in user_perms
        elif 'faqs' in path:
            return 'faqs' in user_perms
        elif 'testimonials' in path:
            return 'testimonials' in user_perms
        elif 'lms' in path:
            return 'lms' in user_perms
        elif 'coupons' in path:
            return 'coupons' in user_perms
        elif 'employees' in path:
            return 'staff' in user_perms
        elif 'audit-logs' in path:
            return 'audit_logs' in user_perms
        elif 'billing-settings' in path:
            return 'billing_settings' in user_perms

        return True


from rest_framework import exceptions

class DRFCheckModePermission(permissions.BasePermission):
    """
    DRF permission class to enforce check_mode logic.
    Raises 503 Service Unavailable for maintenance/down states and 403 Forbidden for readonly states.
    """
    def has_permission(self, request, view):
        from core.decorators import get_system_mode_status
        status_mode = get_system_mode_status(request)
        if status_mode in ['down', 'maintenance']:
            class ServiceUnavailable(exceptions.APIException):
                status_code = 503
                default_detail = f"Application currently in {status_mode} mode. Please try again later."
                default_code = 'service_unavailable'
            raise ServiceUnavailable()
        if status_mode == 'readonly':
            class ReadOnlyMode(exceptions.APIException):
                status_code = 403
                default_detail = "Application now in readonly mode. Writes are disabled."
                default_code = 'readonly_mode'
            raise ReadOnlyMode()
        return True


class DRFPlanPermissionRequired(permissions.BasePermission):
    """
    DRF permission class to validate user plan features.
    Expects `required_plan_feature` view attribute.
    """
    def has_permission(self, request, view):
        required = getattr(view, 'required_plan_feature', None)
        if not required:
            return True
        from core.decorators import has_plan_feature
        if not has_plan_feature(request.user, required):
            class PlanRestriction(exceptions.APIException):
                status_code = 403
                default_detail = "Your current subscription plan does not support this feature."
                default_code = 'plan_restriction'
            raise PlanRestriction()
        return True


class ActionPermissionMixin:
    """
    Mixin for ViewSets to map list, retrieve, create, update, partial_update, destroy actions
    to action-specific permission classes defined in `permission_classes_by_action`.
    """
    def get_permissions(self):
        try:
            return [permission() for permission in self.permission_classes_by_action[self.action]]
        except (KeyError, AttributeError):
            return super().get_permissions()

