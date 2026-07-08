"""
api/views/misc.py — Backoffice HTML views and Stripe webhook
"""
import json
import stripe
from decimal import Decimal
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import stripe.error

from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from api.models import (
    Employee, AuditLog, Organization, OrgSettings,
    Wallet, WalletTransaction, SubscriberAccount
)


# ─── Backoffice HTML views ─────────────────────────────────────────────────────

def backoffice_view(request):
    if not request.user.is_authenticated or not getattr(request.user, 'isSuperAdmin', False):
        return redirect('/backoffice/login/?next=/')

    user_perms = getattr(request.user, 'permissions', [])
    if not isinstance(user_perms, list):
        user_perms = []

    all_backoffice_perms = ['packages', 'subscribers', 'payments', 'leads', 'cms', 'faqs', 'testimonials', 'coupons', 'staff', 'audit_logs']
    has_any_backoffice_perm = any(p in user_perms for p in all_backoffice_perms)
    if not has_any_backoffice_perm or request.user.email == 'salmankcsiju@gmail.com' or request.user.organization is not None:
        user_perms = all_backoffice_perms

    context = {
        'user_permissions_json': json.dumps(user_perms)
    }
    return render(request, 'api/backoffice.html', context)


def backoffice_login_view(request):
    if request.user.is_authenticated and getattr(request.user, 'isSuperAdmin', False):
        return redirect('/')

    error = None
    if request.method == 'POST':
        email = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=email, password=password)
        if user is not None:
            if getattr(user, 'isSuperAdmin', False):
                login(request, user)

                AuditLog.objects.create(
                    employee=user,
                    employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
                    action="Logged In",
                    details="User logged in to Backoffice Console."
                )

                next_url = request.GET.get('next', '/')
                return redirect(next_url)
            else:
                error = "Access denied. Only system operators are authorized to access the Backoffice Console."
        else:
            error = "Invalid email or security password."

    return render(request, 'api/backoffice_login.html', {'error': error})


def backoffice_logout_view(request):
    logout(request)
    return redirect('/backoffice/login/')


# ─── Stripe Webhook ────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

    from django.conf import settings as dj_settings
    endpoint_secret = getattr(dj_settings, 'STRIPE_WEBHOOK_SECRET', None)
    if not endpoint_secret:
        import os
        endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

    import os
    from dotenv import load_dotenv
    dotenv_path = os.path.join(str(dj_settings.BASE_DIR), '.env')
    load_dotenv(dotenv_path, override=True)
    stripe.api_key = os.environ.get('STRIPE_SECRET_KEY') or getattr(dj_settings, 'STRIPE_SECRET_KEY', None)

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse(status=400)
    except Exception:
        return HttpResponse(status=400)

    event_type = event.get('type')
    data_object = event.get('data', {}).get('object', {})
    event_id = event.get('id')

    # Idempotency check
    if WalletTransaction.objects.filter(stripeEventId=event_id).exists():
        return HttpResponse(status=200)

    if event_type == 'checkout.session.completed':
        session_id = data_object.get('id')
        customer_id = data_object.get('customer')
        customer_email = data_object.get('customer_email') or data_object.get('customer_details', {}).get('email')
        mode = data_object.get('mode')
        metadata = data_object.get('metadata', {})

        if metadata.get('type') == 'dynamic_subscription':
            org_id = metadata.get('org_id')
            employee_count = int(metadata.get('employee_count', 10))
            addons_str = metadata.get('addons', '')
            addons = [a.strip() for a in addons_str.split(',') if a.strip()]
            total_cost = Decimal(metadata.get('total_cost', '0'))

            try:
                org = Organization.objects.get(id=org_id)
                org_settings = org.settings
                if not org_settings:
                    org_settings = OrgSettings.objects.create()
                    org.settings = org_settings
                    org.save()

                org_settings.max_employees_allowed = employee_count
                org_settings.is_attendance_enabled = 'attendance' in addons
                org_settings.is_project_enabled = 'project' in addons
                org_settings.subscriptionDays = 30
                org_settings.save()

                transaction = WalletTransaction.objects.filter(stripe_session_id=session_id, status='Pending').first()
                if transaction:
                    transaction.status = 'Success'
                    transaction.success = True
                    transaction.stripeEventId = event_id
                    transaction.details = f"Dynamic subscription activated: {employee_count} employees (Addons: {', '.join(addons)})"
                    transaction.receipt_url = data_object.get('hosted_invoice_url') or data_object.get('invoice_pdf')
                    transaction.save()
                else:
                    superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
                    if superadmin:
                        wallet, _ = Wallet.objects.get_or_create(employee=superadmin, defaults={'organization': org})
                        if customer_id and not wallet.stripe_customer_id:
                            wallet.stripe_customer_id = customer_id
                            wallet.save()
                        WalletTransaction.objects.create(
                            wallet=wallet,
                            amount=total_cost,
                            transactionType='Debit',
                            success=True,
                            stripeEventId=event_id,
                            stripe_session_id=session_id,
                            status='Success',
                            details=f"Dynamic subscription activated: {employee_count} employees (Addons: {', '.join(addons)})",
                            receipt_url=data_object.get('hosted_invoice_url') or data_object.get('invoice_pdf')
                        )
            except Organization.DoesNotExist:
                print(f"Organization ID {org_id} not found in dynamic subscription checkout webhook")

        elif mode == 'payment':
            # Prepaid top-up
            transaction = WalletTransaction.objects.filter(stripe_session_id=session_id, status='Pending').first()
            coupon_code = metadata.get('coupon_code')
            bonus_amount_str = metadata.get('bonus_amount', '0.00')
            bonus_amount = Decimal(bonus_amount_str)

            if transaction:
                wallet = transaction.wallet
                amount = transaction.amount

                transaction.status = 'Success'
                transaction.success = True
                transaction.stripeEventId = event_id
                transaction.details = "Wallet Top-up via Stripe"
                transaction.save()

                wallet.balance = Decimal(str(wallet.balance)) + amount + bonus_amount
                if customer_id and not wallet.stripe_customer_id:
                    wallet.stripe_customer_id = customer_id
                wallet.save()

                if coupon_code and bonus_amount > 0:
                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=bonus_amount,
                        transactionType='Credit',
                        success=True,
                        stripeEventId=event_id,
                        stripe_session_id=session_id,
                        status='Success',
                        details=f"Promotional Coupon Bonus - {coupon_code}"
                    )
            elif customer_email:
                employee = Employee.objects.filter(email=customer_email).first()
                if employee:
                    wallet, _ = Wallet.objects.get_or_create(employee=employee, defaults={'organization': employee.organization})
                    amount = Decimal(str(data_object.get('amount_total', 0))) / Decimal('100.0')
                    wallet.balance = Decimal(str(wallet.balance)) + amount + bonus_amount
                    if customer_id and not wallet.stripe_customer_id:
                        wallet.stripe_customer_id = customer_id
                    wallet.save()

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=amount,
                        transactionType='Credit',
                        success=True,
                        stripeEventId=event_id,
                        stripe_session_id=session_id,
                        status='Success',
                        details="Wallet Top-up via Stripe"
                    )

                    if coupon_code and bonus_amount > 0:
                        WalletTransaction.objects.create(
                            wallet=wallet,
                            amount=bonus_amount,
                            transactionType='Credit',
                            success=True,
                            stripeEventId=event_id,
                            stripe_session_id=session_id,
                            status='Success',
                            details=f"Promotional Coupon Bonus - {coupon_code}"
                        )
        elif mode == 'subscription':
            if customer_email:
                superadmin = Employee.objects.filter(email=customer_email, isSuperAdmin=True).first()
                if superadmin:
                    org = superadmin.organization
                    wallet, _ = Wallet.objects.get_or_create(employee=superadmin, defaults={'organization': org})
                    if customer_id:
                        wallet.stripe_customer_id = customer_id
                        wallet.save()

                    sub, _ = SubscriberAccount.objects.get_or_create(
                        email=customer_email,
                        defaults={'packageName': 'Professional', 'isActive': True}
                    )
                    sub.isActive = True
                    sub.expiresAt = timezone.now() + timezone.timedelta(days=30)

                    pkg_name = data_object.get('metadata', {}).get('package_name')
                    if pkg_name:
                        sub.packageName = pkg_name
                    sub.save()

                    if org:
                        if not org.settings:
                            org.settings = OrgSettings.objects.create()
                            org.save()
                        org.settings.subscriptionDays = 30
                        org.settings.save()

    elif event_type == 'customer.subscription.updated':
        customer_id = data_object.get('customer')
        wallet = Wallet.objects.filter(stripe_customer_id=customer_id).first()
        org = None
        superadmin_email = None

        if wallet:
            org = wallet.organization
            superadmin = Employee.objects.filter(organization=org, isSuperAdmin=True).first()
            if superadmin:
                superadmin_email = superadmin.email

        if not superadmin_email and customer_id:
            try:
                cust = stripe.Customer.retrieve(customer_id)
                email = cust.get('email')
                if email:
                    superadmin = Employee.objects.filter(email=email, isSuperAdmin=True).first()
                    if superadmin:
                        superadmin_email = email
                        org = superadmin.organization
                        wallet, _ = Wallet.objects.get_or_create(employee=superadmin, defaults={'organization': org})
                        wallet.stripe_customer_id = customer_id
                        wallet.save()
            except Exception as e:
                print(f"Stripe customer retrieve failed: {e}")

        if superadmin_email:
            sub, _ = SubscriberAccount.objects.get_or_create(
                email=superadmin_email,
                defaults={'packageName': 'Professional', 'isActive': True}
            )
            sub.isActive = True
            sub.expiresAt = timezone.now() + timezone.timedelta(days=30)
            sub.save()

            if org:
                if not org.settings:
                    org.settings = OrgSettings.objects.create()
                    org.save()
                org.settings.subscriptionDays = 30
                org.settings.save()

    elif event_type == 'invoice.paid':
        email = data_object.get('customer_email')
        amount = Decimal(str(data_object.get('amount_paid', 0))) / Decimal('100.0')

        if email:
            employee = Employee.objects.filter(email=email).first()
            if employee:
                wallet, _ = Wallet.objects.get_or_create(employee=employee, defaults={'organization': employee.organization})
                wallet.balance = Decimal(str(wallet.balance)) - amount
                wallet.save()

                hosted_invoice_url = data_object.get('hosted_invoice_url')
                invoice_pdf = data_object.get('invoice_pdf')
                receipt_url = hosted_invoice_url or invoice_pdf

                WalletTransaction.objects.create(
                    wallet=wallet,
                    amount=amount,
                    transactionType='Debit',
                    success=True,
                    stripeEventId=event_id,
                    status='Success',
                    details=f"Subscription renewal paid: Invoice {data_object.get('id')}",
                    receipt_url=receipt_url
                )

    elif event_type == 'invoice.payment_failed':
        email = data_object.get('customer_email')
        amount = Decimal(str(data_object.get('amount_due', 0))) / Decimal('100.0')

        if email:
            employee = Employee.objects.filter(email=email).first()
            if employee:
                wallet, _ = Wallet.objects.get_or_create(employee=employee, defaults={'organization': employee.organization})

                hosted_invoice_url = data_object.get('hosted_invoice_url')
                invoice_pdf = data_object.get('invoice_pdf')
                receipt_url = hosted_invoice_url or invoice_pdf

                WalletTransaction.objects.create(
                    wallet=wallet,
                    amount=amount,
                    transactionType='Debit',
                    success=False,
                    stripeEventId=event_id,
                    status='Failed',
                    details=f"Subscription renewal payment failed: Invoice {data_object.get('id')}",
                    receipt_url=receipt_url
                )

    elif event_type == 'payment_intent.succeeded':
        email = data_object.get('receipt_email')
        if not email:
            metadata = data_object.get('metadata', {})
            email = metadata.get('email') or metadata.get('customer_email')
        if not email:
            charges = data_object.get('charges', {}).get('data', [])
            if charges:
                email = charges[0].get('billing_details', {}).get('email')

        amount = Decimal(str(data_object.get('amount_received', 0))) / Decimal('100.0')

        if email:
            employee = Employee.objects.filter(email=email).first()
            if employee:
                wallet, _ = Wallet.objects.get_or_create(employee=employee, defaults={'organization': employee.organization})
                wallet.balance = Decimal(str(wallet.balance)) + amount
                wallet.save()

                charges = data_object.get('charges', {}).get('data', [])
                receipt_url = charges[0].get('receipt_url') if charges else None

                WalletTransaction.objects.create(
                    wallet=wallet,
                    amount=amount,
                    transactionType='Credit',
                    success=True,
                    stripeEventId=event_id,
                    status='Success',
                    details=f"Direct wallet top-up succeeded: PaymentIntent {data_object.get('id')}",
                    receipt_url=receipt_url
                )

    return HttpResponse(status=200)
