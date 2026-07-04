from django.urls import path, include
from rest_framework.routers import DefaultRouter
from api.views import (
    EmployeeViewSet, AttendanceLogViewSet, TaskViewSet, LeaveTypeViewSet,
    LeaveViewSet, HolidayViewSet, TemplateViewSet, OfficeLocationViewSet,
    ScheduleViewSet, OrgSettingsViewSet, CustomTokenObtainPairView, CurrentUserView, MagicLoginView,
    AuditLogViewSet, PasswordResetRequestView, PasswordResetValidateView, PasswordResetConfirmView,
    ChangePasswordView, LeadViewSet,
    SubscriptionPackageViewSet, SubscriberAccountViewSet, CMSContentViewSet,
    LMSModuleViewSet, CouponViewSet, backoffice_view, WalletViewSet, DynamicCheckoutView,
    ConfirmSubscriptionView, BackofficeRegisterCompanyView, PermissionsConfigView,
    AttendanceApprovalView, HRAttendanceDashboardView, HolidaySettingsView,
    BackofficeOrganizationListView, backoffice_login_view,
    PublicLeadCreateView, BackofficeLeadListView, BackofficeLeadDetailView, BackofficePaymentListView,
    BackofficeCouponViewSet, PromoVideoSectionViewSet, TestimonialViewSet
)
from rest_framework_simplejwt.views import TokenRefreshView
from api.serializers import CustomTokenRefreshSerializer

router = DefaultRouter()
router.register('employees', EmployeeViewSet, basename='employee')
router.register('attendance', AttendanceLogViewSet, basename='attendance')
router.register('tasks', TaskViewSet, basename='task')
router.register('leave-types', LeaveTypeViewSet, basename='leave-type')
router.register('leaves', LeaveViewSet, basename='leave')
router.register('holidays', HolidayViewSet, basename='holiday')
router.register('templates', TemplateViewSet, basename='template')
router.register('locations', OfficeLocationViewSet, basename='location')
router.register('schedules', ScheduleViewSet, basename='schedule')
router.register('audit-logs', AuditLogViewSet, basename='audit-log')
router.register('leads', LeadViewSet, basename='lead')
router.register('packages', SubscriptionPackageViewSet, basename='package')
router.register('subscribers', SubscriberAccountViewSet, basename='subscriber')
router.register('cms', CMSContentViewSet, basename='cms')
router.register('lms', LMSModuleViewSet, basename='lms')
router.register('coupons', CouponViewSet, basename='coupon')
router.register('backoffice/coupons', BackofficeCouponViewSet, basename='backoffice-coupon')
router.register('wallet', WalletViewSet, basename='wallet')
router.register('promo-video', PromoVideoSectionViewSet, basename='promo-video')
router.register('testimonials', TestimonialViewSet, basename='testimonial')

urlpatterns = [
    # Dynamic checkout endpoint
    path('subscription/dynamic-checkout/', DynamicCheckoutView.as_view(), name='dynamic_checkout'),
    path('subscription/confirm/', ConfirmSubscriptionView.as_view(), name='confirm_subscription'),

    # Backoffice portal page
    path('backoffice/', backoffice_view, name='backoffice'),
    path('backoffice/login/', backoffice_login_view, name='backoffice_login'),
    path('backoffice/organizations/', BackofficeOrganizationListView.as_view(), name='backoffice_organizations'),
    path('register-company/', BackofficeRegisterCompanyView.as_view(), name='register_company'),
    path('leads/public/', PublicLeadCreateView.as_view(), name='public-lead-create'),
    path('leads/backoffice/', BackofficeLeadListView.as_view(), name='backoffice-lead-list'),
    path('leads/backoffice/<int:pk>/', BackofficeLeadDetailView.as_view(), name='backoffice-lead-detail'),
    path('payments/backoffice/', BackofficePaymentListView.as_view(), name='backoffice-payment-list'),

    # Auth endpoints
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/magic-login/', MagicLoginView.as_view(), name='magic_login'),
    path('auth/refresh/', TokenRefreshView.as_view(serializer_class=CustomTokenRefreshSerializer), name='token_refresh'),
    path('auth/me/', CurrentUserView.as_view(), name='auth_me'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('auth/password-reset/request/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset/validate/', PasswordResetValidateView.as_view(), name='password_reset_validate'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    
    # Org Settings
    path('settings/', OrgSettingsViewSet.as_view({'get': 'list', 'put': 'update', 'patch': 'partial_update'}), name='org-settings'),
    path('settings/current/', OrgSettingsViewSet.as_view({'get': 'current_settings', 'put': 'current_settings', 'patch': 'current_settings'}), name='org-settings-current'),
    path('settings/holidays/', HolidaySettingsView.as_view(), name='holiday-settings'),
    
    # Permissions Registry
    path('permissions/config/', PermissionsConfigView.as_view(), name='permissions-config'),

    # HR Attendance Management
    path('attendance/hr-dashboard/', HRAttendanceDashboardView.as_view(), name='attendance-hr-dashboard'),
    path('attendance/<int:pk>/approve/', AttendanceApprovalView.as_view(), name='attendance-approve'),
    
    # Other models CRUD
    path('', include(router.urls)),
]
