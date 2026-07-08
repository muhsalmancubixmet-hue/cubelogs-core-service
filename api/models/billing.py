import secrets
from decimal import Decimal

from django.db import models
from api.models.employee import Employee


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


class Wallet(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='wallet')
    organization = models.ForeignKey(
        'api.Organization',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='wallets',
    )
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
            from api.tasks import queue_and_send_email
            from django.utils import timezone

            unpaid_invoices = list(
                MonthlyInvoice.objects.filter(
                    organization=self.organization, is_paid=False
                ).order_by('billing_month')
            )
            total_due = sum((inv.amount for inv in unpaid_invoices), Decimal('0'))

            if total_due > 0 and self.balance >= total_due:
                self.balance = self.balance - total_due  # type: ignore[assignment]
                self.save()

                for inv in unpaid_invoices:
                    inv.is_paid = True
                    inv.paid_at = timezone.now()
                    inv.save()

                WalletTransaction.objects.create(
                    wallet=self,
                    amount=total_due,
                    transactionType='Debit',
                    success=True,
                    status='Success',
                    details="Automated wallet deduction: Outstanding dues cleared on top-up.",
                )

                if self.organization and self.organization.settings:
                    settings = self.organization.settings
                    settings.subscriptionStatus = 'Active'  # type: ignore[assignment]
                    settings.save()

                superadmin = Employee.objects.filter(
                    organization=self.organization, isSuperAdmin=True
                ).first()
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


class MonthlyInvoice(models.Model):
    organization = models.ForeignKey(
        'api.Organization',
        on_delete=models.CASCADE,
        related_name='monthly_invoices',
    )
    billing_month = models.DateField()  # Date of the 1st day of the billing month
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    invoice_email_sent = models.BooleanField(default=False)
    deduction_reminder_sent = models.BooleanField(default=False)

    def __str__(self):
        return (
            f"Invoice for {self.organization.name} - "
            f"{self.billing_month.strftime('%B %Y')} - "
            f"Amount: {self.amount} (Paid: {self.is_paid})"
        )


class Coupon(models.Model):
    code = models.CharField(max_length=100, unique=True)
    discountType = models.CharField(max_length=50, default='Percentage')  # Percentage | Flat
    discountValue = models.IntegerField(default=10)
    usageLimit = models.IntegerField(default=100)
    usageCount = models.IntegerField(default=0)
    expiresAt = models.DateTimeField(null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.discountValue} {self.discountType})"
