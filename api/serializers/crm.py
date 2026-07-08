# api/serializers/crm.py
from rest_framework import serializers
from api.models import Lead, LeadHistory

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
