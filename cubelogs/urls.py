"""
URL configuration for cubelogs project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import logout
from django.views.generic import RedirectView
from django.views.static import serve

from users.api.v1.views import (
    backoffice_view, backoffice_login_view, backoffice_logout_view,
    backoffice_manifest_view, backoffice_sw_view
)
from subscribers.api.v1.views import stripe_webhook

def custom_admin_login(request, extra_context=None):
    if request.user.is_authenticated and not request.user.is_staff:
        logout(request)
    return admin.site.login(request, extra_context)

from django.core.exceptions import PermissionDenied

def secure_media_block_view(request, path):
    raise PermissionDenied("Direct access to project attachments is forbidden.")

urlpatterns = [
    path('admin/login/', custom_admin_login),
    path('admin/', admin.site.urls),
    path('', backoffice_view, name='backoffice'),
    path('backoffice/', RedirectView.as_view(url='/', permanent=True)),
    path('backoffice/login/', backoffice_login_view, name='backoffice_login'),
    path('backoffice/logout/', backoffice_logout_view, name='backoffice_logout'),
    path('manifest.json', backoffice_manifest_view, name='backoffice_manifest'),
    path('sw.js', backoffice_sw_view, name='backoffice_sw'),
    path('backoffice/stripe-webhook/', stripe_webhook, name='stripe-webhook'),
    path('webhook/', stripe_webhook, name='stripe-webhook-root'),
    path('api/', include('users.api.urls')),
    path('api/', include('attendance.api.urls')),
    path('api/', include('company.api.urls')),
    path('api/', include('subscribers.api.urls')),
    path('api/', include('projects.api.urls')),
    re_path(r'^media/project_attachments/(?P<path>.*)$', secure_media_block_view),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
]


