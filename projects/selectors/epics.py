# --------------------------------------------------------------------------------
#       Projects Selectors - Epics
# --------------------------------------------------------------------------------

from core.decorators import has_fine_grained_permission
from projects.models import ProjectEpic
from projects.selectors.projects import projects_for_user


def epics_for_user(user, project=None):
    """
    Returns ProjectEpic queryset accessible by the user.
    """
    if not (user and user.is_authenticated and user.organization):
        return ProjectEpic.objects.none()

    allowed_projects = projects_for_user(user)
    if project:
        allowed_projects = allowed_projects.filter(id=project.id)

    return ProjectEpic.objects.filter(project__in=allowed_projects).select_related('project', 'status', 'created_by')
