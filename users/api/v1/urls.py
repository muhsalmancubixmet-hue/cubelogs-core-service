# --------------------------------------------------------------------------------
#       Users API Routing
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY
from rest_framework.routers import DefaultRouter

# APPLICATION SPECIFIC
from users.api.v1.views import (
    EmployeeViewSet, RoleViewSet, PermissionFlagViewSet, CustomTokenObtainPairView, CurrentUserView,
    MagicLoginView, SwitchOrganizationView, ChangePasswordView, PasswordResetRequestView,
    PasswordResetValidateView, PasswordResetConfirmView, PermissionsConfigView,
    backoffice_view, backoffice_login_view, CustomTokenRefreshView, LogoutView
)

router = DefaultRouter()
router.register('employees', EmployeeViewSet, basename='employee')
router.register('roles', RoleViewSet, basename='role')
router.register('permissions-flags', PermissionFlagViewSet, basename='permission-flag')

urlpatterns = [
    # Auth endpoints
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair_standard'),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh_standard'),
    path('auth/magic-login/', MagicLoginView.as_view(), name='magic_login'),
    path('auth/logout/', LogoutView.as_view(), name='magic_logout'),
    path('auth/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh_alias'),
    path('auth/me/', CurrentUserView.as_view(), name='auth_me'),
    path('users/me/', CurrentUserView.as_view(), name='users_me'),
    path('auth/switch-organization/', SwitchOrganizationView.as_view(), name='switch_organization'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('auth/password-reset/request/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset/validate/', PasswordResetValidateView.as_view(), name='password_reset_validate'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    
    # Permissions Registry
    path('permissions/config/', PermissionsConfigView.as_view(), name='permissions-config'),
    
    # Backoffice Portal Page
    path('backoffice/', backoffice_view, name='backoffice_users_api'),
    path('backoffice/login/', backoffice_login_view, name='backoffice_login_users_api'),

    path('', include(router.urls)),
]
