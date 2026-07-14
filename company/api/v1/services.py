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
from subscribers.models import SubscriptionPackage, SubscriberAccount, Wallet, MonthlyInvoice, WalletTransaction

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
    def process_outstanding_dues(wallet):
        if getattr(wallet, '_processing_dues', False):
            return
        
        try:
            wallet._processing_dues = True
            
            unpaid_invoices = list(
                MonthlyInvoice.objects.filter(
                    organization=wallet.organization, is_paid=False
                ).order_by('billing_month')
            )
            total_due = sum((inv.amount for inv in unpaid_invoices), Decimal('0'))

            if total_due > 0 and wallet.balance >= total_due:
                # Update wallet balance directly
                wallet.balance = wallet.balance - total_due
                # Direct save to prevent infinite cycles
                super(wallet.__class__, wallet).save(update_fields=['balance'])

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
                    details="Automated wallet deduction: Outstanding dues cleared on top-up.",
                )

                if wallet.organization and wallet.organization.settings:
                    settings_obj = wallet.organization.settings
                    settings_obj.subscriptionStatus = 'Active'
                    settings_obj.save()

                superadmin = Employee.objects.filter(
                    organization=wallet.organization, isSuperAdmin=True
                ).first()
                if superadmin:
                    subject = f"Notice: Dues Paid & Workspace Activated for {wallet.organization.name}"
                    message = (
                        f"Hi {superadmin.first_name or 'Superadmin'},\n\n"
                        f"Thank you! Your outstanding dues of ₹{total_due} INR have been successfully paid after your recent top-up.\n"
                        f"Your workspace is fully active.\n"
                        f"Updated Wallet Balance: ₹{wallet.balance} INR.\n\n"
                        f"CubeLogs Billing Team"
                    )
                    try:
                        EmailService.queue_and_send_email(superadmin.email, subject, message)
                    except Exception:
                        pass
        finally:
            wallet._processing_dues = False

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
