# --------------------------------------------------------------------------------
#       Projects Services - Sprints
# --------------------------------------------------------------------------------

from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from projects.models import ProjectSprint, ProjectSprintEvent, ProjectStory, ProjectActivity
from projects.selectors.sprints import active_sprint_for_project


@transaction.atomic
def create_sprint(project, name, goal=None, start_date=None, end_date=None, capacity=0, user=None):
    """
    Creates a new sprint in planning status for a project with validations.
    """
    if not name or not str(name).strip():
        raise ValidationError("Sprint name is required.")

    if not goal or not str(goal).strip():
        raise ValidationError("Sprint goal is required.")

    if not start_date:
        raise ValidationError("Start date is required.")

    if not end_date:
        raise ValidationError("End date is required.")

    # Convert string dates if passed as strings
    import datetime
    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

    if end_date <= start_date:
        raise ValidationError("End date must be after start date.")

    if project.start_date and start_date < project.start_date:
        raise ValidationError(f"Sprint start date ({start_date}) cannot be before project start date ({project.start_date}).")

    if project.end_date and end_date > project.end_date:
        raise ValidationError(f"Sprint end date ({end_date}) cannot be after project end date ({project.end_date}).")

    if capacity is not None and int(capacity) < 0:
        raise ValidationError("Capacity cannot be negative.")

    sprint = ProjectSprint.objects.create(
        project=project,
        name=str(name).strip(),
        goal=str(goal).strip() if goal else None,
        start_date=start_date,
        end_date=end_date,
        capacity=int(capacity) if capacity else 0,
        status='planning',
        created_by=user,
    )

    if user:
        ProjectActivity.objects.create(
            project=project,
            user=user,
            action="Sprint Created",
            entity_type="Sprint",
            entity_id=sprint.id,
            details={"sprint_name": sprint.name, "status": "planning"}
        )
    return sprint


@transaction.atomic
def start_sprint(sprint, user=None):
    """
    Starts a sprint. Enforces at least 1 story, valid dates, and single active sprint per project.
    Uses select_for_update to prevent concurrent start_sprint race conditions.
    """
    # Lock both sprint and project rows to atomically enforce the single-active-sprint invariant
    locked_sprint = ProjectSprint.objects.select_for_update().get(id=sprint.id)
    from projects.models import Project
    locked_project = Project.objects.select_for_update().get(id=locked_sprint.project_id)

    if locked_sprint.status == 'active':
        return locked_sprint

    if locked_sprint.status == 'completed':
        raise ValidationError("Completed sprints cannot be started.")

    if ProjectStory.objects.filter(sprint=locked_sprint).count() == 0:
        raise ValidationError(f"Cannot start sprint '{locked_sprint.name}': No stories have been added to this sprint.")

    import datetime
    if not locked_sprint.start_date:
        locked_sprint.start_date = timezone.now().date()
    if not locked_sprint.end_date:
        locked_sprint.end_date = locked_sprint.start_date + datetime.timedelta(days=14)

    if locked_sprint.end_date <= locked_sprint.start_date:
        raise ValidationError("End date must be after start date.")

    active = active_sprint_for_project(locked_project)
    if active and active.id != locked_sprint.id:
        raise ValidationError(f"Cannot start sprint '{locked_sprint.name}': Sprint '{active.name}' is currently active.")

    locked_sprint.status = 'active'
    locked_sprint.started_at = timezone.now()
    locked_sprint.save()

    # Record historical sprint_started event
    stories = list(locked_sprint.stories.all())
    total_pts = sum(s.story_points for s in stories)
    completed_pts = sum(s.story_points for s in stories if s.status and s.status.category == 'completed')

    ProjectSprintEvent.objects.create(
        sprint=locked_sprint,
        event_type='sprint_started',
        total_points=total_pts,
        completed_points=completed_pts,
    )

    if user:
        ProjectActivity.objects.create(
            project=locked_project,
            user=user,
            action="Sprint Started",
            entity_type="Sprint",
            entity_id=locked_sprint.id,
            details={"sprint_name": locked_sprint.name, "committed_points": total_pts}
        )

    # Transition Project status to Active when sprint starts
    if locked_project.status and locked_project.status.category == 'pending':
        from projects.models import ProjectStatusOption
        active_status = ProjectStatusOption.objects.filter(
            company=locked_project.company,
            category='active',
            is_active=True
        ).first()
        if active_status and locked_project.status_id != active_status.id:
            locked_project.status = active_status
            locked_project.save(update_fields=['status'])

    sprint.refresh_from_db()
    return sprint


@transaction.atomic
def complete_sprint(sprint, move_uncompleted_to_sprint=None, user=None):
    """
    Completes a sprint. Moves uncompleted stories to backlog or specified target sprint.
    Uses select_for_update for idempotency under concurrent complete requests.
    """
    # Lock sprint row to ensure idempotent completion
    locked_sprint = ProjectSprint.objects.select_for_update().get(id=sprint.id)

    if locked_sprint.status == 'completed':
        sprint.refresh_from_db()
        return sprint

    stories = list(locked_sprint.stories.all())
    uncompleted = [s for s in stories if not (s.status and s.status.category == 'completed')]

    for story in uncompleted:
        story.sprint = move_uncompleted_to_sprint
        story.save()

    locked_sprint.status = 'completed'
    locked_sprint.completed_at = timezone.now()
    locked_sprint.save()

    total_pts = sum(s.story_points for s in stories)
    completed_pts = sum(s.story_points for s in stories if s.status and s.status.category == 'completed')

    ProjectSprintEvent.objects.create(
        sprint=locked_sprint,
        event_type='sprint_completed',
        total_points=total_pts,
        completed_points=completed_pts,
    )

    if user:
        ProjectActivity.objects.create(
            project=locked_sprint.project,
            user=user,
            action="Sprint Completed",
            entity_type="Sprint",
            entity_id=locked_sprint.id,
            details={
                "sprint_name": locked_sprint.name,
                "completed_points": completed_pts,
                "moved_uncompleted_stories": len(uncompleted)
            }
        )

    sprint.refresh_from_db()
    return sprint


@transaction.atomic
def delete_sprint(sprint, user=None):
    """
    Deletes a planning sprint. Moves assigned stories to Product Backlog.
    Rejects active, completed, or cancelled sprints.
    """
    locked_sprint = ProjectSprint.objects.select_for_update().get(id=sprint.id)
    if locked_sprint.status != 'planning':
        raise ValidationError("Only Planning Sprints can be deleted.")

    sprint_name = locked_sprint.name
    project = locked_sprint.project
    sprint_id = locked_sprint.id

    assigned_stories = list(locked_sprint.stories.all())
    for story in assigned_stories:
        story.sprint = None
        story.save(update_fields=['sprint'])

    locked_sprint.delete()

    if user:
        ProjectActivity.objects.create(
            project=project,
            user=user,
            action="Sprint Deleted",
            entity_type="Sprint",
            entity_id=sprint_id,
            details={
                "sprint_name": sprint_name,
                "deleted_sprint_id": sprint_id,
                "returned_story_count": len(assigned_stories)
            }
        )
    return True


@transaction.atomic
def cancel_sprint(sprint, reason=None, move_incomplete_to='backlog', target_sprint_id=None, user=None):
    """
    Cancels a planning or active sprint.
    Preserves completed stories for historical reporting, moves incomplete stories to backlog or target planning sprint.
    """
    locked_sprint = ProjectSprint.objects.select_for_update().get(id=sprint.id)
    if locked_sprint.status == 'completed':
        raise ValidationError("Completed sprints cannot be cancelled.")

    if not reason or not str(reason).strip():
        raise ValidationError("Cancellation reason is required.")

    target_sprint = None
    if move_incomplete_to == 'sprint' and target_sprint_id:
        if int(target_sprint_id) == locked_sprint.id:
            raise ValidationError("Target sprint cannot be the same sprint.")
        target_sprint = ProjectSprint.objects.filter(id=target_sprint_id).first()
        if not target_sprint or target_sprint.project_id != locked_sprint.project_id or target_sprint.project.company_id != locked_sprint.project.company_id:
            raise ValidationError("This Sprint cannot be cancelled because the target Sprint is invalid.")
        if target_sprint.status != 'planning':
            raise ValidationError("Target sprint must be in planning status.")

    stories = list(locked_sprint.stories.all())
    incomplete_stories = []

    if locked_sprint.status == 'planning':
        incomplete_stories = stories
    else:
        incomplete_stories = [s for s in stories if not (s.status and s.status.category == 'completed')]

    for story in incomplete_stories:
        story.sprint = target_sprint
        story.save(update_fields=['sprint'])

    locked_sprint.status = 'cancelled'
    locked_sprint.cancelled_at = timezone.now()
    locked_sprint.cancelled_by = user
    locked_sprint.cancellation_reason = str(reason).strip()
    locked_sprint.save()

    if locked_sprint.status == 'active':
        completed_pts = sum(s.story_points for s in stories if s.status and s.status.category == 'completed')
        total_pts = sum(s.story_points for s in stories)
        ProjectSprintEvent.objects.create(
            sprint=locked_sprint,
            event_type='sprint_completed',
            total_points=total_pts,
            completed_points=completed_pts,
        )

    if user:
        ProjectActivity.objects.create(
            project=locked_sprint.project,
            user=user,
            action="Sprint Cancelled",
            entity_type="Sprint",
            entity_id=locked_sprint.id,
            details={
                "sprint_name": locked_sprint.name,
                "reason": locked_sprint.cancellation_reason,
                "incomplete_moved": len(incomplete_stories),
                "destination": target_sprint.name if target_sprint else "Product Backlog"
            }
        )
    return locked_sprint


@transaction.atomic
def reopen_sprint(sprint, user=None):
    """
    Reopens a cancelled sprint back to planning status.
    Rejects completed or active sprints.
    """
    locked_sprint = ProjectSprint.objects.select_for_update().get(id=sprint.id)
    if locked_sprint.status != 'cancelled':
        raise ValidationError("Only Cancelled Sprints can be reopened.")

    locked_sprint.status = 'planning'
    locked_sprint.cancelled_at = None
    locked_sprint.cancelled_by = None
    locked_sprint.cancellation_reason = None
    locked_sprint.save()

    if user:
        ProjectActivity.objects.create(
            project=locked_sprint.project,
            user=user,
            action="Sprint Reopened",
            entity_type="Sprint",
            entity_id=locked_sprint.id,
            details={"sprint_name": locked_sprint.name}
        )
    return locked_sprint


@transaction.atomic
def assign_story_to_sprint(story, sprint, user=None):
    """
    Assigns a story to a sprint or moves back to backlog (sprint=None).
    """
    story.sprint = sprint
    story.save()
    if user:
        ProjectActivity.objects.create(
            project=story.project,
            user=user,
            action="Story Assigned to Sprint",
            entity_type="Story",
            entity_id=story.id,
            details={"story_title": story.title, "sprint_name": sprint.name if sprint else None}
        )
    return story
