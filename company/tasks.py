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
from django.db import transaction, IntegrityError
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
            from company.api.v1.services import BillingService
            from django.template.loader import render_to_string

            recipient_email = BillingService.get_billing_recipient_email(org)

            # ── 1. CATCH-UP INVOICE GENERATION ──────────────────────────────
            # Generate invoice for current month if missing (handles Celery down on Day 1 safely)
            billing_month = today.replace(day=1)

            # Check for missing historical invoices (Cross-Month Multi-Gap Recovery Detection)
            # Detects every missing billing period between the organization's first eligible month
            # and the month prior to current billing_month. Current state is never used to fabricate historical charges.
            org_created_date = getattr(org, 'created_at', None)
            if org_created_date and hasattr(org_created_date, 'date'):
                org_created_date = org_created_date.date()
            first_eligible_month = (org_created_date or today).replace(day=1)

            curr_cursor = first_eligible_month
            while curr_cursor < billing_month:
                inv_exists = MonthlyInvoice.objects.filter(organization=org, billing_month=curr_cursor).exists()
                if not inv_exists:
                    logger.warning(
                        "CROSS-MONTH BILLING RECOVERY GAP: Organization %s (ID: %s) is missing invoice for billing month %s. "
                        "Current state will NOT be used to fabricate historical charges. Operational review required.",
                        org.name, org.id, curr_cursor.strftime('%Y-%m-%d')
                    )
                days_in_curr = calendar.monthrange(curr_cursor.year, curr_cursor.month)[1]
                curr_cursor = (curr_cursor + timedelta(days=days_in_curr)).replace(day=1)

            inv_type = 'DATA_RETENTION' if settings_obj.subscriptionStatus == 'Restricted' else 'SUBSCRIPTION'

            # DATA_RETENTION must not be generated incorrectly mid-month just because status became Restricted
            if inv_type == 'DATA_RETENTION' and MonthlyInvoice.objects.filter(
                organization=org, billing_month=billing_month, invoice_type='SUBSCRIPTION'
            ).exists():
                logger.info(
                    "Skipping mid-month DATA_RETENTION invoice generation for org %s: SUBSCRIPTION invoice already exists for billing month %s.",
                    org.name, billing_month
                )
                continue

            with transaction.atomic():
                # Lock the parent Organization row to serialize concurrent billing workers for this tenant
                Organization.objects.select_for_update().filter(id=org.id).first()

                # Restore invoice identity: (organization, billing_month, invoice_type)
                existing_inv = MonthlyInvoice.objects.filter(
                    organization=org, billing_month=billing_month, invoice_type=inv_type
                ).first()

                if existing_inv:
                    invoice = existing_inv
                    created = False
                else:
                    try:
                        invoice = MonthlyInvoice.objects.create(
                            organization=org,
                            billing_month=billing_month,
                            invoice_type=inv_type,
                            amount=Decimal('0.00'),
                            is_paid=False
                        )
                        created = True
                    except IntegrityError:
                        invoice = MonthlyInvoice.objects.filter(
                            organization=org, billing_month=billing_month, invoice_type=inv_type
                        ).first()
                        created = False

                    if created:
                        if settings_obj.subscriptionStatus != 'Restricted':
                            emp_count = BillingService.get_billable_memberships_qs(org).count()
                            seat_price = Decimal(str(g_settings.employee_seat_price))
                            emp_total = Decimal(str(emp_count)) * seat_price

                            att_enabled = settings_obj.is_attendance_enabled
                            att_unit = Decimal(str(g_settings.attendance_module_price)) if att_enabled else Decimal('0.00')
                            att_total = (Decimal(str(emp_count)) * att_unit) if att_enabled else Decimal('0.00')

                            proj_enabled = settings_obj.is_project_enabled
                            proj_unit = Decimal(str(g_settings.tasks_module_price)) if proj_enabled else Decimal('0.00')
                            proj_total = (Decimal(str(emp_count)) * proj_unit) if proj_enabled else Decimal('0.00')

                            # Storage billing in arrears
                            storage_info = BillingService.get_previous_month_storage_billing(org, billing_month)
                            storage_charge = storage_info['storage_charge']

                            total = emp_total + att_total + proj_total + storage_charge

                            invoice.employee_count_snapshot = emp_count
                            invoice.employee_unit_price_snapshot = seat_price
                            invoice.employee_total_snapshot = emp_total
                            invoice.base_price_snapshot = Decimal('0.00')
                            invoice.attendance_enabled_snapshot = att_enabled
                            invoice.attendance_unit_price_snapshot = att_unit
                            invoice.attendance_total_snapshot = att_total
                            invoice.attendance_price_snapshot = att_total
                            invoice.project_enabled_snapshot = proj_enabled
                            invoice.project_unit_price_snapshot = proj_unit
                            invoice.project_total_snapshot = proj_total
                            invoice.project_price_snapshot = proj_total

                            # Storage snapshots
                            invoice.storage_usage_month_snapshot = storage_info['storage_usage_month']
                            invoice.storage_charge_snapshot = storage_charge
                            invoice.storage_finalized_days_snapshot = storage_info['storage_finalized_days']
                            invoice.storage_billable_bytes_days_snapshot = storage_info['storage_billable_bytes_days']
                            invoice.storage_credit_days_snapshot = storage_info['storage_credit_days']

                            invoice.subtotal_snapshot = total
                            invoice.tax_percentage_snapshot = Decimal('0.00')
                            invoice.tax_amount_snapshot = Decimal('0.00')
                            invoice.amount = total.quantize(Decimal('0.01'))
                            invoice.save()
                        else:
                            invoice.amount = Decimal(str(g_settings.monthly_data_rent)).quantize(Decimal('0.01'))
                            invoice.save()

            # Calendar-Safe Date Boundary Computations
            month_max_day = calendar.monthrange(billing_month.year, billing_month.month)[1]
            deduction_day_clamped = min(g_settings.auto_deduction_day, month_max_day)
            auto_deduction_date = billing_month.replace(day=deduction_day_clamped)
            reminder_date = auto_deduction_date - timedelta(days=g_settings.reminder_email_days_before)
            restriction_date = auto_deduction_date + timedelta(days=g_settings.grace_period_days)

            # Send HTML Monthly Invoice Email ONCE per invoice
            if not invoice.invoice_email_sent and recipient_email:
                subject = f"Invoice generated for workspace {org.name}" if settings_obj.subscriptionStatus != 'Restricted' else f"Data Retention Rent Invoice for workspace {org.name}"

                wallet_bal = wallet.balance if wallet else Decimal('0.00')
                req_recharge = max(Decimal('0.00'), invoice.amount - wallet_bal)
                add_money_url = f"{django_settings.FRONTEND_URL}/admin/settings?tab=billing"
                view_invoice_url = f"{django_settings.FRONTEND_URL}/admin/settings?tab=billing&invoice_id={invoice.id}"
                auto_deduction_date_str = auto_deduction_date.strftime('%B %d, %Y at 12:00 PM')

                try:
                    html_content = render_to_string(
                        "emails/billing/monthly_invoice.html",
                        {
                            "org_name": org.name,
                            "billing_month": billing_month.strftime('%B %Y'),
                            "base_price": invoice.base_price_snapshot or Decimal('0.00'),
                            "employee_count": invoice.employee_count_snapshot or 0,
                            "unit_price": invoice.employee_unit_price_snapshot or g_settings.employee_seat_price,
                            "employee_total": invoice.employee_total_snapshot or Decimal('0.00'),
                            "attendance_enabled": invoice.attendance_enabled_snapshot,
                            "attendance_unit_price": invoice.attendance_unit_price_snapshot or g_settings.attendance_module_price,
                            "attendance_price": invoice.attendance_total_snapshot or invoice.attendance_price_snapshot or Decimal('0.00'),
                            "project_enabled": invoice.project_enabled_snapshot,
                            "project_unit_price": invoice.project_unit_price_snapshot or g_settings.tasks_module_price,
                            "project_price": invoice.project_total_snapshot or invoice.project_price_snapshot or Decimal('0.00'),
                            "storage_charge": invoice.storage_charge_snapshot or Decimal('0.00'),
                            "storage_usage_month": invoice.storage_usage_month_snapshot.strftime('%B %Y') if invoice.storage_usage_month_snapshot else "N/A",
                            "storage_finalized_days": invoice.storage_finalized_days_snapshot or 0,
                            "subtotal": invoice.subtotal_snapshot or invoice.amount,
                            "tax_percentage": invoice.tax_percentage_snapshot or Decimal('0.00'),
                            "tax_amount": invoice.tax_amount_snapshot or Decimal('0.00'),
                            "total_amount": invoice.amount,
                            "wallet_balance": wallet_bal,
                            "auto_deduction_date": auto_deduction_date_str,
                            "required_recharge": req_recharge,
                            "add_money_url": add_money_url,
                            "view_invoice_url": view_invoice_url,
                            "product_support_email": django_settings.SUPPORT_EMAIL,
                            "product_website": django_settings.COMPANY_WEBSITE,
                            "product_company_name": django_settings.COMPANY_NAME,
                        }
                    )
                    EmailService.send_transactional_email(recipient_email, subject, html_content, 'MONTHLY_INVOICE')
                    invoice.invoice_email_sent = True
                    invoice.save()
                except Exception as e:
                    logger.error(f"Failed to send invoice email for {org.name}: {e}")

            # ── 2. DEDUCTION REMINDER ────────────────────────────────────────
            if today >= reminder_date and today < auto_deduction_date:
                if not invoice.deduction_reminder_sent and not invoice.is_paid and recipient_email:
                    unpaid_invoices = MonthlyInvoice.objects.filter(organization=org, is_paid=False)
                    total_due = sum(inv.amount for inv in unpaid_invoices)
                    if total_due > 0:
                        subject = f"Deduction Alert: Automatic Wallet Payment Pending for {org.name}"
                        message = f"""Hi,

This is an automated notice that we will attempt to deduct your outstanding workspace dues of ₹{total_due} {g_settings.currency} automatically from your prepaid wallet balance on {auto_deduction_date.strftime('%B %d, %Y')} starting at 12:00 PM.

Please ensure your wallet has sufficient balance to avoid service restriction.

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(recipient_email, subject, message)
                            invoice.deduction_reminder_sent = True
                            invoice.save()
                        except Exception as e:
                            logger.error(f"Failed to send deduction warning for {org.name}: {e}")

            # ── 3. AUTOMATIC DEDUCTION ───────────────────────────────────────
            if today >= auto_deduction_date:
                unpaid_exists = MonthlyInvoice.objects.filter(organization=org, is_paid=False).exists()
                if unpaid_exists and wallet:
                    prev_status = settings_obj.subscriptionStatus
                    BillingService.process_outstanding_dues(wallet)
                    settings_obj.refresh_from_db()

                    # Send Payment Failed notification if invoice remains unpaid after deduction attempt
                    invoice.refresh_from_db()
                    if not invoice.is_paid and not invoice.payment_failed_email_sent and recipient_email:
                        unpaid_invoices = MonthlyInvoice.objects.filter(organization=org, is_paid=False)
                        total_due = sum(inv.amount for inv in unpaid_invoices)
                        subject = f"Payment Failed: Subscription Renewal for {org.name}"
                        message = f"""Hi,

Your automated subscription payment renewal has FAILED due to insufficient wallet balance.
Status is now Pending Payment. Please recharge your wallet immediately to prevent service restriction.

Required Amount: ₹{total_due} {g_settings.currency}
Current Wallet Balance: ₹{wallet.balance} {g_settings.currency}

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(recipient_email, subject, message)
                            invoice.payment_failed_email_sent = True
                            invoice.save()
                        except Exception as e:
                            logger.error(f"Failed to send failure email for {org.name}: {e}")

            # ── 4. GRACE PERIOD & RESTRICTION WORKFLOW ───────────────────────
            if today > restriction_date:
                unpaid_count = MonthlyInvoice.objects.filter(organization=org, is_paid=False).count()
                if unpaid_count > 0:
                    if settings_obj.subscriptionStatus != 'Restricted':
                        settings_obj.subscriptionStatus = 'Restricted'
                        settings_obj.save()

                    if not invoice.restriction_email_sent and recipient_email:
                        subject = f"Alert: Workspace Restricted for {org.name}"
                        message = f"""Hi,

Your workspace {org.name} has been RESTRICTED because your outstanding dues are overdue and the grace period has ended.
Your premium modules have been restricted. Note that all of your data has been retained safely and is NOT deleted.

To restore full access, please deposit outstanding dues into your wallet immediately.

Thank you,
CubeLogs Billing Team"""
                        try:
                            EmailService.queue_and_send_email(recipient_email, subject, message)
                            invoice.restriction_email_sent = True
                            invoice.save()
                        except Exception as e:
                            logger.error(f"Failed to send workspace restriction email for {org.name}: {e}")


@shared_task
def reconcile_pending_wallet_transactions(threshold_hours=24):
    """
    Reconciles stale 'Pending' WalletTransactions older than threshold_hours (default 24h).
    Gateway state is authoritative:
    - If Razorpay order is paid/captured: route through central idempotent credit_wallet_from_payment().
    - If failed/expired/abandoned at gateway, or stale created/attempted past threshold: status -> 'Abandoned'.
    - Financial history is durably preserved; rows are never deleted.
    """
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(hours=threshold_hours)
    pending_txs = list(
        WalletTransaction.objects.filter(
            status='Pending',
            created_at__lt=cutoff
        ).select_related('wallet', 'wallet__organization')
    )

    if not pending_txs:
        return 0

    from subscribers.api.v1.views import get_razorpay_client
    client, _, _ = get_razorpay_client()
    from company.api.v1.services import BillingService

    reconciled_count = 0
    for tx in pending_txs:
        if not tx.razorpay_order_id:
            continue

        # Stale mock orders in dev/test environment
        if tx.razorpay_order_id.startswith('mock_'):
            tx.status = 'Abandoned'
            tx.save(update_fields=['status', 'updated_at'])
            reconciled_count += 1
            continue

        if not client:
            # Cannot reach gateway; leave untouched for next reconciliation run
            continue

        try:
            order = client.order.fetch(tx.razorpay_order_id)
            order_status = order.get('status')
            payments = client.order.payments(tx.razorpay_order_id)
            items = payments.get('items', []) if isinstance(payments, dict) else payments
            captured_pay = None
            for p in items:
                if p.get('status') == 'captured':
                    captured_pay = p
                    break

            if captured_pay:
                pay_id = captured_pay.get('id')
                amount_paise = captured_pay.get('amount', 0)
                amount_dec = Decimal(str(amount_paise)) / Decimal('100.0')
                BillingService.credit_wallet_from_payment(
                    wallet_id=tx.wallet_id,
                    amount_dec=amount_dec,
                    razorpay_order_id=tx.razorpay_order_id,
                    razorpay_payment_id=pay_id,
                    target_org_id=tx.wallet.organization_id if tx.wallet else None,
                    details="Reconciled wallet deposit via automated task"
                )
                reconciled_count += 1
            elif order_status in ['attempted', 'created']:
                tx.status = 'Abandoned'
                tx.save(update_fields=['status', 'updated_at'])
                reconciled_count += 1
        except Exception as exc:
            logger.warning(f"Reconciliation check skipped for tx {tx.id} ({tx.razorpay_order_id}): {exc}")

    return reconciled_count

