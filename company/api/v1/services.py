# --------------------------------------------------------------------------------
#       Company Services (Unified Billing and CRM)
# --------------------------------------------------------------------------------

import re
from decimal import Decimal
import logging
from django.utils import timezone
from django.conf import settings

from core.tasks import EmailService
from company.models import (
    Lead, LeadHistory
)
from subscribers.models import SubscriptionPackage, SubscriberAccount, Wallet, MonthlyInvoice, WalletTransaction, GlobalBillingSettings

from core.models import Organization, OrgSettings
from users.models import Employee, PERMISSION_FLAGS
from users.api.v1.services import UserService
from core.utils import generate_secure_password

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. Billing Service
# ==============================================================================

class BillingService:
    @staticmethod
    def get_billable_memberships_qs(org):
        from users.models import OrganizationMembership
        from django.db.models import Q
        today = timezone.now().date()
        status_condition = Q(employment_status='Active') | (
            Q(employment_status__in=['Resigned', 'Terminated']) & Q(last_working_date__gt=today)
        )
        return OrganizationMembership.objects.filter(
            organization=org,
            is_deleted=False,
            is_active_in_org=True,
            user__is_active=True,
            user__isSuperAdmin=False
        ).filter(status_condition)

    @staticmethod
    def get_billing_recipient_email(org):
        if not org:
            return None
        if hasattr(org, 'settings') and org.settings and org.settings.billing_email:
            email = org.settings.billing_email.strip()
            if email:
                return email

        from users.models import OrganizationMembership
        from django.db.models import Q
        admin_mem = OrganizationMembership.objects.filter(
            organization=org,
            is_deleted=False,
            is_active_in_org=True,
            user__is_active=True
        ).filter(
            Q(user__isSuperAdmin=True) |
            Q(role__slug__in=['company-admin', 'super-admin', 'admin']) |
            Q(designation__icontains='Admin')
        ).select_related('user').first()

        if admin_mem and admin_mem.user and admin_mem.user.email:
            return admin_mem.user.email

        fallback_mem = OrganizationMembership.objects.filter(
            organization=org,
            is_deleted=False,
            is_active_in_org=True,
            user__is_active=True
        ).select_related('user').first()
        if fallback_mem and fallback_mem.user and fallback_mem.user.email:
            return fallback_mem.user.email

        return None

    @staticmethod
    def process_outstanding_dues(wallet):
        if getattr(wallet, '_processing_dues', False):
            return

        from django.db import transaction

        emails_to_send = []
        try:
            wallet._processing_dues = True

            with transaction.atomic():
                # Acquire row-level lock on wallet for financial safety
                locked_wallet = Wallet.objects.select_for_update().get(id=wallet.id)

                unpaid_invoices = list(
                    MonthlyInvoice.objects.filter(
                        organization=locked_wallet.organization, is_paid=False
                    ).order_by('billing_month', 'id')
                )

                if not unpaid_invoices:
                    return

                total_settled_amount = Decimal('0.00')
                settled_invoices = []

                # Oldest complete invoice first: pay invoices sequentially as long as wallet balance covers full amount
                current_bal = locked_wallet.balance
                for inv in unpaid_invoices:
                    if current_bal >= inv.amount:
                        current_bal -= inv.amount
                        total_settled_amount += inv.amount
                        inv.is_paid = True
                        inv.paid_at = timezone.now()
                        inv.save()
                        settled_invoices.append(inv)
                    else:
                        # Cannot pay complete invoice -> DO NOT partially debit
                        break

                if total_settled_amount > 0:
                    locked_wallet.balance = current_bal
                    super(locked_wallet.__class__, locked_wallet).save(update_fields=['balance'])

                    first_inv = settled_invoices[0] if settled_invoices else None
                    inv_labels = ", ".join([inv.billing_month.strftime('%B %Y') for inv in settled_invoices])
                    inv_url = f"/api/monthly-invoices/{first_inv.id}/pdf/" if first_inv else None
                    WalletTransaction.objects.create(
                        wallet=locked_wallet,
                        amount=total_settled_amount,
                        transactionType='Debit',
                        success=True,
                        status='Success',
                        invoice_url=inv_url,
                        details=f"Automated wallet deduction: Settled invoice(s) for {inv_labels}.",
                    )

                    # Check if ALL unpaid invoices are cleared for this organization
                    remaining_unpaid_exists = MonthlyInvoice.objects.filter(
                        organization=locked_wallet.organization, is_paid=False
                    ).exists()

                    recipient_email = BillingService.get_billing_recipient_email(locked_wallet.organization)

                    if not remaining_unpaid_exists:
                        was_inactive = False
                        if locked_wallet.organization and locked_wallet.organization.settings:
                            settings_obj = locked_wallet.organization.settings
                            was_inactive = settings_obj.subscriptionStatus in ['Unpaid', 'Suspended', 'Restricted', 'Pending Payment']
                            settings_obj.subscriptionStatus = 'Active'
                            settings_obj.subscriptionDays = 30
                            if settings_obj.subscriptionExpiresAt:
                                if settings_obj.subscriptionExpiresAt > timezone.now():
                                    settings_obj.subscriptionExpiresAt = settings_obj.subscriptionExpiresAt + timezone.timedelta(days=30)
                                else:
                                    settings_obj.subscriptionExpiresAt = timezone.now() + timezone.timedelta(days=30)
                            else:
                                settings_obj.subscriptionExpiresAt = timezone.now() + timezone.timedelta(days=30)
                            settings_obj.save()

                        if recipient_email:
                            already_reactivated_sent = any(getattr(inv, 'reactivation_email_sent', False) for inv in settled_invoices)
                            if not already_reactivated_sent:
                                for inv in settled_invoices:
                                    inv.reactivation_email_sent = True
                                    inv.save(update_fields=['reactivation_email_sent'])

                                if was_inactive:
                                    subject = f"Workspace Reactivated: CubeLogs"
                                    message = (
                                        f"Hi,\n\n"
                                        f"Thank you! Your outstanding dues of ₹{total_settled_amount} INR have been successfully paid, and your workspace {locked_wallet.organization.name} has been reactivated.\n"
                                        f"All premium modules are now unlocked. All previous employees, attendance sheets, tasks, settings, and workspace data remain fully available.\n\n"
                                        f"Updated Wallet Balance: ₹{locked_wallet.balance} INR.\n\n"
                                        f"CubeLogs Billing Team"
                                    )
                                else:
                                    subject = f"Notice: Dues Paid & Workspace Activated for {locked_wallet.organization.name}"
                                    message = (
                                        f"Hi,\n\n"
                                        f"Thank you! Your outstanding dues of ₹{total_settled_amount} INR have been successfully paid.\n"
                                        f"Your workspace is active.\n"
                                        f"Updated Wallet Balance: ₹{locked_wallet.balance} INR.\n\n"
                                        f"CubeLogs Billing Team"
                                    )
                                emails_to_send.append((recipient_email, subject, message))

                    # Queue email execution AFTER transaction commit
                    if emails_to_send:
                        transaction.on_commit(
                            lambda: [EmailService.queue_and_send_email(rec, subj, msg) for rec, subj, msg in emails_to_send]
                        )
        finally:
            wallet._processing_dues = False

    @staticmethod
    def credit_wallet_from_payment(
        wallet_id, amount_dec, session_id=None, details=None,
        bonus_amount_dec=Decimal('0.00'), coupon_code=None, event_id=None,
        target_org_id=None, razorpay_order_id=None, razorpay_payment_id=None,
        razorpay_signature=None, gateway_event_id=None
    ):
        """
        Durable, atomic, and idempotent wallet credit service.
        Supports Razorpay order/payment IDs while preserving legacy Stripe idempotency fallback.
        """
        from subscribers.models import Wallet, WalletTransaction
        from django.db import transaction
        from django.db.models import Q

        total_credit = amount_dec + bonus_amount_dec

        with transaction.atomic():
            # 1. Lock the target wallet immediately to serialize concurrent operations
            locked_wallet = Wallet.objects.select_for_update().filter(id=wallet_id).first()
            if not locked_wallet:
                raise ValueError(f"Wallet with ID {wallet_id} not found.")

            # 2. Lock any existing/pending transaction row matching the external identifiers
            tx_obj = None
            if razorpay_order_id:
                tx_obj = WalletTransaction.objects.select_for_update().filter(razorpay_order_id=razorpay_order_id).first()
            if not tx_obj and razorpay_payment_id:
                tx_obj = WalletTransaction.objects.select_for_update().filter(razorpay_payment_id=razorpay_payment_id).first()
            if not tx_obj and session_id:
                tx_obj = WalletTransaction.objects.select_for_update().filter(stripe_session_id=session_id).first()

            # 3. Inside the lock, check if payment was already successfully processed
            if tx_obj and tx_obj.success:
                return locked_wallet, tx_obj, False

            tx_existing = None
            if razorpay_payment_id:
                tx_existing = WalletTransaction.objects.filter(
                    razorpay_payment_id=razorpay_payment_id, success=True
                ).first()
            if not tx_existing and razorpay_order_id:
                tx_existing = WalletTransaction.objects.filter(
                    razorpay_order_id=razorpay_order_id, success=True
                ).first()
            if not tx_existing and gateway_event_id:
                tx_existing = WalletTransaction.objects.filter(
                    gateway_event_id=gateway_event_id, success=True
                ).first()
            if not tx_existing and session_id:
                tx_existing = WalletTransaction.objects.filter(
                    stripe_session_id=session_id, success=True
                ).first()

            if tx_existing:
                return tx_existing.wallet, tx_existing, False

            # 4. Enforce tenant isolation against locked wallet
            if target_org_id and locked_wallet.organization_id and str(locked_wallet.organization_id) != str(target_org_id):
                raise ValueError("Tenant mismatch: wallet organization does not match payment metadata organization.")

            # 5. Mutate balance inside lock
            locked_wallet.balance += total_credit
            super(locked_wallet.__class__, locked_wallet).save(update_fields=['balance'])

            detail_text = details or f"Prepaid wallet top-up of ₹{amount_dec}"
            if bonus_amount_dec > 0 and coupon_code:
                detail_text += f" (Bonus ₹{bonus_amount_dec} via code '{coupon_code}')"

            evt_id = gateway_event_id or event_id

            if tx_obj:
                tx_obj.amount = total_credit
                tx_obj.success = True
                tx_obj.status = 'Success'
                tx_obj.details = detail_text
                if razorpay_order_id:
                    tx_obj.razorpay_order_id = razorpay_order_id
                if razorpay_payment_id:
                    tx_obj.razorpay_payment_id = razorpay_payment_id
                if razorpay_signature:
                    tx_obj.razorpay_signature = razorpay_signature
                if evt_id:
                    tx_obj.gateway_event_id = evt_id
                    tx_obj.stripeEventId = evt_id
                tx_obj.save()
            else:
                tx_obj = WalletTransaction.objects.create(
                    wallet=locked_wallet,
                    amount=total_credit,
                    transactionType='Credit',
                    success=True,
                    status='Success',
                    stripe_session_id=session_id,
                    stripeEventId=evt_id,
                    razorpay_order_id=razorpay_order_id,
                    razorpay_payment_id=razorpay_payment_id,
                    razorpay_signature=razorpay_signature,
                    gateway_event_id=evt_id,
                    details=detail_text
                )

            BillingService.process_outstanding_dues(locked_wallet)

            return locked_wallet, tx_obj, True

    @staticmethod
    def trigger_low_balance_alert(user):
        if not user or not user.email:
            return
        wallet = Wallet.objects.filter(employee=user).first()
        balance_str = f"₹{wallet.balance} INR" if wallet else "₹0.00 INR"
        
        subject = "Low Wallet Balance Alert - CubeLogs"
        from django.template.loader import render_to_string
        html_content = render_to_string(
            "emails/billing/low_balance.html",
            {
                "employee_name": user.first_name or 'User',
                "balance": balance_str,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(user.email, subject, html_content, 'LOW_BALANCE')

    @staticmethod
    def trigger_wallet_invoice(user, amount, current_balance):
        if not user or not user.email:
            return
        subject = "Invoice: Debit Transaction Receipt - CubeLogs"
        tx_date = timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        from django.template.loader import render_to_string
        html_content = render_to_string(
            "emails/billing/wallet_invoice.html",
            {
                "employee_name": user.first_name or 'User',
                "amount": amount,
                "current_balance": current_balance,
                "tx_date": tx_date,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(user.email, subject, html_content, 'DEBIT_INVOICE')

    @staticmethod
    def trigger_subscription_expired_alert(user, subscription_name):
        if not user or not user.email:
            return
        subject = "ALERT: Subscription Expired - CubeLogs"
        from django.template.loader import render_to_string
        html_content = render_to_string(
            "emails/billing/subscription_expired.html",
            {
                "employee_name": user.first_name or 'User',
                "subscription_name": subscription_name,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(user.email, subject, html_content, 'SUBSCRIPTION_EXPIRED')

    @staticmethod
    def trigger_data_keeping_invoice(user, fee_amount):
        if not user or not user.email:
            return
        wallet = Wallet.objects.filter(employee=user).first()
        remaining_balance = f"₹{wallet.balance} INR" if wallet else "₹0.00 INR"
        
        subject = "Invoice: Monthly Data Keeping & Maintenance Fee - CubeLogs"
        tx_date = timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        from django.template.loader import render_to_string
        html_content = render_to_string(
            "emails/billing/data_keeping_fee.html",
            {
                "employee_name": user.first_name or 'User',
                "fee_amount": fee_amount,
                "remaining_balance": remaining_balance,
                "tx_date": tx_date,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(user.email, subject, html_content, 'DATA_KEEPING_FEE')


# ==============================================================================
# 2. CRM Service
# ==============================================================================

class CRMService:
    @staticmethod
    def provision_tenant_workspace(lead):
        email = lead.email
        message = lead.message or ''
        
        employee_limit = 50
        emp_match = re.search(r'Employees:\s*(\d+)', message)
        if emp_match:
            try:
                employee_limit = int(emp_match.group(1))
            except ValueError:
                pass

        msg_lower = message.lower()
        is_attendance = any(x in msg_lower for x in ['attendance', 'geofence', 'geofenced', 'biometric', 'scheduling', 'shift', 'enterprise'])
        is_project = any(x in msg_lower for x in ['project', 'tasks', 'task', 'enterprise'])

        features = ['dashboard']
        if is_attendance:
            features.extend([
                'attendance:staff', 'attendance:admin',
                'leaves:apply', 'leaves:approve', 'leaves:manage',
                'holidays:manage', 'holidays:view',
                'geofence', 'biometric', 'scheduling', 'multiLocation'
            ])
        if is_project:
            features.extend(['admin:templates', 'tasks:create', 'tasks:view'])

        package_name = f"Build-Your-Own Plan - {email}"
        price_inr = employee_limit * 100
        
        pkg, _ = SubscriptionPackage.objects.get_or_create(
            name=package_name,
            defaults={
                'price': price_inr,
                'employeeLimit': employee_limit,
                'features': features
            }
        )
        
        from django.utils.text import slugify
        org_name = f"{lead.name or 'Organization'} - {email}"
        subdomain = slugify(email.replace('@', '-').replace('.', '-'))
        
        org = Organization.objects.filter(subdomain=subdomain).first()
        if not org:
            settings_obj = OrgSettings.objects.create(
                max_employees_allowed=employee_limit,
                is_attendance_enabled=is_attendance,
                is_project_enabled=is_project,
                subscriptionDays=30,
                subscriptionStatus='Active'
            )
            org = Organization.objects.create(
                name=org_name,
                subdomain=subdomain,
                settings=settings_obj
            )
        else:
            settings_obj = org.settings
            if not settings_obj:
                settings_obj = OrgSettings.objects.create()
                org.settings = settings_obj
                org.save()
            settings_obj.max_employees_allowed = employee_limit
            settings_obj.is_attendance_enabled = is_attendance
            settings_obj.is_project_enabled = is_project
            settings_obj.subscriptionDays = 30
            settings_obj.subscriptionStatus = 'Active'
            settings_obj.brandLogo = None
            settings_obj.save()
            org.locations.all().delete()

        random_password = generate_secure_password(16)

        name_parts = lead.name.strip().split(' ') if lead.name else ['Admin']
        first_name = name_parts[0]
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else 'User'
        
        email_prefix = email.split('@')[0]
        generated_username = f"{email_prefix}_cb"

        employee = Employee.objects.filter(email=email).first() or Employee.objects.filter(username=generated_username).first()
        if employee:
            employee.email = email
            employee.username = generated_username
            employee.is_staff = False
            employee.is_superuser = False
            employee.isSuperAdmin = True
            employee.useDefaultPermissions = True
            employee.designation = 'Admin'
            employee.permissions = [p['id'] for p in PERMISSION_FLAGS]
            employee.set_password(random_password)
            employee._raw_password = random_password
            employee.organization = org
            employee.first_name = first_name
            employee.last_name = last_name
            employee.phone = lead.phone or employee.phone or ''
            employee.save()
        else:
            employee = Employee.objects.create_user(
                email=email,
                username=generated_username,
                password=random_password,
                first_name=first_name,
                last_name=last_name,
                phone=lead.phone or '',
                is_staff=False,
                is_superuser=False,
                isSuperAdmin=True,
                useDefaultPermissions=True,
                designation='Admin',
                permissions=[p['id'] for p in PERMISSION_FLAGS]
            )
            employee.organization = org
            employee.save()

        SubscriberAccount.objects.get_or_create(
            email=email,
            defaults={
                'packageName': package_name,
                'isActive': True,
                'expiresAt': timezone.now() + timezone.timedelta(days=30)
            }
        )

        Wallet.objects.get_or_create(
            employee=employee,
            defaults={
                'organization': org,
                'balance': 0.00
            }
        )

        if random_password:
            try:
                UserService.send_admin_onboarding_email(employee, random_password)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("Failed to send onboarding email to %s: %s", employee.email, e)
