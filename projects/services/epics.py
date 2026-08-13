# --------------------------------------------------------------------------------
#       Projects Services - Epics
# --------------------------------------------------------------------------------

from django.db import transaction
from projects.models import ProjectEpic, ProjectActivity
from projects.utils import generate_epic_key


@transaction.atomic
def create_epic(project, title, description=None, color='#3b82f6', priority='Medium', status=None, start_date=None, due_date=None, user=None):
    """
    Creates a new epic in a project with auto-generated EPIC-001 key.
    """
    epic_key = generate_epic_key(project)

    project_epic = ProjectEpic.objects.create(
        company=project.company,
        project=project,
        title=title,
        key=epic_key,
        description=description,
        color=color,
        priority=priority,
        status=status,
        start_date=start_date,
        due_date=due_date,
        created_by=user,
    )

    if user:
        ProjectActivity.objects.create(
            project=project,
            user=user,
            action="Epic Created",
            entity_type="Epic",
            entity_id=project_epic.id,
            details={"title": project_epic.title, "key": project_epic.key}
        )

    return project_epic
