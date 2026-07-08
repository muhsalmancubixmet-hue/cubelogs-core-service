from django.db import models
from api.models.employee import Employee


class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    limitPeriod = models.CharField(max_length=50, default='Yearly')  # Monthly | Yearly
    maxLimit = models.IntegerField(default=10)
    restrictedDates = models.JSONField(default=list, blank=True)  # [{date, reason}]
    carryForward = models.BooleanField(default=False)
    maxCarryForward = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default='Active')  # Active | Inactive
    minAdvanceDays = models.IntegerField(default=0)
    organization = models.ForeignKey(
        'api.Organization',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='leave_types',
    )
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Leave(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leaves')
    employeeName = models.CharField(max_length=255)
    leaveType = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='leaves')
    leaveTypeName = models.CharField(max_length=255)
    startDate = models.DateField()
    endDate = models.DateField()
    duration = models.FloatField(default=1.0)
    dayType = models.CharField(max_length=50, default='Full')  # Full | Half
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, default='Pending')  # Pending | Approved | Rejected
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employeeName} - {self.leaveTypeName} ({self.startDate} to {self.endDate})"
