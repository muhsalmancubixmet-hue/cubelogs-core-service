# --------------------------------------------------------------------------------
#       Tasks Serializers
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import serializers

# APPLICATION SPECIFIC
from tasks.models import Task


# --------------------------------------------------------------------------------
# TaskSerializer: Serializes all fields of Task model.
# --------------------------------------------------------------------------------
class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = '__all__'

    def validate_assignedTo(self, value):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            user_org = getattr(request.user, 'organization', None)
            if user_org is not None and value.organization != user_org:
                raise serializers.ValidationError("Cannot assign tasks to employees outside your organization.")
        return value

    def create(self, validated_data):
        if 'assignedName' not in validated_data or not validated_data['assignedName']:
            employee = validated_data['assignedTo']
            validated_data['assignedName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if 'assignedTo' in validated_data and ('assignedName' not in validated_data or not validated_data['assignedName']):
            employee = validated_data['assignedTo']
            validated_data['assignedName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().update(instance, validated_data)

