from django.urls import path, include

urlpatterns = [
    path('v1/', include('storage_billing.api.v1.urls')),
    path('', include('storage_billing.api.v1.urls')),
]
