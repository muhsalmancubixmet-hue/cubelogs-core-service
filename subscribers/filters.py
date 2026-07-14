from django.db import models
import django_filters as filters
from subscribers.models import SubscriptionPackage, SubscriberAccount, Coupon, BackofficeCoupon

class SubscriptionPackageFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
            ('price', 'price'),
        )
    )

    class Meta:
        model = SubscriptionPackage
        fields = [
            'id',
            'name',
            'isActive',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value) |
            models.Q(features__icontains=value)
        )


class SubscriberAccountFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('email', 'email'),
            ('packageName', 'packageName'),
            ('expiresAt', 'expiresAt'),
        )
    )

    class Meta:
        model = SubscriberAccount
        fields = [
            'id',
            'email',
            'packageName',
            'isActive',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(email__icontains=value) |
            models.Q(packageName__icontains=value)
        )


class CouponFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('code', 'code'),
        )
    )

    class Meta:
        model = Coupon
        fields = ['id', 'code', 'discountType']

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(models.Q(code__icontains=value))


class BackofficeCouponFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('code', 'code'),
            ('value', 'value'),
        )
    )

    class Meta:
        model = BackofficeCoupon
        fields = ['id', 'code', 'value_type', 'is_active']

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(code__icontains=value) |
            models.Q(value_type__icontains=value)
        )
