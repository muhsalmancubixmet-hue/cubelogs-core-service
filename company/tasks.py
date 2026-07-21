# ================================================================================
#   company/tasks.py
#   ──────────────────────────────────────────────────────────────────────────────
#   CubeLogs Billing Sweep — Celery Background Task
#
#   This file contains the core subscription billing engine for CubeLogs.
#   The main task `sweep_workspace_subscriptions` is triggered automatically
#   by Celery Beat every 1 minute (configured in settings.py → CELERY_BEAT_SCHEDULE).
#
#   What this task does:
#   ┌─────────────────────────────────────────────────────────────────────────┐
#   │  For every Organization in the system, it:                              │
#   │  1. Generates monthly invoices (1st of every month)                     │
#   │  2. Sends invoice emails to the super admin                             │
#   │  3. Attempts wallet deductions on the 5th of every month               │
#   │  4. Restricts workspace if payment fails (status → 'Unpaid')           │
#   │  5. Auto-renews subscriptions when they expire (deducts from wallet)   │
#   │  6. Permanently deletes workspaces with 90+ days unpaid invoices        │
#   └─────────────────────────────────────────────────────────────────────────┘
#
#   Modes:
#   • TEST_MODE = True  → Full billing cycle runs in 8 minutes (for testing)
#   • TEST_MODE = False → Real production billing (monthly cycle)
# ================================================================================


# ── Celery Import ────────────────────────────────────────────────────────────────
# We use a try/except so the file can be imported even if Celery is not installed
# (e.g., during unit tests or local development without a broker).
try:
    from celery import shared_task
except ImportError:
    # Fallback: turn @shared_task into a no-op decorator so the function still works
    def shared_task(func):
        return func

# ── Standard Django + App Imports ────────────────────────────────────────────────
from django.utils import timezone
from users.models import Employee
from core.models import Organization, OrgSettings, AuditLog
from subscribers.models import Wallet, WalletTransaction, MonthlyInvoice, GlobalBillingSettings
from decimal import Decimal
import logging
from core.tasks import EmailService
from django.conf import settings as django_settings
from django.core.cache import cache          # Used to prevent duplicate emails
from datetime import timedelta
import calendar

# Standard Python logger — output appears in Celery worker logs
logger = logging.getLogger(__name__)


# ================================================================================
#   MAIN TASK: sweep_workspace_subscriptions
#   ──────────────────────────────────────────────────────────────────────────────
#   Called by Celery Beat every 1 minute.
#   Loops through ALL organizations and handles billing lifecycle for each.
# ================================================================================
@shared_task
def sweep_workspace_subscriptions():
    # Read TEST_MODE from settings (default: False = production mode)
    TEST_MODE = getattr(django_settings, 'TEST_MODE', False)
    g_settings = GlobalBillingSettings.get_settings()
    orgs = Organization.objects.all()

    for org in orgs:
        # Get the org's settings object (contains subscription info, feature flags, etc.)
        settings_obj = org.settings
        if not settings_obj:
            # No settings means the org was never fully provisioned — skip safely
            continue

        # Ensure wallet exists for this org
        wallet = Wallet.objects.filter(organization=org).first()
        if not wallet:
            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
            if superadmin:
                wallet, _ = Wallet.objects.get_or_create(
                    employee=superadmin,
                    defaults={'organization': org, 'balance': Decimal('0.00')}
                )

        if TEST_MODE:
            # ── Initialize the timer if this is the first sweep for this org ──
            if not settings_obj.subscriptionRenewedAt:
                settings_obj.subscriptionRenewedAt = timezone.now()
                settings_obj.save()

            # How many seconds have passed since the cycle started?
            elapsed = (timezone.now() - settings_obj.subscriptionRenewedAt).total_seconds()

            # ── Calculate monthly subscription cost for this org dynamically based on g_settings & per-day rate ──
            if settings_obj.subscriptionStatus != 'Restricted':
                rate = Decimal('0.00')
                now_date = timezone.now().date()
                days_in_month = calendar.monthrange(now_date.year, now_date.month)[1]
                if settings_obj.is_attendance_enabled:
                    daily_att = Decimal(str(g_settings.attendance_module_price)) / Decimal(str(days_in_month))
                    rate += daily_att * Decimal(str(days_in_month))
                if settings_obj.is_project_enabled:
                    daily_tasks = Decimal(str(g_settings.tasks_module_price)) / Decimal(str(days_in_month))
                    rate += daily_tasks * Decimal(str(days_in_month))
                active_emp_count = Employee.objects.filter(organization=org, is_active=True).count()
                cost = Decimal(str(g_settings.monthly_subscription_price)) + (Decimal(str(active_emp_count)) * Decimal(str(g_settings.employee_seat_price))) + rate
                if g_settings.tax_percentage > 0:
                    cost += cost * (Decimal(str(g_settings.tax_percentage)) / Decimal('100.00'))
            else:
                cost = Decimal(str(g_settings.monthly_data_rent))
                if g_settings.tax_percentage > 0:
                    cost += cost * (Decimal(str(g_settings.tax_percentage)) / Decimal('100.00'))
            cost_dec = Decimal(str(cost)).quantize(Decimal('0.01'))

            # TEST PHASE 1: 0 – 120 seconds → Invoice Generated
            if 0 <= elapsed < 120:
                sent_flag = f"org_{org.id}_email_1_sent"
                if not cache.get(sent_flag):
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        # Create actual invoice object
                        MonthlyInvoice.objects.create(
                            organization=org,
                            billing_month=timezone.now().date(),
                            amount=cost_dec,
                            is_paid=False
                        )
                        subject = f"[TEST MODE] Invoice generated for workspace {org.name}"
                        message = (
                            f"Hi {superadmin.first_name or 'Superadmin'},\n\n"
                            f"An invoice of ₹{cost_dec} {g_settings.currency} has been generated for workspace {org.name}.\n"
                            f"Current Wallet Balance: ₹{wallet.balance if wallet else '0.00'} {g_settings.currency}.\n\n"
                            f"CubeLogs Billing Team"
                        )
                        EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                    cache.set(sent_flag, True, 600)

            # TEST PHASE 2: 120 – 240 seconds → Grace Period / Pending Payment
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

This is a notice that your automated wallet deduction failed due to insufficient balance.
Your subscription status is Pending Payment. Please recharge your wallet.

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                        cache.set(sent_flag, True, 600)

            # TEST PHASE 3: 240 – 360 seconds → Final Warning + Auto-Deduct
            elif 240 <= elapsed < 360:
                if settings_obj.subscriptionStatus != 'Active':
                    sent_flag = f"org_{org.id}_email_3_sent"
                    if not cache.get(sent_flag):
                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] FINAL WARNING: Automatic payment collection retry for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Reminder: We will attempt automatic payment collection for outstanding dues of ₹{cost_dec} from your wallet.

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

                        # Mark latest invoice as paid
                        latest_inv = MonthlyInvoice.objects.filter(organization=org, is_paid=False).order_by('-id').first()
                        if latest_inv:
                            latest_inv.is_paid = True
                            latest_inv.paid_at = timezone.now()
                            latest_inv.save()

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
                            subject = f"[TEST MODE] Notice: Payment Successful for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Thank you. Your payment was processed successfully.
Updated Wallet Balance: ₹{wallet.balance} {g_settings.currency}

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                    else:
                        settings_obj.subscriptionStatus = 'Pending Payment'
                        settings_obj.save()
                        if wallet:
                            WalletTransaction.objects.create(
                                wallet=wallet,
                                amount=cost_dec,
                                transactionType='Debit',
                                success=False,
                                status='Failed',
                                details="[TEST MODE] Automated auto-pull subscription renewal failed: Insufficient balance."
                            )

            # TEST PHASE 4: 360 – 480 seconds → Workspace Restricted
            elif 360 <= elapsed < 480:
                if settings_obj.subscriptionStatus != 'Active':
                    if settings_obj.subscriptionStatus != 'Restricted':
                        settings_obj.subscriptionStatus = 'Restricted'
                        settings_obj.save()

                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"[TEST MODE] Workspace Restricted: {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace {org.name} has been RESTRICTED due to outstanding dues of ₹{cost_dec} {g_settings.currency}.
Premium modules have been restricted. No customer data has been deleted.

CubeLogs Billing Team"""
                            EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')

            # TEST PHASE 5: 480+ seconds → Data Maintenance Fee (Rent) Invoices
            elif elapsed >= 480:
                # Every 2 minutes, generate a rent invoice
                cycle_count = int((elapsed - 480) // 120)
                sent_flag = f"org_{org.id}_maintenance_email_{cycle_count}_sent"
                if not cache.get(sent_flag):
                    # Create rent invoice
                    MonthlyInvoice.objects.create(
                        organization=org,
                        billing_month=timezone.now().date(),
                        amount=Decimal(str(g_settings.monthly_data_rent)),
                        is_paid=False
                    )
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        subject = f"[TEST MODE] Data Retention Rent Invoice: {org.name}"
                        message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace remains inactive. We have generated a Data Retention Rent invoice of ₹{g_settings.monthly_data_rent} {g_settings.currency} to keep your data securely stored.

CubeLogs Billing Team"""
                        EmailService.queue_and_send_email(superadmin.email, subject, message, 'muhsalman.cubixmet@gmail.com')
                    cache.set(sent_flag, True, 600)

        else:
            # ── PRODUCTION MODE ───────────────────────────────────────────────
            now_dt = timezone.localtime(timezone.now())
            today = now_dt.date()

            # ── 1. INVOICE GENERATION ────────────────────────────────────────
            # Runs on the configured generation day (e.g. 1st)
            if today.day == g_settings.invoice_generation_day:
                billing_month = today.replace(day=1)
                
                # Check if invoice already generated for this month
                invoice, created = MonthlyInvoice.objects.get_or_create(
                    organization=org,
                    billing_month=billing_month,
                    defaults={'amount': Decimal('0.00'), 'is_paid': False}
                )

                if created:
                    # Calculate subscription or rent cost dynamically on a per-day basis
                    if settings_obj.subscriptionStatus != 'Restricted':
                        rate = Decimal('0.00')
                        days_in_month = calendar.monthrange(billing_month.year, billing_month.month)[1]

                        if settings_obj.is_attendance_enabled:
                            daily_att = Decimal(str(g_settings.attendance_module_price)) / Decimal(str(days_in_month))
                            rate += daily_att * Decimal(str(days_in_month))

                        if settings_obj.is_project_enabled:
                            daily_tasks = Decimal(str(g_settings.tasks_module_price)) / Decimal(str(days_in_month))
                            rate += daily_tasks * Decimal(str(days_in_month))

                        active_emp_count = Employee.objects.filter(organization=org, is_active=True).count()
                        amount = g_settings.monthly_subscription_price + (Decimal(str(active_emp_count)) * g_settings.employee_seat_price) + rate
                    else:
                        amount = g_settings.monthly_data_rent

                    if g_settings.tax_percentage > 0:
                        amount += amount * (g_settings.tax_percentage / Decimal('100.00'))

                    invoice.amount = amount.quantize(Decimal('0.01'))
                    invoice.save()

                # Send invoice generated notification
                if not invoice.invoice_email_sent:
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        if settings_obj.subscriptionStatus != 'Restricted':
                            subject = f"Invoice generated for workspace {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

An invoice of ₹{invoice.amount} {g_settings.currency} has been generated for workspace {org.name} for the month of {billing_month.strftime('%B %Y')}.
An automatic wallet deduction check will run on the {g_settings.auto_deduction_day}th of this month at 12:00 PM.

Please ensure your prepaid wallet has sufficient balance.

Thank you,
CubeLogs Billing Team"""
                        else:
                            subject = f"Data Retention Rent Invoice for workspace {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace {org.name} remains restricted/inactive.
To store your historical data securely, a Data Retention Rent invoice of ₹{invoice.amount} {g_settings.currency} has been generated for {billing_month.strftime('%B %Y')}.

Storage charges will continue accumulating until outstanding dues are paid.

Thank you,
CubeLogs Billing Team"""

                        try:
                            EmailService.queue_and_send_email(superadmin.email, subject, message)
                            invoice.invoice_email_sent = True
                            invoice.save()
                        except Exception as e:
                            logger.error(f"Failed to send invoice email for {org.name}: {e}")

            # ── 2. DEDUCTION REMINDER ────────────────────────────────────────
            # Runs X days before the auto deduction day
            reminder_day = g_settings.auto_deduction_day - g_settings.reminder_email_days_before
            if today.day == reminder_day and now_dt.hour == 12:
                reminder_key = f"org_{org.id}_deduction_reminder_sent_{today.year}_{today.month}"
                if not cache.get(reminder_key):
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        unpaid_invoices = MonthlyInvoice.objects.filter(organization=org, is_paid=False)
                        total_due = sum(inv.amount for inv in unpaid_invoices)
                        if total_due > 0:
                            subject = f"Deduction Alert: Automatic Wallet Payment Pending for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

This is an automated notice that we will attempt to deduct your outstanding workspace dues of ₹{total_due} {g_settings.currency} automatically from your prepaid wallet balance on the {g_settings.auto_deduction_day}th of this month starting at 12:00 PM.

Please ensure your wallet has sufficient balance to avoid service restriction.

Thank you,
CubeLogs Billing Team"""
                            try:
                                EmailService.queue_and_send_email(superadmin.email, subject, message)
                                cache.set(reminder_key, True, 86400 * 2)
                            except Exception as e:
                                logger.error(f"Failed to send deduction warning: {e}")

            # ── 3. AUTOMATIC DEDUCTION ───────────────────────────────────────
            # Runs on the configured deduction day at 12:00 PM
            if today.day == g_settings.auto_deduction_day and now_dt.hour == 12:
                deduction_lock_key = f"org_{org.id}_last_deduction_attempt_{today.year}_{today.month}"
                if not cache.get(deduction_lock_key):
                    unpaid_invoices = list(MonthlyInvoice.objects.filter(organization=org, is_paid=False))
                    total_due = sum(inv.amount for inv in unpaid_invoices)

                    if total_due > 0:
                        safe_balance = Decimal(str(wallet.balance)) if wallet else Decimal('0.00')

                        if wallet and safe_balance >= total_due:
                            # ✅ Payment Successful Workflow
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
                                details=f"Automated wallet deduction: Outstanding invoices cleared."
                            )

                            settings_obj.subscriptionStatus = 'Active'
                            settings_obj.save()

                            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                            if superadmin:
                                subject = f"Notice: Payment Successful for {org.name}"
                                message = f"""Hi {superadmin.first_name or 'Superadmin'},

Thank you! Your outstanding dues of ₹{total_due} {g_settings.currency} have been successfully deducted from your wallet.
Your workspace is active.
Updated Wallet Balance: ₹{wallet.balance} {g_settings.currency}.

CubeLogs Billing Team"""
                                try:
                                    EmailService.queue_and_send_email(superadmin.email, subject, message)
                                except Exception as e:
                                    logger.error(f"Failed to send payment success email: {e}")
                        else:
                            # ❌ Payment Failed Workflow
                            settings_obj.subscriptionStatus = 'Pending Payment'
                            settings_obj.save()

                            WalletTransaction.objects.create(
                                wallet=wallet,
                                amount=total_due,
                                transactionType='Debit',
                                success=False,
                                status='Failed',
                                details=f"Auto-deduction failed: Insufficient balance. Required: ₹{total_due}."
                            )

                            AuditLog.objects.create(
                                employee=wallet.employee if wallet else None,
                                employeeName="System / Celery",
                                action="Subscription Renewal Failure",
                                details=f"Workspace {org.name} deduction failed due to insufficient wallet balance (Required: ₹{total_due})."
                            )

                            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                            if superadmin:
                                subject = f"Payment Failed: Subscription Renewal for {org.name}"
                                message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your automated subscription payment renewal has FAILED due to insufficient wallet balance.
Status is now Pending Payment. Please recharge your wallet immediately to prevent service restriction.

Required Amount: ₹{total_due} {g_settings.currency}
Current Wallet Balance: ₹{wallet.balance if wallet else '0.00'} {g_settings.currency}

Thank you,
CubeLogs Billing Team"""
                                try:
                                    EmailService.queue_and_send_email(superadmin.email, subject, message)
                                except Exception as e:
                                    logger.error(f"Failed to send failure email: {e}")

                        # Set lock so we don't retry again during this minute's run
                        cache.set(deduction_lock_key, True, 60)

            # ── 4. GRACE PERIOD & RESTRICTION WORKFLOW ───────────────────────
            # Transition to Restricted status if still unpaid after grace period
            limit_day = g_settings.auto_deduction_day + g_settings.grace_period_days
            if today.day > limit_day:
                unpaid_count = MonthlyInvoice.objects.filter(organization=org, is_paid=False).count()
                if unpaid_count > 0:
                    if settings_obj.subscriptionStatus != 'Restricted':
                        settings_obj.subscriptionStatus = 'Restricted'
                        settings_obj.save()

                        superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                        if superadmin:
                            subject = f"Alert: Workspace Restricted for {org.name}"
                            message = f"""Hi {superadmin.first_name or 'Superadmin'},

Your workspace {org.name} has been RESTRICTED because your outstanding dues are overdue and the grace period has ended.
Your premium modules have been restricted. Note that all of your data has been retained safely and is NOT deleted.

To restore full access, please deposit outstanding dues into your wallet immediately.

Thank you,
CubeLogs Billing Team"""
                            try:
                                EmailService.queue_and_send_email(superadmin.email, subject, message)
                            except Exception as e:
                                logger.error(f"Failed to send workspace restriction email for {org.name}: {e}")

