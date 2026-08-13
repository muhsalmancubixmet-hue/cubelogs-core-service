# --------------------------------------------------------------------------------
#       Projects Services - Subtasks
# --------------------------------------------------------------------------------

from django.db import transaction
from django.utils import timezone
from projects.models import ProjectSubtask, ProjectActivity, ProjectStatusOption
from projects.services.tasks import recalculate_story_progress


@transaction.atomic
def create_subtask(task, title, assigned_to=None, estimated_hours=0.0, user=None):
    """
    Creates a subtask under a ProjectTask.
    """
    project_subtask = ProjectSubtask.objects.create(
        task=task,
        title=title,
        assigned_to=assigned_to,
        estimated_hours=estimated_hours,
        created_by=user,
    )

    if user:
        ProjectActivity.objects.create(
            project=task.story.project,
            user=user,
            action="Subtask Created",
            entity_type="Subtask",
            entity_id=project_subtask.id,
            details={"title": project_subtask.title, "task_key": task.task_key}
        )

    return project_subtask


@transaction.atomic
def toggle_subtask_completion(project_subtask, is_completed, user=None):
    """
    Updates completion status of a subtask.
    When ALL subtasks become completed, automatically updates parent Task status to Completed.
    When a subtask is reopened, reverts Task status if needed.
    Recalculates parent Story and Project progress.
    """
    project_subtask.is_completed = bool(is_completed)
    project_subtask.save()

    task = project_subtask.task
    total_subtasks = task.subtasks.count()
    completed_subtasks = task.subtasks.filter(is_completed=True).count()
    percentage = round((completed_subtasks / total_subtasks * 100)) if total_subtasks > 0 else 0

    # Auto Task Completion Automation Rule
    if total_subtasks > 0 and completed_subtasks == total_subtasks:
        completed_status = ProjectStatusOption.objects.filter(
            company=task.story.project.company,
            category='completed',
            is_active=True
        ).first()
        if completed_status and task.status_id != completed_status.id:
            task.status = completed_status
            task.completed_at = timezone.now()
            task.save(update_fields=['status', 'completed_at', 'updated_at'])
    elif total_subtasks > 0 and completed_subtasks < total_subtasks and task.status and task.status.category == 'completed':
        active_status = ProjectStatusOption.objects.filter(
            company=task.story.project.company,
            category__in=['active', 'todo'],
            is_active=True
        ).order_by('order').first()
        if active_status and task.status_id != active_status.id:
            task.status = active_status
            task.completed_at = None
            task.save(update_fields=['status', 'completed_at', 'updated_at'])

    # Recalculate story progress
    recalculate_story_progress(task.story)

    # Record ProjectActivity event
    ProjectActivity.objects.create(
        project=task.story.project,
        user=user,
        action='Subtask Completed' if is_completed else 'Subtask Reopened',
        entity_type='Subtask',
        entity_id=project_subtask.id,
        details={
            'title': project_subtask.title,
            'is_completed': is_completed,
            'task_key': task.task_key,
        }
    )

    task_progress = {
        'completed_subtasks': completed_subtasks,
        'total_subtasks': total_subtasks,
        'percentage': percentage,
    }

    return project_subtask, task_progress


@transaction.atomic
def delete_subtask(subtask_id, user=None):
    """
    Deletes a subtask and recalculates parent story progress.
    """
    subtask = ProjectSubtask.objects.select_related('task__story__project').filter(id=subtask_id).first()
    if not subtask:
        return None

    task = subtask.task
    story = task.story
    subtask.delete()

    recalculate_story_progress(story)

    if user:
        ProjectActivity.objects.create(
            project=story.project,
            user=user,
            action="Subtask Deleted",
            entity_type="Subtask",
            entity_id=subtask_id,
            details={"task_key": task.task_key}
        )

    return True
