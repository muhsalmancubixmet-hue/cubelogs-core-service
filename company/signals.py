# --------------------------------------------------------------------------------
#       Company Signals
# --------------------------------------------------------------------------------

from django.db.models.signals import post_save
from django.dispatch import receiver

from company.models import Lead
from company.api.v1.services import CRMService

@receiver(post_save, sender=Lead)
def create_tenant_workspace_signal(sender, instance, created, **kwargs):
    if created:
        CRMService.provision_tenant_workspace(instance)
