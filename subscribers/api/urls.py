from django.urls import path, include

urlpatterns = [
    path('', include('subscribers.api.v1.urls')),
]
