# --------------------------------------------------------------------------------
#       Projects Services - Tasks
# --------------------------------------------------------------------------------

from django.db import transaction
from django.utils import timezone

from projects.models import ProjectTask, ProjectStory, Project, ProjectActivity, ProjectStatusOption
from projects.utils import generate_task_key


def recalculate_story_progress(project_story: ProjectStory):
    """
    Recalculates a story's progress based on its child task status categories.
    Automatically marks Story as Completed when ALL tasks reach completed status.
    """
    story_tasks = project_story.tasks.select_related('status').all()
    if not story_tasks.exists():
        project_story.progress = 0.0
    else:
        completed_tasks = sum(1 for t in story_tasks if t.status and t.status.category == 'completed')
        total_count = story_tasks.count()
        project_story.progress = round((completed_tasks / total_count) * 100.0, 2)

        # Automatic Story Completion Automation Rule
        if total_count > 0 and completed_tasks == total_count:
            completed_status = ProjectStatusOption.objects.filter(
                company=project_story.project.company,
                category='completed',
                is_active=True
            ).first()
            if completed_status and project_story.status_id != completed_status.id:
                project_story.status = completed_status
                project_story.save(update_fields=['status', 'progress'])
            else:
                project_story.save(update_fields=['progress'])
        else:
            project_story.save(update_fields=['progress'])

    recalculate_project_progress(project_story.project)


def recalculate_project_progress(project: Project):
    """
    Recalculates project progress based on average of child story progress values.
    Automatically transitions Project status to Completed when ALL stories finish.
    Automatically transitions Project status to Active when a Sprint is started.
    """
    project_stories = project.stories.select_related('status').all()
    if not project_stories.exists():
        project.progress = 0.0
        project.save(update_fields=['progress'])
    else:
        completed_stories = sum(1 for s in project_stories if s.status and s.status.category == 'completed')
        total_count = project_stories.count()
        total_story_progress = sum(story.progress for story in project_stories)
        project.progress = round(total_story_progress / total_count, 2)

        # Automatic Project Status Transition Rule
        if total_count > 0 and (completed_stories == total_count or project.progress >= 100.0):
            completed_status = ProjectStatusOption.objects.filter(
                company=project.company,
                category='completed',
                is_active=True
            ).first()
            if completed_status and project.status_id != completed_status.id:
                project.status = completed_status
                project.save(update_fields=['status', 'progress'])
            else:
                project.save(update_fields=['progress'])
        else:
            from projects.models import ProjectSprint
            has_active_sprint = ProjectSprint.objects.filter(project=project, status='active').exists()
            if has_active_sprint and project.status and project.status.category == 'pending':
                active_status = ProjectStatusOption.objects.filter(
                    company=project.company,
                    category='active',
                    is_active=True
                ).first()
                if active_status and project.status_id != active_status.id:
                    project.status = active_status
                    project.save(update_fields=['status', 'progress'])
                else:
                    project.save(update_fields=['progress'])
            else:
                project.save(update_fields=['progress'])


@transaction.atomic
def create_task(story, title, description=None, assigned_to=None, priority='Medium', status=None, estimated_hours=0.0, logged_hours=0.0, start_date=None, due_date=None, user=None):
    """
    Creates a ProjectTask under a ProjectStory with TASK-001 key.
    """
    from projects.services.statuses import get_default_status

    if status is not None and not hasattr(status, 'category'):
        if isinstance(status, (int, str)) and (isinstance(status, int) or str(status).isdigit()):
            status_obj = ProjectStatusOption.objects.filter(id=int(status)).first()
        else:
            status_obj = ProjectStatusOption.objects.filter(company=story.project.company, name__iexact=str(status)).first()
        status = status_obj

    if status is None:
        status = get_default_status(story.project.company)

    completed_at = timezone.now() if (status and status.category == 'completed') else None
    task_key = generate_task_key(story)

    project_task = ProjectTask.objects.create(
        story=story,
        task_key=task_key,
        title=title,
        description=description,
        assigned_to=assigned_to,
        priority=priority,
        status=status,
        estimated_hours=estimated_hours,
        logged_hours=logged_hours,
        start_date=start_date,
        due_date=due_date,
        completed_at=completed_at,
        created_by=user,
    )

    if user:
        ProjectActivity.objects.create(
            project=story.project,
            user=user,
            action="Task Created",
            entity_type="Task",
            entity_id=project_task.id,
            details={"task_key": project_task.task_key, "title": project_task.title}
        )

    recalculate_story_progress(story)
    return project_task


@transaction.atomic
def update_task_status(project_task, new_status_option, user=None):
    # Lock task and its parent story to ensure sequential progress recalculation
    from projects.models import ProjectTask as PT, ProjectStory
    locked_task = PT.objects.select_for_update().select_related('story').get(id=project_task.id)
    ProjectStory.objects.select_for_update().filter(id=locked_task.story_id)

    old_status_option = locked_task.status
    if old_status_option == new_status_option:
        return locked_task

    locked_task.status = new_status_option
    if new_status_option and new_status_option.category == 'completed':
        locked_task.completed_at = timezone.now()
    else:
        locked_task.completed_at = None

    locked_task.save(update_fields=['status', 'completed_at', 'updated_at'])

    if user:
        ProjectActivity.objects.create(
            project=locked_task.story.project,
            user=user,
            action="Task Status Updated",
            entity_type="Task",
            entity_id=locked_task.id,
            details={
                "task_key": locked_task.task_key,
                "old_status": old_status_option.name if old_status_option else None,
                "new_status": new_status_option.name if new_status_option else None,
            }
        )

    recalculate_story_progress(locked_task.story)
    return locked_task


@transaction.atomic
def delete_task(task_id, user=None):
    """
    Deletes a ProjectTask and recalculates parent story progress.
    """
    task = ProjectTask.objects.select_related('story__project').filter(id=task_id).first()
    if not task:
        return None

    story = task.story
    task_key = task.task_key
    project = story.project

    task.delete()
    recalculate_story_progress(story)

    if user:
        ProjectActivity.objects.create(
            project=project,
            user=user,
            action="Task Deleted",
            entity_type="Task",
            entity_id=task_id,
            details={"task_key": task_key}
        )

    return True
