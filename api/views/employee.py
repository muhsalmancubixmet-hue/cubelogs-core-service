"""
api/views/employee.py — Employee management views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from api.models import Employee, AuditLog
from api.serializers import EmployeeSerializer


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.all().order_by('id')
    serializer_class = EmployeeSerializer

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
            if request.user.email == 'salmankcsiju@gmail.com':
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
            # Token is valid for 7 days (604800 seconds)
            employee_id = signer.unsign(token, max_age=604800)
            employee = Employee.objects.get(id=employee_id)
            name = f"{employee.first_name} {employee.last_name}".strip() or employee.email
            email = employee.email
            # Delete the employee record as requested "instantly revoke the registration, deactivate or delete the account"
            employee.delete()

            # Log revocation
            AuditLog.objects.create(
                employee=None,
                employeeName=name,
                action="Registration Revoked",
                details=f"Revoked registration for {name} ({email}) via revoke token."
            )
            return Response({'message': 'Registration successfully revoked.'}, status=status.HTTP_200_OK)
        except SignatureExpired:
            return Response({'error': 'Token has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({'error': 'Invalid token.'}, status=status.HTTP_400_BAD_REQUEST)
        except Employee.DoesNotExist:
            return Response({'error': 'Account already deleted or does not exist.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'], url_path='bulk-upload')
    def bulk_upload(self, request):
        import pandas as pd
        import re
        import secrets
        import string
        from django.core.mail import send_mail
        from django.core.signing import TimestampSigner
        from django.conf import settings as dj_settings
        from api.models import Template, Schedule

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
                alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
                raw_password = ''.join(secrets.choice(alphabet) for _ in range(12))

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

                # Generate tokens
                signer = TimestampSigner()
                revoke_token = signer.sign(str(employee.id))

                login_signer = TimestampSigner(salt='auto-login')
                login_token = login_signer.sign(str(employee.id))

                frontend_url = getattr(dj_settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')
                manual_login_url = f"{frontend_url}/login"
                magic_login_url = f"{frontend_url}/login/verify?token={login_token}"
                revoke_url = f"{frontend_url}/revoke?token={revoke_token}"

                subject = 'Welcome to CubeLogs - Your Login Credentials'
                password_line = f"Password: {raw_password}"
                message = f"Hello {employee.first_name},\n\nWelcome to our company! An administrator has created an account for you on CubeLogs.\n\nClick the link below to log in:\nMagic Login Link: {magic_login_url}\n\nCredentials:\nUsername: {employee.username}\n{password_line}\n\n---\nRevoke Registration: {revoke_url}"

                # Send Onboarding credential email
                try:
                    send_mail(
                        subject,
                        message,
                        dj_settings.DEFAULT_FROM_EMAIL,
                        [employee.email],
                        fail_silently=False
                    )
                except Exception:
                    # SMTP Delivery Failed! delete created user so admin can fix and re-onboard
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
