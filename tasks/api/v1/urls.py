# --------------------------------------------------------------------------------
#       Tasks API v1 Routing
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY
from rest_framework.routers import DefaultRouter

# APPLICATION SPECIFIC
from tasks.api.v1.views import TaskViewSet


router = DefaultRouter()
router.register('tasks', TaskViewSet, basename='task')

urlpatterns = [
    path('', include(router.urls)),
]
