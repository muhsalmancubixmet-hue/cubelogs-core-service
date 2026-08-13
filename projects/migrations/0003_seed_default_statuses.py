# --------------------------------------------------------------------------------
#       Projects Data Migration — Seed Default Statuses
# --------------------------------------------------------------------------------
#
# This migration seeds mandatory system statuses (Pending, Completed) for every
# existing Organization in the database. It is idempotent via get_or_create().
#
# New organizations should call projects.services.statuses.initialize_default_statuses()
# during the organization creation workflow.
# --------------------------------------------------------------------------------

from django.db import migrations

DEFAULT_STATUSES = [
    {
        'name': 'Pending',
        'code': 'pending',
        'category': 'pending',
        'scope': 'all',
        'order': 0,
        'is_default': True,
        'is_system': True,
        'is_active': True,
    },
    {
        'name': 'In Progress',
        'code': 'in_progress',
        'category': 'active',
        'scope': 'all',
        'order': 1,
        'is_default': False,
        'is_system': False,
        'is_active': True,
    },
    {
        'name': 'Completed',
        'code': 'completed',
        'category': 'completed',
        'scope': 'all',
        'order': 2,
        'is_default': False,
        'is_system': True,
        'is_active': True,
    },
]


def seed_default_statuses(apps, schema_editor):
    Organization = apps.get_model('core', 'Organization')
    ProjectStatusOption = apps.get_model('projects', 'ProjectStatusOption')

    for org in Organization.objects.all():
        for s in DEFAULT_STATUSES:
            ProjectStatusOption.objects.get_or_create(
                company=org,
                code=s['code'],
                defaults={
                    'name': s['name'],
                    'category': s['category'],
                    'scope': s['scope'],
                    'order': s['order'],
                    'is_default': s['is_default'],
                    'is_system': s['is_system'],
                    'is_active': s['is_active'],
                }
            )


def reverse_seed(apps, schema_editor):
    # Reverse: only delete non-system statuses we seeded (safe rollback)
    # System statuses are kept to avoid breaking references.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0002_projectstatusoption_projectstorymember_and_more'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_default_statuses, reverse_seed),
    ]
