# api/serializers/leave.py
from rest_framework import serializers
from api.models import LeaveType, Leave

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
