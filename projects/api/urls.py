# --------------------------------------------------------------------------------
#       Projects API Router
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY

# APPLICATION SPECIFIC


urlpatterns = [
    path('v1/', include('projects.api.v1.urls')),
    path('', include('projects.api.v1.urls')),
]
