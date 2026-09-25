# --------------------------------------------------------------------------------
#       Users Signals
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

# THIRD PARTY

# APPLICATION SPECIFIC
from users.models import Employee, EmployeeProfile
from subscribers.models import SubscriberAccount

# Note: Generic welcome email signal disabled to avoid duplicate emails during administrative onboarding.
# Administrative employee creation explicitly dispatches single onboarding credential email.

@receiver(post_delete, sender=Employee)
def cleanup_subscriber_account_on_employee_delete(sender, instance, **kwargs):
    if instance.email:
        if not Employee.objects.filter(email=instance.email, isSuperAdmin=True, organization__isnull=False).exists():
            SubscriberAccount.objects.filter(email=instance.email).delete()


@receiver(post_save, sender=Employee)
def sync_employee_to_profile(sender, instance, created, **kwargs):
    """
    Automated dual-write signal:
    Synchronizes HR attributes from Employee model to EmployeeProfile.
    Uses kwargs guard and update_fields optimization.
    """
    if kwargs.get('raw', False):
        return

    field_mappings = {
        'employee_code': instance.employee_code,
        'designation': instance.designation,
        'department': instance.department or '',
        'employment_status': instance.employment_status or 'Active',
        'joining_date': instance.joining_date,
        'last_working_date': instance.last_working_date,
        'phone': instance.phone,
        'profile_photo': instance.profilePhoto,
        'bank_name': instance.bank_name,
        'account_number': instance.account_number,
        'ifsc_code': instance.ifsc_code,
        'account_holder_name': instance.account_holder_name,
        'bank_branch': instance.bank_branch,
        'upi_id': instance.upi_id,
    }

    profile, is_new = EmployeeProfile.objects.get_or_create(
        user=instance,
        defaults={
            'organization': instance.organization,
            **field_mappings,
        }
    )

    if not is_new:
        updates = []
        if profile.organization_id != instance.organization_id:
            profile.organization_id = instance.organization_id
            updates.append('organization')

        for target_field, val in field_mappings.items():
            if getattr(profile, target_field) != val:
                setattr(profile, target_field, val)
                updates.append(target_field)

        if updates:
            profile.save(update_fields=updates)

