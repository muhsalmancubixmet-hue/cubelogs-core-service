# --------------------------------------------------------------------------------
#       Projects Permissions
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import permissions

# APPLICATION SPECIFIC
from core.decorators import has_fine_grained_permission, has_plan_feature


# --------------------------------------------------------------------------------
# IsProjectModuleEnabled: Checks if organization has is_project_enabled subscription flag
# --------------------------------------------------------------------------------
class IsProjectModuleEnabled(permissions.BasePermission):
    """
    Validates that company subscription has is_project_enabled = True.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return has_plan_feature(request.user, 'is_project_enabled')


# --------------------------------------------------------------------------------
# HasProjectCapability: Validates functional capabilities (projects:view, projects:create, etc.)
# --------------------------------------------------------------------------------
class HasProjectCapability(permissions.BasePermission):
    """
    Validates required functional capability specified on view.required_capability or view action.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            return True

        required_cap = getattr(view, 'required_capability', None)
        if not required_cap:
            return True

        return has_fine_grained_permission(user, required_cap)


# --------------------------------------------------------------------------------
# IsProjectObjectOwnerOrLead: Object-level permissions for all Scrum entities
# --------------------------------------------------------------------------------
class IsProjectObjectOwnerOrLead(permissions.BasePermission):
    """
    Object level permission for Projects, Epics, Sprints, Stories, Tasks, Subtasks, Comments, Attachments.
    - SuperAdmin / Project Managers (projects:update capability): full access.
    - Team Lead: access objects in projects where project.team_lead == user.
    - Employee / Member: access objects where user is a project member, story member, or assignee.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'projects:update'):
            return True

        # Extract project reference based on object type
        project = None
        if hasattr(obj, 'company') and hasattr(obj, 'project_manager'):  # Project
            project = obj
        elif hasattr(obj, 'project'):  # ProjectEpic, ProjectSprint, ProjectStory, ProjectMember
            project = obj.project
        elif hasattr(obj, 'story'):  # ProjectTask, ProjectAttachment (if on story)
            project = obj.story.project if obj.story else None
        elif hasattr(obj, 'task'):  # ProjectSubtask, ProjectAttachment (if on task)
            project = obj.task.story.project if obj.task and obj.task.story else None
        elif hasattr(obj, 'epic'):  # ProjectComment (if on epic)
            project = obj.epic.project if obj.epic else None
            if not project and getattr(obj, 'story', None):
                project = obj.story.project
            elif not project and getattr(obj, 'task', None):
                project = obj.task.story.project
            elif not project and getattr(obj, 'subtask', None):
                project = obj.subtask.task.story.project

        if not project or project.company != user.organization:
            return False

        # Team lead access
        if project.team_lead == user:
            return True

        # Assignee check
        if hasattr(obj, 'assigned_to') and obj.assigned_to == user:
            return True

        # Story member check
        if hasattr(obj, 'story_members') and obj.story_members.filter(member__user=user, member__is_active=True).exists():
            return True

        # Member check
        return project.members.filter(user=user, is_active=True).exists()


# --------------------------------------------------------------------------------
# Centralized Project Effective Permission Evaluator
# --------------------------------------------------------------------------------
def get_project_effective_permissions(employee, project=None):
    """
    Computes effective permissions for an employee within a specific project:
    Project Effective Permissions = System Role Permissions + User Extra Permissions - User Denied Permissions + Project Role Permissions
    """
    if not (employee and employee.is_authenticated):
        return []

    cache_key = f"_project_perms_cache_{project.id}" if project else "_project_perms_cache_none"
    if hasattr(employee, cache_key):
        return getattr(employee, cache_key)

    if employee.is_superuser or getattr(employee, 'isSuperAdmin', False) or (employee.role and employee.role.slug in ['super-admin', 'company-admin']) or getattr(employee, 'role_name', None) in ['Super Admin', 'Company Admin', 'Admin']:
        from users.roles import ALL_PERMISSION_KEYS
        setattr(employee, cache_key, ALL_PERMISSION_KEYS)
        return ALL_PERMISSION_KEYS

    base_perms = set(employee.get_effective_permissions()) if hasattr(employee, 'get_effective_permissions') else set(getattr(employee, 'permissions', []))

    # Check if user has global projects capability (any project-related permission globally)
    # or belongs to standard employee roles
    has_global_projects_view = any(p.startswith('project') for p in base_perms)
    if not has_global_projects_view and employee.role:
        if employee.role.slug in ['employee', 'project-manager']:
            has_global_projects_view = True

    if not has_global_projects_view:
        setattr(employee, cache_key, [])
        return []

    if not project:
        res = list(base_perms)
        setattr(employee, cache_key, res)
        return res

    # Strip any permission starting with 'project' from global base_perms
    # to ensure that the project-specific role's permissions are authoritative.
    base_perms = {p for p in base_perms if not p.startswith('project')}

    from projects.models import ProjectMember
    from projects.constants import PROJECT_ROLE_PERMISSIONS

    project_role_perms = set()

    # Project Manager on instance
    if getattr(project, 'project_manager_id', None) == employee.id:
        project_role_perms.update(PROJECT_ROLE_PERMISSIONS.get('Project Manager', []))

    # Team Lead on instance
    if getattr(project, 'team_lead_id', None) == employee.id:
        project_role_perms.update(PROJECT_ROLE_PERMISSIONS.get('Team Lead', []))

    # Look up ProjectMember for employee
    pm = ProjectMember.objects.filter(project=project, user=employee, is_active=True).first()
    if pm and pm.project_role:
        role_key = pm.project_role
        role_perms = PROJECT_ROLE_PERMISSIONS.get(role_key, PROJECT_ROLE_PERMISSIONS.get('Contributor', []))
        project_role_perms.update(role_perms)

    denied_keys = set()
    if hasattr(employee, 'denied_permissions'):
        denied_keys = set(employee.denied_permissions.values_list('key', flat=True))
    if not denied_keys and hasattr(employee, 'denied_permissions_json') and isinstance(employee.denied_permissions_json, list):
        denied_keys = set(employee.denied_permissions_json)

    effective = base_perms.union(project_role_perms).difference(denied_keys)
    res = list(effective)
    setattr(employee, cache_key, res)
    return res


def has_project_permission(employee, permissions, project=None):
    if not (employee and employee.is_authenticated):
        return False
    if employee.is_superuser or getattr(employee, 'isSuperAdmin', False) or (employee.role and employee.role.slug in ['super-admin', 'company-admin']) or getattr(employee, 'role_name', None) in ['Super Admin', 'Company Admin', 'Admin']:
        return True

    effective = get_project_effective_permissions(employee, project)
    if isinstance(permissions, str):
        permissions = [permissions]
    return any(p in effective for p in permissions)


class HasProjectPermission(permissions.BasePermission):
    """
    Custom permission class to validate user permissions in DRF views.
    Checks required_permission globally first. If a project context is resolved,
    checks capability at the project level.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.is_superuser or getattr(user, 'isSuperAdmin', False) or (user.role and user.role.slug in ['super-admin', 'company-admin']) or getattr(user, 'role_name', None) in ['Super Admin', 'Company Admin', 'Admin']:
            return True

        required_permission = getattr(view, 'required_permission', None)
        if not required_permission:
            return True

        project = None

        # 1. Detail view check (kwargs pk)
        if 'pk' in view.kwargs:
            try:
                from django.http import Http404
                model = view.get_queryset().model
                obj = model.objects.filter(pk=view.kwargs['pk']).first()
                if obj:
                    if hasattr(obj, 'company') and hasattr(obj, 'project_manager'):  # Project
                        project = obj
                    elif hasattr(obj, 'project'):
                        project = obj.project
                    elif hasattr(obj, 'story'):
                        project = obj.story.project if obj.story else None
                    elif hasattr(obj, 'task'):
                        project = obj.task.story.project if obj.task and obj.task.story else None
                    elif hasattr(obj, 'epic'):
                        project = obj.epic.project if obj.epic else None
                        if not project and getattr(obj, 'story', None):
                            project = obj.story.project
                        elif not project and getattr(obj, 'task', None):
                            project = obj.task.story.project
                        elif not project and getattr(obj, 'subtask', None):
                            project = obj.subtask.task.story.project
                    
                    if project:
                        from projects.selectors.projects import projects_for_user
                        if not projects_for_user(user).filter(id=project.id).exists():
                            raise Http404("Not found.")
            except Http404:
                raise
            except Exception:
                pass

        # 2. Query parameters check
        if not project:
            project_id = request.query_params.get('project_id') or request.query_params.get('project')
            if project_id:
                from projects.models import Project
                project = Project.objects.filter(id=project_id, company=user.organization).first()

        # 3. Request body check
        if not project and request.data:
            project_id = request.data.get('project') or request.data.get('project_id')
            if project_id:
                from projects.models import Project
                project = Project.objects.filter(id=project_id, company=user.organization).first()

        # 4. Fallback for nested objects in data
        if not project and request.data:
            epic_id = request.data.get('epic')
            if epic_id:
                from projects.models import ProjectEpic
                epic = ProjectEpic.objects.filter(id=epic_id, project__company=user.organization).first()
                if epic:
                    project = epic.project
            story_id = request.data.get('story')
            if story_id:
                from projects.models import ProjectStory
                story = ProjectStory.objects.filter(id=story_id, project__company=user.organization).first()
                if story:
                    project = story.project
            task_id = request.data.get('task')
            if task_id:
                from projects.models import ProjectTask
                task = ProjectTask.objects.filter(id=task_id, story__project__company=user.organization).first()
                if task:
                    project = task.story.project

        # If project is resolved, verify the user has membership/access to this project
        if project:
            from projects.selectors.projects import projects_for_user
            if not projects_for_user(user).filter(id=project.id).exists():
                return False
        else:
            # If this is a create request but no project context could be resolved,
            # allow the request to proceed to the serializer for validation (returning 400 instead of 403).
            if request.method == 'POST':
                from projects.selectors.projects import projects_for_user
                if projects_for_user(user).exists():
                    return True

        # Evaluate capability check with project context
        from core.decorators import has_fine_grained_permission
        return has_fine_grained_permission(user, required_permission, project=project)
