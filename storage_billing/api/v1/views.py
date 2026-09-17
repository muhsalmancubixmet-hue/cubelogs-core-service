import logging
from django.db.models import Sum, Count
from django.db.models.functions import Coalesce
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import HasRequiredPermission
from storage_billing.api.v1.serializers import (
    CompanyStorageSummarySerializer,
    StorageDailyUsageHistorySerializer,
)
from storage_billing.models import StorageFile, StorageDailyUsage
from storage_billing.services import StorageCalculationService
from subscribers.models import GlobalBillingSettings

logger = logging.getLogger(__name__)


def _validate_tenant_context(request):
    """
    Validates that the request has an active, valid tenant context.
    Strictly requires request.active_organization.
    Never falls back to request.user.organization or Employee.organization.
    Fails closed if the organization or active membership is invalid/revoked.
    """
    org = getattr(request, 'active_organization', None)
    if not org:
        return None, Response(
            {'error': 'Active organization context is required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    active_mem = getattr(request, 'active_membership', None)
    if active_mem:
        if not getattr(active_mem, 'is_active_in_org', True) or getattr(active_mem, 'is_deleted', False):
            return None, Response(
                {'error': 'Active organization membership is revoked or inactive.'},
                status=status.HTTP_403_FORBIDDEN
            )

    return org, None


class CompanyStorageSummaryView(APIView):
    """
    GET /api/v1/storage/summary/
    Returns current active storage consumption, commercial decimal GB, current credits,
    global storage rate cards, file counts, and source module breakdown.
    Requires 'settings:billing' permission.
    Pure read operation with zero database side-effects.
    """
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'settings:billing'

    def get(self, request):
        org, error_response = _validate_tenant_context(request)
        if error_response:
            return error_response

        # Current ACTIVE storage aggregated at DB level
        active_agg = StorageFile.objects.filter(
            organization=org,
            status='ACTIVE'
        ).aggregate(
            total_bytes=Coalesce(Sum('size_bytes'), 0),
            active_files=Count('id')
        )
        active_bytes = active_agg['total_bytes']
        active_files = active_agg['active_files']

        # Count historical DELETED files
        deleted_files = StorageFile.objects.filter(
            organization=org,
            status='DELETED'
        ).count()

        # Pricing and credit parameters
        g_settings = GlobalBillingSettings.get_settings()
        credit_size_bytes = g_settings.storage_credit_size_bytes
        credit_monthly_price = g_settings.storage_credit_monthly_price
        currency = g_settings.currency or 'INR'
        billing_enabled = bool(g_settings.storage_billing_enabled)

        # Organization-wide credit calculation (whole credits round-up on total active bytes)
        billable_gb, current_credits = StorageCalculationService.calculate_storage_credits(
            billable_bytes=active_bytes,
            credit_size_bytes=credit_size_bytes
        )

        # Source / module breakdown
        breakdown_qs = StorageFile.objects.filter(
            organization=org,
            status='ACTIVE'
        ).values('source_module', 'source_model').annotate(
            bytes=Coalesce(Sum('size_bytes'), 0),
            active_files=Count('id')
        ).order_by('-bytes')

        source_breakdown = []
        for row in breakdown_qs:
            row_gb, _ = StorageCalculationService.calculate_storage_credits(
                billable_bytes=row['bytes'],
                credit_size_bytes=credit_size_bytes
            )
            source_breakdown.append({
                'source_module': row['source_module'],
                'source_model': row['source_model'],
                'bytes': row['bytes'],
                'gb': str(row_gb),
                'active_files': row['active_files']
            })

        payload = {
            'organization_id': org.id,
            'active_bytes': active_bytes,
            'active_gb': str(billable_gb),
            'current_credits': current_credits,
            'credit_size_bytes': credit_size_bytes,
            'credit_monthly_price': str(credit_monthly_price),
            'currency': currency,
            'billing_enabled': billing_enabled,
            'active_files': active_files,
            'deleted_files': deleted_files,
            'source_breakdown': source_breakdown,
        }

        serializer = CompanyStorageSummarySerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CompanyStorageHistoryView(APIView):
    """
    GET /api/v1/storage/history/
    Returns daily usage history from StorageDailyUsage ledger snapshots.
    Requires 'settings:billing' permission.
    Pure read operation with zero database writes.
    Returns HTTP 200 with [] if no usage rows exist.
    """
    permission_classes = [permissions.IsAuthenticated, HasRequiredPermission]
    required_permission = 'settings:billing'

    def get(self, request):
        org, error_response = _validate_tenant_context(request)
        if error_response:
            return error_response

        daily_qs = StorageDailyUsage.objects.filter(
            organization=org
        ).order_by('usage_date')

        serializer = StorageDailyUsageHistorySerializer(daily_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
