# api/serializers/attendance.py
from rest_framework import serializers
from api.models import AttendanceLog

class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'

    def create(self, validated_data):
        if 'employeeName' not in validated_data or not validated_data['employeeName']:
            employee = validated_data['employee']
            validated_data['employeeName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)
