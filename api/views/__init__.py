# api/views/__init__.py
#
# Re-exports every public name from the feature sub-modules so that the
# existing `from api.views import X` imports in urls.py continue to work
# without any modification.

# ── Auth ──────────────────────────────────────────────────────────────────────
from api.views.auth import (
    CustomTokenObtainPairSerializer,
    CustomTokenObtainPairView,
    CurrentUserView,
    MagicLoginView,
    PasswordResetRequestView,
    PasswordResetValidateView,
    PasswordResetConfirmView,
    ChangePasswordView,
)

# ── Employee ───────────────────────────────────────────────────────────────────
from api.views.employee import (
    EmployeeViewSet,
)

# ── Attendance ─────────────────────────────────────────────────────────────────
from api.views.attendance import (
    AttendanceLogViewSet,
    AttendanceApprovalView,
    HRAttendanceDashboardView,
)

# ── Leave ──────────────────────────────────────────────────────────────────────
from api.views.leave import (
    LeaveTypeViewSet,
    LeaveViewSet,
)

# ── Organization / Settings / Tasks / Holidays ─────────────────────────────────
from api.views.organization import (
    TaskViewSet,
    HolidayViewSet,
    HolidaySettingsView,
    TemplateViewSet,
    OfficeLocationViewSet,
    ScheduleViewSet,
    OrgSettingsViewSet,
    AuditLogViewSet,
    PermissionsConfigView,
)

# ── CRM ────────────────────────────────────────────────────────────────────────
from api.views.crm import (
    IsSuperAdminUser,
    LeadViewSet,
    PublicLeadCreateView,
    BackofficeLeadListView,
    BackofficeLeadDetailView,
)

# ── Billing / Wallet / Stripe ──────────────────────────────────────────────────
from api.views.billing import (
    CanViewPackagesOrSuperAdmin,
    SubscriptionPackageViewSet,
    SubscriberAccountViewSet,
    WalletViewSet,
    DynamicCheckoutView,
    ConfirmSubscriptionView,
    BackofficePaymentListView,
    BackofficeOrganizationListView,
    BackofficeRegisterCompanyView,
)

# ── CMS / LMS / Coupon ────────────────────────────────────────────────────────
from api.views.cms import (
    CMSContentViewSet,
    LMSModuleViewSet,
    CouponViewSet,
    BackofficeCouponViewSet,
    PromoVideoSectionViewSet,
    TestimonialViewSet,
)

# ── Misc / Backoffice HTML / Stripe Webhook ───────────────────────────────────
from api.views.misc import (
    backoffice_view,
    backoffice_login_view,
    backoffice_logout_view,
    stripe_webhook,
)

__all__ = [
    # auth
    'CustomTokenObtainPairSerializer',
    'CustomTokenObtainPairView',
    'CurrentUserView',
    'MagicLoginView',
    'PasswordResetRequestView',
    'PasswordResetValidateView',
    'PasswordResetConfirmView',
    'ChangePasswordView',
    # employee
    'EmployeeViewSet',
    # attendance
    'AttendanceLogViewSet',
    'AttendanceApprovalView',
    'HRAttendanceDashboardView',
    # leave
    'LeaveTypeViewSet',
    'LeaveViewSet',
    # organization
    'TaskViewSet',
    'HolidayViewSet',
    'HolidaySettingsView',
    'TemplateViewSet',
    'OfficeLocationViewSet',
    'ScheduleViewSet',
    'OrgSettingsViewSet',
    'AuditLogViewSet',
    'PermissionsConfigView',
    # crm
    'IsSuperAdminUser',
    'LeadViewSet',
    'PublicLeadCreateView',
    'BackofficeLeadListView',
    'BackofficeLeadDetailView',
    # billing
    'CanViewPackagesOrSuperAdmin',
    'SubscriptionPackageViewSet',
    'SubscriberAccountViewSet',
    'WalletViewSet',
    'DynamicCheckoutView',
    'ConfirmSubscriptionView',
    'BackofficePaymentListView',
    'BackofficeOrganizationListView',
    'BackofficeRegisterCompanyView',
    # cms
    'CMSContentViewSet',
    'LMSModuleViewSet',
    'CouponViewSet',
    'BackofficeCouponViewSet',
    'PromoVideoSectionViewSet',
    'TestimonialViewSet',
    # misc
    'backoffice_view',
    'backoffice_login_view',
    'backoffice_logout_view',
    'stripe_webhook',
]
