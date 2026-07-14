from django.urls import path, include
from rest_framework.routers import DefaultRouter
from subscribers.api.v1.views import (
    SubscriptionPackageViewSet, SubscriberAccountViewSet,
    DynamicCheckoutView, ConfirmSubscriptionView, BackofficeRegisterCompanyView,
    BackofficeOrganizationListView, stripe_webhook,
    WalletViewSet, BackofficePaymentListView, CouponViewSet, BackofficeCouponViewSet,
)

router = DefaultRouter()
router.register('packages', SubscriptionPackageViewSet, basename='package')
router.register('subscribers', SubscriberAccountViewSet, basename='subscriber')
router.register('wallet', WalletViewSet, basename='wallet')
router.register('coupons', CouponViewSet, basename='coupon')
router.register('backoffice/coupons', BackofficeCouponViewSet, basename='backoffice-coupon')

urlpatterns = [
    path('subscription/dynamic-checkout/', DynamicCheckoutView.as_view(), name='dynamic_checkout'),
    path('subscription/confirm/', ConfirmSubscriptionView.as_view(), name='confirm_subscription'),
    path('register-company/', BackofficeRegisterCompanyView.as_view(), name='register_company'),
    path('backoffice/organizations/', BackofficeOrganizationListView.as_view(), name='backoffice_organizations'),
    path('payments/backoffice/', BackofficePaymentListView.as_view(), name='backoffice-payment-list'),
    path('stripe/webhook/', stripe_webhook, name='stripe_webhook'),
    path('', include(router.urls)),
]
