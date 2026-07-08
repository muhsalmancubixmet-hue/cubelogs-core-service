from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager

PERMISSION_FLAGS = [
    { 'id': 'dashboard', 'label': 'My Dashboard Analytics' },
    { 'id': 'admin:templates', 'label': 'Manage Templates (Admin Panel)' },
    { 'id': 'admin:employees', 'label': 'Manage Employees (Onboard / Edit)' },
    { 'id': 'attendance:staff', 'label': 'Clock-In / Clock-Out Dashboard' },
    { 'id': 'attendance:admin', 'label': 'Real-time Global Attendance Monitor' },
    { 'id': 'attendance:management_portal', 'label': 'Attendance Management Portal' },
    { 'id': 'tasks:create', 'label': 'Add Task Workspace (Assign tasks)' },
    { 'id': 'tasks:view', 'label': 'My Tasks View (Track objectives)' },
    { 'id': 'leaves:apply', 'label': 'Apply Leave Form' },
    { 'id': 'leaves:approve', 'label': 'Leave Approval Portal' },
    { 'id': 'leaves:manage', 'label': 'Manage Leave Types (Rules & Allowances)' },
    { 'id': 'holidays:manage', 'label': 'Configure System Holidays' },
    { 'id': 'holidays:view', 'label': 'View Holiday Calendar' },
    { 'id': 'locations:manage', 'label': 'Manage Locations (Latitude/Longitude)' },
    { 'id': 'settings:branding', 'label': 'Manage Branding (Change Logo)' },
    { 'id': 'settings:billing', 'label': 'Manage Billing & Subscriptions' },
]


class EmployeeManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        extra_fields.setdefault('username', email)
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
        extra_fields.setdefault('permissions', [p['id'] for p in PERMISSION_FLAGS])

        return self.create_user(email, password, **extra_fields)


class Employee(AbstractUser):
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)
    isSuperAdmin = models.BooleanField(default=False)
    useDefaultPermissions = models.BooleanField(default=True)
    permissions = models.JSONField(default=list, blank=True)
    profilePhoto = models.TextField(blank=True, null=True)  # base64
    organization = models.ForeignKey(
        'api.Organization',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='employees',
    )
    raw_password = models.CharField(max_length=255, blank=True, null=True)

    objects = EmployeeManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.raw_password = raw_password

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email
