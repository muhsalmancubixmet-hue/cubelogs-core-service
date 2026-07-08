from django.db import models
from api.models.employee import Employee


class Lead(models.Model):
    STATUS_CHOICES = [
        ('New', 'New'),
        ('In Progress', 'In Progress'),
        ('Closed', 'Closed'),
    ]

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, null=True)
    companyName = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    # Status & assignment fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='New')
    assigned_staff = models.ForeignKey(
        Employee, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_leads',
    )
    is_read = models.BooleanField(default=False)
    read_by = models.ForeignKey(
        Employee, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='read_leads',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.email})"


class LeadHistory(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='histories')
    modified_by = models.ForeignKey(
        Employee, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='lead_histories',
    )
    action = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Lead {self.lead_id} history - {self.action}"
