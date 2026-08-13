# --------------------------------------------------------------------------------
#       Projects Selectors - Tasks
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY

# APPLICATION SPECIFIC
from projects.models import ProjectTask
from projects.selectors.projects import projects_for_user


# --------------------------------------------------------------------------------
# tasks_for_user: Returns role-filtered ProjectTask queryset for the authenticated user
# --------------------------------------------------------------------------------
def tasks_for_user(user, story=None, project=None):
    """
    Returns tasks accessible by the user:
    - SuperAdmin / Project Manager (project_tasks:view_all): all tasks in all company projects.
    - Active Project Member (any role): all tasks within the projects they are members of.
      This mirrors backlog/stories visibility — all members see all project tasks.
    """
    user_projects = projects_for_user(user)
    queryset = ProjectTask.objects.filter(story__project__in=user_projects)

    if story:
        queryset = queryset.filter(story=story)
    elif project:
        queryset = queryset.filter(story__project=project)

    # All project members can see all tasks in their accessible projects
    # (mirrors how stories/backlog are fully visible to all project members)
    return queryset.select_related(
        'story', 'story__project', 'status', 'assigned_to'
    ).distinct()
