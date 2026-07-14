# --------------------------------------------------------------------------------
#       Billing Tasks
# --------------------------------------------------------------------------------

try:
    from celery import shared_task
except ImportError:
    def shared_task(func):
        return func

from django.utils import timezone
from users.models import Employee
from core.models import Organization, OrgSettings, AuditLog
from subscribers.models import Wallet, WalletTransaction, MonthlyInvoice
from decimal import Decimal
import logging
from core.tasks import EmailService
from django.conf import settings as django_settings
from django.core.cache import cache
from datetime import timedelta

logger = logging.getLogger(__name__)

@shared_task
def sweep_workspace_subscriptions():
    TEST_MODE = getattr(django_settings, 'TEST_MODE', False)
    logger.info(f"Starting subscription validation sweep. TEST_MODE={TEST_MODE}")
    
    orgs = Organization.objects.all()
    for org in orgs:
        settings_obj = org.settings
        if not settings_obj:
            continue
            
        if TEST_MODE:
            if not settings_obj.subscriptionRenewedAt:
                settings_obj.subscriptionRenewedAt = timezone.now()
                settings_obj.save()
                
            elapsed = (timezone.now() - settings_obj.subscriptionRenewedAt).total_seconds()
            
            rate = 0
            if settings_obj.is_attendance_enabled:
                rate += 100
            if settings_obj.is_project_enabled:
                rate += 100
            cost = settings_obj.max_employees_allowed * rate
            cost_dec = Decimal(str(cost))
            
            wallet = Wallet.objects.filter(organization=org).first()
            if not wallet:
                superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                if superadmin:
                    wallet, _ = Wallet.objects.get_or_create(
                        employee=superadmin,
                        defaults={'organization': org, 'balance': Decimal('0.00')}
                    )
            
            if 0 <= elapsed < 120:
                sent_flag = f"org_{org.id}_email_1_sent"
                if not cache.get(sent_flag):
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        subject = f"[TEST MODE] Invoice generated for workspace {org.name}"
                        message = (
                            f"Hi {superadmin.first_name or 'Superadmin'},\n\n"
                            f"An invoice of ₹{cost} INR has been generated for workspace {org.name}.\n"
                            f"Current Wallet Balance: ₹{wallet.balance if wallet else '0.00'} INR.\n\n"
                            f"CubeLogs Billing Team"
                        )
                        EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                    cache.set(sent_flag, True, 600)
            
            elif 120 <= elapsed < 240:
                if settings_obj.subscriptionStatus == 'Active':
                    settings_obj.subscriptionStatus = 'Pending Payment'
                    settings_obj.save()

                if settings_obj.subscriptionStatus == 'Pending Payment':
                    sent_flag = f"org_{org.id}_email_2_sent"
                    if not cache.get(sent_flag):
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] Grace Period Reminder: Unpaid subscription for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is a reminder that your subscription invoice of ₹{cost} INR remains unpaid.
Your workspace is in a grace period. Please top up your wallet.

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        cache.set(sent_flag, True, 600)
                        
            elif 240 <= elapsed < 360:
                if settings_obj.subscriptionStatus != 'Active':
                    sent_flag = f"org_{org.id}_email_3_sent"
                    if not cache.get(sent_flag):
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] FINAL WARNING: Subscription suspension imminent for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

FINAL WARNING: Your workspace {org.name} will be suspended in less than 2 minutes due to an unpaid invoice of ₹{cost} INR.

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        cache.set(sent_flag, True, 600)
                    
                    safe_balance = wallet.balance if wallet else Decimal('0.00')
                    if wallet and safe_balance >= cost_dec:
                        wallet.balance = safe_balance - cost_dec
                        wallet.save()
                        
                        settings_obj.subscriptionStatus = 'Active'
                        settings_obj.subscriptionRenewedAt = timezone.now()
                        settings_obj.subscriptionExpiresAt = timezone.now() + timedelta(minutes=10)
                        settings_obj.save()
                        
                        WalletTransaction.objects.create(
                            wallet=wallet,
                            amount=cost_dec,
                            transactionType='Debit',
                            success=True,
                            status='Success',
                            details=f"[TEST MODE] Automated auto-pull subscription renewal"
                        )
                        
                        cache.delete(f"org_{org.id}_email_1_sent")
                        cache.delete(f"org_{org.id}_email_2_sent")
                        cache.delete(f"org_{org.id}_email_3_sent")
                        
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] Notice: Subscription Paid & Activated for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Thank you. Your workspace subscription has been activated.
Updated Wallet Balance: ₹{wallet.balance} INR

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                            
            elif 360 <= elapsed < 480:
                if settings_obj.subscriptionStatus != 'Active':
                    if settings_obj.subscriptionStatus != 'Suspended':
                        settings_obj.subscriptionStatus = 'Suspended'
                        settings_obj.save()
                        
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] Workspace Suspended: {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace {org.name} has been SUSPENDED due to an unpaid invoice of ₹{cost} INR.

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                            
            elif elapsed >= 480:
                sent_flag = f"org_{org.id}_maintenance_email_sent"
                if not cache.get(sent_flag):
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        subject = f"[TEST MODE] Data Maintenance Rent Invoice: {org.name}"
                        message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is your Monthly Data Maintenance Rent Invoice simulation for workspace {org.name}.
Rent Charge: ₹50 INR

CubeLogs Billing Team"""
                        EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                    cache.set(sent_flag, True, 600)
        else:
            # Production/Staging standard sweep billing logic
            now_dt = timezone.localtime(timezone.now())
            today = now_dt.date()
            billing_month = today.replace(day=1)

            if today.day == 1:
                invoice, created = MonthlyInvoice.objects.get_or_create(
                    organization=org,
                    billing_month=billing_month,
                    defaults={'amount': Decimal('0.00'), 'is_paid': False}
                )
                if created:
                    has_previous_unpaid = MonthlyInvoice.objects.filter(
                        organization=org, billing_month__lt=billing_month, is_paid=False
                    ).exists()
                    if has_previous_unpaid or settings_obj.subscriptionStatus in ['Unpaid', 'Suspended']:
                        amount = Decimal('50.00')
                    else:
                        rate = 0
                        if settings_obj.is_attendance_enabled:
                            rate += 100
                        if settings_obj.is_project_enabled:
                            rate += 100
                        amount = Decimal(str(settings_obj.max_employees_allowed * rate))
                    invoice.amount = amount
                    invoice.save()

                if not invoice.invoice_email_sent:
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        unpaid_list = list(MonthlyInvoice.objects.filter(organization=org, is_paid=False).order_by('billing_month'))
                        unpaid_count = len(unpaid_list)
                        if unpaid_count == 1:
                            subject = f"Invoice generated for workspace {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

An invoice of ₹{invoice.amount} INR has been generated for workspace {org.name} for the month of {billing_month.strftime('%B %Y')}.
An automatic wallet deduction check will run on the 5th of this month at 12:00 PM.

Please ensure your prepaid wallet has sufficient balance.

Thank you,
CubeLogs Billing Team"""
                        elif unpaid_count == 2:
                            overdue_inv = unpaid_list[0]
                            subject = f"Overdue Payment Reminder: Invoice for workspace {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is an overdue payment reminder that your previous invoice for {overdue_inv.billing_month.strftime('%B %Y')} of ₹{overdue_inv.amount} INR is still unpaid.
A new bill for the current month's data retention rent charge of ₹{invoice.amount} INR has been generated.

Please deposit funds into your wallet immediately to reactivate premium features.

Thank you,
CubeLogs Billing Team"""
                        else:
                            subject = f"URGENT: Workspace Data Deletion Warning - Unpaid Invoice for {org.name}"
                            dues_details = "\n".join([f"- {inv.billing_month.strftime('%B %Y')}: ₹{inv.amount} INR" for inv in unpaid_list])
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

URGENT WARNING: Your workspace {org.name} has multiple pending payments that are overdue:
{dues_details}

ALL YOUR USER AND WORKSPACE DATA WILL BE PERMANENTLY DELETED after three months if outstanding payments are not completed.

Please settle the dues immediately to prevent data loss.

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(superadmin.email, subject, message)
                            invoice.invoice_email_sent = True
                            invoice.save()
                        except Exception as e:
                            logger.error(f"Failed to send 1st-of-month invoice email for {org.name}: {e}")

                oldest_unpaid = MonthlyInvoice.objects.filter(organization=org, is_paid=False).order_by('billing_month').first()
                if oldest_unpaid:
                    days_overdue = (today - oldest_unpaid.billing_month).days
                    if days_overdue >= 90:
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"Notice of Permanent Data Deletion: Workspace {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

As your workspace {org.name} has remained unpaid for over three months, all user profiles, attendance sheets, tasks, settings, and other associated workspace data have been permanently deleted from our servers.

If you wish to use our platform again, you will need to register a new account.

CubeLogs System Administrator"""
                            try:
                                from django.core.mail import send_mail
                                send_mail(subject, message, django_settings.DEFAULT_FROM_EMAIL, [superadmin.email])
                            except Exception as e:
                                logger.error(f"Failed to send final deletion email: {e}")
                        logger.warning(f"Permanently deleting organization {org.name} due to 3+ months non-payment.")
                        org.delete()
                        continue

            is_retry_day = (today.day == 5 and now_dt.hour >= 12) or (today.day == 6 and now_dt.hour < 12)
            if is_retry_day:
                if today.day == 5 and now_dt.hour == 12:
                    reminder_key = f"org_{org.id}_deduction_reminder_sent_{today.year}_{today.month}"
                    if not cache.get(reminder_key):
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            unpaid_invoices = MonthlyInvoice.objects.filter(organization=org, is_paid=False)
                            total_due = sum(inv.amount for inv in unpaid_invoices)
                            if total_due > 0:
                                subject = f"Deduction Alert: Automatic Wallet Payment Pending for {org.name}"
                                message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is an automated notice that we will attempt to deduct your outstanding workspace dues of ₹{total_due} INR automatically from your prepaid wallet balance starting today at 12:00 PM.

Please ensure your wallet has sufficient balance to avoid service restriction.

Thank you,
CubeLogs Billing Team"""
                                try:
                                    EmailService.queue_and_send_email(superadmin.email, subject, message)
                                    cache.set(reminder_key, True, 86400 * 2)
                                except Exception as e:
                                    logger.error(f"Failed to send deduction warning: {e}")

                retry_lock_key = f"org_{org.id}_last_deduction_retry_{today.year}_{today.month}"
                if not cache.get(retry_lock_key):
                    unpaid_invoices = list(MonthlyInvoice.objects.filter(organization=org, is_paid=False))
                    total_due = sum(inv.amount for inv in unpaid_invoices)
                    
                    if total_due > 0:
                        wallet = Wallet.objects.filter(organization=org).first()
                        if not wallet:
                            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                            if superadmin:
                                wallet, _ = Wallet.objects.get_or_create(
                                    employee=superadmin,
                                    defaults={'organization': org, 'balance': Decimal('0.00')}
                                )
                        safe_balance = Decimal(str(wallet.balance)) if wallet else Decimal('0.00')
                        
                        if wallet and safe_balance >= total_due:
                            wallet.balance = safe_balance - total_due
                            wallet.save()
                            
                            for inv in unpaid_invoices:
                                inv.is_paid = True
                                inv.paid_at = timezone.now()
                                inv.save()
                                
                            WalletTransaction.objects.create(
                                wallet=wallet,
                                amount=total_due,
                                transactionType='Debit',
                                success=True,
                                status='Success',
                                details=f"Automated wallet deduction: Monthly outstanding invoices cleared."
                            )
                            settings_obj.subscriptionStatus = 'Active'
                            settings_obj.save()
                            
                            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                            if superadmin:
                                subject = f"Notice: Dues Paid & Workspace Activated for {org.name}"
                                message = f"""Hi {superadmin.first_name or 'Superadmin'},

Thank you! Your outstanding dues of ₹{total_due} INR have been successfully deducted from your wallet.
Your workspace is fully active.
Updated Wallet Balance: ₹{wallet.balance} INR.

CubeLogs Billing Team"""
                                try:
                                    EmailService.queue_and_send_email(superadmin.email, subject, message)
                                except Exception as e:
                                    logger.error(f"Failed to send payment success email: {e}")
                        else:
                            WalletTransaction.objects.create(
                                wallet=wallet,
                                amount=total_due,
                                transactionType='Debit',
                                success=False,
                                status='Failed',
                                details=f"Auto-deduction failed: Insufficient balance. Required: ₹{total_due}."
                            )
                            cache.set(retry_lock_key, True, 600)
                            logger.warning(f"Deduction failed for organization {org.name} due to insufficient balance.")

            is_after_retry_period = (today.day == 6 and now_dt.hour >= 12) or (today.day > 6)
            if is_after_retry_period:
                unpaid_count = MonthlyInvoice.objects.filter(organization=org, is_paid=False).count()
                if unpaid_count > 0:
                    if settings_obj.subscriptionStatus != 'Unpaid':
                        settings_obj.subscriptionStatus = 'Unpaid'
                        settings_obj.save()
                        
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"Alert: Workspace Restricted for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace {org.name} has been restricted because the automatic wallet deduction on the 5th failed due to insufficient balance, and the retry period has ended.

To restore full access, please deposit outstanding dues into your wallet immediately.

Thank you,
CubeLogs Billing Team"""
                            try:
                                EmailService.queue_and_send_email(superadmin.email, subject, message)
                            except Exception as e:
                                logger.error(f"Failed to send restriction notice email: {e}")
            
            if settings_obj.subscriptionStatus == 'Active' and settings_obj.subscriptionExpiresAt:
                time_left = settings_obj.subscriptionExpiresAt - timezone.now()
                if time_left.total_seconds() > 0 and time_left.total_seconds() <= 300 and not settings_obj.has_sent_billing_warning:
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        rate = 0
                        if settings_obj.is_attendance_enabled:
                            rate += 100
                        if settings_obj.is_project_enabled:
                            rate += 100
                        cost = settings_obj.max_employees_allowed * rate
                        
                        current_balance = '0.00'
                        wallet_obj = Wallet.objects.filter(organization=org).first()
                        if wallet_obj:
                            current_balance = wallet_obj.balance
                            
                        subject = f"Invoice Alert: Subscription Renewal Pending for {org.name}"
                        message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is an automated notice that your subscription for workspace {org.name} will renew in approximately 5 minutes.
An invoice of ₹{cost} INR will be charged automatically from your prepaid wallet balance.

Current Wallet Balance: ₹{current_balance}

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        except Exception as e:
                            logger.error(f"Failed to queue billing warning email to {superadmin.email}: {e}")
                    
                    settings_obj.has_sent_billing_warning = True
                    settings_obj.save()
            
            is_new = not settings_obj.subscriptionExpiresAt
            is_expired_active = (
                settings_obj.subscriptionStatus == 'Active' and
                settings_obj.subscriptionExpiresAt is not None and
                settings_obj.subscriptionExpiresAt <= timezone.now()
            )
            is_pending_retry = (
                settings_obj.subscriptionStatus == 'Pending Payment' and
                settings_obj.subscriptionExpiresAt is not None and
                settings_obj.subscriptionExpiresAt + timedelta(minutes=5) <= timezone.now()
            )

            if is_new or is_expired_active or is_pending_retry:
                rate = 0
                if settings_obj.is_attendance_enabled:
                    rate += 100
                if settings_obj.is_project_enabled:
                    rate += 100
                try:
                    cost = int(settings_obj.max_employees_allowed or 0) * rate
                    cost_dec = Decimal(str(cost))
                except (ValueError, TypeError):
                    cost_dec = Decimal('0.00')
                
                wallet = Wallet.objects.filter(organization=org).first()
                if not wallet:
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        wallet, _ = Wallet.objects.get_or_create(
                            employee=superadmin, 
                            defaults={'organization': org, 'balance': Decimal('0.00')}
                        )
                
                safe_balance = Decimal('0.00')
                if wallet:
                    try:
                        safe_balance = Decimal(str(wallet.balance))
                    except Exception:
                        safe_balance = Decimal('0.00')
                
                if wallet and safe_balance >= cost_dec:
                    wallet.balance = safe_balance - cost_dec
                    wallet.save()
                    
                    settings_obj.subscriptionDays = 30
                    settings_obj.subscriptionStatus = 'Active'
                    settings_obj.subscriptionExpiresAt = timezone.now() + timedelta(minutes=10)
                    settings_obj.has_sent_billing_warning = False
                    settings_obj.save()
                    
                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=cost_dec,
                        transactionType='Debit',
                        success=True,
                        status='Success',
                        details=f"Automated subscription renewal: {settings_obj.max_employees_allowed} employees (Addons: {'attendance' if settings_obj.is_attendance_enabled else 'none'}, {'project' if settings_obj.is_project_enabled else 'none'})"
                    )
                    
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        subject = f"Notice: Subscription Expired & Auto-Renewed for {org.name}"
                        message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your 10-minute subscription for workspace {org.name} has EXPIRED.
We have automatically processed the renewal payment of ₹{cost_dec} INR from your prepaid wallet balance.

Updated Wallet Balance: ₹{wallet.balance} INR
Subscription Validity: Extended by 10 Minutes

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        except Exception as e:
                            logger.error(f"Failed to queue renewal email: {e}")
                else:
                    settings_obj.subscriptionStatus = 'Pending Payment'
                    settings_obj.subscriptionExpiresAt = timezone.now()
                    settings_obj.save()
                    
                    if wallet:
                        WalletTransaction.objects.create(
                            wallet=wallet,
                            amount=cost_dec,
                            transactionType='Debit',
                            success=False,
                            status='Failed',
                            details=f"Automated subscription renewal failed: Insufficient balance. Required: ₹{cost_dec}."
                        )
                    
                    AuditLog.objects.create(
                        employee=wallet.employee if wallet else None,
                        employeeName="System / Celery",
                        action="Subscription Renewal Failure",
                        details=f"Workspace {org.name} subscription renewal failed due to insufficient wallet balance (Required: ₹{cost_dec})."
                    )
                    
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        subject = f"URGENT: Subscription Expired & Renewal Failed for {org.name}"
                        message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your 10-minute subscription for workspace {org.name} has EXPIRED.
The automated renewal has FAILED due to insufficient wallet balance.

Required Amount: ₹{cost_dec} INR
Current Wallet Balance: ₹{wallet.balance if wallet else '0.00'} INR

Please deposit money into your wallet immediately to reactivate premium modules.

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        except Exception as e:
                            logger.error(f"Failed to queue failure email: {e}")
