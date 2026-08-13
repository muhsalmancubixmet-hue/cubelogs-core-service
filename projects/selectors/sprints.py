# --------------------------------------------------------------------------------
#       Projects Selectors - Sprints
# --------------------------------------------------------------------------------

from projects.models import ProjectSprint
from projects.selectors.projects import projects_for_user


def sprints_for_user(user, project=None):
    """
    Returns ProjectSprint queryset accessible by the user.
    """
    if not (user and user.is_authenticated and user.organization):
        return ProjectSprint.objects.none()

    allowed_projects = projects_for_user(user)
    if project:
        allowed_projects = allowed_projects.filter(id=project.id)

    return ProjectSprint.objects.filter(project__in=allowed_projects, is_deleted=False).select_related('project', 'created_by')


def active_sprint_for_project(project):
    """
    Returns the single active sprint for a project, or None.
    """
    return ProjectSprint.objects.filter(project=project, status='active').first()
