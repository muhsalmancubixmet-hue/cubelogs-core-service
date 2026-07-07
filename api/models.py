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
    profilePhoto = models.TextField(blank=True, null=True) # base64
    organization = models.ForeignKey('Organization', null=True, blank=True, on_delete=models.SET_NULL, related_name='employees')
    raw_password = models.CharField(max_length=255, blank=True, null=True)

    objects = EmployeeManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.raw_password = raw_password

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

class AttendanceLog(models.Model):
    ATTENDANCE_STATUS_CHOICES = [
        ('Pending Approval', 'Pending Approval'),
        ('Approved', 'Approved'),
        ('Late', 'Late'),
        ('Half Day', 'Half Day'),
        ('Absent', 'Absent'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance_logs')
    employeeName = models.CharField(max_length=255)
    date = models.DateField()
    clockIn = models.DateTimeField(null=True, blank=True)
    clockOut = models.DateTimeField(null=True, blank=True)
    totalDuration = models.CharField(max_length=50, blank=True, null=True) # "HH:MM"
    verificationPhoto = models.TextField(blank=True, null=True) # base64
    verificationLocation = models.JSONField(default=dict, blank=True) # {lat, lon}
    status = models.CharField(
        max_length=50,
        choices=ATTENDANCE_STATUS_CHOICES,
        default='Pending Approval'
    )  # Pending Approval | Approved | Late | Half Day | Absent

    def __str__(self):
        return f"{self.employeeName} - {self.date} ({self.status})"

class Task(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    assignedTo = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='tasks')
    assignedName = models.CharField(max_length=255)
    dueDate = models.DateField()
    status = models.CharField(max_length=50, default='Pending') # Pending | In Progress | Completed
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    limitPeriod = models.CharField(max_length=50, default='Yearly') # Monthly | Yearly
    maxLimit = models.IntegerField(default=10)
    restrictedDates = models.JSONField(default=list, blank=True) # [{date, reason}]
    carryForward = models.BooleanField(default=False)
    maxCarryForward = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default='Active') # Active | Inactive
    minAdvanceDays = models.IntegerField(default=0)
    organization = models.ForeignKey('Organization', null=True, blank=True, on_delete=models.CASCADE, related_name='leave_types')
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
    dayType = models.CharField(max_length=50, default='Full') # Full | Half
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, default='Pending') # Pending | Approved | Rejected
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employeeName} - {self.leaveTypeName} ({self.startDate} to {self.endDate})"

class Holiday(models.Model):
    organization = models.ForeignKey('Organization', null=True, blank=True, on_delete=models.CASCADE, related_name='holidays')
    name = models.CharField(max_length=255)
    date = models.DateField()
    description = models.TextField(blank=True, null=True)
    banner = models.TextField(blank=True, null=True) # base64
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
    organization = models.ForeignKey('Organization', null=True, blank=True, on_delete=models.CASCADE, related_name='locations')
    name = models.CharField(max_length=255)
    lat = models.FloatField()
    lon = models.FloatField()
    radius = models.FloatField(default=100.0)
    isPrimary = models.BooleanField(default=False)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Schedule(models.Model):
    designation = models.CharField(max_length=100, unique=True)
    shiftStart = models.CharField(max_length=5, default="09:00") # "HH:MM"
    shiftEnd = models.CharField(max_length=5, default="17:00") # "HH:MM"
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.designation} ({self.shiftStart} - {self.shiftEnd})"

def default_weekly_holidays_default():
    return ["Sunday"]

class OrgSettings(models.Model):
    brandLogo = models.TextField(blank=True, null=True) # base64
    subscriptionDays = models.IntegerField(default=12)
    subscriptionRenewedAt = models.DateTimeField(auto_now_add=True)
    max_employees_allowed = models.IntegerField(default=10)
    is_attendance_enabled = models.BooleanField(default=False)
    is_project_enabled = models.BooleanField(default=False)
    subscriptionStatus = models.CharField(max_length=50, default='Active')
    subscriptionExpiresAt = models.DateTimeField(null=True, blank=True)
    has_sent_billing_warning = models.BooleanField(default=False)
    # Attendance time-rule configuration
    grace_period_minutes = models.IntegerField(default=15)  # Minutes after shift start before considered Late
    half_day_threshold_minutes = models.IntegerField(default=240)  # Minutes worked to qualify as Half Day (vs Absent)
    full_day_absent_threshold_minutes = models.IntegerField(default=60)  # Minutes after shift start before marked Absent
    auto_approve_attendance = models.BooleanField(default=False)  # Toggle switch for auto-approving attendance
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
    settings = models.OneToOneField(OrgSettings, on_delete=models.CASCADE, related_name='organization', null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class AuditLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    employeeName = models.CharField(max_length=255, blank=True, null=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True, null=True)
    ipAddress = models.CharField(max_length=45, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employeeName or 'System'} - {self.action} ({self.createdAt})"


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
    
    # New fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='New')
    assigned_staff = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_leads')
    is_read = models.BooleanField(default=False)
    read_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='read_leads')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.email})"


class LeadHistory(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='histories')
    modified_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='lead_histories')
    action = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Lead {self.lead_id} history - {self.action}"


class SubscriptionPackage(models.Model):
    name = models.CharField(max_length=255, unique=True)
    price = models.DecimalField(max_digits=20, decimal_places=2)
    employeeLimit = models.IntegerField(default=10)
    features = models.JSONField(default=list, blank=True)
    isActive = models.BooleanField(default=True)
    video_url = models.URLField(max_length=1024, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class SubscriberAccount(models.Model):
    email = models.EmailField(unique=True)
    packageName = models.CharField(max_length=255, default='Free Package')
    isActive = models.BooleanField(default=True)
    expiresAt = models.DateTimeField(null=True, blank=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.email} - {self.packageName}"


class CMSContent(models.Model):
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True, null=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key


class LMSModule(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=100, default='Coaching')
    content = models.TextField(blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Coupon(models.Model):
    code = models.CharField(max_length=100, unique=True)
    discountType = models.CharField(max_length=50, default='Percentage') # Percentage | Flat
    discountValue = models.IntegerField(default=10)
    usageLimit = models.IntegerField(default=100)
    usageCount = models.IntegerField(default=0)
    expiresAt = models.DateTimeField(null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.discountValue} {self.discountType})"


class Wallet(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='wallet')
    organization = models.ForeignKey('Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='wallets')
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0.00)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee.email}'s Wallet - Balance: {self.balance}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if getattr(self, '_processing_dues', False):
            return
        try:
            self._processing_dues = True
            from api.models import MonthlyInvoice, WalletTransaction
            from api.tasks import queue_and_send_email
            from decimal import Decimal
            
            unpaid_invoices = list(MonthlyInvoice.objects.filter(organization=self.organization, is_paid=False).order_by('billing_month'))
            total_due = sum(inv.amount for inv in unpaid_invoices)
            
            if total_due > 0 and self.balance >= total_due:
                self.balance = self.balance - total_due
                self.save()
                
                for inv in unpaid_invoices:
                    inv.is_paid = True
                    from django.utils import timezone
                    inv.paid_at = timezone.now()
                    inv.save()
                    
                WalletTransaction.objects.create(
                    wallet=self,
                    amount=total_due,
                    transactionType='Debit',
                    success=True,
                    status='Success',
                    details=f"Automated wallet deduction: Outstanding dues cleared on top-up."
                )
                
                if self.organization and self.organization.settings:
                    settings = self.organization.settings
                    settings.subscriptionStatus = 'Active'
                    settings.save()
                    
                superadmin = Employee.objects.filter(organization=self.organization, isSuperAdmin=True).first()
                if superadmin:
                    subject = f"Notice: Dues Paid & Workspace Activated for {self.organization.name}"
                    message = (
                        f"Hi {superadmin.first_name or 'Superadmin'},\n\n"
                        f"Thank you! Your outstanding dues of ₹{total_due} INR have been successfully paid after your recent top-up.\n"
                        f"Your workspace is fully active.\n"
                        f"Updated Wallet Balance: ₹{self.balance} INR.\n\n"
                        f"CubeLogs Billing Team"
                    )
                    try:
                        queue_and_send_email(superadmin.email, subject, message)
                    except Exception:
                        pass
        finally:
            self._processing_dues = False


class WalletTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('Credit', 'Credit'),
        ('Debit', 'Debit'),
    ]
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    transactionType = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    success = models.BooleanField(default=True)
    stripeEventId = models.CharField(max_length=255, blank=True, null=True)
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=50, default='Success')
    details = models.TextField(blank=True, null=True)
    receipt_url = models.URLField(max_length=1024, blank=True, null=True)
    invoice_url = models.URLField(max_length=1024, blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transactionType} - {self.amount} - {self.success} ({self.wallet.employee.email})"


import secrets

def default_coupon_code():
    return secrets.token_hex(4).upper()

class BackofficeCoupon(models.Model):
    VALUE_TYPE_CHOICES = [
        ('Fixed Amount', 'Fixed Amount'),
        ('Percentage', 'Percentage'),
    ]
    code = models.CharField(max_length=100, unique=True, default=default_coupon_code)
    value_type = models.CharField(max_length=50, choices=VALUE_TYPE_CHOICES, default='Percentage')
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_deposit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)
    expiry_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.value} {self.value_type})"


class EmailQueue(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
        ('RETRYING', 'Retrying'),
    ]

    recipient = models.EmailField()
    from_email = models.EmailField(blank=True, null=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    html_body = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    task_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient} - {self.subject} ({self.status})"


class EmailLog(models.Model):
    TEMPLATE_CHOICES = [
        ('WELCOME', 'User Registration Welcome Email'),
        ('LOW_BALANCE', 'Low Wallet Alert'),
        ('DEBIT_INVOICE', 'Standard Transaction Invoice'),
        ('SUBSCRIPTION_EXPIRED', 'Subscription Expired Notice'),
        ('DATA_KEEPING_FEE', 'Monthly Data Maintenance Invoice'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
    ]

    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    template_type = models.CharField(max_length=20, choices=TEMPLATE_CHOICES)
    html_content = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    password = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient} - {self.template_type} ({self.status})"


class PromoVideoSection(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    youtube_url = models.URLField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Testimonial(models.Model):
    stars = models.IntegerField(default=5)
    text = models.TextField()
    author_initials = models.CharField(max_length=10, blank=True, null=True)
    author_name = models.CharField(max_length=255)
    author_title = models.CharField(max_length=255)
    bg_color = models.CharField(max_length=50, default='var(--primary)')
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.author_name} ({self.stars} stars)"


class MonthlyInvoice(models.Model):
    organization = models.ForeignKey('Organization', on_delete=models.CASCADE, related_name='monthly_invoices')
    billing_month = models.DateField()  # Date of the 1st day of the billing month
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    invoice_email_sent = models.BooleanField(default=False)
    deduction_reminder_sent = models.BooleanField(default=False)

    def __str__(self):
        return f"Invoice for {self.organization.name} - {self.billing_month.strftime('%B %Y')} - Amount: {self.amount} (Paid: {self.is_paid})"







