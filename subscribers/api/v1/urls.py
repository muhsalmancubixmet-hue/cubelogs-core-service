from django.urls import path, include
from rest_framework.routers import DefaultRouter
from subscribers.api.v1.views import (
    SubscriptionPackageViewSet, SubscriberAccountViewSet,
    DynamicCheckoutView, ConfirmSubscriptionView, VerifyPaymentView, BackofficeRegisterCompanyView,
    BackofficeOrganizationListView, BackofficeStorageOrganizationListView, stripe_webhook, razorpay_webhook,
    WalletViewSet, BackofficePaymentListView, CouponViewSet, BackofficeCouponViewSet,
    GlobalBillingSettingsViewSet, BackofficeEmailLogListView, BackofficeEmailLogResendView,
    LiveBillingEstimateView, MonthlyInvoicePDFView, PublicPricingView,
)

router = DefaultRouter()
router.register('packages', SubscriptionPackageViewSet, basename='package')
router.register('subscribers', SubscriberAccountViewSet, basename='subscriber')
router.register('wallet', WalletViewSet, basename='wallet')
router.register('coupons', CouponViewSet, basename='coupon')
router.register('backoffice/coupons', BackofficeCouponViewSet, basename='backoffice-coupon')
router.register('backoffice/billing-settings', GlobalBillingSettingsViewSet, basename='backoffice-billing-settings')

urlpatterns = [
    path('public-pricing/', PublicPricingView.as_view(), name='public_pricing'),
    path('subscription/dynamic-checkout/', DynamicCheckoutView.as_view(), name='dynamic_checkout'),
    path('subscription/confirm/', ConfirmSubscriptionView.as_view(), name='confirm_subscription'),
    path('payment/verify/', VerifyPaymentView.as_view(), name='payment_verify'),
    path('register-company/', BackofficeRegisterCompanyView.as_view(), name='register_company'),
    path('backoffice/organizations/', BackofficeOrganizationListView.as_view(), name='backoffice_organizations'),
    path('backoffice/storage/organizations/', BackofficeStorageOrganizationListView.as_view(), name='backoffice-storage-organizations'),
    path('payments/backoffice/', BackofficePaymentListView.as_view(), name='backoffice-payment-list'),
    path('backoffice/email-logs/', BackofficeEmailLogListView.as_view(), name='backoffice-email-logs'),
    path('backoffice/email-logs/<int:pk>/resend/', BackofficeEmailLogResendView.as_view(), name='backoffice-email-logs-resend'),
    path('billing-estimate/', LiveBillingEstimateView.as_view(), name='billing-estimate'),
    path('monthly-invoices/<int:pk>/pdf/', MonthlyInvoicePDFView.as_view(), name='monthly-invoice-pdf'),
    path('razorpay/webhook/', razorpay_webhook, name='razorpay_webhook'),
    path('stripe/webhook/', stripe_webhook, name='stripe_webhook'),
    path('', include(router.urls)),
]

