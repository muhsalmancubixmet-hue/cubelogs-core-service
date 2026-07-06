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
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import logout

from api.views import backoffice_view, stripe_webhook, backoffice_login_view, backoffice_logout_view

def custom_admin_login(request, extra_context=None):
    if request.user.is_authenticated and not request.user.is_staff:
        logout(request)
    return admin.site.login(request, extra_context)

urlpatterns = [
    path('admin/login/', custom_admin_login),
    path('admin/', admin.site.urls),
    path('backoffice/', backoffice_view, name='backoffice'),
    path('backoffice/login/', backoffice_login_view, name='backoffice_login'),
    path('backoffice/logout/', backoffice_logout_view, name='backoffice_logout'),
    path('backoffice/stripe-webhook/', stripe_webhook, name='stripe-webhook'),
    path('webhook/', stripe_webhook, name='stripe-webhook-root'),
    path('api/', include('api.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


