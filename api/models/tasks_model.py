from django.db import models
from api.models.employee import Employee


class Task(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    assignedTo = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='tasks')
    assignedName = models.CharField(max_length=255)
    dueDate = models.DateField()
    status = models.CharField(max_length=50, default='Pending')  # Pending | In Progress | Completed
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
