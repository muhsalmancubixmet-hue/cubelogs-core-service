# --------------------------------------------------------------------------------
#       Attendance Serializers
# --------------------------------------------------------------------------------

# api/serializers/attendance.py
# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import serializers

# APPLICATION SPECIFIC
from attendance.models import AttendanceLog

class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'

    def create(self, validated_data):
        if 'employeeName' not in validated_data or not validated_data['employeeName']:
            employee = validated_data['employee']
            validated_data['employeeName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)


# Organization and Settings Serializers
from rest_framework import serializers
from core.models import OrgSettings, AuditLog
from users.models import Template
from attendance.models import Holiday, OfficeLocation, Schedule

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

class ScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Schedule
        fields = '__all__'

class OrgSettingsSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

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
        if obj.organization_id:
            return obj.organization_id
        if obj.employee and obj.employee.organization:
            return obj.employee.organization.id
        return None


# Leaves and LeaveTypes Serializers
from rest_framework import serializers
from attendance.models import LeaveType, Leave

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
