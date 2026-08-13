# --------------------------------------------------------------------------------
#       Projects Services - Statuses
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db import transaction

# THIRD PARTY

# APPLICATION SPECIFIC


# Default system status definitions — always seeded for every company
DEFAULT_SYSTEM_STATUSES = [
    {
        'name': 'To Do',
        'code': 'pending',
        'category': 'pending',
        'scope': 'all',
        'order': 0,
        'progress_percentage': 0,
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
        'progress_percentage': 50,
        'is_default': False,
        'is_system': False,
        'is_active': True,
    },
    {
        'name': 'Review',
        'code': 'review',
        'category': 'active',
        'scope': 'all',
        'order': 2,
        'progress_percentage': 75,
        'is_default': False,
        'is_system': False,
        'is_active': True,
    },
    {
        'name': 'Testing',
        'code': 'testing',
        'category': 'active',
        'scope': 'all',
        'order': 3,
        'progress_percentage': 85,
        'is_default': False,
        'is_system': False,
        'is_active': True,
    },
    {
        'name': 'Done',
        'code': 'completed',
        'category': 'completed',
        'scope': 'all',
        'order': 4,
        'progress_percentage': 100,
        'is_default': False,
        'is_system': True,
        'is_active': True,
    },
]


# --------------------------------------------------------------------------------
# initialize_default_statuses: Seeds mandatory system statuses for a company
# --------------------------------------------------------------------------------
@transaction.atomic
def initialize_default_statuses(company):
    """
    Idempotent — seeds Pending, In Progress, and Completed status options
    for the given company. Safe to call multiple times; uses get_or_create().

    Call this:
      - When a new Organization is created
      - When Project Management is enabled for an existing organization
    """
    from projects.models import ProjectStatusOption

    for s in DEFAULT_SYSTEM_STATUSES:
        ProjectStatusOption.objects.get_or_create(
            company=company,
            code=s['code'],
            defaults={
                'name': s['name'],
                'category': s['category'],
                'scope': s['scope'],
                'order': s['order'],
                'progress_percentage': s['progress_percentage'],
                'is_default': s['is_default'],
                'is_system': s['is_system'],
                'is_active': s['is_active'],
            }
        )


# --------------------------------------------------------------------------------
# get_default_status: Returns the default 'Pending' status for a company
# --------------------------------------------------------------------------------
def get_default_status(company=None):
    """
    Returns the default (Pending) ProjectStatusOption for the given company.
    If not found, seeds statuses first.
    """
    from projects.models import ProjectStatusOption

    if company:
        status = ProjectStatusOption.objects.filter(
            company=company,
            code='pending',
            is_active=True,
        ).first()

        if not status:
            initialize_default_statuses(company)
            status = ProjectStatusOption.objects.filter(
                company=company,
                code='pending',
                is_active=True,
            ).first()
        if status:
            return status

    return ProjectStatusOption.objects.filter(code='pending', is_active=True).first()
