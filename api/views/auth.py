"""
api/views/auth.py — Authentication & password management views
"""
from datetime import datetime

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from api.models import Employee, AuditLog, EmailQueue
from api.serializers import EmployeeSerializer


def _enrich_user_data(user_data, organization):
    """Inject org-level feature flags and view permissions into user_data dict."""
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


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = dict(super().validate(attrs))
        if self.user:
            # Include full user details in the login response
            serializer = EmployeeSerializer(self.user)
            user_data = serializer.data

            # Map organization settings flags to user structure
            user_data = _enrich_user_data(user_data, self.user.organization)

            data['user'] = user_data  # type: ignore

            # Log login event
            AuditLog.objects.create(
                employee=self.user,
                employeeName=f"{self.user.first_name} {self.user.last_name}".strip() or self.user.email,
                action="Logged In",
                details="User logged in via password authentication."
            )
        return data


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from api.tasks import sweep_workspace_subscriptions
        try:
            sweep_workspace_subscriptions()  # type: ignore
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error sweeping subscriptions in CurrentUserView: {e}")
        serializer = EmployeeSerializer(request.user)
        return Response(serializer.data)


class MagicLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
        from rest_framework_simplejwt.tokens import RefreshToken

        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        signer = TimestampSigner(salt='auto-login')
        try:
            # Token is valid for 7 days (604800 seconds)
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)

            # Generate JWT tokens
            refresh = RefreshToken.for_user(employee)

            # Serialize user
            serializer = EmployeeSerializer(employee)
            user_data = serializer.data

            # Map organization settings flags to user structure
            user_data = _enrich_user_data(user_data, employee.organization)

            # Log login event
            AuditLog.objects.create(
                employee=employee,
                employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
                action="Logged In",
                details="User logged in via magic link authentication."
            )

            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': user_data
            }, status=status.HTTP_200_OK)

        except SignatureExpired:
            return Response({'error': 'Magic link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid magic link.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

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

        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')
        reset_url = f"{frontend_url}/login/reset?token={token}"

        subject = 'Reset Your Password - CubeLogs'
        message = f"""Hello {employee.first_name or employee.email},

We received a request to reset the password for your CubeLogs account.

Click the link below to securely reset your password:
{reset_url}

This link is highly time-sensitive and will expire in 2 minutes.

If you did not request this, you can safely ignore this email.
"""
        from api.tasks import send_queued_emailqueue_task

        # Create the email log in EmailQueue
        email_log = EmailQueue.objects.create(
            recipient=employee.email,
            from_email=settings.DEFAULT_FROM_EMAIL,
            subject=subject,
            body=message,
            status='PENDING'
        )

        try:
            result = send_queued_emailqueue_task.delay(email_log.id)
            email_log.task_id = result.id
            email_log.save()
        except Exception as e:
            print(f"Failed to queue email task: {e}")
            email_log.status = 'FAILED'
            email_log.error_message = f"Failed to queue celery task. Error: {e}"
            email_log.save()

        AuditLog.objects.create(
            employee=employee,
            employeeName=f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            action="Password Reset Requested",
            details="Password reset link was requested."
        )

        return Response({'message': 'Password reset link has been sent to your email.'}, status=status.HTTP_200_OK)


class PasswordResetValidateView(APIView):
    permission_classes = [permissions.AllowAny]

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


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

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


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        current_password = request.data.get('currentPassword')
        new_password = request.data.get('newPassword')
        confirm_password = request.data.get('confirmPassword')

        if not current_password or not new_password or not confirm_password:
            return Response({'error': 'All password fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Validate current password
        if not request.user.check_password(current_password):
            return Response({'error': 'Incorrect current password.'}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Check if new password matches confirmation
        if new_password != confirm_password:
            return Response({'error': 'New passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Check password complexity using Django's validators
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        try:
            validate_password(new_password, user=request.user)
        except ValidationError as e:
            return Response({'error': ' '.join(map(str, e.messages))}, status=status.HTTP_400_BAD_REQUEST)

        # 4. Save new password
        request.user.set_password(new_password)
        request.user.save()

        # 5. Log audit event
        AuditLog.objects.create(
            employee=request.user,
            employeeName=f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email,
            action="Password Changed",
            details="User successfully changed their password."
        )

        # 6. Generate fresh tokens so the user's session remains authenticated without logout
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(request.user)
        serializer = EmployeeSerializer(request.user)

        return Response({
            'message': 'Password has been successfully updated.',
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': serializer.data
        }, status=status.HTTP_200_OK)
