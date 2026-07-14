# STANDARD LIBRARY
import json
import time
from functools import wraps

# DJANGO
from django.db import connection
from django.http.response import HttpResponse
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import AccessMixin

# THIRD PARTY
from rest_framework import status
from rest_framework.response import Response


def _permission_denied_response(request):
    """Helper to return standardized 401 response for API/AJAX requests."""
    response_data = {
        "StatusCode": 6001,
        'message': {
            'title': 'Permission Denied',
            'body': 'You do not have the necessary permissions to perform this action',
        },
    }
    return HttpResponse(
        json.dumps(response_data),
        status=status.HTTP_401_UNAUTHORIZED,
        content_type="application/json",
    )


# --------------------------------------------------------------------------------
# 1. Authorization Decorators
# --------------------------------------------------------------------------------

def permission_required(*permissions):
    """
    Decorator to check user fine-grained permissions stored in request.user.permissions JSONField.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            if not (user and user.is_authenticated):
                return _permission_denied_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False):
                return view_func(request, *args, **kwargs)

            user_perms = getattr(user, 'permissions', [])
            if not isinstance(user_perms, list):
                user_perms = []

            # Check if user has any of the required permissions
            if any(p in user_perms for p in permissions):
                return view_func(request, *args, **kwargs)

            return _permission_denied_response(request)
        return _wrapped_view
    return decorator


def role_required(*permissions):
    """
    Decorator to check Django auth permissions (user_permissions/group permissions).
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            if not (user and user.is_authenticated):
                return _permission_denied_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False):
                return view_func(request, *args, **kwargs)

            # Check Django codenames in user_permissions
            if user.user_permissions.filter(codename__in=permissions).exists():
                return view_func(request, *args, **kwargs)

            return _permission_denied_response(request)
        return _wrapped_view
    return decorator


def group_required(*group_names):
    """
    Decorator to check whether the given user group exists or not.
    """
    def in_groups(u):
        if u.is_authenticated:
            if u.is_superuser or getattr(u, 'isSuperAdmin', False) or u.groups.filter(name__in=group_names).exists():
                return True
        return False
    return user_passes_test(in_groups)


def user_access_required(*group_names):
    """
    Decorator to verify if the user belongs to at least one of the specified groups,
    returning a JSON/JS 401 response when validation fails instead of redirecting.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            if not (user and user.is_authenticated):
                return _permission_denied_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False) or user.groups.filter(name__in=group_names).exists():
                return view_func(request, *args, **kwargs)

            return _permission_denied_response(request)
        return _wrapped_view
    return decorator


def get_user_plan_features(user):
    """Helper to query the user's/organization's subscription features."""
    if not user or not user.is_authenticated:
        return []
    
    # Superuser has all features
    if user.is_superuser or getattr(user, 'isSuperAdmin', False):
        from users.models import PERMISSION_FLAGS
        return [p['id'] for p in PERMISSION_FLAGS]

    target_email = user.email
    if user.organization:
        from users.models import Employee
        superadmin = Employee.objects.filter(organization=user.organization, isSuperAdmin=True).first()
        if superadmin:
            target_email = superadmin.email

    from subscribers.models import SubscriberAccount, SubscriptionPackage
    sub = SubscriberAccount.objects.filter(email=target_email, isActive=True).first()
    packageName = sub.packageName if sub else "Free Package"
    pkg = SubscriptionPackage.objects.filter(name=packageName).first()
    return pkg.features if pkg else []


def plan_permission_required(*features):
    """
    Decorator to check subscription/plan based permissions.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            if not (user and user.is_authenticated):
                return _permission_denied_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False):
                return view_func(request, *args, **kwargs)

            plan_features = get_user_plan_features(user)
            if any(f in plan_features for f in features):
                return view_func(request, *args, **kwargs)

            response_data = {
                "StatusCode": 6001,
                'message': {
                    'title': 'Plan Restriction',
                    'body': 'Your current subscription plan does not support this feature.',
                },
            }
            return HttpResponse(
                json.dumps(response_data),
                status=status.HTTP_401_UNAUTHORIZED,
                content_type="application/json",
            )
        return _wrapped_view
    return decorator


class PermissionRequiredMixin(AccessMixin):
    """
    Class-based view mixin to check Django user permissions.
    """
    permissions = []

    def check_permissions(self, request):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False):
            return True
        user_permissions = request.user.user_permissions.all()
        for permission in self.permissions:
            if not user_permissions.filter(codename=permission).exists():
                return False
        return True

    def dispatch(self, request, *args, **kwargs):
        if not self.check_permissions(request):
            response_data = {
                "StatusCode": 6001,
                'data': {
                    'title': 'Permission Denied',
                    'message': 'You do not have the necessary permissions to perform this action',
                }
            }
            return Response(response_data, status=status.HTTP_401_UNAUTHORIZED)
        return super().dispatch(request, *args, **kwargs)


# --------------------------------------------------------------------------------
# 2. Mode / Request Validation Decorators
# --------------------------------------------------------------------------------

def check_mode(function):
    """
    Decorator to check whether the application is down, in read_only or maintenance mode.
    """
    def is_ajax(request):
        return (
            request.headers.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
            or request.path.startswith('/api/')
            or 'application/json' in request.META.get('HTTP_ACCEPT', '')
        )

    @wraps(function)
    def wrap(request, *args, **kwargs):
        from core.models import Mode
        mode, created = Mode.objects.get_or_create(id=1)
        down = mode.down
        readonly = mode.readonly
        maintenance = mode.maintenance

        if down:
            if is_ajax(request):
                response_data = {
                    "status": "false",
                    "message": "Application currently down. Please try again later.",
                    "static_message": "true"
                }
                return HttpResponse(json.dumps(response_data), content_type="application/json", status=503)
            else:
                return HttpResponse("<h1>503 Service Temporarily Unavailable</h1><p>Application is currently down for maintenance. Please try again later.</p>", status=503)

        elif readonly:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if is_ajax(request):
                    response_data = {
                        "status": "false",
                        "message": "Application now in readonly mode. Writes are disabled.",
                        "static_message": "true"
                    }
                    return HttpResponse(json.dumps(response_data), content_type="application/json", status=403)
                else:
                    return HttpResponse("<h1>403 Forbidden</h1><p>Application is currently in read-only mode. Writes are disabled.</p>", status=403)

        elif maintenance:
            if is_ajax(request):
                response_data = {
                    "status": "false",
                    "message": "Application now in maintenance mode. Please try again later.",
                    "static_message": "true"
                }
                return HttpResponse(json.dumps(response_data), content_type="application/json", status=503)
            else:
                return HttpResponse("<h1>503 Service Under Maintenance</h1><p>Application is undergoing scheduled maintenance. Please try again later.</p>", status=503)

        return function(request, *args, **kwargs)

    return wrap


def ajax_required(function):
    """
    Decorator to restrict requests to AJAX queries only.
    """
    @wraps(function)
    def wrap(request, *args, **kwargs):
        is_ajax = request.headers.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        if not is_ajax:
            response_data = {
                "status": "false",
                "message": "AJAX request required."
            }
            return HttpResponse(json.dumps(response_data), content_type="application/json", status=400)
        return function(request, *args, **kwargs)
    return wrap


# --------------------------------------------------------------------------------
# 3. Performance Monitoring Decorator
# --------------------------------------------------------------------------------

def timer(func):
    """
    Helper decorator to estimate view execution time and count database queries.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        len_start_queries = len(connection.queries)

        result = func(*args, **kwargs)

        duration = time.time() - start
        len_end_queries = len(connection.queries)

        duplicate_count = 0
        check_duplicates = set()
        for query in connection.queries[len_start_queries:len_end_queries]:
            sql = query["sql"]
            if sql in check_duplicates:
                duplicate_count += 1
            else:
                check_duplicates.add(sql)

        print(f"[DEBUG TIMER] {func.__name__} executed in {duration:.4f}s ({duration*1000:.2f}ms) with {len_end_queries - len_start_queries} queries (Duplicate Queries: {duplicate_count})")
        return result
    return wrapper
