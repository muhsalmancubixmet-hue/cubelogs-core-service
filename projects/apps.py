# --------------------------------------------------------------------------------
#       Projects App Config
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.apps import AppConfig

# THIRD PARTY

# APPLICATION SPECIFIC


# --------------------------------------------------------------------------------
# ProjectsConfig: Application configuration for the Projects module
# --------------------------------------------------------------------------------
class ProjectsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'projects'
    verbose_name = 'Project Management'
