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

    def create(self, validated_data):
        if 'assignedName' not in validated_data or not validated_data['assignedName']:
            employee = validated_data['assignedTo']
            validated_data['assignedName'] = f"{employee.first_name} {employee.last_name}".strip() or employee.email
        return super().create(validated_data)
