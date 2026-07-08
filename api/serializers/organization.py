# api/serializers/organization.py
from rest_framework import serializers
from api.models import Task, Holiday, Template, OfficeLocation, Schedule, OrgSettings, AuditLog

class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = '__all__'

    def create(self, validated_data):
        if 'assignedName' not in validated_data or not validated_data['assignedName']:
            employee = validated_data['assignedTo']
            validated_data['assignedName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
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
