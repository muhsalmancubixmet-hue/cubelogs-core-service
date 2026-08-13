# --------------------------------------------------------------------------------
#       Users Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.text import slugify

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import BaseModel

PERMISSION_FLAGS = [
    { 'id': 'dashboard', 'label': 'My Dashboard Analytics' },
    { 'id': 'audit_logs:view', 'label': 'System Audit Logs' },
    { 'id': 'admin:templates', 'label': 'Manage Templates (Admin Panel)' },
    { 'id': 'admin:employees', 'label': 'Manage Employees (Onboard / Edit)' },
    { 'id': 'attendance:staff', 'label': 'Clock-In / Clock-Out Dashboard' },
    { 'id': 'attendance:admin', 'label': 'Real-time Global Attendance Monitor' },
    { 'id': 'attendance:management_portal', 'label': 'Attendance Management Portal' },
    { 'id': 'projects:view', 'label': 'View Projects' },
    { 'id': 'projects:create', 'label': 'Create Projects' },
    { 'id': 'projects:update', 'label': 'Edit Projects' },
    { 'id': 'projects:delete', 'label': 'Delete Projects' },
    { 'id': 'projects:members_manage', 'label': 'Manage Project Members' },
    { 'id': 'project_stories:view', 'label': 'View Project Sections' },
    { 'id': 'project_stories:create', 'label': 'Create Project Sections' },
    { 'id': 'project_stories:update', 'label': 'Edit Project Sections' },
    { 'id': 'project_stories:delete', 'label': 'Delete Project Sections' },
    { 'id': 'project_tasks:view_all', 'label': 'View All Project Tasks' },
    { 'id': 'project_tasks:view_own', 'label': 'View Assigned Project Tasks' },
    { 'id': 'project_tasks:create', 'label': 'Create Project Tasks' },
    { 'id': 'project_tasks:update_all', 'label': 'Edit All Project Tasks' },
    { 'id': 'project_tasks:update_own', 'label': 'Update Assigned Task Status' },
    { 'id': 'project_tasks:delete', 'label': 'Delete Project Tasks' },
    { 'id': 'project_statuses:view', 'label': 'View Project Statuses' },
    { 'id': 'project_statuses:create', 'label': 'Create Project Statuses' },
    { 'id': 'project_statuses:update', 'label': 'Edit Project Statuses' },
    { 'id': 'project_statuses:delete', 'label': 'Delete Project Statuses' },
    { 'id': 'project_epics:view', 'label': 'View Epics' },
    { 'id': 'project_epics:create', 'label': 'Create Epics' },
    { 'id': 'project_epics:update', 'label': 'Edit Epics' },
    { 'id': 'project_epics:delete', 'label': 'Delete Epics' },
    { 'id': 'project_sprints:view', 'label': 'View Sprints' },
    { 'id': 'project_sprints:create', 'label': 'Create Sprints' },
    { 'id': 'project_sprints:update', 'label': 'Edit Sprints' },
    { 'id': 'project_sprints:manage', 'label': 'Manage Sprint Lifecycle (Start/Complete/Cancel/Reopen)' },
    { 'id': 'project_sprints:delete', 'label': 'Delete Sprints' },
    { 'id': 'leaves:apply', 'label': 'Apply Leave Form' },
    { 'id': 'leaves:approve', 'label': 'Leave Approval Portal' },
    { 'id': 'leaves:manage', 'label': 'Manage Leave Types (Rules & Allowances)' },
    { 'id': 'holidays:manage', 'label': 'Configure System Holidays' },
    { 'id': 'holidays:view', 'label': 'View Holiday Calendar' },
    { 'id': 'locations:manage', 'label': 'Manage Locations (Latitude/Longitude)' },
    { 'id': 'settings:branding', 'label': 'Manage Branding (Change Logo)' },
    { 'id': 'settings:billing', 'label': 'Manage Billing & Subscriptions' },
]


# --------------------------------------------------------------------------------
# PermissionFlag Model: Relational database storage of permission keys
# --------------------------------------------------------------------------------
class PermissionFlag(BaseModel):
    key = models.CharField(max_length=150, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    module = models.CharField(max_length=100, default='General', db_index=True)
    category = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'api_permission_flag'
        ordering = ['module', 'category', 'key']

    def __str__(self):
        return f"{self.name} ({self.key})"


# --------------------------------------------------------------------------------
# Role Model: Relational database storage of system and custom roles
# --------------------------------------------------------------------------------
class Role(BaseModel):
    organization = models.ForeignKey(
        'core.Organization',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='roles'
    )
    name = models.CharField(max_length=100, db_index=True)
    slug = models.SlugField(max_length=100, db_index=True)
    label = models.CharField(max_length=150, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_system_role = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(
        PermissionFlag,
        blank=True,
        related_name='roles'
    )

    class Meta:
        db_table = 'api_role'
        unique_together = ('organization', 'slug')
        ordering = ['name']

    def __str__(self):
        return self.label or self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class EmployeeManager(BaseUserManager):
    def create_user(self, email, password=None, username=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        extra_fields.setdefault('username', username or email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user._raw_password = password
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('isSuperAdmin', True)
        extra_fields.setdefault('useDefaultPermissions', True)
        extra_fields.setdefault('designation', 'Admin')
        extra_fields.setdefault('role_name', 'Super Admin')
        extra_fields.setdefault('permissions', [p['id'] for p in PERMISSION_FLAGS])
        return self.create_user(email, password, **extra_fields)


# --------------------------------------------------------------------------------
# Employee Model: Extends AbstractUser to represent employees, including organization,
#                 role relational foreign key, job designation, and M2M permission overrides.
# --------------------------------------------------------------------------------
class Employee(AbstractUser):
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)  # HR Job Title ONLY (e.g. Developer, QA, Designer)
    
    role = models.ForeignKey(
        Role,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='employees'
    )
    role_name = models.CharField(max_length=100, default='Employee', blank=True)  # Legacy string cache
    
    isSuperAdmin = models.BooleanField(default=False)
    useDefaultPermissions = models.BooleanField(default=True)
    permissions = models.JSONField(default=list, blank=True)  # Legacy JSON cache
    
    extra_permissions = models.ManyToManyField(
        PermissionFlag,
        blank=True,
        related_name='extra_permission_employees'
    )
    extra_permissions_json = models.JSONField(default=list, blank=True)  # Legacy extra JSON cache
    
    denied_permissions = models.ManyToManyField(
        PermissionFlag,
        blank=True,
        related_name='denied_permission_employees'
    )
    denied_permissions_json = models.JSONField(default=list, blank=True)  # Legacy denied JSON cache

    profilePhoto = models.TextField(blank=True, null=True)
    organization = models.ForeignKey(
        'core.Organization',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='employees',
    )

    EMPLOYMENT_STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Deactivated', 'Deactivated'),
        ('Terminated', 'Terminated'),
        ('Resigned', 'Resigned'),
    ]
    employment_status = models.CharField(max_length=20, choices=EMPLOYMENT_STATUS_CHOICES, default='Active')

    objects: EmployeeManager = EmployeeManager()  # type: ignore[assignment]

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'api_employee'

    def clear_permission_cache(self):
        if hasattr(self, '_effective_permissions_cache'):
            del self._effective_permissions_cache

    def save(self, *args, **kwargs):
        self.clear_permission_cache()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    @property
    def role_title(self):
        if self.role:
            return self.role.name
        return self.role_name or 'Employee'

    def get_effective_permissions(self):
        """
        Computes effective permissions for authorization via relational DB queries:
        Effective Permissions = (Role Default M2M Permissions + Extra M2M Permissions) - Denied M2M Permissions
        """
        if hasattr(self, '_effective_permissions_cache'):
            return self._effective_permissions_cache

        if self.is_superuser or self.isSuperAdmin or (self.role and self.role.slug in ['super-admin', 'company-admin']) or self.role_name in ['Super Admin', 'Company Admin', 'Admin']:
            from users.roles import ALL_PERMISSION_KEYS
            self._effective_permissions_cache = ALL_PERMISSION_KEYS
            return ALL_PERMISSION_KEYS

        base_keys = set()
        if self.role:
            base_keys = set(self.role.permissions.values_list('key', flat=True))
        elif self.permissions and isinstance(self.permissions, list) and len(self.permissions) > 0:
            base_keys = set(self.permissions)
        else:
            from users.roles import DEFAULT_ROLES
            role_info = DEFAULT_ROLES.get(self.role_name, DEFAULT_ROLES.get('Employee', {}))
            base_keys = set(role_info.get('permissions', []))

        extra_keys = set(self.extra_permissions.values_list('key', flat=True))
        if not extra_keys and isinstance(self.extra_permissions_json, list):
            extra_keys = set(self.extra_permissions_json)

        denied_keys = set(self.denied_permissions.values_list('key', flat=True))
        if not denied_keys and isinstance(self.denied_permissions_json, list):
            denied_keys = set(self.denied_permissions_json)

        effective = base_keys.union(extra_keys).difference(denied_keys)
        res = list(effective)
        self._effective_permissions_cache = res
        return res

    def has_capability(self, permission_key, project=None):
        """
        Central capability checker method on user model.
        """
        if self.is_superuser or self.isSuperAdmin or (self.role and self.role.slug in ['super-admin', 'company-admin']) or self.role_name in ['Super Admin', 'Company Admin', 'Admin']:
            return True

        if project is not None:
            from projects.permissions import get_project_effective_permissions
            effective = get_project_effective_permissions(self, project)
        else:
            effective = self.get_effective_permissions()

        if isinstance(permission_key, (list, tuple)):
            return any(k in effective for k in permission_key)
        return permission_key in effective


# --------------------------------------------------------------------------------
# Template Model: Represents permission templates containing authorization presets
# --------------------------------------------------------------------------------
class Template(BaseModel):
    organization = models.ForeignKey(
        'core.Organization',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='templates'
    )
    name = models.CharField(max_length=255)
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'api_template'
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name
