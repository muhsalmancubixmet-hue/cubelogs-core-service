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

def _find_request(*args, **kwargs):
    """
    Find the request object among positional arguments or keyword arguments.
    Supports FBVs, CBV methods, kwargs request, Django HttpRequest, and DRF Request.
    Raises ValueError if no request object with a 'user' attribute is found.
    """
    for arg in args:
        if hasattr(arg, 'user') and (hasattr(arg, 'META') or hasattr(arg, '_request')):
            return arg
    if 'request' in kwargs:
        req = kwargs['request']
        if hasattr(req, 'user') and (hasattr(req, 'META') or hasattr(req, '_request')):
            return req
    # Fallback to look for any object having a 'user' attribute (mock requests)
    for arg in args:
        if hasattr(arg, 'user'):
            return arg
    if 'request' in kwargs:
        req = kwargs['request']
        if hasattr(req, 'user'):
            return req
    raise ValueError("Request object not found in view arguments or keyword arguments.")

def _unauthenticated_response(request):
    """Helper to return standardized 401 response for unauthenticated requests."""
    response_data = {
        "StatusCode": 6001,
        'message': {
            'title': 'Authentication Required',
            'body': 'You must be authenticated to perform this action',
        },
    }
    return HttpResponse(
        json.dumps(response_data),
        status=status.HTTP_401_UNAUTHORIZED,
        content_type="application/json",
    )

def _permission_denied_response(request):
    """Helper to return standardized 403 response for unauthorized requests."""
    response_data = {
        "StatusCode": 6001,
        'message': {
            'title': 'Permission Denied',
            'body': 'You do not have the necessary permissions to perform this action',
        },
    }
    return HttpResponse(
        json.dumps(response_data),
        status=status.HTTP_403_FORBIDDEN,
        content_type="application/json",
    )

def _plan_restricted_response(request):
    """Helper to return standardized 403 response for subscription restriction."""
    response_data = {
        "StatusCode": 6001,
        'message': {
            'title': 'Plan Restriction',
            'body': 'Your current subscription plan does not support this feature.',
        },
    }
    return HttpResponse(
        json.dumps(response_data),
        status=status.HTTP_403_FORBIDDEN,
        content_type="application/json",
    )


# --------------------------------------------------------------------------------
# Shared Evaluators
# --------------------------------------------------------------------------------

def has_fine_grained_permission(user, permissions):
    """
    Check if the user is a superuser/superadmin or possesses one of the required permissions.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or getattr(user, 'isSuperAdmin', False):
        return True
    user_perms = getattr(user, 'permissions', [])
    if not isinstance(user_perms, list):
        user_perms = []
    # If permissions is a single string, convert to list
    if isinstance(permissions, str):
        permissions = [permissions]
    return any(p in user_perms for p in permissions)

def has_plan_feature(user, features):
    """
    Check if the user has the required plan features based on active subscription package.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or getattr(user, 'isSuperAdmin', False):
        return True
    plan_features = get_user_plan_features(user)
    if isinstance(features, str):
        features = [features]
    return any(f in plan_features for f in features)

def get_system_mode_status(request):
    """
    Retrieve the current system mode status restriction if down, maintenance, or readonly.
    """
    from core.models import Mode
    mode, created = Mode.objects.get_or_create(id=1)
    if mode.down:
        return 'down'
    if mode.maintenance:
        return 'maintenance'
    if mode.readonly and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return 'readonly'
    return None


# --------------------------------------------------------------------------------
# 1. Authorization Decorators
# --------------------------------------------------------------------------------

def permission_required(*permissions):
    """
    Decorator to check user fine-grained permissions stored in request.user.permissions.
    Works for both FBVs and CBV methods.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(*args, **kwargs):
            request = _find_request(*args, **kwargs)
            user = request.user
            if not (user and user.is_authenticated):
                return _unauthenticated_response(request)

            if has_fine_grained_permission(user, permissions):
                return view_func(*args, **kwargs)

            return _permission_denied_response(request)
        return _wrapped_view
    return decorator


def role_required(*permissions):
    """
    Decorator to check Django auth permissions (user_permissions/group permissions).
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(*args, **kwargs):
            request = _find_request(*args, **kwargs)
            user = request.user
            if not (user and user.is_authenticated):
                return _unauthenticated_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False):
                return view_func(*args, **kwargs)

            if user.user_permissions.filter(codename__in=permissions).exists():
                return view_func(*args, **kwargs)

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
    Decorator to verify if the user belongs to at least one of the specified groups.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(*args, **kwargs):
            request = _find_request(*args, **kwargs)
            user = request.user
            if not (user and user.is_authenticated):
                return _unauthenticated_response(request)

            if user.is_superuser or getattr(user, 'isSuperAdmin', False) or user.groups.filter(name__in=group_names).exists():
                return view_func(*args, **kwargs)

            return _permission_denied_response(request)
        return _wrapped_view
    return decorator


def get_user_plan_features(user):
    """Helper to query the user's/organization's subscription features."""
    if not user or not user.is_authenticated:
        return []
    
    if user.is_superuser or getattr(user, 'isSuperAdmin', False):
        from users.models import PERMISSION_FLAGS
        return [p['id'] for p in PERMISSION_FLAGS]

    if not user.organization:
        from users.models import PERMISSION_FLAGS
        return [p['id'] for p in PERMISSION_FLAGS] + ['is_attendance_enabled', 'is_project_enabled']

    target_email = user.email
    from users.models import Employee
    superadmin = Employee.objects.filter(organization=user.organization, isSuperAdmin=True).first()
    if superadmin:
        target_email = superadmin.email

    from subscribers.models import SubscriberAccount, SubscriptionPackage
    sub = SubscriberAccount.objects.filter(email=target_email, isActive=True).first()
    packageName = sub.packageName if sub else "Free Package"
    pkg = SubscriptionPackage.objects.filter(name=packageName).first()
    if not pkg:
        return ['is_attendance_enabled', 'is_project_enabled']
    return pkg.features


def plan_permission_required(*features):
    """
    Decorator to check subscription/plan based permissions.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(*args, **kwargs):
            request = _find_request(*args, **kwargs)
            user = request.user
            if not (user and user.is_authenticated):
                return _unauthenticated_response(request)

            if has_plan_feature(user, features):
                return view_func(*args, **kwargs)

            return _plan_restricted_response(request)
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
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)
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
    def wrap(*args, **kwargs):
        request = _find_request(*args, **kwargs)
        status_mode = get_system_mode_status(request)

        if status_mode in ['down', 'maintenance']:
            if is_ajax(request):
                response_data = {
                    "status": "false",
                    "message": f"Application currently in {status_mode} mode. Please try again later.",
                    "static_message": "true"
                }
                return HttpResponse(json.dumps(response_data), content_type="application/json", status=503)
            else:
                return HttpResponse(f"<h1>503 Service Unavailable</h1><p>Application is currently in {status_mode} mode. Please try again later.</p>", status=503)

        elif status_mode == 'readonly':
            if is_ajax(request):
                response_data = {
                    "status": "false",
                    "message": "Application now in readonly mode. Writes are disabled.",
                    "static_message": "true"
                }
                return HttpResponse(json.dumps(response_data), content_type="application/json", status=403)
            else:
                return HttpResponse("<h1>403 Forbidden</h1><p>Application is currently in read-only mode. Writes are disabled.</p>", status=403)

        return function(*args, **kwargs)

    return wrap


def ajax_required(function):
    """
    Decorator to restrict requests to AJAX queries only.
    """
    @wraps(function)
    def wrap(*args, **kwargs):
        request = _find_request(*args, **kwargs)
        is_ajax = request.headers.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        if not is_ajax:
            response_data = {
                "status": "false",
                "message": "AJAX request required."
            }
            return HttpResponse(json.dumps(response_data), content_type="application/json", status=400)
        return function(*args, **kwargs)
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
