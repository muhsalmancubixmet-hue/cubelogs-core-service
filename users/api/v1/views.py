# --------------------------------------------------------------------------------
#       Users Views
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
import json
import logging

logger = logging.getLogger(__name__)

# DJANGO
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.utils.decorators import method_decorator

# THIRD PARTY
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from django_filters.rest_framework import DjangoFilterBackend

# APPLICATION SPECIFIC
from users.models import Employee
from core.models import AuditLog
from core.mixins import FilterMixinNew
from core.permissions import ActionPermissionMixin, DRFCheckModePermission, HasRequiredPermission, IsSuperAdminUser
from users.filters import EmployeeFilter
from users.api.v1.serializers import EmployeeSerializer, RoleSerializer, PermissionFlagSerializer, CustomTokenRefreshSerializer
from users.api.v1.services import UserService
from core.module_registry.loader import load_modules
from core.decorators import check_mode
from core.throttling import AuthRateThrottle


def _enrich_user_data(user_data, organization):
    if organization and hasattr(organization, 'settings') and organization.settings:
        org_settings = organization.settings
        user_data['is_attendance_enabled'] = org_settings.is_attendance_enabled
        user_data['is_project_enabled'] = org_settings.is_project_enabled

        if 'subscription' in user_data and isinstance(user_data['subscription'], dict):
            user_data['subscription']['is_attendance_enabled'] = org_settings.is_attendance_enabled
            user_data['subscription']['is_project_enabled'] = org_settings.is_project_enabled

    return user_data


class PermissionFlagViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PermissionFlagSerializer
    pagination_class = None

    def get_queryset(self):
        from users.roles import sync_default_roles
        from users.models import PermissionFlag
        sync_default_roles()
        return PermissionFlag.objects.filter(is_active=True).order_by('module', 'category', 'key')


class RoleViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission: list[str] | str | None = 'roles.view'
    serializer_class = RoleSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = None
        else:
            self.required_permission = ['roles.edit', 'roles.create', 'admin:templates']
        return super().get_permissions()

    def get_queryset(self):
        from users.roles import sync_default_roles
        from users.models import Role
        from django.db.models import Q
        sync_default_roles()

        user = self.request.user
        qs = Role.objects.filter(is_active=True).prefetch_related('permissions', 'employees')

        if not user.is_superuser:
            active_org = getattr(self.request, 'active_organization', None) or getattr(user, 'organization', None)
            if active_org:
                qs = qs.filter(Q(organization=active_org) | Q(organization__isnull=True))
            else:
                qs = qs.filter(organization__isnull=True)

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(label__icontains=search) | Q(slug__icontains=search))

        role_type = self.request.query_params.get('type')
        if role_type == 'system':
            qs = qs.filter(is_system_role=True)
        elif role_type == 'custom':
            qs = qs.filter(is_system_role=False)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        active_org = getattr(self.request, 'active_organization', None) or getattr(user, 'organization', None)
        role = serializer.save(organization=active_org)
        AuditLog.objects.create(
            employee=user if user.is_authenticated else None,
            employeeName=f"{user.first_name} {user.last_name}".strip() or (user.email if user.is_authenticated else "System"),
            action='ROLE_CREATED',
            details=f"Created custom role '{role.name}' ({role.slug}).",
            ipAddress=self.request.META.get('REMOTE_ADDR')
        )

    def update(self, request, *args, **kwargs):
        role = self.get_object()
        if not request.user.is_superuser:
            active_org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
            if role.organization is None or role.organization != active_org:
                return Response(
                    {"detail": "You do not have permission to modify this role."},
                    status=status.HTTP_403_FORBIDDEN
                )
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        role = serializer.save()
        user = self.request.user
        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action='ROLE_UPDATED',
            details=f"Updated role '{role.name}' ({role.slug}).",
            ipAddress=self.request.META.get('REMOTE_ADDR')
        )

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if not request.user.is_superuser:
            active_org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
            if role.organization is None or role.organization != active_org:
                return Response(
                    {"detail": "You do not have permission to delete this role."},
                    status=status.HTTP_403_FORBIDDEN
                )
        if role.is_system_role or role.organization is None:
            return Response(
                {"detail": f"System role '{role.name}' is protected and cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if role.org_memberships.filter(is_active_in_org=True, is_deleted=False).exists():
            return Response(
                {"detail": "This role is assigned to active employees. Please reassign employees first before deleting."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if role.employees.count() > 0:
            return Response(
                {"detail": f"This role is assigned to {role.employees.count()} employees. Please reassign employees first before deleting."},
                status=status.HTTP_400_BAD_REQUEST
            )
        role_name = role.name
        role.delete()
        user = request.user
        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action='ROLE_DELETED',
            details=f"Deleted custom role '{role_name}'.",
            ipAddress=request.META.get('REMOTE_ADDR')
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate(self, request, pk=None):
        role = self.get_object()
        active_org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
        new_name = request.data.get('name')
        new_label = request.data.get('label') or new_name

        if not new_name:
            return Response({"name": ["New role name is required for duplication."]}, status=status.HTTP_400_BAD_REQUEST)

        from django.utils.text import slugify
        from users.models import Role
        new_slug = slugify(new_name)

        if Role.objects.filter(slug=new_slug, organization=active_org).exists():
            return Response({"name": ["A role with this name/slug already exists."]}, status=status.HTTP_400_BAD_REQUEST)

        duplicated_role = Role.objects.create(
            name=new_name,
            slug=new_slug,
            label=new_label,
            description=f"Duplicated from '{role.name}'. " + (role.description or ''),
            organization=active_org,
            is_system_role=False,
            is_active=True
        )
        duplicated_role.permissions.set(role.permissions.all())

        user = request.user
        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action='ROLE_DUPLICATED',
            details=f"Duplicated role '{role.name}' to create '{new_name}'.",
            ipAddress=request.META.get('REMOTE_ADDR')
        )

        serializer = self.get_serializer(duplicated_role)
        return Response(serializer.data, status=status.HTTP_201_CREATED)




# --------------------------------------------------------------------------------
class CustomTokenObtainPairView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response(
                {'error': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(request, username=email, password=password)

        if not user or not user.is_active:
            return Response(
                {'error': 'Invalid email or security password.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        serializer = EmployeeSerializer(user)
        user_data = serializer.data
        user_data = _enrich_user_data(user_data, user.organization)

        refresh = UserService.get_tokens_for_user(user)

        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action="Logged In",
            details="User logged in via password authentication."
        )

        return Response({
            'user': user_data,
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# CurrentUserView: API endpoint to retrieve the currently logged in employee profile details.
# --------------------------------------------------------------------------------
@method_decorator(ensure_csrf_cookie, name='dispatch')
class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.middleware.csrf import get_token
        get_token(request)
        from users.models import OrganizationMembership

        active_org = request.active_organization
        active_mem = request.active_membership

        serializer = EmployeeSerializer(request.user, context={'request': request})
        user_data = dict(serializer.data)
        # Enrich with subscription, feature flags, and permission gates
        user_data = _enrich_user_data(user_data, active_org or request.user.organization)

        if active_mem:
            user_data['designation'] = active_mem.designation or ''
            user_data['department'] = active_mem.department or ''
            if active_mem.role:
                user_data['role'] = active_mem.role.id
                user_data['role_name'] = active_mem.role.name

        # Phase 2C Additive Context Fields

        user_data['active_organization'] = {
            'id': active_org.id,
            'name': active_org.name,
            'subdomain': getattr(active_org, 'subdomain', '')
        } if active_org else None

        user_data['active_membership'] = {
            'id': active_mem.id,
            'organization_id': active_mem.organization_id,
            'role': active_mem.role.name if active_mem.role else None,
            'department': active_mem.department or '',
            'designation': active_mem.designation or '',
            'employee_code': active_mem.employee_code or '',
            'employment_status': active_mem.employment_status,
            'is_active_in_org': active_mem.is_active_in_org
        } if active_mem else None

        memberships = OrganizationMembership.objects.filter(
            user=request.user,
            is_deleted=False,
            is_active_in_org=True
        ).select_related('organization', 'role')

        user_data['available_memberships'] = [
            {
                'id': m.id,
                'organization_id': m.organization_id,
                'organization_name': m.organization.name,
                'organization_subdomain': getattr(m.organization, 'subdomain', ''),
                'role': m.role.name if m.role else None,
                'designation': m.designation or '',
                'department': m.department or '',
                'employee_code': m.employee_code or '',
                'employment_status': m.employment_status,
                'is_active_in_org': m.is_active_in_org
            }
            for m in memberships
        ]

        return Response(user_data)


# --------------------------------------------------------------------------------
# SwitchOrganizationView: Authenticated endpoint to switch active tenant workspace.
# --------------------------------------------------------------------------------
class SwitchOrganizationView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        user = request.user
        org_id = request.data.get('organization_id') or request.data.get('organization')
        if not org_id:
            return Response({'error': 'Organization ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        from users.models import OrganizationMembership
        membership = OrganizationMembership.objects.filter(
            user=user,
            organization_id=org_id,
            is_active_in_org=True,
            is_deleted=False
        ).select_related('organization', 'role').first()

        if not membership:
            return Response({'error': 'You do not have an active membership in this organization.'}, status=status.HTTP_403_FORBIDDEN)

        # Issue new JWT tokens for selected active membership
        tokens = UserService.get_tokens_for_user(user, active_membership=membership)

        # Attach cached active membership to request context
        request._cached_active_membership = membership
        request._cached_active_organization = membership.organization
        if hasattr(request, '_request'):
            request._request._cached_active_membership = membership
            request._request._cached_active_organization = membership.organization

        serializer = EmployeeSerializer(user, context={'request': request})
        user_data = serializer.data
        user_data = _enrich_user_data(user_data, membership.organization)

        user_data['designation'] = membership.designation or ''
        user_data['department'] = membership.department or ''
        if membership.role:
            user_data['role'] = membership.role.id
            user_data['role_name'] = membership.role.name

        user_data['active_organization'] = {
            'id': membership.organization.id,
            'name': membership.organization.name,
            'subdomain': getattr(membership.organization, 'subdomain', '')
        }
        user_data['active_membership'] = {
            'id': membership.id,
            'organization_id': membership.organization_id,
            'role': membership.role.name if membership.role else None,
            'department': membership.department or '',
            'designation': membership.designation or '',
            'employee_code': membership.employee_code or '',
            'employment_status': membership.employment_status,
            'is_active_in_org': membership.is_active_in_org
        }

        # Attach available active memberships list
        m_qs = OrganizationMembership.objects.filter(
            user=user,
            is_deleted=False,
            is_active_in_org=True
        ).select_related('organization', 'role')

        user_data['available_memberships'] = [
            {
                'id': m.id,
                'organization_id': m.organization_id,
                'organization_name': m.organization.name,
                'organization_subdomain': getattr(m.organization, 'subdomain', ''),
                'role': m.role.name if m.role else None,
                'designation': m.designation or '',
                'department': m.department or '',
                'employee_code': m.employee_code or '',
                'employment_status': m.employment_status,
                'is_active_in_org': m.is_active_in_org
            }
            for m in m_qs
        ]

        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action="Switched Workspace",
            details=f"Switched active workspace to organization: {membership.organization.name} (ID: {membership.organization.id})."
        )

        return Response({
            'user': user_data,
            'access': str(tokens.access_token),
            'refresh': str(tokens),
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# MagicLoginView: API endpoint to authenticate employees using signed auto-login links.
# --------------------------------------------------------------------------------
class MagicLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='auto-login')
        try:
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)
            if not employee.is_active:
                return Response({'error': 'User account is inactive'}, status=status.HTTP_400_BAD_REQUEST)

            serializer = EmployeeSerializer(employee)
            user_data = serializer.data
            user_data = _enrich_user_data(user_data, employee.organization)

            refresh = UserService.get_tokens_for_user(employee)

            AuditLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                action="Logged In",
                details="User logged in via magic link authentication."
            )

            return Response({
                'user': user_data,
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response(
                {'error': 'Magic link has expired.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        except BadSignature:
            return Response(
                {'error': 'Invalid magic link.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        except Employee.DoesNotExist:
            return Response(
                {'error': 'Account not found.'},
                status=status.HTTP_404_NOT_FOUND
            )

# --------------------------------------------------------------------------------
# PasswordResetRequestView: API endpoint to generate and send a password reset code/email.
# --------------------------------------------------------------------------------
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.core.signing import TimestampSigner
        from django.conf import settings

        email = request.data.get('email')
        if not email:
            return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            employee = Employee.objects.get(email=email)
        except Employee.DoesNotExist:
            return Response({'error': 'Account with this email does not exist.'}, status=status.HTTP_404_NOT_FOUND)

        signer = TimestampSigner(salt='password-reset')
        token = signer.sign(str(employee.id))

        frontend_url = settings.FRONTEND_URL
        reset_url = f"{frontend_url}/login/reset?token={token}"

        subject = 'Reset Your Password - CubeLogs'
        message = f"""Hello {employee.first_name or employee.email},

We received a request to reset the password for your CubeLogs account.

Click the link below to securely reset your password:
{reset_url}

This link is highly time-sensitive and will expire in 2 minutes.

If you did not request this, you can safely ignore this email.
"""
        from core.tasks import EmailService
        EmailService.queue_and_send_email(employee.email, subject, message)

        AuditLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Password Reset Requested",
            details="Password reset link was requested."
        )

        return Response({'message': 'Password reset link has been sent to your email.'}, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# PasswordResetValidateView: API endpoint to validate a password reset confirmation token.
# --------------------------------------------------------------------------------
class PasswordResetValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='password-reset')
        try:
            employee_id = signer.unsign(token, max_age=120)
            Employee.objects.get(id=employee_id)
            return Response({'message': 'Token is valid.'}, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response({'error': 'Password reset link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid password reset link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)


# --------------------------------------------------------------------------------
# PasswordResetConfirmView: API endpoint to finalize user password reset confirmation.
# --------------------------------------------------------------------------------
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

        token = request.data.get('token')
        password = request.data.get('password')
        password_confirm = request.data.get('passwordConfirm')

        if not token:
            return Response({'error': 'Token is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not password or not password_confirm:
            return Response({'error': 'Password fields are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if password != password_confirm:
            return Response({'error': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='password-reset')
        try:
            employee_id = signer.unsign(token, max_age=120)
            employee = Employee.objects.get(id=employee_id)

            employee.set_password(password)
            employee.save()

            AuditLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                action="Password Reset Confirmed",
                details="Password was successfully reset."
            )

            return Response({'message': 'Password has been successfully updated.'}, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response({'error': 'Password reset link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid password reset link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)


# --------------------------------------------------------------------------------
# ChangePasswordView: API endpoint to update the password of an authenticated user.
# --------------------------------------------------------------------------------
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        current_password = request.data.get('currentPassword')
        new_password = request.data.get('newPassword')
        confirm_password = request.data.get('confirmPassword')

        if not current_password or not new_password or not confirm_password:
            return Response({'error': 'All password fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user.check_password(current_password):
            return Response({'error': 'Incorrect current password.'}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({'error': 'New passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        try:
            validate_password(new_password, user=request.user)
        except ValidationError as e:
            return Response({'error': ' '.join(map(str, e.messages))}, status=status.HTTP_400_BAD_REQUEST)

        from django.contrib.auth import update_session_auth_hash
        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)

        AuditLog.objects.create(
            employee=request.user,
            employeeName=f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email,
            action="Password Changed",
            details="User successfully changed their password."
        )

        serializer = EmployeeSerializer(request.user)
        return Response({
            'message': 'Password has been successfully updated.',
            'user': serializer.data
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# EmployeeViewSet: ViewSet managing employee CRUD operations, onboard validations, and template sync.
# --------------------------------------------------------------------------------
class EmployeeViewSet(ActionPermissionMixin, FilterMixinNew, viewsets.ModelViewSet):
    queryset = Employee.objects.all().order_by('id')
    serializer_class = EmployeeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = EmployeeFilter
    required_permission: list[str] | str | None = None

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission],
        'revoke': [permissions.AllowAny],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
        'change_status': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
        'bulk_upload': [permissions.IsAuthenticated, DRFCheckModePermission, IsSuperAdminUser],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            if self.request.user.isSuperAdmin and self.request.user.organization is None:
                org_id = self.request.query_params.get('organization_id')
                if org_id:
                    qs = qs.filter(memberships__organization_id=org_id, memberships__is_deleted=False)
                elif self.request.query_params.get('backoffice_only') == 'true':
                    qs = qs.filter(organization__isnull=True)
            else:
                active_org = getattr(self.request, 'active_organization', None)
                if active_org:
                    from django.db.models import Q
                    qs = qs.filter(
                        Q(memberships__organization=active_org, memberships__is_deleted=False) |
                        Q(organization=active_org)
                    )
                else:
                    return qs.none()
        return qs.distinct().order_by('id')

    def check_permissions(self, request):
        super().check_permissions(request)
        from users.permissions import check_backoffice_staff_management
        check_backoffice_staff_management(self, request)

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'revoke']:
            self.required_permission = None
        else:
            self.required_permission = ['admin:employees', 'roles.edit', 'roles.assign', 'permissions.manage', 'roles.create']
        return super().get_permissions()

    def perform_create(self, serializer):
        employee = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email if user.is_authenticated else "System/Registration"
        AuditLog.objects.create(
            employee=user if user.is_authenticated else None,
            employeeName=actor_name,
            action="Employee Created",
            details=f"Created new employee profile: {employee.first_name} {employee.last_name} ({employee.email})."
        )

    def perform_update(self, serializer):
        employee = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Employee Updated",
            details=f"Updated employee profile for {employee.first_name} {employee.last_name} ({employee.email})."
        )

    def perform_destroy(self, instance):
        from users.models import OrganizationMembership

        request_user = self.request.user
        active_org = getattr(self.request, 'active_organization', None) or (request_user.organization if request_user and request_user.is_authenticated else None)

        name = f"{instance.first_name} {instance.last_name}".strip() or instance.email
        email = instance.email

        if active_org:
            mem = OrganizationMembership.objects.filter(user=instance, organization=active_org).first()
            if mem:
                mem.is_active_in_org = False
                mem.is_deleted = True
                mem.employment_status = 'Deactivated'
                mem.save()

            # Check if employee has any other active memberships across all orgs
            other_mems = OrganizationMembership.objects.filter(user=instance, is_deleted=False, is_active_in_org=True).exclude(organization=active_org).exists()
            if not other_mems and not instance.isSuperAdmin:
                instance.is_active = False
                instance.save(update_fields=['is_active'])
        else:
            instance.delete()

        actor_name = f"{request_user.first_name} {request_user.last_name}".strip() or request_user.email
        AuditLog.objects.create(
            employee=request_user,
            employeeName=actor_name,
            action="Employee Removed",
            details=f"Removed employee {name} ({email}) from organization membership."
        )

    @action(detail=False, methods=['post'], url_path='revoke')
    def revoke(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
        from users.models import OrganizationMembership

        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='revoke-registration')
        try:
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)
            name = f"{employee.first_name} {employee.last_name}".strip() or employee.email

            mems = OrganizationMembership.objects.filter(user=employee, is_deleted=False)
            if mems.count() <= 1:
                employee.delete()
            else:
                target_org = getattr(request, 'active_organization', None) or employee.organization
                if target_org:
                    m = mems.filter(organization=target_org).first()
                    if m:
                        m.is_active_in_org = False
                        m.is_deleted = True
                        m.employment_status = 'Deactivated'
                        m.save()

            AuditLog.objects.create(
                employee=None,
                employeeName=name,
                action="Registration Revoked",
                details="Revoked registration link."
            )
            return Response({'message': 'Registration successfully revoked.'}, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response({'error': 'Revocation link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid revocation link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee profile not found.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path='change-status')
    def change_status(self, request, pk=None):
        from users.models import OrganizationMembership
        from django.utils import timezone
        from django.utils.dateparse import parse_date

        employee = self.get_object()
        active_org = getattr(request, 'active_organization', None) or (request.user.organization if request.user and request.user.is_authenticated else None)

        new_status = request.data.get('status')
        VALID_STATUSES = ['Active', 'Deactivated', 'Terminated', 'Resigned']
        if not new_status or new_status not in VALID_STATUSES:
            return Response({'error': f'Invalid status. Allowed values: {", ".join(VALID_STATUSES)}'}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.now().date()
        mem = OrganizationMembership.objects.filter(user=employee, organization=active_org).first() if active_org else None

        if new_status in ['Resigned', 'Terminated']:
            lwd_raw = request.data.get('last_working_date') or request.data.get('lastWorkingDate')
            if not lwd_raw:
                return Response({'error': 'Last Working Date is required when status is Resigned or Terminated.'}, status=status.HTTP_400_BAD_REQUEST)
            target_lwd = parse_date(str(lwd_raw)) if (lwd_raw and isinstance(lwd_raw, str)) else lwd_raw
            if not target_lwd:
                return Response({'error': 'Invalid Last Working Date format.'}, status=status.HTTP_400_BAD_REQUEST)
            if employee.joining_date and target_lwd < employee.joining_date:
                return Response({'error': 'Last working date cannot be earlier than joining date.'}, status=status.HTTP_400_BAD_REQUEST)

            if mem:
                mem.employment_status = new_status
                mem.last_working_date = target_lwd
                mem.is_active_in_org = (target_lwd >= today)
                mem.save()
        elif new_status == 'Deactivated':
            if mem:
                mem.employment_status = 'Deactivated'
                mem.is_active_in_org = False
                mem.save()
        elif new_status == 'Active':
            if mem:
                mem.employment_status = 'Active'
                mem.is_active_in_org = True
                mem.last_working_date = None
                mem.save()

        if active_org and active_org == employee.organization:
            employee.employment_status = new_status
            if new_status in ['Resigned', 'Terminated']:
                employee.last_working_date = target_lwd
            employee.save()

        user = request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email if user and user.is_authenticated else "System"
        AuditLog.objects.create(
            employee=user if user and user.is_authenticated else None,
            employeeName=actor_name,
            action=f"Employee {new_status}",
            details=f"Changed employment status of {employee.first_name} {employee.last_name} ({employee.email}) to '{new_status}' in active organization."
        )

        serializer = self.get_serializer(employee)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='bulk-upload')
    def bulk_upload(self, request):
        import pandas as pd
        import re
        import secrets
        import string
        from django.core.mail import send_mail
        from django.core.signing import TimestampSigner
        from django.conf import settings as dj_settings
        from users.models import Template
        from attendance.models import Schedule

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        name = file_obj.name.lower()
        try:
            if name.endswith('.csv'):
                df = pd.read_csv(file_obj)
            elif name.endswith(('.xls', '.xlsx')):
                df = pd.read_excel(file_obj)
            else:
                return Response({"error": "Unsupported file format. Please upload an Excel or CSV file."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Clean column headers (all become plain str after this)
        df.columns = [c.strip().lower() for c in df.columns.astype(str)]
        cols: list[str] = list(df.columns)  # typed list for type checker

        # Locate columns case-insensitively
        name_col: str | None = next((c for c in cols if 'name' in c), None)
        email_col: str | None = next((c for c in cols if 'email' in c), None)
        phone_col: str | None = next((c for c in cols if 'phone' in c), None)
        designation_col: str | None = next((c for c in cols if 'designation' in c or 'role' in c), None)

        # Positional fallback
        if not name_col and len(cols) > 0: name_col = cols[0]
        if not email_col and len(cols) > 1: email_col = cols[1]
        if not phone_col and len(cols) > 2: phone_col = cols[2]
        if not designation_col and len(cols) > 3: designation_col = cols[3]

        successful_onboards = []
        failed_rows = []

        # List of existing/valid roles
        valid_roles = list(Template.objects.values_list('name', flat=True)) + list(Schedule.objects.values_list('designation', flat=True))
        valid_roles = [r.lower().strip() for r in valid_roles]

        for idx, row in df.iterrows():
            row_num = int(str(idx)) + 2  # 1-based sheet row number (index + 2 because header is row 1)

            # Extract and sanitize values
            full_name = str(row.get(name_col, '')).strip() if name_col else ''
            email = str(row.get(email_col, '')).strip() if email_col else ''
            phone = str(row.get(phone_col, '')).strip() if phone_col else ''
            designations = str(row.get(designation_col, '')).strip() if designation_col else ''

            # Basic validation checks
            if not email or email == 'nan' or email == '':
                failed_rows.append({"row": row_num, "email": "", "reason": "Missing Email Address"})
                continue

            if not full_name or full_name == 'nan' or full_name == '':
                failed_rows.append({"row": row_num, "email": email, "reason": "Missing Full Name"})
                continue

            # Target active organization
            org = getattr(request, 'active_organization', None) or (request.user.organization if request.user and request.user.is_authenticated else None)

            # Duplicate membership validation in target DB organization:
            from users.models import OrganizationMembership
            if OrganizationMembership.objects.filter(user__email__iexact=email, organization=org, is_active_in_org=True, is_deleted=False).exists():
                failed_rows.append({"row": row_num, "email": email, "reason": "Email Already Exists in this Organization"})
                continue

            # Email format validation
            if not re.match(r'[^@]+@[^@]+\.[^@]+', email):
                failed_rows.append({"row": row_num, "email": email, "reason": "Invalid Email Format"})
                continue

            # Invalid phone number check
            clean_phone = re.sub(r'[\s\-()+]', '', phone)
            if phone and phone != 'nan' and (not clean_phone.isdigit() or len(clean_phone) < 5 or len(clean_phone) > 15):
                failed_rows.append({"row": row_num, "email": email, "reason": "Invalid Phone Number"})
                continue

            # Soft designation check
            roles_list = [r.strip() for r in designations.split(',') if r.strip() and r.strip() != 'nan']
            invalid_role_found = None
            for r in roles_list:
                if r.lower() not in valid_roles:
                    invalid_role_found = r
                    break

            if invalid_role_found:
                failed_rows.append({"row": row_num, "email": email, "reason": f"Designation Role '{invalid_role_found}' does not exist"})
                continue

            # Multi-org Classification & creation / rehire
            name_parts = full_name.split(' ', 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else 'User'

            from users.api.v1.services import UserService
            existing = Employee.objects.filter(email__iexact=email).first()

            try:
                if existing:
                    # Existing Global User -> Create or reactivate membership in target org
                    mem = OrganizationMembership.objects.filter(user=existing, organization=org).first()
                    if mem:
                        mem.employment_status = 'Active'
                        mem.is_active_in_org = True
                        mem.is_deleted = False
                        mem.last_working_date = None
                        if designations and designations != 'nan':
                            mem.designation = designations
                        mem.save()
                    else:
                        OrganizationMembership.objects.create(
                            user=existing,
                            organization=org,
                            designation=designations if (designations and designations != 'nan') else '',
                            employment_status='Active',
                            is_active_in_org=True
                        )

                    # Update basic details if present
                    existing.first_name = first_name
                    existing.last_name = last_name
                    if phone and phone != 'nan':
                        existing.phone = phone
                    existing.save()

                    # Queue welcome email for existing user joining org
                    try:
                        from django.db import transaction
                        transaction.on_commit(
                            lambda emp=existing: UserService.send_welcome_email(emp, synchronous=False)
                        )
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error("Failed to queue welcome email for %s: %s", email, exc)

                    employee = existing

                else:
                    # Brand-New Global User
                    from core.utils import generate_secure_password
                    raw_password = generate_secure_password(12)

                    employee = Employee.objects.create_user(
                        email=email,
                        username=email,
                        password=raw_password,
                        first_name=first_name,
                        last_name=last_name,
                        phone=phone if (phone and phone != 'nan') else '',
                        designation=designations if (designations and designations != 'nan') else '',
                        organization=org,
                        isSuperAdmin=False,
                        useDefaultPermissions=True
                    )

                    # Send onboarding credential email asynchronously via Celery after DB transaction commits
                    try:
                        from django.db import transaction
                        transaction.on_commit(
                            lambda emp=employee, pwd=raw_password: UserService.send_admin_onboarding_email(emp, pwd, synchronous=False)
                        )
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error("Failed to queue onboarding email for %s: %s", email, exc)

                # Sync shadow membership for Phase 2A coverage
                try:
                    UserService.sync_employee_shadow_membership(employee)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Failed to sync shadow membership for %s: %s", email, exc)

                # Log audit
                actor_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email if request.user.is_authenticated else "System/Registration"
                AuditLog.objects.create(
                    employee=request.user if request.user.is_authenticated else None,
                    employeeName=actor_name,
                    action="Employee Created",
                    details=f"[Bulk Upload] Onboarded employee: {employee.first_name} {employee.last_name} ({employee.email})"
                )

                successful_onboards.append(employee.id)

            except Exception as create_err:
                failed_rows.append({"row": row_num, "email": email, "reason": f"Database creation failed: {str(create_err)}"})
                continue

        return Response({
            "success": True,
            "total_processed": len(df),
            "inserted_count": len(successful_onboards),
            "failed_count": len(failed_rows),
            "failures": failed_rows
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# PermissionsConfigView: View to retrieve system authorization flag configuration registry.
# --------------------------------------------------------------------------------
class PermissionsConfigView(APIView):
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission]

    def get(self, request):
        try:
            config_data = load_modules()
            return Response(config_data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f"Failed to load permissions config: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@check_mode
def backoffice_view(request):
    if not request.user.is_authenticated or not getattr(request.user, 'isSuperAdmin', False):
        return redirect('/backoffice/login/?next=/')

    user_perms = getattr(request.user, 'permissions', [])
    if not isinstance(user_perms, list):
        user_perms = []

    all_backoffice_perms = ['packages', 'subscribers', 'payments', 'leads', 'cms', 'faqs', 'testimonials', 'coupons', 'staff', 'audit_logs', 'billing_settings']
    has_any_backoffice_perm = any(p in user_perms for p in all_backoffice_perms)
    if not has_any_backoffice_perm or request.user.is_superuser or request.user.organization is not None:
        user_perms = all_backoffice_perms

    context = {
        'user_permissions_json': json.dumps(user_perms)
    }
    return render(request, 'backoffice.html', context)


def backoffice_login_view(request):
    if request.user.is_authenticated and getattr(request.user, 'isSuperAdmin', False):
        return redirect('/')

    error = None
    if request.method == 'POST':
        throttle = AuthRateThrottle()
        if not throttle.allow_request(request, throttle):  # type: ignore[arg-type]
            return render(request, 'backoffice_login.html', {
                'error': 'Too many login attempts. Please try again in 1 minute.'
            }, status=429)

        email = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=email, password=password)
        if user is not None:
            if getattr(user, 'isSuperAdmin', False):
                login(request, user)

                AuditLog.objects.create(
                    employee=user,
                    employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
                    action="Logged In",
                    details="User logged in to Backoffice Console."
                )

                next_url = request.GET.get('next', '/')
                return redirect(next_url)
            else:
                error = "Access denied. Only system operators are authorized to access the Backoffice Console."
        else:
            error = "Invalid email or security password."

    return render(request, 'backoffice_login.html', {'error': error})


def backoffice_logout_view(request):
    logout(request)
    return redirect('/backoffice/login/')


class CustomTokenRefreshView(TokenRefreshView):
    serializer_class = CustomTokenRefreshSerializer
    throttle_classes = [AuthRateThrottle]


class LogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        refresh_token = request.data.get('refresh')

        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except Exception as e:
                logger.debug("Logout token blacklist skipped or failed: %s", str(e))

        return Response(
            {'message': 'Logged out successfully'},
            status=status.HTTP_200_OK
        )


def backoffice_manifest_view(request):
    manifest_data = {
        "name": "CubeLogs Backoffice",
        "short_name": "Backoffice",
        "description": "CubeLogs Backoffice & Operator Administration Console",
        "categories": ["business", "productivity", "admin"],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0f172a",
        "theme_color": "#2563eb",
        "icons": [
            {
                "src": "/static/icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "/static/icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable"
            },
            {
                "src": "/static/icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "/static/icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ]
    }
    return JsonResponse(manifest_data)


def backoffice_sw_view(request):
    sw_code = """
const CACHE_NAME = 'cubelogs-backoffice-pwa-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/backoffice/login/',
  '/manifest.json',
  '/static/icon-192x192.png',
  '/static/icon-512x512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE).catch(() => {});
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then((cachedResponse) => {
          return cachedResponse || caches.match('/');
        });
      })
  );
});
"""
    return HttpResponse(sw_code.strip(), content_type='application/javascript')


