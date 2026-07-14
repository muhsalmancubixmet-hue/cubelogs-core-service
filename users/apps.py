# --------------------------------------------------------------------------------
#       Users Apps Configuration
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.apps import AppConfig

# THIRD PARTY

# APPLICATION SPECIFIC

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        import users.signals  # noqa: F401
