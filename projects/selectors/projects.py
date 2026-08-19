# --------------------------------------------------------------------------------
#       Projects Selectors - Projects
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY

# APPLICATION SPECIFIC
from core.decorators import has_fine_grained_permission
from projects.models import Project


# --------------------------------------------------------------------------------
# projects_for_user: Returns role-filtered Project queryset for the authenticated user
# --------------------------------------------------------------------------------
def projects_for_user(user):
    """
    Returns the queryset of Project objects accessible by the given user within their company.
    - SuperAdmin / users with 'projects:update' capability can access all company projects.
    - Team Lead: access projects where team_lead == user.
    - Employee / Member: access projects where user is an active project member.
    """
    if not (user and user.is_authenticated and user.organization):
        return Project.objects.none()

    company_projects = Project.objects.filter(company=user.organization, is_deleted=False)

    if user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'projects:update'):
        return company_projects

    pm_projects = company_projects.filter(project_manager=user)
    lead_projects = company_projects.filter(team_lead=user)
    member_projects = company_projects.filter(members__user=user, members__is_active=True)

    return (pm_projects | lead_projects | member_projects).distinct()
