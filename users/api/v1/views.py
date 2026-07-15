# --------------------------------------------------------------------------------
#       Users Views
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
from datetime import datetime
import json

# DJANGO
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
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
from users.filters import EmployeeFilter
from users.api.v1.serializers import EmployeeSerializer, CustomTokenRefreshSerializer
from core.module_registry.loader import load_modules
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



from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = dict(super().validate(attrs))
        if self.user:
            serializer = EmployeeSerializer(self.user)
            user_data = serializer.data
            user_data = _enrich_user_data(user_data, self.user.organization)
            data['user'] = user_data  # type: ignore

            AuditLog.objects.create(
                employee=self.user,
                employeeName=f"{self.user.first_name} {self.user.last_name}".strip() or self.user.email,
                action="Logged In",
                details="User logged in via password authentication."
            )
        return data


# --------------------------------------------------------------------------------
class CustomTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [AuthRateThrottle]
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            access = response.data.get('access')
            refresh = response.data.get('refresh')
            from django.conf import settings
            is_secure = not getattr(settings, 'is_dev', False)

            response.set_cookie(
                'cubelogs_access_token',
                access,
                httponly=True,
                secure=is_secure,
                samesite='Lax',
                max_age=24 * 3600
            )
            response.set_cookie(
                'cubelogs_refresh_token',
                refresh,
                httponly=True,
                secure=is_secure,
                samesite='Lax',
                max_age=30 * 24 * 3600
            )
        return response


# --------------------------------------------------------------------------------
# CurrentUserView: API endpoint to retrieve the currently logged in employee profile details.
# --------------------------------------------------------------------------------
class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = EmployeeSerializer(request.user)
        return Response(serializer.data)


# --------------------------------------------------------------------------------
# MagicLoginView: API endpoint to authenticate employees using signed auto-login links.
# --------------------------------------------------------------------------------
class MagicLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
        from rest_framework_simplejwt.tokens import RefreshToken

        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='auto-login')
        try:
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)
            refresh = RefreshToken.for_user(employee)
            serializer = EmployeeSerializer(employee)
            user_data = serializer.data
            user_data = _enrich_user_data(user_data, employee.organization)

            AuditLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                action="Logged In",
                details="User logged in via magic link authentication."
            )

            response = Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': user_data
            }, status=status.HTTP_200_OK)

            from django.conf import settings
            is_secure = not getattr(settings, 'is_dev', False)

            response.set_cookie(
                'cubelogs_access_token',
                str(refresh.access_token),
                httponly=True,
                secure=is_secure,
                samesite='Lax',
                max_age=24 * 3600
            )
            response.set_cookie(
                'cubelogs_refresh_token',
                str(refresh),
                httponly=True,
                secure=is_secure,
                samesite='Lax',
                max_age=30 * 24 * 3600
            )
            return response

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

        request.user.set_password(new_password)
        request.user.save()

        AuditLog.objects.create(
            employee=request.user,
            employeeName=f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email,
            action="Password Changed",
            details="User successfully changed their password."
        )

        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(request.user)
        serializer = EmployeeSerializer(request.user)

        response = Response({
            'message': 'Password has been successfully updated.',
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': serializer.data
        }, status=status.HTTP_200_OK)

        from django.conf import settings
        is_secure = not getattr(settings, 'is_dev', False)

        response.set_cookie(
            'cubelogs_access_token',
            str(refresh.access_token),
            httponly=True,
            secure=is_secure,
            samesite='Lax',
            max_age=24 * 3600
        )
        response.set_cookie(
            'cubelogs_refresh_token',
            str(refresh),
            httponly=True,
            secure=is_secure,
            samesite='Lax',
            max_age=30 * 24 * 3600
        )
        return response


# --------------------------------------------------------------------------------
# EmployeeViewSet: ViewSet managing employee CRUD operations, onboard validations, and template sync.
# --------------------------------------------------------------------------------
class EmployeeViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = Employee.objects.all().order_by('id')
    serializer_class = EmployeeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = EmployeeFilter


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
        if request.user.is_authenticated and request.user.isSuperAdmin and request.user.organization is None:
            if request.user.is_superuser:
                return
            user_perms = getattr(request.user, 'permissions', [])
            if not isinstance(user_perms, list) or 'staff' not in user_perms:
                self.permission_denied(request, message="You do not have permission to manage backoffice staff.")

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.IsAuthenticated()]
        if self.action == 'revoke':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

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

        # Clean column headers
        df.columns = [str(c).strip().lower() for c in df.columns]

        # Locate columns case-insensitively
        name_col = next((c for c in df.columns if 'name' in c), None)
        email_col = next((c for c in df.columns if 'email' in c), None)
        phone_col = next((c for c in df.columns if 'phone' in c), None)
        designation_col = next((c for c in df.columns if 'designation' in c or 'role' in c), None)

        # Positional fallback
        if not name_col and len(df.columns) > 0: name_col = df.columns[0]
        if not email_col and len(df.columns) > 1: email_col = df.columns[1]
        if not phone_col and len(df.columns) > 2: phone_col = df.columns[2]
        if not designation_col and len(df.columns) > 3: designation_col = df.columns[3]

        successful_onboards = []
        failed_rows = []

        # List of existing/valid roles
        valid_roles = list(Template.objects.values_list('name', flat=True)) + list(Schedule.objects.values_list('designation', flat=True))
        valid_roles = [r.lower().strip() for r in valid_roles]

        for idx, row in df.iterrows():
            row_num = idx + 2  # 1-based sheet row number (index + 2 because header is row 1)

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
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            config_data = load_modules()
            return Response(config_data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f"Failed to load permissions config: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
        if not throttle.allow_request(request, None):
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


from rest_framework_simplejwt.views import TokenRefreshView

class CustomTokenRefreshView(TokenRefreshView):
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        # Extract refresh token from cookies if not provided in JSON body
        refresh_token = request.COOKIES.get('cubelogs_refresh_token')
        if refresh_token and 'refresh' not in request.data:
            # request.data is immutable if it's a QueryDict.
            # Convert or set mutable to True.
            if hasattr(request.data, '_mutable'):
                request.data._mutable = True
                request.data['refresh'] = refresh_token
            elif isinstance(request.data, dict):
                request.data['refresh'] = refresh_token

        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            access = response.data.get('access')
            refresh = response.data.get('refresh')
            from django.conf import settings
            is_secure = not getattr(settings, 'is_dev', False)

            if access:
                response.set_cookie(
                    'cubelogs_access_token',
                    access,
                    httponly=True,
                    secure=is_secure,
                    samesite='Lax',
                    max_age=24 * 3600
                )
            if refresh:
                response.set_cookie(
                    'cubelogs_refresh_token',
                    refresh,
                    httponly=True,
                    secure=is_secure,
                    samesite='Lax',
                    max_age=30 * 24 * 3600
                )
        return response


class LogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        response = Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
        response.delete_cookie('cubelogs_access_token')
        response.delete_cookie('cubelogs_refresh_token')
        return response
