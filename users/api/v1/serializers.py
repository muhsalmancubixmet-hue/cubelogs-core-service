# --------------------------------------------------------------------------------
#       Users Serializers
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib.auth import get_user_model

# THIRD PARTY
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.exceptions import InvalidToken

# APPLICATION SPECIFIC
from users.models import Employee

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
            'phone', 'designation', 'isSuperAdmin', 'is_active', 'employment_status',
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
        from subscribers.models import SubscriberAccount, SubscriptionPackage
        from users.models import Employee
        from core.models import OrgSettings
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
        from users.models import Employee
        from users.api.v1.services import UserService
        from core.utils import generate_secure_password

        request = self.context.get('request')
        request_user = request.user if request and request.user and request.user.is_authenticated else None

        # Enforce org employee cap
        if request_user and request_user.organization:
            org = request_user.organization
            if org.settings:
                limit = org.settings.max_employees_allowed
                if Employee.objects.filter(organization=org).count() >= limit:
                    raise serializers.ValidationError({
                        'non_field_errors': [
                            f"Your active organization workspace is capped at {limit} active employees. "
                            f"Please upgrade your package to onboard more employees."
                        ]
                    })

        # Parse combined name field if provided
        name = self.initial_data.get('name')
        if name:
            parts = name.strip().split(' ', 1)
            validated_data['first_name'] = parts[0]
            validated_data['last_name'] = parts[1] if len(parts) > 1 else ''

        email = validated_data.get('email', '')
        employee = (
            Employee.objects.filter(email=email).first()
            or Employee.objects.filter(username=email).first()
        )
        raw_password = None

        if employee:
            # Update existing employee record
            for attr, value in validated_data.items():
                if attr not in ['password', 'username', 'email']:
                    setattr(employee, attr, value)
            employee.save()
        else:
            validated_data.setdefault('username', email)
            validated_data.pop('password', None)

            # Assign org (root superadmins may specify their own org via payload)
            if request_user:
                if not (request_user.isSuperAdmin and request_user.organization is None and validated_data.get('organization')):
                    validated_data['organization'] = request_user.organization

            employee = super().create(validated_data)

            raw_password = generate_secure_password(12)
            employee.set_password(raw_password)
            employee._raw_password = raw_password
            employee.save()

        # Send admin onboarding email via service
        try:
            UserService.send_admin_onboarding_email(employee, raw_password, synchronous=True)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to send onboarding email to %s: %s", employee.email, exc)

        return employee

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        status_val = validated_data.get('employment_status')
        if status_val:
            if status_val in ['Deactivated', 'Terminated', 'Resigned']:
                validated_data['is_active'] = False
            elif status_val == 'Active':
                validated_data['is_active'] = True

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
