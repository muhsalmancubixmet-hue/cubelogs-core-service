# --------------------------------------------------------------------------------
#       Users Signals
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db.models.signals import post_save
from django.dispatch import receiver

# THIRD PARTY

# APPLICATION SPECIFIC
from users.models import Employee
from users.api.v1.services import UserService

@receiver(post_save, sender=Employee)
def send_employee_registration_email_signal(sender, instance, created, **kwargs):
    if created:
        UserService.send_welcome_email(instance)
