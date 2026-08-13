# --------------------------------------------------------------------------------
#       Projects Selectors - Stories
# --------------------------------------------------------------------------------

from core.decorators import has_fine_grained_permission
from projects.models import ProjectStory, ProjectStoryMember
from projects.selectors.projects import projects_for_user


def stories_for_user(user, project=None, sprint=None, epic=None, backlog_only=False):
    """
    Returns stories accessible by the user with optional filtering by project, sprint, epic, or backlog.
    """
    user_projects = projects_for_user(user)
    queryset = ProjectStory.objects.filter(project__in=user_projects)

    if project:
        queryset = queryset.filter(project=project)
    if sprint:
        queryset = queryset.filter(sprint=sprint)
    if epic:
        queryset = queryset.filter(epic=epic)
    if backlog_only:
        queryset = queryset.filter(sprint__isnull=True)

    queryset = queryset.select_related('project', 'epic', 'sprint', 'status', 'created_by')

    if user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'projects:update') or has_fine_grained_permission(user, 'project_stories:create'):
        return queryset

    if user_projects.filter(team_lead=user).exists() or user_projects.filter(project_manager=user).exists():
        return queryset

    # Employee access: stories assigned explicitly or via task assignment
    assigned_story_ids = ProjectStoryMember.objects.filter(
        member__user=user,
        member__is_active=True,
        story__project__in=user_projects
    ).values_list('story_id', flat=True)

    task_story_ids = ProjectStory.objects.filter(
        tasks__assigned_to=user,
        project__in=user_projects
    ).values_list('id', flat=True)

    return queryset.filter(id__in=set(assigned_story_ids) | set(task_story_ids)).distinct()
