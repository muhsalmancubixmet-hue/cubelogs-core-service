from rest_framework import serializers
from storage_billing.models import StorageDailyUsage


class SourceBreakdownItemSerializer(serializers.Serializer):
    source_module = serializers.CharField()
    source_model = serializers.CharField()
    bytes = serializers.IntegerField()
    gb = serializers.CharField()
    active_files = serializers.IntegerField()


class CompanyStorageSummarySerializer(serializers.Serializer):
    organization_id = serializers.IntegerField()
    active_bytes = serializers.IntegerField()
    active_gb = serializers.CharField()
    current_credits = serializers.IntegerField()
    credit_size_bytes = serializers.IntegerField()
    credit_monthly_price = serializers.CharField()
    currency = serializers.CharField()
    billing_enabled = serializers.BooleanField()
    active_files = serializers.IntegerField()
    deleted_files = serializers.IntegerField()
    source_breakdown = SourceBreakdownItemSerializer(many=True)


class StorageDailyUsageHistorySerializer(serializers.ModelSerializer):
    date = serializers.DateField(source='usage_date')
    billable_gb = serializers.CharField()
    storage_charge = serializers.CharField()

    class Meta:
        model = StorageDailyUsage
        fields = [
            'date',
            'billable_bytes',
            'billable_gb',
            'storage_credits',
            'storage_charge',
            'is_finalized',
        ]
