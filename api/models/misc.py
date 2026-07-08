from django.db import models
from api.models.employee import Employee


class Holiday(models.Model):
    organization = models.ForeignKey(
        'api.Organization',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='holidays',
    )
    name = models.CharField(max_length=255)
    date = models.DateField()
    description = models.TextField(blank=True, null=True)
    banner = models.TextField(blank=True, null=True)  # base64
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.date}"


class Template(models.Model):
    name = models.CharField(max_length=255, unique=True)
    permissions = models.JSONField(default=list, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class OfficeLocation(models.Model):
    organization = models.ForeignKey(
        'api.Organization',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='locations',
    )
    name = models.CharField(max_length=255)
    lat = models.FloatField()
    lon = models.FloatField()
    radius = models.FloatField(default=100.0)
    isPrimary = models.BooleanField(default=False)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class AuditLog(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='audit_logs',
    )
    employeeName = models.CharField(max_length=255, blank=True, null=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True, null=True)
    ipAddress = models.CharField(max_length=45, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employeeName or 'System'} - {self.action} ({self.createdAt})"
