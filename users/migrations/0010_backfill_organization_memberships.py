from django.db import migrations
from django.utils import timezone


def backfill_organization_memberships(apps, schema_editor):
    Employee = apps.get_model('users', 'Employee')
    OrganizationMembership = apps.get_model('users', 'OrganizationMembership')
    today = timezone.now().date()

    tenant_employees = Employee.objects.filter(organization__isnull=False)
    created_count = 0

    for emp in tenant_employees:
        status = emp.employment_status
        lwd = emp.last_working_date

        is_active_in_org = True
        if status == 'Deactivated' or not emp.is_active:
            is_active_in_org = False
        elif status in ['Resigned', 'Terminated']:
            if lwd is None or lwd <= today:
                is_active_in_org = False

        OrganizationMembership.objects.get_or_create(
            user_id=emp.id,
            organization_id=emp.organization_id,
            defaults={
                'employee_code': emp.employee_code,
                'department': emp.department or '',
                'designation': emp.designation,
                'role_id': emp.role_id,
                'employment_status': emp.employment_status,
                'joining_date': emp.joining_date,
                'last_working_date': emp.last_working_date,
                'is_active_in_org': is_active_in_org,
            }
        )
        created_count += 1


def reverse_backfill_organization_memberships(apps, schema_editor):
    # Non-destructive no-op for data migration 0010.
    # Schema rollback to 0008 via migration 0009 drops the OrganizationMembership table cleanly.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_organizationmembership'),
    ]

    operations = [
        migrations.RunPython(
            backfill_organization_memberships,
            reverse_code=reverse_backfill_organization_memberships
        ),
    ]
