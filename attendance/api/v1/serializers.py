# --------------------------------------------------------------------------------
#       Attendance Serializers
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import serializers

# APPLICATION SPECIFIC
from core.models import OrgSettings, AuditLog
from users.models import Template
from attendance.models import (
    AttendanceLog, Holiday, OfficeLocation, Schedule,
    AttendancePolicy, LeaveType, Leave,
    AttendancePeriod, AttendancePeriodEmployeeSnapshot
)
from attendance.services import get_attendance_policy, save_attendance_policy


class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'

    def create(self, validated_data):
        if 'employeeName' not in validated_data or not validated_data['employeeName']:
            employee = validated_data['employee']
            validated_data['employeeName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = '__all__'


class TemplateSerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Template
        fields = '__all__'


class OfficeLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OfficeLocation
        fields = '__all__'

    def validate_lat(self, value):
        if value is None or value < -90.0 or value > 90.0:
            raise serializers.ValidationError("Latitude must be a valid number between -90 and 90 degrees.")
        return value

    def validate_lon(self, value):
        if value is None or value < -180.0 or value > 180.0:
            raise serializers.ValidationError("Longitude must be a valid number between -180 and 180 degrees.")
        return value

    def validate_radius(self, value):
        if value is None or value <= 0 or value > 50000:
            raise serializers.ValidationError("Radius must be a positive number of meters up to 50,000 meters.")
        return value


class AttendancePolicySerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(read_only=True)
    half_day_threshold_minutes = serializers.IntegerField(source='half_day_minimum_minutes', required=False)

    class Meta:
        model = AttendancePolicy
        fields = [
            'id', 'organization', 'effective_from', 'grace_period_minutes',
            'full_day_minimum_minutes', 'half_day_minimum_minutes', 'half_day_threshold_minutes',
            'minimum_session_minutes',
            'break_duration_minutes', 'break_type', 'default_weekly_holidays',
            'auto_approve_attendance', 'created_at', 'updated_at'
        ]


class ScheduleSerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Schedule
        fields = '__all__'


class OrgSettingsSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    companyName = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    full_day_minimum_minutes = serializers.IntegerField(required=False, default=480)
    half_day_minimum_minutes = serializers.IntegerField(required=False, default=240)
    minimum_session_minutes = serializers.IntegerField(required=False, default=5, min_value=0, max_value=180)
    break_duration_minutes = serializers.IntegerField(required=False, default=60)
    break_type = serializers.CharField(required=False, default='Unpaid')
    policy_effective_from = serializers.DateField(read_only=True, required=False)

    class Meta:
        model = OrgSettings
        fields = [
            'id', 'brandLogo', 'subscriptionDays', 'subscriptionRenewedAt',
            'max_employees_allowed', 'is_attendance_enabled', 'is_project_enabled',
            'subscriptionStatus', 'subscriptionExpiresAt', 'createdAt', 'updatedAt', 'companyName',
            'grace_period_minutes', 'half_day_threshold_minutes', 'full_day_absent_threshold_minutes',
            'auto_approve_attendance', 'full_day_minimum_minutes', 'half_day_minimum_minutes',
            'minimum_session_minutes',
            'break_duration_minutes', 'break_type', 'policy_effective_from',
            'payroll_currency', 'payroll_proration_basis', 'daily_wage_paid_leave_eligible',
            'hourly_wage_paid_leave_eligible',
            'company_address', 'company_phone', 'company_email', 'billing_email', 'company_tax_id',
            'payroll_frequency', 'payroll_processing_day', 'salary_payment_day',
            'corporate_bank_name', 'corporate_account_number', 'corporate_ifsc_code',
            'corporate_account_holder_name', 'corporate_client_code', 'corporate_bank_branch'
        ]

    def validate_corporate_ifsc_code(self, value):
        if value:
            import re
            cleaned = value.strip().upper()
            if not re.match(r'^[A-Z]{4}0[A-Z0-9]{6}$', cleaned):
                raise serializers.ValidationError("Invalid corporate IFSC code format. Expected 4 letters, 0, followed by 6 alphanumeric characters (e.g., HDFC0001234).")
            return cleaned
        return value

    def validate_payroll_processing_day(self, value):
        if value < 1 or value > 31:
            raise serializers.ValidationError("Payroll processing day must be between 1 and 31.")
        return value

    def validate_salary_payment_day(self, value):
        if value < 1 or value > 31:
            raise serializers.ValidationError("Salary payment day must be between 1 and 31.")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if hasattr(instance, 'organization') and instance.organization:
            data['companyName'] = instance.organization.name
            policy = get_attendance_policy(instance.organization)
            if policy:
                data['grace_period_minutes'] = policy.grace_period_minutes
                data['half_day_threshold_minutes'] = policy.half_day_minimum_minutes
                data['half_day_minimum_minutes'] = policy.half_day_minimum_minutes
                data['full_day_minimum_minutes'] = policy.full_day_minimum_minutes
                data['minimum_session_minutes'] = policy.minimum_session_minutes
                data['break_duration_minutes'] = policy.break_duration_minutes
                data['break_type'] = policy.break_type
                data['auto_approve_attendance'] = policy.auto_approve_attendance
                data['default_weekly_holidays'] = policy.default_weekly_holidays
                data['policy_effective_from'] = policy.effective_from.isoformat() if policy.effective_from else None
        else:
            data['companyName'] = "Head Office"
        return data

    def update(self, instance, validated_data):
        company_name = validated_data.pop('companyName', None)
        full_day_min = validated_data.pop('full_day_minimum_minutes', None)
        half_day_min = validated_data.pop('half_day_minimum_minutes', None)
        min_session = validated_data.pop('minimum_session_minutes', None)
        if min_session is None and 'minimum_session_minutes' in self.initial_data:
            try:
                min_session = int(self.initial_data['minimum_session_minutes'])
            except (ValueError, TypeError):
                pass
        break_duration = validated_data.pop('break_duration_minutes', None)
        break_type = validated_data.pop('break_type', None)

        org = getattr(instance, 'organization', None)
        if not org:
            request = self.context.get('request')
            if request and request.user and getattr(request.user, 'organization', None):
                org = request.user.organization
                instance.organization = org
                instance.save(update_fields=['organization'])

        if org:
            # Check if any policy fields are being updated
            policy_payload = {}
            if full_day_min is not None:
                policy_payload['full_day_minimum_minutes'] = full_day_min
            if half_day_min is not None:
                policy_payload['half_day_minimum_minutes'] = half_day_min
            elif 'half_day_threshold_minutes' in validated_data:
                policy_payload['half_day_minimum_minutes'] = validated_data['half_day_threshold_minutes']
            if min_session is not None:
                policy_payload['minimum_session_minutes'] = min_session
            if break_duration is not None:
                policy_payload['break_duration_minutes'] = break_duration
            if break_type is not None:
                policy_payload['break_type'] = break_type
            if 'grace_period_minutes' in validated_data:
                policy_payload['grace_period_minutes'] = validated_data['grace_period_minutes']
            if 'auto_approve_attendance' in validated_data:
                policy_payload['auto_approve_attendance'] = validated_data['auto_approve_attendance']
            if 'default_weekly_holidays' in self.initial_data:
                policy_payload['default_weekly_holidays'] = self.initial_data['default_weekly_holidays']

            if policy_payload:
                # Fill missing policy values from current active policy to avoid resetting them
                current_policy = get_attendance_policy(org)
                if current_policy:
                    policy_payload.setdefault('grace_period_minutes', current_policy.grace_period_minutes)
                    policy_payload.setdefault('full_day_minimum_minutes', current_policy.full_day_minimum_minutes)
                    policy_payload.setdefault('half_day_minimum_minutes', current_policy.half_day_minimum_minutes)
                    policy_payload.setdefault('minimum_session_minutes', current_policy.minimum_session_minutes)
                    policy_payload.setdefault('break_duration_minutes', current_policy.break_duration_minutes)
                    policy_payload.setdefault('break_type', current_policy.break_type)
                    policy_payload.setdefault('default_weekly_holidays', current_policy.default_weekly_holidays)
                    policy_payload.setdefault('auto_approve_attendance', current_policy.auto_approve_attendance)
                save_attendance_policy(org, policy_payload)

        instance = super().update(instance, validated_data)
        if company_name is not None:
            target_org = getattr(instance, 'organization', None) or org
            if target_org:
                target_org.name = company_name
                target_org.save()
        return instance


class AuditLogSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = '__all__'

    def get_organization(self, obj):
        if obj.organization_id:
            return obj.organization_id
        if obj.employee and obj.employee.organization:
            return obj.employee.organization.id
        return None


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


class AttendancePeriodEmployeeSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendancePeriodEmployeeSnapshot
        fields = '__all__'


class AttendancePeriodSerializer(serializers.ModelSerializer):
    finalized_by_name = serializers.SerializerMethodField()
    reopened_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendancePeriod
        fields = '__all__'

    def get_finalized_by_name(self, obj):
        if obj.finalized_by:
            return f"{obj.finalized_by.first_name} {obj.finalized_by.last_name}".strip() or obj.finalized_by.email
        return None

    def get_reopened_by_name(self, obj):
        if obj.reopened_by:
            return f"{obj.reopened_by.first_name} {obj.reopened_by.last_name}".strip() or obj.reopened_by.email
        return None


class FinalizePeriodSerializer(serializers.Serializer):
    year = serializers.IntegerField(required=True)
    month = serializers.IntegerField(required=True, min_value=1, max_value=12)


class ReopenPeriodSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, min_length=5)

