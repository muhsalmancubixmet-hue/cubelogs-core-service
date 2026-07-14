# --------------------------------------------------------------------------------
#       Tasks App Config
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.apps import AppConfig

# THIRD PARTY

# APPLICATION SPECIFIC


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'
