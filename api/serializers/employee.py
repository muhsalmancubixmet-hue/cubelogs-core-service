# api/serializers/employee.py
from rest_framework import serializers
from api.models import Employee

class EmployeeSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    email = serializers.EmailField()
    username = serializers.CharField(required=False)

    def validate_email(self, value):
        if self.instance and self.instance.email != value:
            if Employee.objects.filter(email=value).exists():
                raise serializers.ValidationError("An employee with this email already exists.")
        return value

    class Meta:
        model = Employee
        fields = [
            'id', 'email', 'username', 'first_name', 'last_name', 'name',
            'phone', 'designation', 'isSuperAdmin', 
            'useDefaultPermissions', 'permissions', 'profilePhoto', 'password',
            'subscription', 'organization'
        ]
        extra_kwargs = {
            'password': {'write_only': True, 'required': False},
            'username': {'required': False}
        }

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email

    def get_subscription(self, obj):
        from api.models import SubscriberAccount, SubscriptionPackage, Employee, OrgSettings
        from django.utils import timezone
        
        target_email = obj.email
        if not obj.isSuperAdmin:
            domain = obj.email.split('@')[-1] if '@' in obj.email else ''
            superadmin = Employee.objects.filter(isSuperAdmin=True, email__endswith='@' + domain).first()
            if not superadmin:
                superadmin = Employee.objects.filter(isSuperAdmin=True).first()
            if superadmin:
                target_email = superadmin.email
                
        sub = SubscriberAccount.objects.filter(email=target_email, isActive=True).first()
        
        packageName = "Free Package"
        expiresAt = None
        isActive = True
        
        if sub:
            packageName = sub.packageName
            expiresAt = sub.expiresAt
            isActive = sub.isActive
            
        pkg = SubscriptionPackage.objects.filter(name=packageName).first()
        features = pkg.features if pkg else []
        employeeLimit = pkg.employeeLimit if pkg else 5
        
        days_remaining = 12
        if expiresAt:
            delta = expiresAt - timezone.now()
            days_remaining = max(0, delta.days)
        else:
            settings_obj = OrgSettings.objects.filter(id=1).first()
            if settings_obj:
                days_remaining = settings_obj.subscriptionDays
                
        is_attendance_enabled = False
        is_project_enabled = False
        max_employees_allowed = 10
        org = obj.organization
        warning_active = False
        seconds_remaining = 0
        if org and org.settings:
            is_attendance_enabled = org.settings.is_attendance_enabled
            is_project_enabled = org.settings.is_project_enabled
            max_employees_allowed = org.settings.max_employees_allowed
            if org.settings.subscriptionExpiresAt:
                delta = org.settings.subscriptionExpiresAt - timezone.now()
                seconds_remaining = max(0, int(delta.total_seconds()))
                days_remaining = max(0, int(delta.total_seconds() / 60))
                if 0 < seconds_remaining <= 300:
                    warning_active = True
            else:
                days_remaining = org.settings.subscriptionDays
        else:
            settings_obj = OrgSettings.objects.filter(id=1).first()
            if settings_obj:
                is_attendance_enabled = settings_obj.is_attendance_enabled
                is_project_enabled = settings_obj.is_project_enabled
                max_employees_allowed = settings_obj.max_employees_allowed
                if settings_obj.subscriptionExpiresAt:
                    delta = settings_obj.subscriptionExpiresAt - timezone.now()
                    seconds_remaining = max(0, int(delta.total_seconds()))
                    days_remaining = max(0, int(delta.total_seconds() / 60))
                    if 0 < seconds_remaining <= 300:
                        warning_active = True
                else:
                    days_remaining = settings_obj.subscriptionDays
                
        return {
            'packageName': packageName,
            'isActive': isActive,
            'expiresAt': expiresAt.isoformat() if expiresAt else None,
            'features': features,
            'employeeLimit': employeeLimit,
            'daysRemaining': days_remaining,
            'is_attendance_enabled': is_attendance_enabled,
            'is_project_enabled': is_project_enabled,
            'max_employees_allowed': max_employees_allowed,
            'subscriptionStatus': getattr(org.settings, 'subscriptionStatus', 'Active') if org and org.settings else 'Active',
            'warningActive': warning_active,
            'secondsRemaining': seconds_remaining,
        }

    def create(self, validated_data):
        from django.contrib.auth.models import BaseUserManager
        from django.core.mail import send_mail
        from django.core.signing import TimestampSigner
        from django.conf import settings
        from api.models import SubscriberAccount, SubscriptionPackage, Employee
        
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            user = request.user
            org = user.organization
            if org and org.settings:
                limit = org.settings.max_employees_allowed
                current_count = Employee.objects.filter(organization=org).count()
                if current_count >= limit:
                    raise serializers.ValidationError({
                        'non_field_errors': [
                            f"Your active organization workspace is capped at {limit} active employees. "
                            f"Please upgrade your package to onboard more employees."
                        ]
                    })
        
        name = self.initial_data.get('name')
        if name:
            parts = name.strip().split(' ', 1)
            validated_data['first_name'] = parts[0]
            validated_data['last_name'] = parts[1] if len(parts) > 1 else ''

        email = validated_data.get('email', '')
        employee = Employee.objects.filter(email=email).first() or Employee.objects.filter(username=email).first()
        raw_password = None
        
        if employee:
            if request and request.user and request.user.is_authenticated:
                employee.organization = request.user.organization
            
            for attr, value in validated_data.items():
                if attr not in ['password', 'username', 'email']:
                    setattr(employee, attr, value)
            employee.save()
        else:
            if 'username' not in validated_data:
                validated_data['username'] = email
            
            validated_data.pop('password', None)
            
            if request and request.user and request.user.is_authenticated:
                if request.user.isSuperAdmin and request.user.organization is None and validated_data.get('organization'):
                    pass
                else:
                    validated_data['organization'] = request.user.organization

            employee = super().create(validated_data)
            
            import secrets
            import string
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            raw_password = ''.join(secrets.choice(alphabet) for i in range(12))
            employee.set_password(raw_password)
            employee.save()
        
        signer = TimestampSigner()
        revoke_token = signer.sign(str(employee.id))
        
        login_signer = TimestampSigner(salt='auto-login')
        login_token = login_signer.sign(str(employee.id))
        
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')
        manual_login_url = f"{frontend_url}/login"
        magic_login_url = f"{frontend_url}/login/verify?token={login_token}"
        revoke_url = f"{frontend_url}/revoke?token={revoke_token}"
        
        subject = 'Welcome to CubeLogs - Your Login Credentials'
        password_val = raw_password if raw_password else "Use your existing password"
        message = f"""Hello {employee.first_name or employee.email},

Welcome to our company! An administrator has created an account for you on CubeLogs.

Click the link below to instantly log in to your dashboard:
Magic Login Link: {magic_login_url}
Email: {employee.email}
Password: {password_val}

---
If you did not expect this or believe this was a mistake, please click the link below to instantly revoke your registration:
It's Not Me: {revoke_url}
"""

        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f0f4f8; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); }}
                .header {{ background-color: #2563eb; color: #ffffff; padding: 30px 40px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px; }}
                .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 15px; }}
                .content h2 {{ color: #1e293b; font-size: 20px; margin-top: 0; font-weight: 600; }}
                .btn {{ display: inline-block; background-color: #3b82f6; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 6px; font-weight: 600; margin: 24px 0; text-align: center; font-size: 15px; transition: background-color 0.2s; }}
                .btn:hover {{ background-color: #1d4ed8; }}
                .btn-danger {{ display: inline-block; background-color: #ef4444; color: #ffffff !important; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; margin-top: 10px; font-size: 14px; transition: background-color 0.2s; }}
                .btn-danger:hover {{ background-color: #dc2626; }}
                .credentials {{ background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; margin-top: 20px; }}
                .credentials p {{ margin: 6px 0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px; color: #0f172a; }}
                .credentials strong {{ color: #64748b; font-family: 'Inter', sans-serif; display: inline-block; width: 80px; }}
                .footer {{ background-color: #f8fafc; padding: 20px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
                .revoke-section {{ margin-top: 40px; padding-top: 20px; border-top: 1px dashed #cbd5e1; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Welcome to CubeLogs</h1>
                </div>
                <div class="content">
                    <h2>Hello,</h2>
                    <p>Welcome to our company! An administrator has created a new account for you on the CubeLogs platform.</p>
                    <p>You can instantly log in to your dashboard by clicking the button below:</p>
                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{magic_login_url}" class="btn" style="margin: 0 auto 12px auto; display: inline-block;">Go to Dashboard</a>
                        <div style="font-size: 14px; color: #475569; margin-top: 8px; text-align: center;">
                            <p style="margin: 4px 0;"><strong>Email:</strong> {employee.email}</p>
                            <p style="margin: 4px 0;"><strong>Password:</strong> {password_val}</p>
                        </div>
                    </div>
                    
                    <div class="revoke-section">
                        <p style="color: #64748b; font-size: 14px; margin-bottom: 10px;">If you did not expect this or believe it was a mistake, please revoke this registration instantly:</p>
                        <a href="{revoke_url}" class="btn-danger">It's Not Me - Revoke</a>
                    </div>
                </div>
                <div class="footer">
                    &copy; 2026 CubeLogs Inc. All rights reserved.<br>
                    This is an automated message.
                </div>
            </div>
        </body>
        </html>
        """
        try:
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [employee.email],
                fail_silently=False,
                html_message=html_message
            )
        except Exception as e:
            print(f"Failed to send email: {e}")
            
        return employee

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        name = self.initial_data.get('name')
        if name:
            parts = name.strip().split(' ', 1)
            validated_data['first_name'] = parts[0]
            validated_data['last_name'] = parts[1] if len(parts) > 1 else ''
        employee = super().update(instance, validated_data)
        if password:
            employee.set_password(password)
            employee.save()
        return employee
