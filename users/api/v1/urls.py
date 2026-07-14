# --------------------------------------------------------------------------------
#       Users API Routing
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

# APPLICATION SPECIFIC
from users.api.v1.views import (
    EmployeeViewSet, CustomTokenObtainPairView, CurrentUserView,
    MagicLoginView, ChangePasswordView, PasswordResetRequestView,
    PasswordResetValidateView, PasswordResetConfirmView, PermissionsConfigView,
    backoffice_view, backoffice_login_view
)
from users.api.v1.serializers import CustomTokenRefreshSerializer

router = DefaultRouter()
router.register('employees', EmployeeViewSet, basename='employee')

urlpatterns = [
    # Auth endpoints
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/magic-login/', MagicLoginView.as_view(), name='magic_login'),
    path('auth/refresh/', TokenRefreshView.as_view(serializer_class=CustomTokenRefreshSerializer), name='token_refresh'),
    path('auth/me/', CurrentUserView.as_view(), name='auth_me'),
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
