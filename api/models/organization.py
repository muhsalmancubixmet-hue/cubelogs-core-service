from django.db import models


def default_weekly_holidays_default():
    return ["Sunday"]


class OrgSettings(models.Model):
    brandLogo = models.TextField(blank=True, null=True)  # base64
    subscriptionDays = models.IntegerField(default=12)
    subscriptionRenewedAt = models.DateTimeField(auto_now_add=True)
    max_employees_allowed = models.IntegerField(default=10)
    is_attendance_enabled = models.BooleanField(default=False)
    is_project_enabled = models.BooleanField(default=False)
    subscriptionStatus = models.CharField(max_length=50, default='Active')
    subscriptionExpiresAt = models.DateTimeField(null=True, blank=True)
    has_sent_billing_warning = models.BooleanField(default=False)
    # Attendance time-rule configuration
    grace_period_minutes = models.IntegerField(default=15)          # Minutes after shift start before considered Late
    half_day_threshold_minutes = models.IntegerField(default=240)   # Minutes worked to qualify as Half Day (vs Absent)
    full_day_absent_threshold_minutes = models.IntegerField(default=60)  # Minutes after shift start before marked Absent
    auto_approve_attendance = models.BooleanField(default=False)    # Toggle switch for auto-approving attendance
    default_weekly_holidays = models.JSONField(default=default_weekly_holidays_default, blank=True)
    monthly_recurring_holidays = models.JSONField(default=list, blank=True)
    yearly_recurring_holidays = models.JSONField(default=list, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Organization Settings"


class Organization(models.Model):
    name = models.CharField(max_length=255)
    subdomain = models.CharField(max_length=255, unique=True)
    settings = models.OneToOneField(
        OrgSettings,
        on_delete=models.CASCADE,
        related_name='organization',
        null=True, blank=True,
    )
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
