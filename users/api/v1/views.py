# --------------------------------------------------------------------------------
#       Users Views
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
from datetime import datetime
import json

# DJANGO
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

# THIRD PARTY
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django_filters.rest_framework import DjangoFilterBackend

# APPLICATION SPECIFIC
from users.models import Employee
from core.models import AuditLog
from core.mixins import FilterMixinNew
from core.permissions import ActionPermissionMixin, DRFCheckModePermission, HasRequiredPermission
from users.filters import EmployeeFilter
from users.api.v1.serializers import EmployeeSerializer, CustomTokenRefreshSerializer
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

        if isinstance(user_data.get('permissions'), list):
            if user_data['is_attendance_enabled'] and 'attendance:view' not in user_data['permissions']:
                user_data['permissions'].append('attendance:view')
            if user_data['is_project_enabled'] and 'project:view' not in user_data['permissions']:
                user_data['permissions'].append('project:view')
    return user_data



# --------------------------------------------------------------------------------
class CustomTokenObtainPairView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        email = request.data.get('email')
        password = request.data.get('password')
        if not email or not password:
            return Response({'error': 'Email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=email, password=password)
        if not user or not user.is_active:
            return Response({'error': 'Invalid email or security password.'}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)

        serializer = EmployeeSerializer(user)
        user_data = serializer.data
        user_data = _enrich_user_data(user_data, user.organization)

        AuditLog.objects.create(
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action="Logged In",
            details="User logged in via password authentication."
        )

        return Response({
            'user': user_data,
            'access': 'session',
            'refresh': 'session',
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# CurrentUserView: API endpoint to retrieve the currently logged in employee profile details.
# --------------------------------------------------------------------------------
class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = EmployeeSerializer(request.user)
        user_data = dict(serializer.data)
        # Enrich with subscription, feature flags, and permission gates
        user_data = _enrich_user_data(user_data, request.user.organization)
        return Response(user_data)


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

            login(request, employee)

            serializer = EmployeeSerializer(employee)
            user_data = serializer.data
            user_data = _enrich_user_data(user_data, employee.organization)

            AuditLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                action="Logged In",
                details="User logged in via magic link authentication."
            )

            return Response({
                'user': user_data,
                'access': 'session',
                'refresh': 'session',
            }, status=status.HTTP_200_OK)
        except (BadSignature, SignatureExpired, Employee.DoesNotExist):
            return Response({'error': 'Invalid or expired magic link token'}, status=status.HTTP_400_BAD_REQUEST)

        except SignatureExpired:
            return Response({'error': 'Magic link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid magic link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)


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

    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission],
        'revoke': [permissions.AllowAny],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'change_status': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
        'bulk_upload': [permissions.IsAuthenticated, DRFCheckModePermission, HasRequiredPermission],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            if self.request.user.isSuperAdmin and self.request.user.organization is None:
                org_id = self.request.query_params.get('organization_id')
                if org_id:
                    qs = qs.filter(organization_id=org_id)
                elif self.request.query_params.get('backoffice_only') == 'true':
                    qs = qs.filter(organization__isnull=True)
            else:
                qs = qs.filter(organization=self.request.user.organization)
        return qs

    def check_permissions(self, request):
        super().check_permissions(request)
        from users.permissions import check_backoffice_staff_management
        check_backoffice_staff_management(self, request)

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'revoke']:
            self.required_permission = None
        else:
            self.required_permission = 'admin:employees'
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
        name = f"{instance.first_name} {instance.last_name}".strip() or instance.email
        email = instance.email
        instance.delete()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Employee Deleted",
            details=f"Deleted employee profile for {name} ({email})."
        )

    @action(detail=False, methods=['post'], url_path='revoke')
    def revoke(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner()
        try:
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)
            name = f"{employee.first_name} {employee.last_name}".strip() or employee.email
            employee.delete()

            AuditLog.objects.create(
                employee=None,
                employeeName=name,
                action="Registration Revoked",
                details="Revoked registration and deleted profile as requested."
            )
            return Response({'message': 'Registration successfully revoked and account deleted.'}, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response({'error': 'Revocation link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid revocation link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee profile not found.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path='change-status')
    def change_status(self, request, pk=None):
        employee = self.get_object()
        new_status = request.data.get('status')
        VALID_STATUSES = ['Active', 'Deactivated', 'Terminated', 'Resigned']
        if not new_status or new_status not in VALID_STATUSES:
            return Response({'error': f'Invalid status. Allowed values: {", ".join(VALID_STATUSES)}'}, status=status.HTTP_400_BAD_REQUEST)

        employee.employment_status = new_status
        if new_status == 'Active':
            employee.is_active = True
        else:
            employee.is_active = False
        employee.save()

        user = request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email if user and user.is_authenticated else "System"
        AuditLog.objects.create(
            employee=user if user and user.is_authenticated else None,
            employeeName=actor_name,
            action=f"Employee {new_status}",
            details=f"Changed employment status of {employee.first_name} {employee.last_name} ({employee.email}) to '{new_status}'."
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

            # Duplicate email validation in DB:
            if Employee.objects.filter(email=email).exists():
                failed_rows.append({"row": row_num, "email": email, "reason": "Email Already Exists"})
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

            # Database creation & email dispatch
            name_parts = full_name.split(' ', 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else 'User'

            org = request.user.organization if request.user.is_authenticated else None

            try:
                # Check Organization limit first:
                if org and org.settings:
                    limit = org.settings.max_employees_allowed
                    current_count = Employee.objects.filter(organization=org).count() + len(successful_onboards)
                    if current_count >= limit:
                        failed_rows.append({"row": row_num, "email": email, "reason": f"Organization employee cap of {limit} reached"})
                        continue

                # Generate random password
                from core.utils import generate_secure_password
                from users.api.v1.services import UserService
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

                # Send onboarding credential email
                try:
                    UserService.send_admin_onboarding_email(employee, raw_password, synchronous=True)
                except Exception:
                    # SMTP Delivery Failed — delete created user so admin can fix and re-onboard
                    employee.delete()
                    failed_rows.append({"row": row_num, "email": email, "reason": "SMTP Mail Delivery Failed"})
                    continue

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


class CustomTokenRefreshView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        return Response({
            'message': 'Token refresh is deprecated. Session authentication is active.'
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        logout(request)

        response = Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
        samesite = 'Lax'
        response.delete_cookie('sessionid', samesite=samesite)
        response.delete_cookie('csrftoken', samesite=samesite)
        response.delete_cookie('cubelogs_access_token', samesite=samesite)
        response.delete_cookie('cubelogs_refresh_token', samesite=samesite)
        return response


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


