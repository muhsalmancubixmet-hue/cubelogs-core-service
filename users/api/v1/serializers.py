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
from users.models import Employee, Role, PermissionFlag

class CustomTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
            from core.tenant import TenantContext
            from users.models import OrganizationMembership

            # attrs['refresh'] is the incoming refresh token string
            # Inspect claims BEFORE super().validate blacklists it when BLACKLIST_AFTER_ROTATION is enabled
            incoming_refresh = RefreshToken(attrs['refresh'])
            token_active_org_id = incoming_refresh.get('active_org_id')
            token_membership_id = incoming_refresh.get('membership_id')

            data = super().validate(attrs)

            access_token = AccessToken(data['access'])
            user_id = access_token.get('user_id')
            if user_id:
                user = get_user_model().objects.get(id=user_id)
                if not user.is_active:
                    raise InvalidToken("User account is inactive.")

                active_membership = None
                if token_membership_id:
                    active_membership = OrganizationMembership.objects.filter(
                        id=token_membership_id,
                        user=user,
                        is_active_in_org=True,
                        is_deleted=False
                    ).select_related('organization', 'role').first()
                    if not active_membership:
                        raise InvalidToken("Active membership has been deactivated or removed.")
                elif token_active_org_id:
                    # Only enforce if user has memberships; if user has an inactive membership in that org, fail closed
                    active_membership = OrganizationMembership.objects.filter(
                        organization_id=token_active_org_id,
                        user=user,
                        is_active_in_org=True,
                        is_deleted=False
                    ).select_related('organization', 'role').first()
                    if not active_membership:
                        # Check if an inactive membership exists for this org
                        inactive_mem = OrganizationMembership.objects.filter(
                            organization_id=token_active_org_id,
                            user=user,
                            is_active_in_org=False
                        ).exists()
                        if inactive_mem:
                            raise InvalidToken("Active membership in organization has been deactivated or removed.")
                else:
                    fake_req = type('Req', (), {'user': user, 'is_authenticated': True})()
                    active_membership = TenantContext.get_active_membership(fake_req)

                if active_membership:
                    access_token['active_org_id'] = active_membership.organization_id
                    access_token['membership_id'] = active_membership.id
                    # If refresh rotation is enabled, preserve claims on rotated refresh
                    if 'refresh' in data:
                        rotated_refresh = RefreshToken(data['refresh'])
                        rotated_refresh['active_org_id'] = active_membership.organization_id
                        rotated_refresh['membership_id'] = active_membership.id
                        data['refresh'] = str(rotated_refresh)
                else:
                    access_token['active_org_id'] = getattr(user, 'organization_id', None)
                    access_token['membership_id'] = None

                data['access'] = str(access_token)

            return data
        except get_user_model().DoesNotExist:
            raise InvalidToken("User does not exist or has been deleted.")



class PermissionFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = PermissionFlag
        fields = ['id', 'key', 'name', 'description', 'module', 'category', 'is_active']


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    permission_keys = serializers.ListField(child=serializers.CharField(), write_only=True, required=False)
    users_count = serializers.SerializerMethodField()
    permissions_count = serializers.SerializerMethodField()
    assigned_employees = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = [
            'id', 'name', 'slug', 'label', 'description', 'is_system_role',
            'is_active', 'organization', 'permissions', 'permission_keys',
            'users_count', 'permissions_count', 'assigned_employees',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['is_system_role', 'created_at', 'updated_at']
        extra_kwargs = {
            'slug': {'required': False},
            'organization': {'required': False}
        }
        validators = []

    def get_permissions(self, obj):
        return list(obj.permissions.values_list('key', flat=True))

    def get_users_count(self, obj):
        return obj.employees.count()

    def get_permissions_count(self, obj):
        return obj.permissions.count()

    def get_assigned_employees(self, obj):
        return [
            {
                'id': emp.id,
                'name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                'email': emp.email,
                'designation': emp.designation or 'Employee'
            }
            for emp in obj.employees.all()[:10]
        ]

    def create(self, validated_data):
        permission_keys = validated_data.pop('permission_keys', None)
        if permission_keys is None:
            permission_keys = self.initial_data.get('permissions') or self.initial_data.get('permission_keys')
        request = self.context.get('request')
        if request and request.user:
            active_org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
            if active_org:
                validated_data['organization'] = active_org

        name = validated_data.get('name', '')
        if not validated_data.get('slug'):
            from django.utils.text import slugify
            validated_data['slug'] = slugify(name)

        role = super().create(validated_data)
        if permission_keys is not None:
            perm_objs = PermissionFlag.objects.filter(key__in=permission_keys)
            role.permissions.set(perm_objs)
        return role

    def update(self, instance, validated_data):
        permission_keys = validated_data.pop('permission_keys', None)
        if permission_keys is None and ('permissions' in self.initial_data or 'permission_keys' in self.initial_data):
            permission_keys = self.initial_data.get('permissions') or self.initial_data.get('permission_keys')
        if instance.is_system_role:
            # System roles cannot be renamed or change slug
            validated_data.pop('name', None)
            validated_data.pop('slug', None)

        role = super().update(instance, validated_data)
        if permission_keys is not None:
            perm_objs = PermissionFlag.objects.filter(key__in=permission_keys)
            role.permissions.set(perm_objs)
        return role


class EmployeeSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    effective_permissions = serializers.SerializerMethodField()
    email = serializers.EmailField()
    username = serializers.CharField(required=False)

    def validate_email(self, value):
        if self.instance and self.instance.email != value:
            if Employee.objects.filter(email=value).exists():
                raise serializers.ValidationError("An employee with this email already exists.")
        return value

    def validate(self, attrs):
        # Strict tenant validation for Role
        role_input = self.initial_data.get('role')
        if role_input is not None and role_input != '':
            from users.models import Role
            request_user = self.context.get('request').user if (self.context.get('request') and self.context.get('request').user) else None
            org = attrs.get('organization') or (self.instance.organization if self.instance else None) or (getattr(request_user, 'organization', None) if request_user else None)

            role_obj = None
            if isinstance(role_input, Role):
                role_obj = role_input
            elif isinstance(role_input, int) or (isinstance(role_input, str) and str(role_input).isdigit()):
                role_obj = Role.objects.filter(id=int(role_input), is_active=True).first()
            elif isinstance(role_input, str):
                role_obj = Role.objects.filter(name=role_input, is_active=True).first()

            if not role_obj:
                raise serializers.ValidationError({"role": "Invalid role for this organization."})

            # Check organization ownership: must belong to same org or be active system role
            is_same_org = (org is not None and role_obj.organization_id == getattr(org, 'id', org))
            is_valid_system = (role_obj.organization is None and role_obj.is_system_role)
            if not (is_same_org or is_valid_system):
                raise serializers.ValidationError({"role": "Invalid role for this organization."})

            attrs['role'] = role_obj
            attrs['role_name'] = role_obj.name

        # Date Validation
        joining_date = attrs.get('joining_date', getattr(self.instance, 'joining_date', None))
        last_working_date = attrs.get('last_working_date', getattr(self.instance, 'last_working_date', None))
        if joining_date and last_working_date and last_working_date < joining_date:
            raise serializers.ValidationError({"last_working_date": "Last working date cannot be earlier than joining date."})

        # Resigned / Terminated Requires Last Working Date (default to today if not provided)
        employment_status = attrs.get('employment_status', getattr(self.instance, 'employment_status', None))
        if employment_status in ['Resigned', 'Terminated'] and not last_working_date:
            from django.utils import timezone
            attrs['last_working_date'] = timezone.now().date()

        # Classification & Tenant Security Checks on creation
        if not self.instance:
            email = attrs.get('email', '')
            request = self.context.get('request')
            request_user = request.user if request and request.user and request.user.is_authenticated else None

            target_org = getattr(request, 'active_organization', None) or attrs.get('organization')
            if not target_org and request_user and not (request_user.isSuperAdmin and request_user.organization is None and target_org):
                target_org = getattr(request_user, 'organization', None)

            from users.api.v1.services import UserService
            intent, existing = UserService.classify_employee_creation(email, target_org)

            if intent == 'SAME_ORG_EXISTING':
                raise serializers.ValidationError({"email": ["An employee with this email already exists in your organization. Please edit the existing employee instead."]})
            elif intent == 'OTHER_ORG_ACTIVE':
                if not getattr(request, 'active_organization', None):
                    raise serializers.ValidationError({"email": ["This email is currently associated with an active employee in another organization."]})

        return super().validate(attrs)

    class Meta:
        model = Employee
        fields = [
            'id', 'email', 'username', 'first_name', 'last_name', 'name',
            'phone', 'designation', 'role', 'isSuperAdmin', 'is_active', 'employment_status',
            'joining_date', 'last_working_date', 'employee_code', 'department',
            'useDefaultPermissions', 'permissions', 'extra_permissions', 'denied_permissions', 'effective_permissions',
            'profilePhoto', 'password', 'subscription', 'organization'
        ]
        extra_kwargs = {
            'password': {'write_only': True, 'required': False},
            'username': {'required': False}
        }

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        target_org = None
        if request:
            target_org = getattr(request, 'active_organization', None) or (request.user.organization if request.user and request.user.is_authenticated else None)

        if not target_org:
            target_org = instance.organization

        if target_org:
            from users.models import OrganizationMembership
            mem = OrganizationMembership.objects.filter(user=instance, organization=target_org, is_deleted=False).select_related('role').first()
            if mem:
                data['membership_id'] = mem.id
                data['employee_code'] = mem.employee_code
                data['department'] = mem.department or ''
                data['designation'] = mem.designation
                data['employment_status'] = mem.employment_status
                data['joining_date'] = mem.joining_date.isoformat() if mem.joining_date else None
                data['last_working_date'] = mem.last_working_date.isoformat() if mem.last_working_date else None
                data['is_active_in_org'] = mem.is_active_in_org
                if mem.role:
                    data['role'] = mem.role.id
                    data['role_name'] = mem.role.name

        if instance.useDefaultPermissions or not data.get('permissions'):
            data['permissions'] = data.get('effective_permissions') or []

        return data

    def get_effective_permissions(self, obj):
        return obj.get_effective_permissions()
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
        subscription_expires_at = None
        access_locked_date = None

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
        is_expired = False

        # Fetch GlobalBillingSettings for access lock date computation
        try:
            from subscribers.models import GlobalBillingSettings
            g_settings, _ = GlobalBillingSettings.objects.get_or_create(id=1)
            lock_day = g_settings.auto_deduction_day + g_settings.grace_period_days
        except Exception:
            lock_day = 10  # fallback: 5 + 5

        if org and org.settings:
            is_attendance_enabled = org.settings.is_attendance_enabled
            is_project_enabled = org.settings.is_project_enabled
            max_employees_allowed = org.settings.max_employees_allowed
            if org.settings.subscriptionExpiresAt:
                subscription_expires_at = org.settings.subscriptionExpiresAt
                delta = org.settings.subscriptionExpiresAt - timezone.now()
                seconds_remaining = max(0, int(delta.total_seconds()))
                days_remaining = max(0, delta.days)
                if delta.total_seconds() <= 0:
                    is_expired = True
                    # Compute access locked date: lock_day of the current or next month
                    import calendar as cal_mod
                    now_local = timezone.now()
                    lock_month = now_local.month if now_local.day < lock_day else now_local.month + 1
                    lock_year = now_local.year if lock_month <= 12 else now_local.year + 1
                    lock_month = lock_month if lock_month <= 12 else 1
                    max_day = cal_mod.monthrange(lock_year, lock_month)[1]
                    lock_day_clamped = min(lock_day, max_day)
                    from datetime import datetime, timezone as dt_timezone
                    lock_dt = datetime(lock_year, lock_month, lock_day_clamped, tzinfo=dt_timezone.utc)
                    access_locked_date = lock_dt.date().isoformat()
                    lock_delta = lock_dt - timezone.now()
                    days_remaining = max(0, lock_delta.days)
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
                    subscription_expires_at = settings_obj.subscriptionExpiresAt
                    delta = settings_obj.subscriptionExpiresAt - timezone.now()
                    seconds_remaining = max(0, int(delta.total_seconds()))
                    days_remaining = max(0, delta.days)
                    if delta.total_seconds() <= 0:
                        is_expired = True
                    if 0 < seconds_remaining <= 300:
                        warning_active = True
                else:
                    days_remaining = settings_obj.subscriptionDays
                
        return {
            'packageName': packageName,
            'isActive': isActive,
            'expiresAt': expiresAt.isoformat() if expiresAt else None,
            'subscriptionExpiresAt': subscription_expires_at.isoformat() if subscription_expires_at else None,
            'isExpired': is_expired,
            'accessLockedDate': access_locked_date,
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
        from users.models import Employee, OrganizationMembership
        from users.api.v1.services import UserService
        from core.utils import generate_secure_password
        from rest_framework import serializers

        request = self.context.get('request')
        request_user = request.user if request and request.user and request.user.is_authenticated else None

        # Parse combined name field if provided
        name = self.initial_data.get('name')
        if name:
            parts = name.strip().split(' ', 1)
            validated_data['first_name'] = parts[0]
            validated_data['last_name'] = parts[1] if len(parts) > 1 else ''

        email = validated_data.get('email', '').strip().lower()

        # Determine target organization from active tenant context
        target_org = None
        if request:
            target_org = getattr(request, 'active_organization', None)
        if not target_org and request_user:
            target_org = request_user.organization
        if not target_org:
            target_org = validated_data.get('organization')

        if not target_org:
            raise serializers.ValidationError({"organization": "Active organization context is required to create an employee."})

        validated_data['organization'] = target_org

        existing = (
            Employee.objects.filter(email__iexact=email).first()
            or Employee.objects.filter(username__iexact=email).first()
        )

        emp_code = validated_data.get('employee_code')
        dept = validated_data.get('department', '')
        desig = validated_data.get('designation')
        role_obj = validated_data.get('role')
        status_val = validated_data.get('employment_status', 'Active')
        j_date = validated_data.get('joining_date')
        lwd_val = validated_data.get('last_working_date')

        if existing:
            # Check existing membership in target org
            mem = OrganizationMembership.objects.filter(user=existing, organization=target_org).first()

            if mem and mem.is_active_in_org and not mem.is_deleted and mem.employment_status == 'Active':
                raise serializers.ValidationError({"email": "An employee with this email already exists in this organization."})

            if mem:
                # Reactivate / Rehire existing membership
                mem.employment_status = 'Active'
                mem.is_active_in_org = True
                mem.is_deleted = False
                mem.last_working_date = None
                mem.joining_date = j_date or mem.joining_date
                mem.department = dept or mem.department
                mem.designation = desig or mem.designation
                mem.employee_code = emp_code or mem.employee_code
                if role_obj:
                    mem.role = role_obj
                mem.save()
            else:
                # Create new membership in target org for existing global user
                mem = OrganizationMembership.objects.create(
                    user=existing,
                    organization=target_org,
                    employee_code=emp_code,
                    department=dept or '',
                    designation=desig,
                    role=role_obj,
                    employment_status='Active',
                    joining_date=j_date,
                    last_working_date=None,
                    is_active_in_org=True
                )

            # Update basic info if provided
            if validated_data.get('first_name'):
                existing.first_name = validated_data['first_name']
            if validated_data.get('last_name'):
                existing.last_name = validated_data['last_name']
            if validated_data.get('phone'):
                existing.phone = validated_data['phone']
            if role_obj:
                existing.role = role_obj
                existing.role_name = role_obj.name
            existing.organization = target_org
            existing.employment_status = 'Active'
            existing.save()

            use_default = validated_data.get('useDefaultPermissions', self.initial_data.get('useDefaultPermissions', getattr(existing, 'useDefaultPermissions', True)))
            perms_input = validated_data.get('permissions', self.initial_data.get('permissions', getattr(existing, 'permissions', [])))
            effective_role = role_obj or existing.role
            self._sync_permission_overrides(existing, effective_role, use_default, perms_input)

            # Queue welcome email for existing user joining new/reactivated org (Use existing password)
            try:
                from django.db import transaction
                transaction.on_commit(
                    lambda emp=existing: UserService.send_welcome_email(emp, synchronous=False)
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Failed to queue welcome email for existing user %s: %s", existing.email, exc)

            return existing

        else:
            # Brand-New Global User
            validated_data.setdefault('username', email)
            validated_data.pop('password', None)

            employee = super().create(validated_data)

            raw_password = generate_secure_password(12)
            employee.set_password(raw_password)
            employee._raw_password = raw_password
            employee.save()

            if employee.role:
                employee.role_name = employee.role.name
                employee.save(update_fields=['role_name'])

            use_default = validated_data.get('useDefaultPermissions', self.initial_data.get('useDefaultPermissions', getattr(employee, 'useDefaultPermissions', True)))
            perms_input = validated_data.get('permissions', self.initial_data.get('permissions', getattr(employee, 'permissions', [])))
            effective_role = role_obj or employee.role
            self._sync_permission_overrides(employee, effective_role, use_default, perms_input)

            # Create initial OrganizationMembership for target org
            OrganizationMembership.objects.create(
                user=employee,
                organization=target_org,
                employee_code=emp_code,
                department=dept or '',
                designation=desig,
                role=role_obj,
                employment_status='Active',
                joining_date=j_date,
                last_working_date=None,
                is_active_in_org=True
            )

            # Queue admin_onboarding.html for brand new user with temp password
            try:
                from django.db import transaction
                transaction.on_commit(
                    lambda emp=employee, pwd=raw_password: UserService.send_admin_onboarding_email(emp, pwd, synchronous=False)
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Failed to queue onboarding email for %s: %s", employee.email, exc)

            return employee

    def _sync_permission_overrides(self, instance, role, use_default, permissions):
        from users.models import PermissionFlag

        if isinstance(use_default, str):
            use_default = use_default.lower() not in ('false', '0')
        if isinstance(permissions, str):
            import json
            try:
                permissions = json.loads(permissions)
            except Exception:
                permissions = []

        update_fields = []
        if use_default is not None and instance.useDefaultPermissions != use_default:
            instance.useDefaultPermissions = use_default
            update_fields.append('useDefaultPermissions')

        if permissions is not None and instance.permissions != permissions:
            instance.permissions = list(permissions)
            update_fields.append('permissions')

        if not use_default and role:
            perms_set = set(permissions or [])
            role_perms = set(role.permissions.values_list('key', flat=True))
            additions = perms_set - role_perms
            removals = role_perms - perms_set

            extra_flags = PermissionFlag.objects.filter(key__in=additions, is_active=True)
            denied_flags = PermissionFlag.objects.filter(key__in=removals, is_active=True)

            instance.extra_permissions.set(extra_flags)
            instance.denied_permissions.set(denied_flags)
            instance.extra_permissions_json = list(additions)
            instance.denied_permissions_json = list(removals)
            update_fields.extend(['extra_permissions_json', 'denied_permissions_json'])
        elif use_default:
            instance.extra_permissions.clear()
            instance.denied_permissions.clear()
            if instance.extra_permissions_json or instance.denied_permissions_json:
                instance.extra_permissions_json = []
                instance.denied_permissions_json = []
                update_fields.extend(['extra_permissions_json', 'denied_permissions_json'])

        if update_fields:
            instance.save(update_fields=list(set(update_fields)))
        instance.clear_permission_cache()

    def update(self, instance, validated_data):
        from users.models import OrganizationMembership

        request = self.context.get('request')
        target_org = getattr(request, 'active_organization', None) if request else None
        if not target_org:
            target_org = instance.organization

        password = validated_data.pop('password', None)
        status_val = validated_data.get('employment_status')
        emp_code = validated_data.get('employee_code')
        dept = validated_data.get('department')
        desig = validated_data.get('designation')
        role_obj = validated_data.get('role')
        j_date = validated_data.get('joining_date')
        lwd_val = validated_data.get('last_working_date')

        # Update global employee fields
        name = self.initial_data.get('name')
        if name:
            parts = name.strip().split(' ', 1)
            validated_data['first_name'] = parts[0]
            validated_data['last_name'] = parts[1] if len(parts) > 1 else ''

        employee = super().update(instance, validated_data)

        if password:
            employee.set_password(password)
            employee.save()

        # Update target organization membership specifically
        if target_org:
            mem = OrganizationMembership.objects.filter(user=employee, organization=target_org).first()
            if mem:
                if status_val:
                    mem.employment_status = status_val
                    if status_val in ['Deactivated', 'Terminated', 'Resigned']:
                        mem.is_active_in_org = False
                    elif status_val == 'Active':
                        mem.is_active_in_org = True
                if emp_code is not None:
                    mem.employee_code = emp_code
                if dept is not None:
                    mem.department = dept
                if desig is not None:
                    mem.designation = desig
                if role_obj is not None:
                    mem.role = role_obj
                if j_date is not None:
                    mem.joining_date = j_date
                if lwd_val is not None:
                    mem.last_working_date = lwd_val
                mem.save()

        use_default = validated_data.get('useDefaultPermissions', self.initial_data.get('useDefaultPermissions', getattr(employee, 'useDefaultPermissions', True)))
        perms_input = validated_data.get('permissions', self.initial_data.get('permissions', getattr(employee, 'permissions', [])))
        effective_role = role_obj or employee.role
        self._sync_permission_overrides(employee, effective_role, use_default, perms_input)

        return employee
