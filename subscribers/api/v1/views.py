import os
import sys
import re
import json
import razorpay
from decimal import Decimal
from datetime import timedelta

import logging
logger = logging.getLogger(__name__)

def get_razorpay_client():
    key_id = os.environ.get('RAZORPAY_KEY_ID') or getattr(dj_settings, 'RAZORPAY_KEY_ID', None)
    key_secret = os.environ.get('RAZORPAY_KEY_SECRET') or getattr(dj_settings, 'RAZORPAY_KEY_SECRET', None)
    if not key_id or not key_secret or key_id.startswith('rzp_test_mock_'):
        return None, key_id, key_secret
    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        return client, key_id, key_secret
    except Exception:
        return None, key_id, key_secret

from django.utils import timezone
from django.conf import settings as dj_settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from rest_framework import viewsets, status, permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from core.permissions import HasRequiredPermission, IsSuperAdminUser, IsPlatformBillingAdmin
from core.mixins import FilterMixinNew
from subscribers.models import (
    SubscriptionPackage, SubscriberAccount, Wallet, WalletTransaction,
    MonthlyInvoice, Coupon, BackofficeCoupon, GlobalBillingSettings, default_coupon_code
)
from subscribers.filters import SubscriptionPackageFilter, SubscriberAccountFilter, CouponFilter, BackofficeCouponFilter
from subscribers.api.v1.serializers import (
    SubscriptionPackageSerializer, SubscriberAccountSerializer,
    WalletSerializer, WalletTransactionSerializer, CouponSerializer, BackofficeCouponSerializer,
    GlobalBillingSettingsSerializer
)

from users.models import Employee, PERMISSION_FLAGS
from core.models import Organization, OrgSettings
from core.pagination import StandardResultsSetPagination
from company.models import Lead
from users.api.v1.serializers import EmployeeSerializer
from storage_billing.models import StorageEvent
from storage_billing.services import StorageCalculationService
from django.db.models import Sum, Count, Q, Value, OuterRef, Subquery, DateTimeField
from django.db.models.functions import Coalesce


# --------------------------------------------------------------------------------
# SubscriptionPackageViewSet: ViewSet managing subscription pricing packages.
# --------------------------------------------------------------------------------
class SubscriptionPackageViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = SubscriptionPackage.objects.all().order_by('-created_at')
    serializer_class = SubscriptionPackageSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = SubscriptionPackageFilter

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsSuperAdminUser()]

    def create(self, request, *args, **kwargs):
        name = request.data.get('name')
        if name:
            instance = SubscriptionPackage.objects.filter(name=name).first()
            if instance:
                serializer = self.get_serializer(instance, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
                return Response(serializer.data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def perform_update(self, serializer):
        package = serializer.save()
        from subscribers.tasks import cascade_package_update_task, dispatch_task_safely
        dispatch_task_safely(cascade_package_update_task, package_id=package.id)

    def perform_create(self, serializer):
        package = serializer.save()
        from subscribers.tasks import cascade_package_update_task, dispatch_task_safely
        dispatch_task_safely(cascade_package_update_task, package_id=package.id)


# --------------------------------------------------------------------------------
# SubscriberAccountViewSet: ViewSet managing active subscriber account details and limits.
# --------------------------------------------------------------------------------
class SubscriberAccountViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = SubscriberAccount.objects.all().order_by('-updated_at')
    serializer_class = SubscriberAccountSerializer
    permission_classes = [IsSuperAdminUser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SubscriberAccountFilter

    def get_queryset(self):
        # Sync subscriber accounts for all active tenant superadmins
        superadmins = Employee.objects.filter(
            isSuperAdmin=True,
            is_active=True,
            organization__isnull=False,
            organization__is_deleted=False
        ).select_related('organization', 'organization__settings')
        valid_emails = set()
        for sa in superadmins:
            valid_emails.add(sa.email)
            settings = getattr(sa.organization, 'settings', None)
            modules = []
            if settings:
                if settings.is_attendance_enabled:
                    modules.append('Attendance Management')
                if settings.is_project_enabled:
                    modules.append('Project & Tasks Management')
            pkg_name = ', '.join(modules) if modules else 'Core Package'
            is_active = (settings.subscriptionStatus == 'Active') if settings else True
            expires_at = settings.subscriptionExpiresAt if settings else None

            sub = SubscriberAccount.objects.filter(email=sa.email).first()
            if not sub:
                SubscriberAccount.objects.create(
                    email=sa.email,
                    packageName=pkg_name,
                    isActive=is_active,
                    expiresAt=expires_at
                )
            else:
                if settings and (sub.isActive != is_active or sub.expiresAt != expires_at or sub.packageName != pkg_name or sub.is_deleted):
                    sub.packageName = pkg_name
                    sub.isActive = is_active
                    sub.expiresAt = expires_at
                    sub.is_deleted = False
                    sub.save(update_fields=['packageName', 'isActive', 'expiresAt', 'is_deleted', 'updated_at'])

        # Prune stale/orphaned subscriber accounts that are no longer active tenant superadmins
        if valid_emails:
            SubscriberAccount.objects.exclude(email__in=valid_emails).delete()

        return SubscriberAccount.objects.all().order_by('-updated_at')

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        email = instance.email

        # Deactivate associated tenant organizations and their memberships/superadmins
        superadmins = Employee.objects.filter(email=email)
        for sa in superadmins:
            if sa.organization:
                org = sa.organization
                org.is_deleted = True
                org.save(update_fields=['is_deleted', 'updated_at'])
                if hasattr(org, 'settings') and org.settings:
                    org.settings.subscriptionStatus = 'Cancelled'
                    org.settings.save(update_fields=['subscriptionStatus', 'updated_at'])
                org.memberships.update(is_active_in_org=False, is_deleted=True, employment_status='Deactivated')
            if not request.user or sa.id != request.user.id:
                sa.is_active = False
                sa.save(update_fields=['is_active'])

        # Hard delete the subscriber account record
        instance.hard_delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------
# DynamicCheckoutView: API view generating Razorpay payment checkout orders dynamically.
# --------------------------------------------------------------------------------
class DynamicCheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'settings:billing'

    def post(self, request):
        employee_count = request.data.get('employee_count')
        addons = request.data.get('addons', [])

        if employee_count is None:
            return Response({'error': 'Employee count is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            employee_count = int(employee_count)
            if employee_count <= 0:
                return Response({'error': 'Employee count must be greater than zero'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Invalid employee count value'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.organization:
            org, _ = Organization.objects.get_or_create(
                subdomain="mock",
                defaults={'name': 'Mock Organization'}
            )
            user.organization = org
            user.save()
        org = user.organization

        g_settings, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        rate = 0
        if 'attendance' in addons:
            rate += g_settings.attendance_module_price
        if 'project' in addons:
            rate += g_settings.tasks_module_price
        total_cost = employee_count * int(rate)

        settings = org.settings
        if not settings:
            settings = OrgSettings.objects.create()
            org.settings = settings
            org.save()

        if total_cost == 0:
            settings.max_employees_allowed = employee_count
            settings.is_attendance_enabled = False
            settings.is_project_enabled = False
            settings.subscriptionDays = 30
            settings.subscriptionStatus = 'Active'
            settings.subscriptionExpiresAt = timezone.now() + timedelta(minutes=10)
            settings.save()

            wallet, _ = Wallet.objects.get_or_create(employee=user, defaults={'organization': org})
            WalletTransaction.objects.create(
                wallet=wallet,
                amount=Decimal('0.00'),
                transactionType='Debit',
                success=True,
                status='Success',
                details=f"Core plan activated with {employee_count} employees (0 paid addons)"
            )
            return Response({'status': 'subscription_success', 'message': 'Core plan activated successfully!'}, status=status.HTTP_200_OK)

        client, key_id, _ = get_razorpay_client()
        allow_mock = getattr(dj_settings, 'ALLOW_MOCK_PAYMENTS', False) or getattr(dj_settings, 'DEBUG', False)
        import uuid

        if not client:
            if not allow_mock:
                return Response({'error': 'Razorpay payment gateway is not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            mock_order_id = f"mock_rzp_order_{uuid.uuid4().hex}"
            wallet, _ = Wallet.objects.get_or_create(employee=user, defaults={'organization': org})
            WalletTransaction.objects.create(
                wallet=wallet,
                amount=Decimal(str(total_cost)),
                transactionType='Debit',
                success=False,
                razorpay_order_id=mock_order_id,
                status='Pending',
                details=f"Pending dynamic subscription activation: {employee_count} employees (Addons: {', '.join(addons)})"
            )

            return Response({
                'gateway': 'razorpay',
                'key_id': key_id or 'rzp_test_mock',
                'order_id': mock_order_id,
                'amount': int(total_cost * 100),
                'currency': 'INR',
                'name': 'CubeLogs',
                'description': f"Dynamic Subscription Plan ({employee_count} employees)",
                'payment_type': 'subscription',
                'is_mock': True
            }, status=status.HTTP_200_OK)

        try:
            order_data = {
                'amount': int(total_cost * 100),
                'currency': 'INR',
                'receipt': f"rcpt_sub_{uuid.uuid4().hex[:12]}",
                'notes': {
                    'payment_type': 'subscription',
                    'org_id': str(org.id),
                    'user_id': str(user.id),
                    'employee_count': str(employee_count),
                    'addons': ','.join(addons),
                    'total_cost': str(total_cost)
                }
            }
            order = client.order.create(data=order_data)

            wallet, _ = Wallet.objects.get_or_create(employee=user, defaults={'organization': org})
            WalletTransaction.objects.create(
                wallet=wallet,
                amount=Decimal(str(total_cost)),
                transactionType='Debit',
                success=False,
                razorpay_order_id=order['id'],
                status='Pending',
                details=f"Pending dynamic subscription activation: {employee_count} employees (Addons: {', '.join(addons)})"
            )

            return Response({
                'gateway': 'razorpay',
                'key_id': key_id,
                'order_id': order['id'],
                'amount': order['amount'],
                'currency': 'INR',
                'name': 'CubeLogs',
                'description': f"Dynamic Subscription Plan ({employee_count} employees)",
                'payment_type': 'subscription'
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': f"Failed to initiate Razorpay checkout: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        return None

# --------------------------------------------------------------------------------
# VerifyPaymentView: Generic API view verifying Razorpay payment signatures & completing billing transactions.
# --------------------------------------------------------------------------------
class VerifyPaymentView(APIView):
    authentication_classes = [JWTAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        import uuid
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_signature = request.data.get('razorpay_signature')
        payment_type = request.data.get('payment_type', 'wallet')

        if not razorpay_order_id:
            return Response({'error': 'Razorpay order ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        allow_mock = getattr(dj_settings, 'ALLOW_MOCK_PAYMENTS', False) or getattr(dj_settings, 'DEBUG', False) or getattr(dj_settings, 'TEST_MODE', False) or ('test' in sys.argv)
        is_mock_order = razorpay_order_id.startswith('mock_')

        active_org = getattr(request, 'active_organization', None)
        if not active_org:
            return Response({'error': 'Active organization context required for payment verification.'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        from users.models import OrganizationMembership
        active_mem = getattr(request, 'active_membership', None)
        if not active_mem and user.is_authenticated:
            active_mem = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).first()

        has_org_match = (active_mem is not None) or (getattr(user, 'organization_id', None) == getattr(active_org, 'id', None))
        if not has_org_match and not (getattr(user, 'isSuperAdmin', False) and getattr(user, 'organization', None) is None):
            return Response({'error': 'Active membership in organization required for payment verification.'}, status=status.HTTP_403_FORBIDDEN)

        # Enforce PAYMENT ORGANIZATION == ACTIVE ORGANIZATION
        tx_check = WalletTransaction.objects.filter(razorpay_order_id=razorpay_order_id).select_related('wallet__organization').first()
        if tx_check and tx_check.wallet and tx_check.wallet.organization_id:
            if tx_check.wallet.organization_id != active_org.id:
                return Response({'error': 'Payment order does not belong to active organization.'}, status=status.HTTP_403_FORBIDDEN)

        if is_mock_order:
            if not allow_mock:
                return Response({'error': 'Mock payment sessions are disabled in production'}, status=status.HTTP_400_BAD_REQUEST)

            mock_status = request.data.get('payment_status') or 'captured'
            if mock_status == 'authorized':
                return Response({
                    'status': 'payment_authorized',
                    'message': 'Payment is authorized and pending capture. Wallet will be credited once capture is confirmed.'
                }, status=status.HTTP_202_ACCEPTED)
            if mock_status not in ['captured']:
                return Response({'error': f"Payment status invalid: {mock_status}"}, status=status.HTTP_400_BAD_REQUEST)

            if request.data.get('currency') and request.data.get('currency') != 'INR':
                return Response({'error': 'Currency mismatch. Expected INR.'}, status=status.HTTP_400_BAD_REQUEST)

            tx_check_amt = WalletTransaction.objects.filter(razorpay_order_id=razorpay_order_id).first()
            if request.data.get('amount') and tx_check_amt:
                req_paise = int(Decimal(str(request.data.get('amount'))) * Decimal('100.0'))
                expected_paise = int(Decimal(str(tx_check_amt.amount)) * Decimal('100.0'))
                if req_paise != expected_paise:
                    return Response({'error': 'Payment amount does not match stored transaction amount.'}, status=status.HTTP_400_BAD_REQUEST)

            if payment_type == 'wallet' or 'wallet' in razorpay_order_id or 'topup' in razorpay_order_id:
                wallet = Wallet.objects.filter(organization=active_org).first()
                if not wallet:
                    wallet = Wallet.objects.create(employee=request.user, organization=active_org, balance=Decimal('0.00'))

                tx = WalletTransaction.objects.filter(razorpay_order_id=razorpay_order_id).first()
                deposit_amount = tx.amount if tx else Decimal('1000.00')

                from company.api.v1.services import BillingService
                BillingService.credit_wallet_from_payment(
                    wallet_id=wallet.id,
                    amount_dec=deposit_amount,
                    razorpay_order_id=razorpay_order_id,
                    razorpay_payment_id=razorpay_payment_id or f"mock_pay_{uuid.uuid4().hex[:10]}",
                    razorpay_signature=razorpay_signature or "mock_signature",
                    details=tx.details if tx else "Mock Prepaid Wallet Deposit",
                    target_org_id=active_org.id
                )
                return Response({'status': 'wallet_success', 'message': 'Mock Wallet top-up confirmed!'}, status=status.HTTP_200_OK)
            else:
                org = active_org
                if org and org.settings:
                    org.settings.max_employees_allowed = 50
                    org.settings.is_attendance_enabled = True
                    org.settings.is_project_enabled = True
                    org.settings.subscriptionDays = 30
                    org.settings.subscriptionStatus = 'Active'
                    org.settings.save()

                tx = WalletTransaction.objects.filter(razorpay_order_id=razorpay_order_id).first()
                if tx:
                    tx.status = 'Success'
                    tx.success = True
                    tx.razorpay_payment_id = razorpay_payment_id or "mock_payment"
                    tx.razorpay_signature = razorpay_signature or "mock_signature"
                    tx.save()

                return Response({'status': 'subscription_success', 'message': 'Mock Subscription confirmed successfully!'}, status=status.HTTP_200_OK)

        if not razorpay_payment_id or not razorpay_signature:
            return Response({'error': 'Razorpay payment ID and signature are required'}, status=status.HTTP_400_BAD_REQUEST)

        client, key_id, key_secret = get_razorpay_client()
        if not client:
            return Response({'error': 'Razorpay payment gateway is not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })
        except Exception as se:
            return Response({'error': f'Payment signature verification failed: {str(se)}'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payment_info = client.payment.fetch(razorpay_payment_id)
            if payment_info.get('order_id') != razorpay_order_id:
                return Response({'error': 'Order ID mismatch'}, status=status.HTTP_400_BAD_REQUEST)
            if payment_info.get('currency') != 'INR':
                return Response({'error': 'Currency mismatch'}, status=status.HTTP_400_BAD_REQUEST)

            pay_status = payment_info.get('status')
            if pay_status != 'captured':
                if pay_status == 'authorized':
                    logger.info("Payment %s is authorized but not yet captured. Waiting for capture.", razorpay_payment_id)
                    return Response({
                        'status': 'payment_authorized',
                        'message': 'Payment is authorized and pending capture. Wallet will be credited automatically once capture is confirmed.'
                    }, status=status.HTTP_202_ACCEPTED)
                return Response({'error': f"Payment status invalid: {pay_status}"}, status=status.HTTP_400_BAD_REQUEST)

            order_info = client.order.fetch(razorpay_order_id)
            if order_info.get('currency') != 'INR':
                return Response({'error': 'Order currency mismatch'}, status=status.HTTP_400_BAD_REQUEST)

            pay_amount = int(payment_info.get('amount') or 0)
            order_amount = int(order_info.get('amount') or 0)
            if pay_amount != order_amount:
                logger.warning("AMOUNT_MISMATCH: payment %s != order %s", pay_amount, order_amount)
                return Response({'error': 'Payment amount does not match order amount.'}, status=status.HTTP_400_BAD_REQUEST)

            tx_pending = WalletTransaction.objects.filter(razorpay_order_id=razorpay_order_id).first()
            if tx_pending:
                expected_paise = int((Decimal(str(tx_pending.amount)) * Decimal('100.0')).to_integral_value())
                if pay_amount != expected_paise:
                    logger.warning("AMOUNT_MISMATCH: payment %s != expected %s", pay_amount, expected_paise)
                    return Response({'error': 'Payment amount does not match stored transaction amount.'}, status=status.HTTP_400_BAD_REQUEST)

            order_notes = payment_info.get('notes', {}) or order_info.get('notes', {}) or {}
            order_org_id = order_notes.get('org_id')
            if order_org_id and str(order_org_id) != str(active_org.id):
                return Response({'error': 'Payment order does not belong to active organization.'}, status=status.HTTP_403_FORBIDDEN)
        except Exception as pe:
            return Response({'error': f'Failed to fetch payment details: {str(pe)}'}, status=status.HTTP_400_BAD_REQUEST)

        if payment_type == 'wallet' or (tx_pending and tx_pending.transactionType == 'Credit'):
            wallet = Wallet.objects.filter(organization=active_org).first()
            if not wallet:
                wallet = Wallet.objects.create(employee=request.user, organization=active_org, balance=Decimal('0.00'))

            amount_dec = tx_pending.amount if tx_pending else (Decimal(str(payment_info.get('amount', 0))) / Decimal('100.0'))

            from company.api.v1.services import BillingService
            BillingService.credit_wallet_from_payment(
                wallet_id=wallet.id,
                amount_dec=amount_dec,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
                details=f"Prepaid Wallet Deposit (Razorpay Order {razorpay_order_id[:12]})",
                target_org_id=active_org.id
            )
            return Response({'status': 'wallet_success', 'message': 'Wallet top-up confirmed!'}, status=status.HTTP_200_OK)

        elif payment_type == 'subscription' or (tx_pending and tx_pending.transactionType == 'Debit'):
            org = active_org
            if org and org.settings:
                notes = payment_info.get('notes', {}) or {}
                emp_count = int(notes.get('employee_count') or 10)
                addons_str = notes.get('addons') or ''
                addons = [a.strip() for a in addons_str.split(',') if a.strip()]

                org.settings.max_employees_allowed = emp_count
                org.settings.is_attendance_enabled = 'attendance' in addons
                org.settings.is_project_enabled = 'project' in addons
                org.settings.subscriptionDays = 30
                org.settings.subscriptionStatus = 'Active'
                org.settings.save()

            if tx_pending:
                tx_pending.status = 'Success'
                tx_pending.success = True
                tx_pending.razorpay_payment_id = razorpay_payment_id
                tx_pending.razorpay_signature = razorpay_signature
                tx_pending.save()

            return Response({'status': 'subscription_success', 'message': 'Subscription confirmed successfully!'}, status=status.HTTP_200_OK)

        return Response({'error': 'Unknown payment type'}, status=status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------------
# ConfirmSubscriptionView: Backward compatibility view delegating to VerifyPaymentView logic.
# --------------------------------------------------------------------------------
class ConfirmSubscriptionView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'settings:billing'

    def post(self, request):
        session_id = request.data.get('session_id') or request.data.get('razorpay_order_id')
        if not session_id:
            return Response({'error': 'Session or Order ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        request.data['razorpay_order_id'] = session_id
        verify_view = VerifyPaymentView()
        return verify_view.post(request)


# --------------------------------------------------------------------------------
# BackofficeRegisterCompanyView: View enabling direct registration of a new company with a subscription package.
# --------------------------------------------------------------------------------
class BackofficeRegisterCompanyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if not getattr(user, 'isSuperAdmin', False):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        company_name = request.data.get('companyName')
        admin_full_name = request.data.get('adminFullName', '')
        admin_email = request.data.get('adminEmail')
        admin_phone = request.data.get('adminPhone', '')
        package_name = request.data.get('packageName', 'Starter')

        if not admin_email:
            return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

        if not company_name:
            company_name = admin_email.split('@')[-1].split('.')[0].capitalize()
            if not company_name:
                company_name = "Tenant"

        subdomain = re.sub(r'[^a-zA-Z0-9]', '', company_name).lower()
        if not subdomain:
            subdomain = 'workspace' + str(Organization.objects.count())

        if Organization.objects.filter(subdomain=subdomain).exists():
            subdomain = subdomain + str(Organization.objects.count())

        if Employee.objects.filter(email=admin_email).exists():
            return Response({'error': 'Admin email already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        name_parts = admin_full_name.split(' ', 1)
        admin_first_name = name_parts[0]
        admin_last_name = name_parts[1] if len(name_parts) > 1 else ''
        from core.utils import generate_secure_password
        admin_password = generate_secure_password(14)

        try:
            org = Organization.objects.create(name=company_name, subdomain=subdomain)
            settings = OrgSettings.objects.create()

            pkg_lower = package_name.lower()

            # Check if this package maps to a Lead to preserve custom modules
            lead = Lead.objects.filter(email=admin_email).first()
            if lead and lead.message:
                msg_lower = lead.message.lower()
                if any(x in msg_lower for x in ['attendance', 'geofence', 'geofenced', 'biometric', 'scheduling', 'shift']):
                    settings.is_attendance_enabled = True
                if any(x in msg_lower for x in ['project', 'tasks', 'task']):
                    settings.is_project_enabled = True
            else:
                if 'attendance' in pkg_lower:
                    settings.is_attendance_enabled = True
                if 'project' in pkg_lower:
                    settings.is_project_enabled = True

            settings.subscriptionStatus = 'Active'
            settings.subscriptionExpiresAt = timezone.now() + timedelta(minutes=10)
            settings.subscriptionDays = 0

            settings.save()
            org.settings = settings
            org.save()

            admin_user = Employee.objects.create_user(
                email=admin_email,
                username=admin_email,
                password=admin_password,
                first_name=admin_first_name,
                last_name=admin_last_name,
                phone=admin_phone,
                organization=org,
                is_active=True,
                is_staff=False,
                is_superuser=False,
                isSuperAdmin=True,
                useDefaultPermissions=True,
                designation='Admin'
            )

            base_perms = ['dashboard', 'admin:templates', 'admin:employees', 'locations:manage', 'settings:branding', 'settings:billing']
            if settings.is_attendance_enabled:
                if 'attendance:view' not in base_perms:
                    base_perms.append('attendance:view')
                base_perms.extend([p['id'] for p in PERMISSION_FLAGS if p['id'].startswith('attendance') or p['id'].startswith('leaves') or p['id'].startswith('holidays')])
            if settings.is_project_enabled:
                if 'projects:view' not in base_perms:
                    base_perms.append('projects:view')
                base_perms.extend([p['id'] for p in PERMISSION_FLAGS if p['id'].startswith('projects') or p['id'].startswith('project_')])

            admin_user.permissions = base_perms
            admin_user.save()

            SubscriberAccount.objects.update_or_create(
                email=admin_email,
                defaults={
                    'packageName': package_name,
                    'isActive': True
                }
            )

            Wallet.objects.create(
                employee=admin_user,
                organization=org,
                balance=Decimal('0.00')
            )

            # Send welcome email with token
            from django.core.signing import TimestampSigner
            from django.template.loader import render_to_string

            signer = TimestampSigner(salt='auto-login')
            token = signer.sign(str(admin_user.id))

            frontend_url = dj_settings.FRONTEND_URL
            login_link = f"{frontend_url}/login/verify?token={token}"

            subject = 'Welcome to CubeLogs - Your Workspace is Ready!'
            message = f"""Hello {admin_first_name},

Your CubeLogs workspace for '{company_name}' has been successfully registered and provisioned!

You can log in to your dashboard directly using the link below:
{login_link}
Email: {admin_email}
Password: {admin_password}

Welcome aboard!
The CubeLogs Team
"""

            html_message = render_to_string(
                "emails/company/workspace_created.html",
                {
                    "admin_first_name": admin_first_name,
                    "company_name": company_name,
                    "login_link": login_link,
                    "admin_email": admin_email,
                    "admin_password": admin_password,
                    "product_support_email": dj_settings.SUPPORT_EMAIL,
                    "product_website": dj_settings.COMPANY_WEBSITE,
                    "product_company_name": dj_settings.COMPANY_NAME,
                }
            )
            try:
                from core.tasks import EmailService
                EmailService.send_transactional_email(
                    recipient=admin_email,
                    subject=subject,
                    html_content=html_message,
                    template_type='WELCOME',
                    password=admin_password
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to queue registration welcome email: {e}")

            from users.api.v1.serializers import EmployeeSerializer
            serializer = EmployeeSerializer(admin_user)
            user_data = serializer.data
            if admin_user.organization and hasattr(admin_user.organization, 'settings') and admin_user.organization.settings:
                org_settings = admin_user.organization.settings
                user_data['is_attendance_enabled'] = org_settings.is_attendance_enabled
                user_data['is_project_enabled'] = org_settings.is_project_enabled

                if 'subscription' in user_data and isinstance(user_data['subscription'], dict):
                    user_data['subscription']['is_attendance_enabled'] = org_settings.is_attendance_enabled
                    user_data['subscription']['is_project_enabled'] = org_settings.is_project_enabled

                if isinstance(user_data.get('permissions'), list):
                    if user_data['is_attendance_enabled'] and 'attendance:view' not in user_data['permissions']:
                        user_data['permissions'].append('attendance:view')
                    if user_data['is_project_enabled'] and 'projects:view' not in user_data['permissions']:
                        user_data['permissions'].append('projects:view')

            return Response({'message': 'Company successfully registered.', 'user': user_data}, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    raw_body = request.body
    payload = raw_body.decode('utf-8')
    sig_header = request.META.get('HTTP_X_RAZORPAY_SIGNATURE')

    webhook_secret = getattr(dj_settings, 'RAZORPAY_WEBHOOK_SECRET', None) or os.environ.get('RAZORPAY_WEBHOOK_SECRET')
    client, key_id, key_secret = get_razorpay_client()

    # Reject missing signature header immediately
    if not sig_header:
        logger.warning("Razorpay webhook rejected: missing HTTP_X_RAZORPAY_SIGNATURE")
        return HttpResponse("Missing signature", status=400)

    if not webhook_secret or not client:
        logger.error("Razorpay webhook secret or client not configured")
        return HttpResponse("Webhook unconfigured", status=500)

    try:
        client.utility.verify_webhook_signature(payload, sig_header, webhook_secret)
    except Exception as exc:
        logger.warning("Razorpay webhook signature verification failed: %s", exc)
        return HttpResponse("Invalid signature", status=400)

    try:
        data = json.loads(payload)
    except Exception:
        return HttpResponse("Invalid payload", status=400)

    event_type = data.get('event')
    event_id = str(data.get('id') or data.get('account_id') or data.get('created_at') or '')

    # Authorized-only event must not credit wallet
    if event_type == 'payment.authorized':
        logger.info("Webhook received payment.authorized. Payment is not yet captured; no wallet credit.")
        return HttpResponse("Payment authorized, pending capture", status=200)

    if event_type not in ['payment.captured', 'order.paid']:
        return HttpResponse(status=200)

    if event_id and WalletTransaction.objects.filter(gateway_event_id=event_id, success=True).exists():
        return HttpResponse("Event already processed", status=200)

    payload_payment = data.get('payload', {}).get('payment', {}).get('entity', {})
    payload_order = data.get('payload', {}).get('order', {}).get('entity', {})
    payload_entity = payload_payment or payload_order

    # For payment.captured, verify status is explicitly 'captured'
    if payload_payment and payload_payment.get('status') != 'captured':
        logger.warning("Webhook received payment entity with non-captured status: %s", payload_payment.get('status'))
        return HttpResponse("Non-captured payment", status=200)

    order_id = payload_entity.get('order_id') or (payload_order.get('id') if payload_order else None)
    payment_id = payload_payment.get('id') if payload_payment else None
    notes = payload_payment.get('notes', {}) or payload_order.get('notes', {}) or {}
    payment_type = notes.get('payment_type')

    # Currency validation
    currency = payload_entity.get('currency')
    if currency and currency != 'INR':
        logger.warning("Webhook currency mismatch: %s", currency)
        return HttpResponse("Currency mismatch", status=400)

    if order_id:
        tx = WalletTransaction.objects.filter(razorpay_order_id=order_id).select_related('wallet__organization').first()
        if tx and tx.success:
            return HttpResponse("Already processed", status=200)

        amount_paise = int(payload_entity.get('amount') or 0)
        amount_dec = (Decimal(str(amount_paise)) / Decimal('100.0')).quantize(Decimal('0.01'))

        # Invariant: if stored transaction exists, amount must match
        if tx:
            expected_paise = int((Decimal(str(tx.amount)) * Decimal('100.0')).to_integral_value())
            if amount_paise != expected_paise:
                logger.warning("WEBHOOK_AMOUNT_MISMATCH: received %s != expected %s", amount_paise, expected_paise)
                return HttpResponse("Amount mismatch", status=400)

        # Organization & Wallet ownership validation from notes vs tx
        note_org_id = notes.get('org_id')
        note_wallet_id = notes.get('wallet_id')
        if tx and tx.wallet:
            if note_org_id and str(note_org_id) != str(tx.wallet.organization_id):
                logger.warning("WEBHOOK_TENANT_MISMATCH: note org %s != tx org %s", note_org_id, tx.wallet.organization_id)
                return HttpResponse("Tenant mismatch", status=403)
            if note_wallet_id and str(note_wallet_id) != str(tx.wallet_id):
                logger.warning("WEBHOOK_WALLET_MISMATCH: note wallet %s != tx wallet %s", note_wallet_id, tx.wallet_id)
                return HttpResponse("Wallet mismatch", status=403)

        if payment_type == 'wallet' or (tx and tx.transactionType == 'Credit'):
            wallet_id = note_wallet_id or (tx.wallet_id if tx else None)
            target_org_id = note_org_id or (tx.wallet.organization_id if tx and tx.wallet else None)
            bonus_amount_str = notes.get('bonus_amount', '0.00')
            coupon_code = notes.get('coupon_code')
            try:
                bonus_amount = Decimal(bonus_amount_str)
            except Exception:
                bonus_amount = Decimal('0.00')

            if wallet_id:
                from company.api.v1.services import BillingService
                BillingService.credit_wallet_from_payment(
                    wallet_id=wallet_id,
                    amount_dec=tx.amount if tx else amount_dec,
                    bonus_amount_dec=bonus_amount,
                    coupon_code=coupon_code,
                    razorpay_order_id=order_id,
                    razorpay_payment_id=payment_id,
                    gateway_event_id=event_id,
                    details="Wallet Top-up via Razorpay Webhook",
                    target_org_id=target_org_id
                )
        elif payment_type == 'subscription' or (tx and tx.transactionType == 'Debit'):
            target_org_id = note_org_id or (tx.wallet.organization_id if tx and tx.wallet else None)
            if target_org_id:
                try:
                    org = Organization.objects.get(id=target_org_id)
                    org_settings = org.settings
                    if not org_settings:
                        org_settings = OrgSettings.objects.create()
                        org.settings = org_settings
                        org.save()

                    emp_count = int(notes.get('employee_count', 10))
                    addons_str = notes.get('addons', '')
                    addons = [a.strip() for a in addons_str.split(',') if a.strip()]

                    org_settings.max_employees_allowed = emp_count
                    org_settings.is_attendance_enabled = 'attendance' in addons
                    org_settings.is_project_enabled = 'project' in addons
                    org_settings.subscriptionDays = 30
                    org_settings.subscriptionStatus = 'Active'
                    org_settings.save()

                    if tx:
                        tx.status = 'Success'
                        tx.success = True
                        tx.razorpay_payment_id = payment_id
                        tx.gateway_event_id = event_id
                        tx.save()
                except Organization.DoesNotExist:
                    pass

    return HttpResponse(status=200)


def stripe_webhook(request):
    return razorpay_webhook(request)


# ==============================================================================
# Billing Views (moved from company app)
# ==============================================================================


# --------------------------------------------------------------------------------
# WalletViewSet: ViewSet managing employee wallets and current balances.
# --------------------------------------------------------------------------------
class WalletViewSet(viewsets.ModelViewSet):
    queryset = Wallet.objects.all().order_by('-id')
    serializer_class = WalletSerializer

    def get_permissions(self):
        if self.action in ['current_wallet']:
            return [permissions.IsAuthenticated()]
        if self.action in ['bulk_adjust']:
            return [permissions.IsAuthenticated(), IsSuperAdminUser()]
        self.required_permission = 'settings:billing'
        return [permissions.IsAuthenticated(), HasRequiredPermission()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and user.organization:
            qs = qs.filter(organization=user.organization)
        return qs

    @action(detail=False, methods=['get'], url_path='current')
    def current_wallet(self, request):
        user = request.user
        active_org = getattr(request, 'active_organization', None) or getattr(user, 'organization', None)
        active_mem = getattr(request, 'active_membership', None)
        if not active_mem and user.is_authenticated and active_org:
            from users.models import OrganizationMembership
            active_mem = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).first()

        is_super = getattr(user, 'isSuperAdmin', False)
        if not active_org or (not active_mem and not is_super) or (active_mem and (not active_mem.is_active_in_org or active_mem.is_deleted)):
            return Response({'error': 'Active organization context and valid membership are required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            wallet = Wallet.objects.filter(organization=active_org).first()
            if not wallet:
                wallet = Wallet.objects.create(
                    employee=user,
                    organization=active_org,
                    balance=Decimal('0.00')
                )

            serializer = self.get_serializer(wallet)
            return Response(serializer.data)

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Database recovery triggered in current_wallet: {e}")
            fallback_data = {"id": "fallback", "balance": "0.00", "transactions": []}
            return Response(fallback_data, status=200)

    @action(detail=False, methods=['post'], url_path='topup')
    def topup(self, request):
        amount = request.data.get('amount')
        coupon_code = request.data.get('coupon_code')

        if not amount:
            return Response({'error': 'Enter a valid deposit amount.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            amount_dec = Decimal(str(amount))
            if amount_dec <= 0:
                return Response({'error': 'Deposit amount must be greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Enter a valid deposit amount.'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        active_org = getattr(request, 'active_organization', None) or getattr(user, 'organization', None)
        active_mem = getattr(request, 'active_membership', None)
        if not active_mem and user.is_authenticated and active_org:
            from users.models import OrganizationMembership
            active_mem = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).first()

        is_super = getattr(user, 'isSuperAdmin', False)
        if not active_org or (not active_mem and not is_super) or (active_mem and (not active_mem.is_active_in_org or active_mem.is_deleted)):
            return Response({'error': 'Active organization context and valid membership are required for wallet top-up.'}, status=status.HTTP_403_FORBIDDEN)

        wallet = Wallet.objects.filter(organization=active_org).order_by('id').first()
        if not wallet:
            wallet = Wallet.objects.create(
                organization=active_org,
                employee=request.user,
                balance=Decimal('0.00')
            )

        bonus_amount_dec = Decimal('0.00')
        validated_code = None
        if coupon_code and coupon_code.strip():
            coupon = BackofficeCoupon.objects.filter(code__iexact=coupon_code.strip()).first()
            if not coupon:
                return Response({'error': 'Invalid coupon code.'}, status=status.HTTP_400_BAD_REQUEST)
            if not coupon.is_active:
                return Response({'error': 'Coupon is inactive.'}, status=status.HTTP_400_BAD_REQUEST)
            if coupon.expiry_date and coupon.expiry_date < timezone.now():
                return Response({'error': 'Coupon has expired.'}, status=status.HTTP_400_BAD_REQUEST)
            if amount_dec < coupon.min_deposit_limit:
                return Response({'error': f'Minimum deposit of ₹{coupon.min_deposit_limit} required for this coupon.'}, status=status.HTTP_400_BAD_REQUEST)

            validated_code = coupon.code
            if coupon.value_type == 'Percentage':
                bonus_amount_dec = (coupon.value / Decimal('100.0')) * amount_dec
            else:
                bonus_amount_dec = coupon.value
            bonus_amount_dec = bonus_amount_dec.quantize(Decimal('0.01'))

        client, key_id, key_secret = get_razorpay_client()
        allow_mock = getattr(dj_settings, 'ALLOW_MOCK_PAYMENTS', False) or getattr(dj_settings, 'DEBUG', False)
        import uuid

        if not client:
            if not allow_mock:
                return Response({'error': 'Payment service is temporarily unavailable.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            mock_order_id = f"mock_rzp_order_{uuid.uuid4().hex}"
            detail_msg = f"Pending wallet top-up of ₹{amount_dec} via Mock Checkout"
            if validated_code:
                detail_msg += f" (Code '{validated_code}', Bonus ₹{bonus_amount_dec})"

            WalletTransaction.objects.create(
                wallet=wallet,
                amount=amount_dec + bonus_amount_dec,
                transactionType='Credit',
                success=False,
                razorpay_order_id=mock_order_id,
                status='Pending',
                details=detail_msg
            )
            return Response({
                'gateway': 'razorpay',
                'key_id': key_id or 'rzp_test_mock',
                'order_id': mock_order_id,
                'amount': int(amount_dec * 100),
                'currency': 'INR',
                'name': 'CubeLogs',
                'description': 'Wallet Top-up',
                'payment_type': 'wallet',
                'is_mock': True
            }, status=status.HTTP_200_OK)

        notes = {
            'wallet_id': str(wallet.id),
            'org_id': str(active_org.id),
            'amount': str(amount_dec),
            'payment_type': 'wallet'
        }
        if validated_code:
            notes['coupon_code'] = validated_code
            notes['bonus_amount'] = str(bonus_amount_dec)

        try:
            order_data = {
                'amount': int(amount_dec * 100),
                'currency': 'INR',
                'receipt': f"rcpt_topup_{uuid.uuid4().hex[:12]}",
                'notes': notes
            }
            order = client.order.create(data=order_data)

            WalletTransaction.objects.create(
                wallet=wallet,
                amount=amount_dec,
                transactionType='Credit',
                success=False,
                razorpay_order_id=order['id'],
                status='Pending',
                details=f"Pending wallet top-up of ₹{amount_dec} via Razorpay Checkout"
            )

            return Response({
                'gateway': 'razorpay',
                'key_id': key_id,
                'order_id': order['id'],
                'amount': order['amount'],
                'currency': 'INR',
                'name': 'CubeLogs',
                'description': 'Wallet Top-up',
                'payment_type': 'wallet'
            }, status=status.HTTP_200_OK)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to initiate Razorpay payment: {e}")
            return Response({'error': 'Payment service is temporarily unavailable.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='toggle-module')
    def toggle_module(self, request):
        import calendar
        from company.api.v1.services import BillingService
        module = request.data.get('module')
        enable = request.data.get('enable')

        SUPPORTED_MODULES = ['attendance', 'project']
        if module not in SUPPORTED_MODULES:
            return Response({'error': f'Unknown module: {module}'}, status=status.HTTP_400_BAD_REQUEST)
        if enable is None:
            return Response({'error': '"enable" field is required (true/false)'}, status=status.HTTP_400_BAD_REQUEST)

        enable = bool(enable)
        user = request.user
        active_org = getattr(request, 'active_organization', None) or getattr(user, 'organization', None)
        active_mem = getattr(request, 'active_membership', None)
        if not active_mem and user.is_authenticated and active_org:
            from users.models import OrganizationMembership
            active_mem = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).first()

        is_super = getattr(user, 'isSuperAdmin', False)
        if not active_org or (not active_mem and not is_super) or (active_mem and (not active_mem.is_active_in_org or active_mem.is_deleted)):
            return Response({'error': 'Active organization context and valid membership are required.'}, status=status.HTTP_403_FORBIDDEN)

        org = active_org
        settings_obj = org.settings
        if not settings_obj:
            settings_obj = OrgSettings.objects.create()
            org.settings = settings_obj
            org.save()

        current_state = getattr(settings_obj, f'is_{module}_enabled', False)
        if current_state == enable:
            return Response({'message': 'Module already in desired state', 'module': module, 'enabled': enable, 'charged': '0.00'})

        setattr(settings_obj, f'is_{module}_enabled', enable)
        settings_obj.save()

        action_word = 'activated' if enable else 'disabled'
        return Response({
            'message': f'{module.title()} module {action_word} successfully.',
            'module': module,
            'enabled': enable,
            'charged': '0.00'
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='validate-coupon')
    def validate_coupon(self, request):
        code_str = request.data.get('code')
        deposit_amount = request.data.get('deposit_amount')

        if not code_str:
            return Response({'error': 'Coupon code is required'}, status=status.HTTP_400_BAD_REQUEST)
        if deposit_amount is None:
            return Response({'error': 'Deposit amount is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            dep_amount_dec = Decimal(str(deposit_amount))
            if dep_amount_dec <= 0:
                return Response({'error': 'Deposit amount must be greater than zero'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Invalid deposit amount'}, status=status.HTTP_400_BAD_REQUEST)

        coupon = BackofficeCoupon.objects.filter(code__iexact=code_str.strip()).first()
        if not coupon:
            return Response({'valid': False, 'error': 'Invalid coupon code'}, status=status.HTTP_404_NOT_FOUND)
        if not coupon.is_active:
            return Response({'valid': False, 'error': 'This coupon is inactive'}, status=status.HTTP_400_BAD_REQUEST)
        if coupon.expiry_date and coupon.expiry_date < timezone.now():
            return Response({'valid': False, 'error': 'This coupon has expired'}, status=status.HTTP_400_BAD_REQUEST)
        if dep_amount_dec < coupon.min_deposit_limit:
            return Response({'valid': False, 'error': f'Minimum deposit of ₹{coupon.min_deposit_limit} required for this coupon'}, status=status.HTTP_400_BAD_REQUEST)

        if coupon.value_type == 'Percentage':
            bonus_val = (coupon.value / Decimal('100.0')) * dep_amount_dec
        else:
            bonus_val = coupon.value
        bonus_val = bonus_val.quantize(Decimal('0.01'))

        return Response({'valid': True, 'code': coupon.code, 'value_type': coupon.value_type, 'value': str(coupon.value), 'computed_bonus': str(bonus_val), 'min_deposit_limit': str(coupon.min_deposit_limit), 'total_value': str(dep_amount_dec + bonus_val), 'net_payable': str(dep_amount_dec)}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='bulk-adjust')
    def bulk_adjust(self, request):
        """
        Asynchronously processes bulk administrative wallet credits/debits.
        Accepts:
          adjustments: list of {organization_id, employee_id, amount, type: 'Credit'|'Debit', details}
        """
        adjustments = request.data.get('adjustments', [])
        if not isinstance(adjustments, list) or not adjustments:
            return Response({'error': 'A non-empty list of adjustments is required.'}, status=status.HTTP_400_BAD_REQUEST)

        from subscribers.tasks import process_bulk_wallet_adjustments_task, dispatch_task_safely
        task = dispatch_task_safely(
            process_bulk_wallet_adjustments_task,
            adjustments=adjustments,
            initiated_by_id=request.user.id
        )

        return Response(
            {
                "task_id": str(task.id),
                "status": getattr(task, 'status', 'PENDING'),
                "message": f"Queued {len(adjustments)} wallet adjustment(s)."
            },
            status=status.HTTP_202_ACCEPTED
        )

    @action(detail=False, methods=['get'], url_path=r'tasks/(?P<task_id>[^/.]+)')
    def task_status(self, request, task_id=None):
        """
        Polls status of asynchronous wallet/billing tasks.
        """
        from celery.result import AsyncResult
        res = AsyncResult(task_id)
        response_data = {
            "task_id": task_id,
            "status": res.status,
            "ready": res.ready(),
            "successful": res.successful() if res.ready() else False,
            "result": res.result if res.ready() and not isinstance(res.result, Exception) else None,
        }
        if res.failed():
            response_data["error"] = str(res.result)
        return Response(response_data, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# BackofficePaymentListView: View managing list of payments for administrative operators.
# --------------------------------------------------------------------------------
class BackofficePaymentListView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request):
        transactions = WalletTransaction.objects.all().order_by('-created_at')
        serializer = WalletTransactionSerializer(transactions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# CouponViewSet: ViewSet managing customer promotion coupons.
class CouponViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = Coupon.objects.all().order_by('-created_at')
    serializer_class = CouponSerializer
    permission_classes = [IsSuperAdminUser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = CouponFilter


# BackofficeCouponViewSet: ViewSet managing backoffice coupons for package activations.
class BackofficeCouponViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = BackofficeCoupon.objects.all().order_by('-created_at')
    serializer_class = BackofficeCouponSerializer
    permission_classes = [IsSuperAdminUser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BackofficeCouponFilter

    def perform_create(self, serializer):
        code = self.request.data.get('code')
        if not code or not str(code).strip():
            code = default_coupon_code()
        serializer.save(code=code.upper())


# --------------------------------------------------------------------------------
# BackofficeOrganizationListView: View managing list of companies for administrative operators.
# --------------------------------------------------------------------------------
class BackofficeOrganizationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if not getattr(user, 'isSuperAdmin', False) or user.organization is not None:
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        orgs = Organization.objects.all().order_by('name')
        org_list = []
        for org in orgs:
            org_list.append({
                'id': org.id,
                'name': org.name,
                'subdomain': org.subdomain,
            })
        return Response(org_list, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# BackofficeStorageOrganizationListView: Read-only platform oversight of tenant storage usage
# --------------------------------------------------------------------------------
class BackofficeStorageOrganizationListView(APIView):
    permission_classes = [IsSuperAdminUser]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        g_settings = GlobalBillingSettings.get_settings()
        credit_size_bytes = g_settings.storage_credit_size_bytes

        last_activity_sub = StorageEvent.objects.filter(
            organization=OuterRef('pk')
        ).order_by('-occurred_at').values('occurred_at')[:1]

        queryset = Organization.objects.annotate(
            active_bytes=Coalesce(
                Sum('storage_files__size_bytes', filter=Q(storage_files__status='ACTIVE')),
                Value(0)
            ),
            active_files=Count('storage_files', filter=Q(storage_files__status='ACTIVE')),
            deleted_files=Count('storage_files', filter=Q(storage_files__status='DELETED')),
            last_storage_activity_at=Subquery(last_activity_sub, output_field=DateTimeField())
        ).order_by('id')

        search_query = (request.query_params.get('search') or request.query_params.get('q') or '').strip()
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(subdomain__icontains=search_query))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        results = []
        target_records = page if page is not None else queryset
        for org in target_records:
            billable_gb, current_credits = StorageCalculationService.calculate_storage_credits(
                org.active_bytes, credit_size_bytes=credit_size_bytes
            )
            results.append({
                "organization_id": org.id,
                "organization_name": org.name,
                "subdomain": org.subdomain,
                "active_bytes": org.active_bytes,
                "active_gb": str(billable_gb),
                "current_credits": current_credits,
                "active_files": org.active_files,
                "deleted_files": org.deleted_files,
                "last_storage_activity_at": org.last_storage_activity_at.isoformat() if org.last_storage_activity_at else None,
            })

        if page is not None:
            return paginator.get_paginated_response(results)
        return Response(results, status=status.HTTP_200_OK)


# GlobalBillingSettingsViewSet: ViewSet managing global billing parameters (e.g. pricing, schedules, grace period).
class GlobalBillingSettingsViewSet(viewsets.ViewSet):
    permission_classes = [IsPlatformBillingAdmin]

    def list(self, request):
        settings_instance, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        serializer = GlobalBillingSettingsSerializer(settings_instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request):
        settings_instance, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        serializer = GlobalBillingSettingsSerializer(settings_instance, data=request.data, partial=True)
        if serializer.is_valid():
            saved = serializer.save()
            if 'attendance_module_price' in request.data:
                SubscriptionPackage.objects.filter(features__icontains='attendance', isActive=True).exclude(price=0).update(price=saved.attendance_module_price)
            if 'tasks_module_price' in request.data:
                SubscriptionPackage.objects.filter(features__icontains='project', isActive=True).exclude(price=0).update(price=saved.tasks_module_price)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------------
# BackofficeEmailLogListView: API enabling administration backoffice to view email logs, queues and resend status.
# --------------------------------------------------------------------------------
class BackofficeEmailLogListView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request):
        from core.models import EmailLog
        logs = EmailLog.objects.all().order_by('-created_at')[:200]
        data = []
        for log in logs:
            data.append({
                'id': log.id,
                'recipient': log.recipient,
                'subject': log.subject,
                'body': log.body,
                'from_email': log.from_email,
                'status': log.status,
                'error_message': log.error_message,
                'sent_at': log.sent_at.isoformat() if log.sent_at else None,
                'created_at': log.created_at.isoformat() if log.created_at else None,
            })
        return Response(data, status=status.HTTP_200_OK)


class BackofficeEmailLogResendView(APIView):
    permission_classes = [IsSuperAdminUser]

    def post(self, request, pk):
        from core.models import EmailLog
        from django.core.mail import send_mail
        from django.conf import settings
        from django.utils import timezone

        try:
            log_item = EmailLog.objects.get(pk=pk)
        except EmailLog.DoesNotExist:
            return Response({'error': 'Email log entry not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            send_mail(
                subject=log_item.subject,
                message=log_item.body or '',
                from_email=log_item.from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[log_item.recipient],
                fail_silently=False,
                html_message=log_item.body if '<' in (log_item.body or '') else None
            )
            log_item.status = 'SENT'
            log_item.sent_at = timezone.now()
            log_item.error_message = None
            log_item.save()
            return Response({'status': 'sent', 'message': f'Email successfully resent to {log_item.recipient}'}, status=status.HTTP_200_OK)
        except Exception as exc:
            log_item.status = 'FAILED'
            log_item.error_message = str(exc)
            log_item.save()
            return Response({'error': f'Failed to resend email: {str(exc)}'}, status=status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------------
# LiveBillingEstimateView: API endpoint providing live estimate for upcoming monthly bill
# --------------------------------------------------------------------------------
class LiveBillingEstimateView(APIView):
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permission = 'settings:billing'

    def get(self, request):
        org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
        active_mem = getattr(request, 'active_membership', None)
        if not org or (active_mem and (not active_mem.is_active_in_org or active_mem.is_deleted)):
            return Response({'error': 'Active organization not found.'}, status=status.HTTP_400_BAD_REQUEST)

        g_settings = GlobalBillingSettings.get_settings()
        settings_obj = org.settings

        from company.api.v1.services import BillingService
        billable_emp_count = BillingService.get_billable_memberships_qs(org).count()
        unit_price = Decimal(str(g_settings.employee_seat_price))
        emp_total = Decimal(str(billable_emp_count)) * unit_price

        att_enabled = settings_obj.is_attendance_enabled if settings_obj else False
        att_rate = Decimal(str(g_settings.attendance_module_price))
        att_charge = (Decimal(str(billable_emp_count)) * att_rate) if att_enabled else Decimal('0.00')

        proj_enabled = settings_obj.is_project_enabled if settings_obj else False
        proj_rate = Decimal(str(g_settings.tasks_module_price))
        proj_charge = (Decimal(str(billable_emp_count)) * proj_rate) if proj_enabled else Decimal('0.00')

        total_estimate = emp_total + att_charge + proj_charge

        return Response({
            'billable_employee_count': billable_emp_count,
            'employee_rate': f"{unit_price:.2f}",
            'employee_charge': f"{emp_total:.2f}",
            'employee_unit_price': float(unit_price),
            'employee_seat_estimate': float(emp_total),

            'attendance_enabled': att_enabled,
            'attendance_rate': f"{att_rate:.2f}",
            'attendance_charge': f"{att_charge:.2f}",
            'attendance_price': float(att_charge),

            'project_enabled': proj_enabled,
            'project_rate': f"{proj_rate:.2f}",
            'project_charge': f"{proj_charge:.2f}",
            'project_price': float(proj_charge),

            'estimated_next_total': float(total_estimate),
            'currency': g_settings.currency,
        }, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------------
# MonthlyInvoicePDFView: API endpoint streaming downloadable PDF invoice
# --------------------------------------------------------------------------------
class MonthlyInvoicePDFView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            invoice = MonthlyInvoice.objects.get(pk=pk)
        except MonthlyInvoice.DoesNotExist:
            return Response({'error': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Cross-Tenant Authorization Check
        org = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
        if not org or invoice.organization_id != org.id:
            if not request.user.is_superuser:
                return Response({'error': 'Invoice not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)

        from subscribers.pdf import generate_invoice_pdf
        pdf_buf = generate_invoice_pdf(invoice)

        from django.http import HttpResponse
        response = HttpResponse(pdf_buf.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="CubeLogs_Invoice_{invoice.id}.pdf"'
        return response


# --------------------------------------------------------------------------------
# PublicPricingView: Safe public read-only rates endpoint backed by GlobalBillingSettings
# --------------------------------------------------------------------------------
class PublicPricingView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        g_settings = GlobalBillingSettings.get_settings()
        return Response({
            'currency': g_settings.currency or 'INR',
            'employee_seat_price': str(g_settings.employee_seat_price),
            'attendance_module_price': str(g_settings.attendance_module_price),
            'project_module_price': str(g_settings.tasks_module_price),
            'base_subscription_price': '0.00',
            'tax_percentage': '0.00'
        }, status=status.HTTP_200_OK)

