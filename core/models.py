# --------------------------------------------------------------------------------
#       Core Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db import models

# THIRD PARTY

# APPLICATION SPECIFIC


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class DeletedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=True)


# --------------------------------------------------------------------------------
# BaseModel: Abstract model providing audit dates and soft-deletion tracking
# --------------------------------------------------------------------------------
class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    
    objects = models.Manager()
    active_objects = ActiveManager()
    deleted_objects = DeletedManager()

    @property
    def createdAt(self):
        return self.created_at

    @createdAt.setter
    def createdAt(self, value):
        self.created_at = value

    @property
    def updatedAt(self):
        return self.updated_at

    @updatedAt.setter
    def updatedAt(self, value):
        self.updated_at = value

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True  # type: ignore
        self.save(update_fields=['is_deleted'])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)


# --------------------------------------------------------------------------------
# OrgSettings Model: Stores configuration parameters for organization subscriptions, 
#                    attendance grace periods, and thresholds
# --------------------------------------------------------------------------------
def default_weekly_holidays_default():
    return []


class OrgSettings(BaseModel):
    brandLogo = models.TextField(blank=True, null=True)
    subscriptionDays = models.IntegerField(default=12)
    subscriptionRenewedAt = models.DateTimeField(auto_now_add=True)
    max_employees_allowed = models.IntegerField(default=10)
    subscriptionStatus = models.CharField(max_length=50, default='Active')
    subscriptionExpiresAt = models.DateTimeField(null=True, blank=True)
    has_sent_billing_warning = models.BooleanField(default=False)
    grace_period_minutes = models.IntegerField(default=15)
    half_day_threshold_minutes = models.IntegerField(default=240)
    full_day_absent_threshold_minutes = models.IntegerField(default=60)
    auto_approve_attendance = models.BooleanField(default=False)
    default_weekly_holidays = models.JSONField(default=default_weekly_holidays_default, blank=True)
    monthly_recurring_holidays = models.JSONField(default=list, blank=True)
    yearly_recurring_holidays = models.JSONField(default=list, blank=True)

    @property
    def is_attendance_enabled(self) -> bool:
        if hasattr(self, '_is_attendance_enabled_temp'):
            return self._is_attendance_enabled_temp
        if not hasattr(self, 'organization') or not self.organization:
            return False
        try:
            return OrganizationModule.objects.filter(
                organization=self.organization,
                module_id='attendance',
                enabled=True
            ).exists()
        except Exception:
            return False

    @is_attendance_enabled.setter
    def is_attendance_enabled(self, value: bool):
        from django.utils import timezone
        self._is_attendance_enabled_temp = value
        if not hasattr(self, 'organization') or not self.organization:
            return
        try:
            org_module, _ = OrganizationModule.objects.get_or_create(
                organization=self.organization,
                module_id='attendance',
                defaults={'enabled': value, 'activated_at': timezone.now()}
            )
            if org_module.enabled != value:
                org_module.enabled = value
                org_module.save()
        except Exception:
            pass

    @property
    def is_project_enabled(self) -> bool:
        if hasattr(self, '_is_project_enabled_temp'):
            return self._is_project_enabled_temp
        if not hasattr(self, 'organization') or not self.organization:
            return False
        try:
            return OrganizationModule.objects.filter(
                organization=self.organization,
                module_id='tasks',
                enabled=True
            ).exists()
        except Exception:
            return False

    @is_project_enabled.setter
    def is_project_enabled(self, value: bool):
        from django.utils import timezone
        self._is_project_enabled_temp = value
        if not hasattr(self, 'organization') or not self.organization:
            return
        try:
            org_module, _ = OrganizationModule.objects.get_or_create(
                organization=self.organization,
                module_id='tasks',
                defaults={'enabled': value, 'activated_at': timezone.now()}
            )
            if org_module.enabled != value:
                org_module.enabled = value
                org_module.save()
        except Exception:
            pass

    class Meta:
        db_table = 'api_orgsettings'

    def __str__(self):
        return "Organization Settings"


OrganizationSettings = OrgSettings


# --------------------------------------------------------------------------------
# Organization Model: Represents tenant organizations with unique names and settings
# --------------------------------------------------------------------------------
class Organization(BaseModel):
    created_at = models.DateTimeField(auto_now_add=True, db_column='createdAt')
    updated_at = models.DateTimeField(auto_now=True, db_column='updatedAt')
    name = models.CharField(max_length=255)
    subdomain = models.CharField(max_length=255, unique=True)
    settings = models.OneToOneField(
        OrgSettings,
        on_delete=models.CASCADE,
        related_name='organization',
        null=True, blank=True,
    )

    class Meta:
        db_table = 'api_organization'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.settings:
            from django.utils import timezone
            if hasattr(self.settings, '_is_attendance_enabled_temp'):
                val = self.settings._is_attendance_enabled_temp
                try:
                    org_module, _ = OrganizationModule.objects.get_or_create(
                        organization=self,
                        module_id='attendance',
                        defaults={'enabled': val, 'activated_at': timezone.now()}
                    )
                    if org_module.enabled != val:
                        org_module.enabled = val
                        org_module.save()
                except Exception:
                    pass
                try:
                    delattr(self.settings, '_is_attendance_enabled_temp')
                except AttributeError:
                    pass

            if hasattr(self.settings, '_is_project_enabled_temp'):
                val = self.settings._is_project_enabled_temp
                try:
                    org_module, _ = OrganizationModule.objects.get_or_create(
                        organization=self,
                        module_id='tasks',
                        defaults={'enabled': val, 'activated_at': timezone.now()}
                    )
                    if org_module.enabled != val:
                        org_module.enabled = val
                        org_module.save()
                except Exception:
                    pass
                try:
                    delattr(self.settings, '_is_project_enabled_temp')
                except AttributeError:
                    pass


# --------------------------------------------------------------------------------
# AuditLog Model: Records employee system actions and access IP logs for auditing
# --------------------------------------------------------------------------------
class AuditLog(models.Model):
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    employee = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='audit_logs',
    )
    employeeName = models.CharField(max_length=255, blank=True, null=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True, null=True)
    ipAddress = models.CharField(max_length=45, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True, db_column='createdAt')

    class Meta:
        db_table = 'api_auditlog'

    def save(self, *args, **kwargs):
        if not self.organization and self.employee and self.employee.organization:
            self.organization = self.employee.organization
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employeeName or 'System'} - {self.action} ({self.createdAt})"


# --------------------------------------------------------------------------------
# OrganizationModule Model: Determines which optional modules are enabled per tenant
# --------------------------------------------------------------------------------
class OrganizationModule(BaseModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='organization_modules')
    module_id = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    activated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'api_organizationmodule'
        unique_together = ('organization', 'module_id')

    def __str__(self):
        return f"{self.organization.name} - {self.module_id} (Enabled: {self.enabled})"


# --------------------------------------------------------------------------------
# Mode Model: Tracks application operational states (Down, Read-Only, Maintenance)
# --------------------------------------------------------------------------------
class Mode(models.Model):
    readonly = models.BooleanField(default=False)
    maintenance = models.BooleanField(default=False)
    down = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_mode'
        verbose_name = 'mode'
        verbose_name_plural = 'modes'

    def __str__(self):
        return f"Mode (ReadOnly={self.readonly}, Maintenance={self.maintenance}, Down={self.down})"


