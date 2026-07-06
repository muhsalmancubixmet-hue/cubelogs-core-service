from rest_framework import serializers
from django.contrib.auth.hashers import make_password
from api.models import (
    Employee, AttendanceLog, Task, LeaveType, Leave,
    Holiday, Template, OfficeLocation, Schedule, OrgSettings, AuditLog, Lead, LeadHistory,
    SubscriptionPackage, SubscriberAccount, CMSContent, LMSModule, Coupon,
    Wallet, WalletTransaction, BackofficeCoupon
)
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.exceptions import InvalidToken

class CustomTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except get_user_model().DoesNotExist:
            raise InvalidToken("User does not exist or has been deleted.")

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
        
        # Check if an employee with this email already exists
        email = validated_data.get('email', '')
        employee = Employee.objects.filter(email=email).first() or Employee.objects.filter(username=email).first()
        raw_password = None
        
        if employee:
            # Update existing employee details and organization
            if request and request.user and request.user.is_authenticated:
                employee.organization = request.user.organization
            
            for attr, value in validated_data.items():
                if attr not in ['password', 'username', 'email']:
                    setattr(employee, attr, value)
            employee.save()
        else:
            # Default username logic
            if 'username' not in validated_data:
                validated_data['username'] = email
            
            # Ignore any password provided by frontend
            validated_data.pop('password', None)
            
            if request and request.user and request.user.is_authenticated:
                if request.user.isSuperAdmin and request.user.organization is None and validated_data.get('organization'):
                    pass
                else:
                    validated_data['organization'] = request.user.organization

            employee = super().create(validated_data)
            
            # Generate random password
            import secrets
            import string
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            raw_password = ''.join(secrets.choice(alphabet) for i in range(12))
            employee.set_password(raw_password)
            employee.save()
        
        # Generate revoke token
        signer = TimestampSigner()
        revoke_token = signer.sign(str(employee.id))
        
        # Generate auto-login token
        login_signer = TimestampSigner(salt='auto-login')
        login_token = login_signer.sign(str(employee.id))
        
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')
        manual_login_url = f"{frontend_url}/login"
        magic_login_url = f"{frontend_url}/login/verify?token={login_token}"
        revoke_url = f"{frontend_url}/revoke?token={revoke_token}"
        
        # Send email
        subject = 'Welcome to CubeLogs - Your Login Credentials'
        password_line = f"Password: {raw_password}" if raw_password else "Password: Use your existing password"
        message = f"""Hello {employee.first_name or employee.email},

Welcome to our company! An administrator has created an account for you on CubeLogs.

Click the link below to instantly log in to your dashboard:
Magic Login Link: {magic_login_url}

Using your secure credentials:
Username: {employee.username}
{password_line}

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
                    <p>You can instantly log in to your dashboard by clicking the magic link below:</p>
                    <div style="text-align: center;">
                        <a href="{magic_login_url}" class="btn">Log In Automatically</a>
                    </div>
                    <p>Or manually login at {manual_login_url} using your secure credentials:</p>
                    <div class="credentials">
                        <p><strong>Username:</strong> {employee.username}</p>
                        <p><strong>{password_line}</strong></p>
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
                'no-reply@cubelogs.com',
                [employee.email],
                fail_silently=False,
                html_message=html_message
            )
        except Exception as e:
            print(f"Failed to send email: {e}")
            
        return employee

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        employee = super().update(instance, validated_data)
        if password:
            employee.set_password(password)
            employee.save()
        return employee

class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'

    def create(self, validated_data):
        if 'employeeName' not in validated_data or not validated_data['employeeName']:
            employee = validated_data['employee']
            validated_data['employeeName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)

class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = '__all__'

    def create(self, validated_data):
        if 'assignedName' not in validated_data or not validated_data['assignedName']:
            employee = validated_data['assignedTo']
            validated_data['assignedName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)

class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = '__all__'

class LeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Leave
        fields = '__all__'

    def create(self, validated_data):
        if 'employeeName' not in validated_data or not validated_data['employeeName']:
            employee = validated_data['employee']
            validated_data['employeeName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        if 'leaveTypeName' not in validated_data or not validated_data['leaveTypeName']:
            leave_type = validated_data['leaveType']
            validated_data['leaveTypeName'] = leave_type.name
        return super().create(validated_data)

class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = '__all__'

class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = '__all__'

class OfficeLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OfficeLocation
        fields = '__all__'

class ScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Schedule
        fields = '__all__'

class OrgSettingsSerializer(serializers.ModelSerializer):
    companyName = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = OrgSettings
        fields = [
            'id', 'brandLogo', 'subscriptionDays', 'subscriptionRenewedAt',
            'max_employees_allowed', 'is_attendance_enabled', 'is_project_enabled',
            'subscriptionStatus', 'subscriptionExpiresAt', 'createdAt', 'updatedAt', 'companyName',
            'grace_period_minutes', 'half_day_threshold_minutes', 'full_day_absent_threshold_minutes',
            'auto_approve_attendance'
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if hasattr(instance, 'organization') and instance.organization:
            data['companyName'] = instance.organization.name
        else:
            data['companyName'] = "Head Office"
        return data

    def update(self, instance, validated_data):
        company_name = validated_data.pop('companyName', None)
        instance = super().update(instance, validated_data)
        if company_name is not None:
            if hasattr(instance, 'organization') and instance.organization:
                instance.organization.name = company_name
                instance.organization.save()
        return instance

class AuditLogSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = '__all__'

    def get_organization(self, obj):
        if obj.employee and obj.employee.organization:
            return obj.employee.organization.id
        return None


class LeadSerializer(serializers.ModelSerializer):
    assigned_staff_name = serializers.SerializerMethodField()
    read_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = '__all__'

    def get_assigned_staff_name(self, obj):
        if obj.assigned_staff:
            return f"{obj.assigned_staff.first_name} {obj.assigned_staff.last_name}".strip() or obj.assigned_staff.email
        return None

    def get_read_by_name(self, obj):
        if obj.read_by:
            return f"{obj.read_by.first_name} {obj.read_by.last_name}".strip() or obj.read_by.email
        return None


class LeadHistorySerializer(serializers.ModelSerializer):
    modified_by_email = serializers.SerializerMethodField()
    modified_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LeadHistory
        fields = ['id', 'lead', 'modified_by', 'modified_by_email', 'modified_by_name', 'action', 'timestamp']

    def get_modified_by_email(self, obj):
        return obj.modified_by.email if obj.modified_by else None

    def get_modified_by_name(self, obj):
        if obj.modified_by:
            return f"{obj.modified_by.first_name} {obj.modified_by.last_name}".strip() or obj.modified_by.email
        return "System"


class SubscriptionPackageSerializer(serializers.ModelSerializer):
    embed_url = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPackage
        fields = ['id', 'name', 'price', 'employeeLimit', 'features', 'isActive', 'video_url', 'embed_url', 'createdAt']

    def get_embed_url(self, obj):
        video_id = extract_youtube_id(obj.video_url)
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&loop=1&playlist={video_id}"
        return ""

    def validate_features(self, value):
        if not isinstance(value, list):
            return value
            
        feature_map = {
            'geofencing': 'geofence',
            'geofence': 'geofence',
            'geo': 'geofence',
            'biometric photos': 'biometric',
            'biometric photo': 'biometric',
            'biometric': 'biometric',
            'biometrics': 'biometric',
            'webcam': 'biometric',
            'scheduling': 'scheduling',
            'schedule': 'scheduling',
            'shifts': 'scheduling',
            'shift scheduling': 'scheduling',
            'audit logs': 'auditLogs',
            'auditlog': 'auditLogs',
            'auditlogs': 'auditLogs',
            'audit log': 'auditLogs',
            'multi location': 'multiLocation',
            'multilocation': 'multiLocation',
            'multi-location': 'multiLocation',
            'dashboard': 'dashboard',
            'attendance staff': 'attendance:staff',
            'attendance:staff': 'attendance:staff',
            'leaves apply': 'leaves:apply',
            'leaves:apply': 'leaves:apply',
            'leaves approve': 'leaves:approve',
            'leaves:approve': 'leaves:approve',
            'tasks view': 'tasks:view',
            'tasks:view': 'tasks:view',
            'tasks create': 'tasks:create',
            'tasks:create': 'tasks:create',
            'admin templates': 'admin:templates',
            'admin:templates': 'admin:templates',
            'attendance admin': 'attendance:admin',
            'attendance:admin': 'attendance:admin',
            'leaves manage': 'leaves:manage',
            'leaves:manage': 'leaves:manage',
            'holidays view': 'holidays:view',
            'holidays:view': 'holidays:view',
            'holidays manage': 'holidays:manage',
            'holidays:manage': 'holidays:manage',
            'locations manage': 'locations:manage',
            'locations:manage': 'locations:manage',
            'settings branding': 'settings:branding',
            'settings:branding': 'settings:branding',
            'settings billing': 'settings:billing',
            'settings:billing': 'settings:billing',
        }
        
        normalized = []
        for item in value:
            item_str = str(item).strip().lower()
            if item_str in feature_map:
                normalized.append(feature_map[item_str])
            else:
                normalized.append(item_str)
        return normalized


class SubscriberAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriberAccount
        fields = '__all__'


class CMSContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CMSContent
        fields = '__all__'


class LMSModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LMSModule
        fields = '__all__'


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = '__all__'


class WalletTransactionSerializer(serializers.ModelSerializer):
    employee_email = serializers.SerializerMethodField()
    organization_name = serializers.SerializerMethodField()
    wallet_balance = serializers.SerializerMethodField()

    class Meta:
        model = WalletTransaction
        fields = '__all__'

    def get_employee_email(self, obj):
        if obj.wallet and obj.wallet.employee:
            return obj.wallet.employee.email
        return None

    def get_organization_name(self, obj):
        if obj.wallet and obj.wallet.organization:
            return obj.wallet.organization.name
        return None

    def get_wallet_balance(self, obj):
        if obj.wallet:
            return str(obj.wallet.balance)
        return None

    def to_representation(self, instance):
        from decimal import InvalidOperation, Decimal
        try:
            Decimal(str(instance.amount))
        except (InvalidOperation, ValueError, TypeError):
            instance.amount = Decimal('0.00')
        return super().to_representation(instance)


class WalletSerializer(serializers.ModelSerializer):
    transactions = WalletTransactionSerializer(many=True, read_only=True)
    employee_email = serializers.EmailField(source='employee.email', read_only=True)
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = ['id', 'employee', 'employee_email', 'employee_name', 'balance', 'stripe_customer_id', 'transactions', 'createdAt', 'updatedAt']

    def get_employee_name(self, obj):
        return f"{obj.employee.first_name} {obj.employee.last_name}".strip() or obj.employee.email

    def to_representation(self, instance):
        from decimal import InvalidOperation, Decimal
        try:
            Decimal(str(instance.balance))
        except (InvalidOperation, ValueError, TypeError):
            instance.balance = Decimal('0.00')
        return super().to_representation(instance)


class BackofficeCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackofficeCoupon
        fields = '__all__'


def extract_youtube_id(url):
    if not url:
        return None
    import urllib.parse as urlparse
    parsed = urlparse.urlparse(url)
    if parsed.hostname == 'youtu.be':
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            p = urlparse.parse_qs(parsed.query)
            return p.get('v', [None])[0]
        if parsed.path.startswith(('/embed/', '/shorts/')):
            parts = parsed.path.split('/')
            if len(parts) > 2:
                return parts[2]
    return None


class PromoVideoSectionSerializer(serializers.ModelSerializer):
    embed_url = serializers.SerializerMethodField()

    class Meta:
        from api.models import PromoVideoSection
        model = PromoVideoSection
        fields = ['id', 'title', 'description', 'youtube_url', 'embed_url', 'is_active']

    def get_embed_url(self, obj):
        video_id = extract_youtube_id(obj.youtube_url)
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&loop=1&playlist={video_id}"
        return ""


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        from api.models import Testimonial
        model = Testimonial
        fields = '__all__'




