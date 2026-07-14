# --------------------------------------------------------------------------------
#       Company API Routing
# --------------------------------------------------------------------------------

from django.urls import path, include

urlpatterns = [
    path('', include('company.api.v1.urls')),
]
