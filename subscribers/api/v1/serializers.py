from rest_framework import serializers
from subscribers.models import SubscriptionPackage, SubscriberAccount, Wallet, WalletTransaction, Coupon, BackofficeCoupon, GlobalBillingSettings
from users.models import Employee
from core.utils import extract_youtube_id

class SubscriptionPackageSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    embed_url = serializers.SerializerMethodField()
    activeUsersCount = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPackage
        fields = ['id', 'name', 'price', 'employeeLimit', 'features', 'isActive', 'video_url', 'embed_url', 'activeUsersCount', 'createdAt']

    def get_activeUsersCount(self, obj):
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
            qs = qs.filter(
                organization__organization_modules__module_id='attendance',
                organization__organization_modules__enabled=True
            ).filter(
                organization__organization_modules__module_id='tasks',
                organization__organization_modules__enabled=True
            )
        elif is_attendance:
            qs = qs.filter(
                organization__organization_modules__module_id='attendance',
                organization__organization_modules__enabled=True
            )
        elif is_project:
            qs = qs.filter(
                organization__organization_modules__module_id='tasks',
                organization__organization_modules__enabled=True
            )

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


# ==============================================================================
# Billing Serializers
# ==============================================================================

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
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    transactions = WalletTransactionSerializer(many=True, read_only=True)
    employee_email = serializers.EmailField(source='employee.email', read_only=True)
    employee_name = serializers.SerializerMethodField()
    attendance_module_price = serializers.SerializerMethodField()
    tasks_module_price = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            'id', 'employee', 'employee_email', 'employee_name', 'balance',
            'stripe_customer_id', 'transactions', 'createdAt', 'updatedAt',
            'attendance_module_price', 'tasks_module_price'
        ]

    def get_employee_name(self, obj):
        return f"{obj.employee.first_name} {obj.employee.last_name}".strip() or obj.employee.email

    def get_attendance_module_price(self, obj):
        # Use the SubscriptionPackage price — same value shown in the backoffice Registered Modules table.
        # Prefer a package explicitly named 'Attendance Management'; exclude free packages (price=0).
        pkg = (
            SubscriptionPackage.objects
            .filter(features__icontains='attendance', isActive=True)
            .exclude(price=0)
            .order_by('-price')  # highest-priced standalone module first (avoids cheap bundles)
            .first()
        )
        # Further narrow: prefer the one whose name includes 'Attendance'
        named_pkg = (
            SubscriptionPackage.objects
            .filter(name__icontains='attendance', isActive=True)
            .exclude(price=0)
            .first()
        )
        if named_pkg:
            return str(named_pkg.price)
        if pkg:
            return str(pkg.price)
        g_settings, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        return str(g_settings.attendance_module_price)

    def get_tasks_module_price(self, obj):
        # Use the SubscriptionPackage price — same value shown in the backoffice Registered Modules table.
        # Prefer a package explicitly named 'Project & Tasks Management'; exclude free packages.
        named_pkg = (
            SubscriptionPackage.objects
            .filter(name__icontains='project', isActive=True)
            .exclude(price=0)
            .first()
        )
        if named_pkg:
            return str(named_pkg.price)
        pkg = (
            SubscriptionPackage.objects
            .filter(features__icontains='project', isActive=True)
            .exclude(price=0)
            .order_by('price')
            .first()
        )
        if pkg:
            return str(pkg.price)
        g_settings, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        return str(g_settings.tasks_module_price)

    def to_representation(self, instance):
        from decimal import InvalidOperation, Decimal
        try:
            Decimal(str(instance.balance))
        except (InvalidOperation, ValueError, TypeError):
            instance.balance = Decimal('0.00')
        return super().to_representation(instance)


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = '__all__'


class BackofficeCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackofficeCoupon
        fields = '__all__'


class GlobalBillingSettingsSerializer(serializers.ModelSerializer):
    attendance_daily_price = serializers.SerializerMethodField()
    tasks_daily_price = serializers.SerializerMethodField()
    days_in_current_month = serializers.SerializerMethodField()

    class Meta:
        model = GlobalBillingSettings
        fields = '__all__'

    def get_days_in_current_month(self, obj):
        import calendar
        from django.utils import timezone
        now = timezone.now()
        return calendar.monthrange(now.year, now.month)[1]

    def get_attendance_daily_price(self, obj):
        import calendar
        from decimal import Decimal
        from django.utils import timezone
        now = timezone.now()
        total_days = calendar.monthrange(now.year, now.month)[1]
        if total_days == 0:
            return "0.00"
        daily = Decimal(str(obj.attendance_module_price)) / Decimal(str(total_days))
        return str(daily.quantize(Decimal('0.01')))

    def get_tasks_daily_price(self, obj):
        import calendar
        from decimal import Decimal
        from django.utils import timezone
        now = timezone.now()
        total_days = calendar.monthrange(now.year, now.month)[1]
        if total_days == 0:
            return "0.00"
        daily = Decimal(str(obj.tasks_module_price)) / Decimal(str(total_days))
        return str(daily.quantize(Decimal('0.01')))
