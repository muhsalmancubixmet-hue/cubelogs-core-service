# --------------------------------------------------------------------------------
#       Tasks Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db import models

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import BaseModel


# --------------------------------------------------------------------------------
# Task Model: Stores task tracking items, deadlines, assignees, and task statuses
# --------------------------------------------------------------------------------
class Task(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    assignedTo = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='tasks')
    assignedName = models.CharField(max_length=255)
    dueDate = models.DateField()
    status = models.CharField(max_length=50, default='Pending')

    class Meta:
        db_table = 'api_task'

    def __str__(self):
        return self.title
