# --------------------------------------------------------------------------------
#       Users Signals
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

# THIRD PARTY

# APPLICATION SPECIFIC
from users.models import Employee
from subscribers.models import SubscriberAccount
from users.api.v1.services import UserService

# Note: Generic welcome email signal disabled to avoid duplicate emails during administrative onboarding.
# Administrative employee creation explicitly dispatches single onboarding credential email.

@receiver(post_delete, sender=Employee)
def cleanup_subscriber_account_on_employee_delete(sender, instance, **kwargs):
    if instance.email:
        if not Employee.objects.filter(email=instance.email, isSuperAdmin=True, organization__isnull=False).exists():
            SubscriberAccount.objects.filter(email=instance.email).delete()
