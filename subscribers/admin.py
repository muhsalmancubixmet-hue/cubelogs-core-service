from django.contrib import admin
from subscribers.models import (
    SubscriptionPackage, SubscriberAccount,
    Wallet, WalletTransaction, BackofficeCoupon, MonthlyInvoice, Coupon
)

@admin.register(SubscriptionPackage)
class SubscriptionPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'employeeLimit', 'isActive')
    list_filter = ('isActive',)
    search_fields = ('name',)

@admin.register(SubscriberAccount)
class SubscriberAccountAdmin(admin.ModelAdmin):
    list_display = ('email', 'packageName', 'isActive', 'expiresAt')
    list_filter = ('isActive', 'packageName')
    search_fields = ('email',)

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('employee', 'organization', 'balance', 'stripe_customer_id')
    search_fields = ('employee__email', 'stripe_customer_id')

@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'amount', 'transactionType', 'success', 'status', 'created_at')
    list_filter = ('transactionType', 'success', 'status')
    search_fields = ('wallet__employee__email', 'details')

@admin.register(BackofficeCoupon)
class BackofficeCouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'value_type', 'value', 'is_active', 'expiry_date')
    list_filter = ('value_type', 'is_active')
    search_fields = ('code',)

@admin.register(MonthlyInvoice)
class MonthlyInvoiceAdmin(admin.ModelAdmin):
    list_display = ('organization', 'billing_month', 'amount', 'is_paid', 'paid_at')
    list_filter = ('is_paid', 'billing_month')
    search_fields = ('organization__name',)

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discountType', 'discountValue', 'usageLimit', 'usageCount', 'expiresAt')
    list_filter = ('discountType', 'expiresAt')
    search_fields = ('code',)

