# --------------------------------------------------------------------------------
#       Users Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.text import slugify
from django.core.exceptions import ObjectDoesNotExist, ValidationError

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
    { 'id': 'salary:view', 'label': 'View Employee Salary Structures' },
    { 'id': 'salary:manage', 'label': 'Configure Salary Components & Manage Employee Salaries' },
    { 'id': 'payroll:view', 'label': 'View Payroll Calculations & Periods' },
    { 'id': 'payroll:process', 'label': 'Process & Calculate Payroll, Manage Adjustments' },
    { 'id': 'payroll:manage', 'label': 'Finalize & Reopen Monthly Payroll Periods' },
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
    employee_code = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text="HR / Payroll Employee ID")
    department = models.CharField(max_length=100, blank=True, default='', help_text="Department or team")
    employment_status = models.CharField(max_length=20, choices=EMPLOYMENT_STATUS_CHOICES, default='Active')
    joining_date = models.DateField(null=True, blank=True, help_text="Official employment start date.")
    last_working_date = models.DateField(null=True, blank=True, help_text="Official employment termination/last working date.")

    # Bank Details
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    ifsc_code = models.CharField(max_length=20, blank=True, null=True)
    account_holder_name = models.CharField(max_length=255, blank=True, null=True)
    bank_branch = models.CharField(max_length=100, blank=True, null=True)
    upi_id = models.CharField(max_length=100, blank=True, null=True)

    objects: EmployeeManager = EmployeeManager()  # type: ignore[assignment]

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'api_employee'

    def clean(self):
        super().clean()
        if self.joining_date and self.last_working_date and self.last_working_date < self.joining_date:
            raise ValidationError({"last_working_date": "Last working date cannot be earlier than joining date."})

    def clear_permission_cache(self):
        if hasattr(self, '_effective_permissions_cache'):
            del self._effective_permissions_cache

    def save(self, *args, **kwargs):
        self.clean()
        self.clear_permission_cache()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    @property
    def role_title(self):
        if self.role:
            return self.role.name
        return self.role_name or 'Employee'

    def get_effective_permissions(self, active_membership=None):
        """
        Computes effective permissions for authorization via relational DB queries:
        Effective Permissions = (Role Default M2M Permissions + Extra M2M Permissions) - Denied M2M Permissions
        Prefers active_membership.role over legacy Employee.role when provided.
        """
        effective_role = active_membership.role if (active_membership and active_membership.role) else self.role

        if self.is_superuser or self.isSuperAdmin or (effective_role and effective_role.slug in ['super-admin', 'company-admin']) or self.role_name in ['Super Admin', 'Company Admin', 'Admin']:
            from users.roles import ALL_PERMISSION_KEYS
            return ALL_PERMISSION_KEYS

        base_keys = set()
        if effective_role:
            base_keys = set(effective_role.permissions.values_list('key', flat=True))
        elif self.role:
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
        return list(effective)

    def has_capability(self, permission_key, project=None, active_membership=None):
        """
        Central capability checker method on user model.
        """
        effective_role = active_membership.role if (active_membership and active_membership.role) else self.role

        if self.is_superuser or self.isSuperAdmin or (effective_role and effective_role.slug in ['super-admin', 'company-admin']) or self.role_name in ['Super Admin', 'Company Admin', 'Admin']:
            return True

        if project is not None:
            from projects.permissions import get_project_effective_permissions
            effective = get_project_effective_permissions(self, project, active_membership=active_membership)
        else:
            effective = self.get_effective_permissions(active_membership=active_membership)

        if isinstance(permission_key, (list, tuple)):
            return any(k in effective for k in permission_key)
        return permission_key in effective

    @property
    def active_profile(self):
        """
        Backward-compatible helper returning the attached EmployeeProfile.
        Safely returns None if no EmployeeProfile exists yet for this user.
        """
        try:
            return self.employee_profile
        except ObjectDoesNotExist:
            return None


# --------------------------------------------------------------------------------
# EmployeeProfile: Tenant-scoped employee HR profile decoupled from auth User
# --------------------------------------------------------------------------------
class EmployeeProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='employee_profile'
    )
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='employee_profiles',
        null=True,
        blank=True
    )
    employee_code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        db_index=True,
        help_text="HR / Payroll Employee ID"
    )
    designation = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="HR Job Title (e.g. Developer, QA, Designer)"
    )
    department = models.CharField(
        max_length=100,
        default='',
        blank=True,
        help_text="Department or team"
    )
    employment_status = models.CharField(
        max_length=20,
        choices=Employee.EMPLOYMENT_STATUS_CHOICES,
        default='Active'
    )
    joining_date = models.DateField(
        null=True,
        blank=True,
        help_text="Official employment start date."
    )
    last_working_date = models.DateField(
        null=True,
        blank=True,
        help_text="Official employment termination/last working date."
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )
    profile_photo = models.TextField(
        blank=True,
        null=True
    )

    # Bank Details
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    ifsc_code = models.CharField(max_length=20, blank=True, null=True)
    account_holder_name = models.CharField(max_length=255, blank=True, null=True)
    bank_branch = models.CharField(max_length=100, blank=True, null=True)
    upi_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = 'api_employeeprofile'
        ordering = ['-id']
        indexes = [
            models.Index(fields=['organization', 'employee_code'], name='empprof_org_code_idx'),
            models.Index(fields=['organization', 'employment_status'], name='empprof_org_status_idx'),
        ]

    def clean(self):
        super().clean()
        if self.joining_date and self.last_working_date and self.last_working_date < self.joining_date:
            raise ValidationError({"last_working_date": "Last working date cannot be earlier than joining date."})

    def __str__(self):
        return f"{self.full_name} ({self.employee_code or 'No Code'})"

    # --- Reverse Property Shims ---
    @property
    def email(self):
        return self.user.email if self.user else None

    @property
    def full_name(self):
        if not self.user:
            return ""
        name = f"{self.user.first_name} {self.user.last_name}".strip()
        return name or self.user.email

    @property
    def is_active(self):
        if not self.user:
            return False
        return bool(self.user.is_active and (self.employment_status == 'Active'))


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


# --------------------------------------------------------------------------------
# OrganizationMembership: Represents organization-specific employment & membership
# --------------------------------------------------------------------------------
import weakref

_MEMBERSHIP_REFS = []


class OrganizationMembership(BaseModel):
    user = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    employee_code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        db_index=True,
        help_text="HR / Payroll Employee ID within this organization"
    )
    department = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Department or team within this organization"
    )
    designation = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="HR Job Title within this organization"
    )
    role = models.ForeignKey(
        Role,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='org_memberships'
    )
    employment_status = models.CharField(
        max_length=20,
        choices=Employee.EMPLOYMENT_STATUS_CHOICES,
        default='Active'
    )
    joining_date = models.DateField(null=True, blank=True)
    last_working_date = models.DateField(null=True, blank=True)
    is_active_in_org = models.BooleanField(
        default=True,
        help_text="Active employment access flag within this specific organization"
    )

    class Meta:
        db_table = 'api_organization_membership'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'organization'],
                name='unique_user_organization_membership'
            ),
            models.UniqueConstraint(
                fields=['organization', 'employee_code'],
                condition=models.Q(employee_code__isnull=False) & ~models.Q(employee_code=''),
                name='unique_org_employee_code'
            )
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _MEMBERSHIP_REFS.append(weakref.ref(self))

    def clean(self):
        super().clean()
        if self.joining_date and self.last_working_date and self.last_working_date < self.joining_date:
            raise ValidationError({"last_working_date": "Last working date cannot be earlier than joining date."})

        if self.role and self.organization:
            is_same_org = (self.role.organization_id == self.organization_id)
            is_valid_system = (self.role.organization_id is None and self.role.is_system_role)
            if not (is_same_org or is_valid_system):
                raise ValidationError({"role": "Role does not belong to this organization."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        alive_refs = []
        for ref in _MEMBERSHIP_REFS:
            obj = ref()
            if obj is not None:
                alive_refs.append(ref)
                if obj is not self and getattr(obj, 'pk', None) == self.pk:
                    obj.is_active_in_org = self.is_active_in_org
                    obj.is_deleted = self.is_deleted
        _MEMBERSHIP_REFS[:] = alive_refs

    def __str__(self):
        return f"{self.user.email} @ {self.organization.name}"
