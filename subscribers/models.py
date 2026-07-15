import secrets
from django.db import models
from core.models import BaseModel

# SubscriptionPackage Model: Represents pricing plan packages, limits, and features
class SubscriptionPackage(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    price = models.DecimalField(max_digits=20, decimal_places=2)
    employeeLimit = models.IntegerField(default=10)
    features = models.JSONField(default=list, blank=True)
    isActive = models.BooleanField(default=True)
    video_url = models.URLField(max_length=1024, blank=True, null=True)

    class Meta:
        db_table = 'api_subscriptionpackage'

    def __str__(self):
        return self.name


# SubscriberAccount Model: Tracks organization level subscription package state
class SubscriberAccount(BaseModel):
    email = models.EmailField(unique=True)
    packageName = models.CharField(max_length=255, default='Free Package')
    isActive = models.BooleanField(default=True)
    expiresAt = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'api_subscriberaccount'

    def __str__(self):
        return f"{self.email} - {self.packageName}"


# ==============================================================================
# Billing Models
# ==============================================================================

# Wallet Model: Represents employee wallets tracking user prepaid account balance
class Wallet(BaseModel):
    employee = models.OneToOneField('users.Employee', on_delete=models.CASCADE, related_name='wallet')
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='wallets',
    )
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0.00)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = 'api_wallet'

    def __str__(self):
        return f"{self.employee.email}'s Wallet - Balance: {self.balance}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Delegate dues processing side-effects directly to BillingService post-save
        from company.api.v1.services import BillingService
        BillingService.process_outstanding_dues(self)


# WalletTransaction Model: Records credits/debits associated with employee wallets
class WalletTransaction(BaseModel):
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

    class Meta:
        db_table = 'api_wallettransaction'

    def __str__(self):
        return f"{self.transactionType} - {self.amount} - {self.success} ({self.wallet.employee.email})"


def default_coupon_code():
    return secrets.token_hex(4).upper()


# BackofficeCoupon Model: Tracks coupons generated from administration backend
class BackofficeCoupon(BaseModel):
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

    class Meta:
        db_table = 'api_backofficecoupon'

    def __str__(self):
        return f"{self.code} ({self.value} {self.value_type})"


# MonthlyInvoice Model: Represents monthly organization level billing statements
class MonthlyInvoice(BaseModel):
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='monthly_invoices',
    )
    billing_month = models.DateField()
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    invoice_email_sent = models.BooleanField(default=False)
    deduction_reminder_sent = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_monthlyinvoice'

    def __str__(self):
        return f"Invoice for {self.organization.name} - {self.billing_month.strftime('%B %Y')} - {self.amount} (Paid: {self.is_paid})"


# Coupon Model: Tracks customer promotion coupons, discount values, and usage
class Coupon(BaseModel):
    code = models.CharField(max_length=100, unique=True)
    discountType = models.CharField(max_length=50, default='Percentage')
    discountValue = models.IntegerField(default=10)
    usageLimit = models.IntegerField(default=100)
    usageCount = models.IntegerField(default=0)
    expiresAt = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'api_coupon'

    def __str__(self):
        return f"{self.code} ({self.discountValue} {self.discountType})"


# GlobalBillingSettings Model: Configurable settings for monthly billing values, cycles, tax and grace period
class GlobalBillingSettings(BaseModel):
    monthly_subscription_price = models.DecimalField(max_digits=20, decimal_places=2, default=100.00)
    monthly_data_rent = models.DecimalField(max_digits=20, decimal_places=2, default=50.00)
    attendance_module_price = models.DecimalField(max_digits=20, decimal_places=2, default=100.00)
    tasks_module_price = models.DecimalField(max_digits=20, decimal_places=2, default=100.00)
    employee_seat_price = models.DecimalField(max_digits=20, decimal_places=2, default=100.00)
    grace_period_days = models.IntegerField(default=5)
    reminder_email_days_before = models.IntegerField(default=1)
    auto_deduction_day = models.IntegerField(default=5)
    invoice_generation_day = models.IntegerField(default=1)
    currency = models.CharField(max_length=10, default='INR')
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    class Meta:
        db_table = 'api_globalbillingsettings'

    def __str__(self):
        return f"Global Billing Settings (ID={self.id})"

