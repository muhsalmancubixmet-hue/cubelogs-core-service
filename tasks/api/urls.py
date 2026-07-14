# --------------------------------------------------------------------------------
#       Tasks API Routing
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY

# APPLICATION SPECIFIC


urlpatterns = [
    path('', include('tasks.api.v1.urls')),
]
