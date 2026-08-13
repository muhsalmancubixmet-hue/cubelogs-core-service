# --------------------------------------------------------------------------------
#       Projects Services - Stories
# --------------------------------------------------------------------------------

from django.db import transaction
from django.core.exceptions import ValidationError

from projects.models import ProjectStory, ProjectStoryMember, ProjectActivity
from projects.constants import FIBONACCI_STORY_POINTS
from projects.utils import generate_story_key
from projects.services.tasks import recalculate_project_progress
from users.models import Employee


@transaction.atomic
def create_story(
    project,
    title,
    description=None,
    acceptance_criteria=None,
    epic=None,
    sprint=None,
    work_type='Feature',
    priority='Medium',
    story_points=0,
    due_date=None,
    labels=None,
    department=None,
    status=None,
    order=0,
    user=None,
    member_ids=None,
):
    """
    Creates a new user story under the given project.
    Always enforces sprint=None for backlog creations.
    Validates Fibonacci story points, due date, epic ownership, and member access.
    """
    if story_points and int(story_points) not in FIBONACCI_STORY_POINTS:
        raise ValidationError({'story_points': f"Story points must be one of {FIBONACCI_STORY_POINTS}"})

    if due_date:
        from datetime import datetime, date
        if isinstance(due_date, str):
            due_date_str = due_date.strip()
            if not due_date_str:
                due_date = None
            else:
                try:
                    due_date_val = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                except ValueError:
                    raise ValidationError({'due_date': 'Invalid date format. Use YYYY-MM-DD.'})
                due_date = due_date_val

    if due_date:
        if project.start_date and due_date < project.start_date:
            raise ValidationError({'due_date': f"Due date ({due_date}) cannot be earlier than project start date ({project.start_date})"})
        if project.end_date and due_date > project.end_date:
            raise ValidationError({'due_date': f"Due date ({due_date}) cannot be later than project target end date ({project.end_date})"})

    if epic and epic.project_id != project.id:
        raise ValidationError({'epic': 'Selected Epic does not belong to this project.'})

    if status is None:
        from projects.services.statuses import get_default_status
        status = get_default_status(project.company)

    story_key = generate_story_key(project)

    project_story = ProjectStory.objects.create(
        project=project,
        epic=epic,
        sprint=sprint,  # For backlog stories, sprint is null
        story_key=story_key,
        title=title,
        description=description,
        acceptance_criteria=acceptance_criteria,
        work_type=work_type or 'Feature',
        priority=priority or 'Medium',
        story_points=int(story_points) if story_points else 0,
        due_date=due_date,
        labels=labels or [],
        department=department,
        status=status,
        order=order,
        created_by=user,
    )

    # Assign members if passed
    if member_ids and isinstance(member_ids, list):
        from projects.models import ProjectMember
        for mid in member_ids:
            pm = ProjectMember.objects.filter(project=project, id=mid).first()
            if not pm:
                pm = ProjectMember.objects.filter(project=project, user_id=mid).first()
            if not pm and Employee.objects.filter(id=mid, organization=project.company, is_active=True).exists():
                pm, _ = ProjectMember.objects.get_or_create(project=project, user_id=mid, defaults={'project_role': 'Developer'})
            if pm:
                ProjectStoryMember.objects.get_or_create(story=project_story, member=pm, defaults={'assigned_by': user})

    # Record ProjectActivity event
    ProjectActivity.objects.create(
        project=project,
        user=user,
        action='Story Created',
        entity_type='Story',
        entity_id=project_story.id,
        details={
            'story_key': story_key,
            'title': title,
            'story_points': story_points,
            'work_type': work_type,
            'priority': priority,
            'due_date': str(due_date) if due_date else None,
        }
    )

    recalculate_project_progress(project)
    return project_story


@transaction.atomic
def delete_story(project_story):
    project = project_story.project
    project_story.delete()
    recalculate_project_progress(project)
