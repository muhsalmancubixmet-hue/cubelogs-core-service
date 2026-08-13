# --------------------------------------------------------------------------------
#       Projects Services - Projects
# --------------------------------------------------------------------------------

from django.db import transaction
from django.core.exceptions import ValidationError
from projects.models import Project, ProjectMember
from projects.services.statuses import initialize_default_statuses, get_default_status
from users.models import Employee


@transaction.atomic
def create_project(company, name, description=None, project_type='Internal', project_manager=None, team_lead=None, status=None, start_date=None, end_date=None, user=None, member_ids=None, draft_token=None):
    """
    Atomic creation of project and automatic initial membership setup.
    Require project_manager, start_date, and end_date.
    Automatically initializes default status options for the company idempotently.
    Promotes temporary draft attachments linked via draft_token to the new project atomically.
    """
    # Initialize default statuses idempotently for organization
    initialize_default_statuses(company)

    if status is None:
        status = get_default_status(company)

    if not project_manager and user:
        project_manager = user

    if not project_manager:
        raise ValidationError("Project Manager is required.")

    if project_manager.organization != company:
        raise ValidationError("Selected Project Manager must belong to the project organization.")
    if not project_manager.is_active:
        raise ValidationError("Selected Project Manager is not an active employee.")

    if team_lead:
        if team_lead.organization != company:
            raise ValidationError("Selected Team Lead must belong to the project organization.")
        if not team_lead.is_active:
            raise ValidationError("Selected Team Lead is not an active employee.")

    from django.utils import timezone
    from datetime import timedelta

    if not start_date:
        start_date = timezone.now().date()
    if not end_date:
        end_date = start_date + timedelta(days=30)

    project = Project.objects.create(
        company=company,
        name=name,
        description=description,
        project_type=project_type,
        project_manager=project_manager,
        team_lead=team_lead,
        status=status,
        start_date=start_date,
        end_date=end_date,
        created_by=user,
    )

    if project_manager:
        ProjectMember.objects.get_or_create(
            project=project, user=project_manager,
            defaults={'project_role': 'Project Manager', 'is_active': True}
        )
    if team_lead:
        ProjectMember.objects.get_or_create(
            project=project, user=team_lead,
            defaults={'project_role': 'Team Lead', 'is_active': True}
        )
    if user and user != project_manager and user != team_lead:
        ProjectMember.objects.get_or_create(
            project=project, user=user,
            defaults={'project_role': 'Contributor', 'is_active': True}
        )

    # Optional multi-select members default to Contributor role (not designation)
    if member_ids and isinstance(member_ids, list):
        for member_id in member_ids:
            try:
                emp = Employee.objects.get(id=member_id, organization=company, is_active=True)
                ProjectMember.objects.get_or_create(
                    project=project, user=emp,
                    defaults={'project_role': 'Contributor', 'is_active': True}
                )
            except Employee.DoesNotExist:
                continue

    # Promote temporary draft attachments linked to this draft_token
    if draft_token:
        from projects.models import ProjectAttachment
        ProjectAttachment.objects.filter(
            draft_token=draft_token,
            uploaded_by=user if user else project_manager,
            company=company,
            is_temporary=True
        ).update(
            project=project,
            draft_token=None,
            is_temporary=False,
            expires_at=None
        )

    return project
