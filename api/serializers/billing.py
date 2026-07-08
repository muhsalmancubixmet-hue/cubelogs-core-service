# api/serializers/billing.py
from rest_framework import serializers
from api.models import SubscriptionPackage, SubscriberAccount, WalletTransaction, Wallet, BackofficeCoupon
from api.serializers.utils import extract_youtube_id

class SubscriptionPackageSerializer(serializers.ModelSerializer):
    embed_url = serializers.SerializerMethodField()
    activeUsersCount = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPackage
        fields = ['id', 'name', 'price', 'employeeLimit', 'features', 'isActive', 'video_url', 'embed_url', 'activeUsersCount', 'createdAt']

    def get_activeUsersCount(self, obj):
        from api.models import Employee
        features = obj.features if isinstance(obj.features, list) else []
        features_lower = [f.lower() for f in features]
        
        is_attendance = 'attendance' in features_lower or any('attendance' in f or 'leaves' in f or 'holidays' in f for f in features_lower)
        is_project = 'project' in features_lower or 'tasks' in features_lower or any('tasks' in f for f in features_lower)
        
        name_lower = obj.name.lower()
        if 'attendance' in name_lower:
            is_attendance = True
        if 'project' in name_lower or 'task' in name_lower:
            is_project = True

        if not is_attendance and not is_project:
            return 0

        qs = Employee.objects.filter(is_active=True, organization__isnull=False)

        if is_attendance and is_project:
            qs = qs.filter(organization__settings__is_attendance_enabled=True, organization__settings__is_project_enabled=True)
        elif is_attendance:
            qs = qs.filter(organization__settings__is_attendance_enabled=True)
        elif is_project:
            qs = qs.filter(organization__settings__is_project_enabled=True)

        return qs.count()

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
