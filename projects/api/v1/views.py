# --------------------------------------------------------------------------------
#       Projects Views
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q, Sum, Avg, Count, ProtectedError
from django.core.exceptions import ValidationError
from django.utils import timezone

# THIRD PARTY
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response

# APPLICATION SPECIFIC
from core.models import AuditLog
from core.permissions import ActionPermissionMixin, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission
from core.decorators import has_fine_grained_permission
from projects.permissions import HasProjectPermission

from projects.models import (
    Project, ProjectMember, ProjectEpic, ProjectSprint, ProjectStory,
    ProjectStoryMember, ProjectTask, ProjectSubtask, ProjectStatusOption,
    ProjectComment, ProjectAttachment, ProjectActivity,
    ProjectRetrospective, ProjectRetrospectiveItem
)
from projects.selectors.projects import projects_for_user
from projects.selectors.stories import stories_for_user
from projects.selectors.tasks import tasks_for_user
from projects.selectors.epics import epics_for_user
from projects.selectors.sprints import sprints_for_user, active_sprint_for_project
from projects.selectors.analytics import calculate_sprint_burndown, calculate_project_velocity

from projects.services.projects import create_project
from projects.services.stories import create_story, delete_story
from projects.services.tasks import create_task, update_task_status, delete_task, recalculate_story_progress
from projects.services.statuses import initialize_default_statuses, get_default_status
from projects.services.epics import create_epic
from projects.services.sprints import (
    create_sprint, start_sprint, complete_sprint, cancel_sprint, reopen_sprint, assign_story_to_sprint, delete_sprint
)
from projects.services.subtasks import create_subtask, toggle_subtask_completion
from projects.services.comments import create_comment, create_attachment

from projects.api.v1.serializers import (
    ProjectStatusOptionSerializer,
    ProjectListSerializer, ProjectDetailSerializer, ProjectCreateSerializer, ProjectUpdateSerializer,
    ProjectMemberSerializer, ProjectMemberCreateSerializer, ProjectMemberUpdateSerializer,
    ProjectEpicSerializer, ProjectSprintSerializer,
    ProjectStorySerializer, ProjectStoryMemberSerializer, ProjectStoryMemberCreateSerializer,
    ProjectTaskSerializer, ProjectSubtaskSerializer,
    ProjectCommentSerializer, ProjectAttachmentSerializer,
    ProjectTaskStatusUpdateSerializer,
    ProjectRetrospectiveSerializer, ProjectRetrospectiveItemSerializer
)


# --------------------------------------------------------------------------------
# ProjectStatusOptionViewSet
# --------------------------------------------------------------------------------
class ProjectStatusOptionViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasRequiredPermission],
    }
    serializer_class = ProjectStatusOptionSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = [
                'projects.board.view',
                'projects.task.view',
                'projects.overview.view',
                'projects.sprint.view',
                'projects.story.view',
                'projects:view',
                'project_statuses:view',
                'project_statuses:create',
                'project_statuses:update',
                'project_statuses:delete'
            ]
        elif self.action == 'create':
            self.required_permission = ['projects:update', 'project_statuses:create']
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['projects:update', 'project_statuses:update']
        elif self.action == 'destroy':
            self.required_permission = ['projects:delete', 'project_statuses:delete']
        else:
            self.required_permission = ['projects.board.view', 'projects:view', 'project_statuses:view']
        return super().get_permissions()

    def get_queryset(self):
        return ProjectStatusOption.objects.filter(
            company=self.request.user.organization,
            is_active=True,
        )

    def list(self, request, *args, **kwargs):
        if not self.get_queryset().exists() and request.user.organization:
            initialize_default_statuses(request.user.organization)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or has_fine_grained_permission(request.user, 'project_statuses:create')):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(company=request.user.organization)
        return Response(ProjectStatusOptionSerializer(instance).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or has_fine_grained_permission(request.user, 'project_statuses:update')):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)
        instance = self.get_object()
        if instance.is_system and 'name' in request.data:
            return Response({'detail': 'System statuses cannot be renamed.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        is_super = request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False)
        has_perm = is_super or has_fine_grained_permission(request.user, ['project_statuses:delete', 'projects:delete', 'projects:create'])
        if not has_perm:
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        instance = self.get_object()
        if instance.is_system:
            return Response({'detail': 'System statuses cannot be deleted.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from projects.services.statuses import get_default_status
            default_status = get_default_status(request.user.organization or instance.company)
            if default_status and default_status.id == instance.id:
                default_status = ProjectStatusOption.objects.filter(
                    company=instance.company, is_active=True
                ).exclude(id=instance.id).first()

            with transaction.atomic():
                if default_status:
                    Project.objects.filter(status=instance).update(status=default_status)
                    ProjectEpic.objects.filter(status=instance).update(status=default_status)
                    ProjectStory.objects.filter(status=instance).update(status=default_status)
                    ProjectTask.objects.filter(status=instance).update(status=default_status)
                instance.delete()
        except Exception as e:
            return Response({'detail': f'Cannot delete status option: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------
# ProjectViewSet
# --------------------------------------------------------------------------------
class ProjectViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'members': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'member_detail': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'stories': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'employees': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'backlog': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'board': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'velocity': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'eligible_employees': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'eligible_members': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'eligible_story_members': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'overview': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }

    def get_permissions(self):
        if self.action == 'board':
            self.required_permission = ['projects.board.view', 'projects:view']
        elif self.action == 'backlog':
            self.required_permission = ['projects.backlog.view', 'projects.sprint.view', 'projects:view']
        elif self.action == 'velocity':
            self.required_permission = ['projects.sprint.view', 'projects.board.view', 'projects.overview.view', 'projects:view']
        elif self.action in ['list', 'retrieve', 'overview', 'employees', 'eligible_employees', 'eligible_members', 'eligible_story_members', 'my_tasks']:
            self.required_permission = [
                'projects.overview.view',
                'projects.board.view',
                'projects.sprint.view',
                'projects.backlog.view',
                'projects.my_tasks.view',
                'projects.task.view',
                'projects.story.view',
                'projects:view',
                'projects:create',
                'projects:update',
                'projects:delete',
                'projects:members_manage'
            ]
        elif self.action == 'create':
            self.required_permission = 'projects:create'
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['projects:update']
        elif self.action == 'destroy':
            self.required_permission = ['projects:delete']
        elif self.action in ['members', 'member_detail']:
            self.required_permission = ['projects.members.manage', 'projects.members.view', 'projects:members_manage']
        else:
            self.required_permission = ['projects.overview.view', 'projects:view']
        return super().get_permissions()

    def get_queryset(self):
        qs = projects_for_user(self.request.user)
        qs = qs.select_related('project_manager', 'team_lead', 'status').prefetch_related('sprints')
        if self.action == 'retrieve':
            qs = qs.prefetch_related(
                'members__user',
                'epics__project',
                'epics__status',
                'epics__created_by'
            )
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ProjectListSerializer
        elif self.action == 'retrieve':
            return ProjectDetailSerializer
        elif self.action == 'create':
            return ProjectCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ProjectUpdateSerializer
        return ProjectListSerializer

    def create(self, request, *args, **kwargs):
        if not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or has_fine_grained_permission(request.user, 'projects:create')):
            return Response({'detail': 'Permission denied: projects:create required.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ProjectCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        initialize_default_statuses(request.user.organization)
        status_option = get_default_status(request.user.organization)

        # Auto-assign Project Manager to logged-in user if not provided
        pm_user = serializer.validated_data.get('project_manager') or request.user

        member_ids = request.data.get('members') or []
        draft_token = request.data.get('draft_token')

        project = create_project(
            company=request.user.organization,
            name=serializer.validated_data['name'],
            description=serializer.validated_data.get('description'),
            project_type=serializer.validated_data.get('project_type', 'Internal'),
            status=status_option,
            start_date=serializer.validated_data.get('start_date'),
            end_date=serializer.validated_data.get('end_date'),
            project_manager=pm_user,
            team_lead=serializer.validated_data.get('team_lead'),
            user=request.user,
            member_ids=member_ids,
            draft_token=draft_token,
        )

        return Response(ProjectDetailSerializer(project).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        user = request.user

        is_super = user.is_superuser or getattr(user, 'isSuperAdmin', False)
        is_pm = project.project_manager == user
        has_delete_perm = has_fine_grained_permission(user, 'projects:delete')

        if not (is_super or (is_pm and has_delete_perm) or has_delete_perm):
            return Response(
                {'detail': 'Permission denied: projects:delete permission required.'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            self.perform_destroy(project)
        except ProtectedError as e:
            return Response(
                {'detail': f'Cannot delete project because it contains protected resources: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'detail': f'Failed to delete project: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get', 'post'], url_path='members')
    def members(self, request, pk=None):
        project = self.get_object()
        if request.method == 'GET':
            return Response(ProjectMemberSerializer(project.members.all(), many=True).data)

        if request.method == 'POST':
            if project.status and getattr(project.status, 'category', None) == 'completed':
                return Response({'detail': 'Project is completed and read-only. Cannot modify project members.'}, status=status.HTTP_403_FORBIDDEN)

            if not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or has_fine_grained_permission(request.user, 'projects:members_manage', project=project) or has_fine_grained_permission(request.user, 'projects.members.manage', project=project) or project.team_lead == request.user or project.project_manager == request.user):
                return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

            requested_role = request.data.get('project_role') or 'Contributor'
            valid_roles = ['Project Manager', 'Team Lead', 'Contributor', 'Developer', 'QA Engineer', 'Designer', 'Product Owner', 'Viewer']
            if requested_role not in valid_roles:
                requested_role = 'Contributor'

            user_ids = request.data.get('user_ids') or request.data.get('members')
            if isinstance(user_ids, (str, int)):
                user_ids = [user_ids]
            if user_ids and isinstance(user_ids, list):
                from users.models import Employee
                added_members = []
                for uid in user_ids:
                    emp = Employee.objects.filter(id=uid, organization=project.company, is_active=True).first()
                    if emp:
                        pm, _ = ProjectMember.objects.update_or_create(
                            project=project,
                            user=emp,
                            defaults={'project_role': requested_role, 'is_active': True}
                        )
                        added_members.append(pm)
                if not added_members:
                    return Response({'detail': 'No valid same-company active employees were selected.'}, status=status.HTTP_400_BAD_REQUEST)
                return Response(ProjectMemberSerializer(added_members, many=True).data, status=status.HTTP_201_CREATED)

            serializer = ProjectMemberCreateSerializer(data=request.data, context={'request': request, 'project': project})
            serializer.is_valid(raise_exception=True)
            member, _ = ProjectMember.objects.update_or_create(
                project=project,
                user=serializer.validated_data['user'],
                defaults={
                    'project_role': serializer.validated_data.get('project_role') or requested_role,
                    'department': serializer.validated_data.get('department'),
                    'is_active': serializer.validated_data.get('is_active', True),
                }
            )
            return Response(ProjectMemberSerializer(member).data, status=status.HTTP_201_CREATED)

        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['get'], url_path='eligible-employees')
    def eligible_employees(self, request, pk=None):
        project = self.get_object()
        existing_user_ids = project.members.values_list('user_id', flat=True)
        from users.models import Employee
        employees = Employee.objects.filter(organization=request.user.organization, is_active=True).exclude(id__in=existing_user_ids)
        data = [{
            'id': emp.id,
            'user_id': emp.id,
            'name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
            'email': emp.email,
            'role': emp.role_title,
            'department': emp.department.name if getattr(emp, 'department', None) else None,
        } for emp in employees]
        return Response(data)

    @action(detail=False, methods=['get'], url_path='eligible-members')
    def eligible_members(self, request):
        if not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or 
                has_fine_grained_permission(request.user, 'projects:view') or 
                has_fine_grained_permission(request.user, 'projects:create') or 
                has_fine_grained_permission(request.user, 'projects:update') or 
                has_fine_grained_permission(request.user, 'projects:members_manage') or
                has_fine_grained_permission(request.user, 'admin:employees')):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        if not request.user.organization and not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False)):
            return Response([], status=status.HTTP_200_OK)

        from users.models import Employee
        qs = Employee.objects.filter(is_active=True)
        if request.user.organization:
            qs = qs.filter(organization=request.user.organization)

        search = request.query_params.get('search', '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(designation__icontains=search)
            )

        employees = qs.order_by('first_name', 'last_name', 'email')
        data = [{
            'id': emp.id,
            'user_id': emp.id,
            'name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
            'full_name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
            'email': emp.email,
            'profilePhoto': getattr(emp, 'profilePhoto', None),
            'profile_image': getattr(emp, 'profilePhoto', None),
            'role': emp.role_title or emp.designation or 'Employee',
            'designation': emp.designation or 'Employee',
            'is_active': emp.is_active,
            'department': emp.department.name if getattr(emp, 'department', None) else None,
        } for emp in employees]
        return Response(data)

    @action(detail=True, methods=['get'], url_path='eligible-story-members')
    def eligible_story_members(self, request, pk=None):
        project = self.get_object()
        from users.models import Employee
        from projects.models import ProjectMember

        qs = Employee.objects.filter(organization=project.company, is_active=True)

        search = request.query_params.get('search', '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(designation__icontains=search)
            )

        employees = qs.order_by('first_name', 'last_name', 'email')
        project_members = ProjectMember.objects.filter(project=project, is_active=True)
        project_member_user_ids = set(project_members.values_list('user_id', flat=True))
        member_role_map = {pm.user_id: pm.project_role for pm in project_members}

        data = []
        for emp in employees:
            is_pm = (emp.id == project.project_manager_id)
            is_tl = (emp.id == project.team_lead_id)
            is_proj_mem = emp.id in project_member_user_ids
            proj_role = member_role_map.get(emp.id, emp.designation or emp.role_title or 'Employee')
            data.append({
                'id': emp.id,
                'user_id': emp.id,
                'full_name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                'name': f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                'email': emp.email,
                'profile_image': getattr(emp, 'profilePhoto', None),
                'profilePhoto': getattr(emp, 'profilePhoto', None),
                'designation': emp.designation or emp.role_title or 'Employee',
                'project_role': proj_role,
                'is_project_manager': is_pm,
                'is_team_lead': is_tl,
                'is_project_member': is_proj_mem,
            })

        page_param = request.query_params.get('page')
        page_size_param = request.query_params.get('page_size')
        if page_param or page_size_param:
            try:
                page = max(1, int(page_param or 1))
            except ValueError:
                page = 1
            try:
                page_size = max(1, int(page_size_param or 5))
            except ValueError:
                page_size = 5

            count = len(data)
            start = (page - 1) * page_size
            end = start + page_size
            results = data[start:end]
            return Response({
                'count': count,
                'page': page,
                'page_size': page_size,
                'total_pages': (count + page_size - 1) // page_size if page_size > 0 else 1,
                'results': results,
            })

        return Response({
            'count': len(data),
            'page': 1,
            'page_size': len(data) or 5,
            'total_pages': 1,
            'results': data,
        })

    @action(detail=True, methods=['get'], url_path='backlog')
    def backlog(self, request, pk=None):
        project = self.get_object()
        backlog_stories = stories_for_user(request.user, project=project, backlog_only=True)
        return Response(ProjectStorySerializer(backlog_stories, many=True).data)

    @action(detail=True, methods=['get'], url_path='board')
    def board(self, request, pk=None):
        project = self.get_object()
        sprint_id = request.query_params.get('sprint_id')
        
        if sprint_id:
            sprint = get_object_or_404(ProjectSprint, id=sprint_id, project=project)
        else:
            sprint = active_sprint_for_project(project)

        # Fetch ALL stories for the active sprint in this project (not user-filtered).
        # The board must show every sprint story and task to every project member, not
        # only those the requesting user is directly assigned to.
        if sprint:
            board_stories = (
                ProjectStory.objects
                .filter(project=project, sprint=sprint)
                .select_related('epic', 'sprint', 'status', 'created_by')
                .prefetch_related('tasks__subtasks', 'tasks__status', 'tasks__assigned_to', 'story_members')
            )
        else:
            board_stories = ProjectStory.objects.none()

        statuses = ProjectStatusOption.objects.filter(company=project.company, is_active=True).order_by('order')

        story_groups = []
        for story in board_stories:
            story_tasks = story.tasks.filter(is_deleted=False)
            story_groups.append({
                'id': story.id,
                'story_key': story.story_key,
                'title': story.title,
                'status': story.status.name if story.status else 'To Do',
                'story_points': story.story_points,
                'tasks': ProjectTaskSerializer(story_tasks, many=True).data
            })

        board_stories_list = list(board_stories)
        columns = []
        for st in statuses:
            col_stories = [s for s in board_stories_list if s.status_id == st.id]
            columns.append({
                'status': ProjectStatusOptionSerializer(st).data,
                'stories': ProjectStorySerializer(col_stories, many=True).data
            })

        return Response({
            'project_id': project.id,
            'project_name': project.name,
            'active_sprint': ProjectSprintSerializer(sprint).data if sprint else None,
            'columns': columns,
            'story_groups': story_groups,
        })

    @action(detail=True, methods=['get'], url_path='velocity')
    def velocity(self, request, pk=None):
        project = self.get_object()
        velocity_data = calculate_project_velocity(project)
        return Response(velocity_data)

    @action(detail=True, methods=['get'], url_path='employees')
    def employees(self, request, pk=None):
        project = self.get_object()
        from users.models import Employee
        employees = Employee.objects.filter(
            organization=project.company,
            employment_status='Active'
        ).values('id', 'first_name', 'last_name', 'email', 'designation')
        return Response(list(employees))

    @action(detail=True, methods=['get'], url_path='overview')
    def overview(self, request, pk=None):
        project = self.get_object()
        user = request.user

        is_admin = user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'projects:update') or has_fine_grained_permission(user, 'projects:delete')
        is_pm = (project.project_manager == user) or has_fine_grained_permission(user, 'projects:members_manage')
        is_tl = (project.team_lead == user)
        is_member = project.members.filter(user=user, is_active=True).exists()

        if not (is_admin or is_pm or is_tl or is_member):
            return Response({'detail': 'Permission denied: You do not belong to this project.'}, status=status.HTTP_403_FORBIDDEN)

        if is_admin:
            user_role = 'ADMIN'
        elif is_pm:
            user_role = 'PROJECT_MANAGER'
        elif is_tl:
            user_role = 'TEAM_LEAD'
        else:
            member_obj = project.members.filter(user=user, is_active=True).first()
            raw_role = (member_obj.project_role if member_obj else user.role_title or user.designation or 'Developer').strip().lower()
            if 'manager' in raw_role or 'pm' in raw_role:
                user_role = 'PROJECT_MANAGER'
            elif 'lead' in raw_role:
                user_role = 'TEAM_LEAD'
            elif 'qa' in raw_role:
                user_role = 'QA'
            elif 'designer' in raw_role:
                user_role = 'DESIGNER'
            else:
                user_role = 'DEVELOPER'

        active_sprint = project.sprints.filter(status='active').first()
        active_sprint_data = None
        if active_sprint:
            sprint_stories = active_sprint.stories.all()
            total_sp = sum(s.story_points for s in sprint_stories)
            completed_sp = sum(s.story_points for s in sprint_stories if s.status and s.status.category == 'completed')
            sprint_tasks = ProjectTask.objects.filter(story__sprint=active_sprint)
            completed_sprint_tasks = sprint_tasks.filter(status__category='completed').count()
            total_sprint_tasks = sprint_tasks.count()
            
            from datetime import date
            remaining_days = None
            if active_sprint.end_date:
                remaining = (active_sprint.end_date - date.today()).days
                remaining_days = max(0, remaining)

            sprint_progress = round((completed_sp / total_sp * 100), 1) if total_sp > 0 else (round((completed_sprint_tasks / total_sprint_tasks * 100), 1) if total_sprint_tasks > 0 else 0)

            active_sprint_data = {
                'id': active_sprint.id,
                'name': active_sprint.name,
                'goal': active_sprint.goal or '',
                'status': active_sprint.status,
                'start_date': active_sprint.start_date,
                'end_date': active_sprint.end_date,
                'remaining_days': remaining_days,
                'stories_count': sprint_stories.count(),
                'tasks_count': total_sprint_tasks,
                'completed_tasks_count': completed_sprint_tasks,
                'total_story_points': total_sp,
                'completed_story_points': completed_sp,
                'progress_percent': sprint_progress
            }

        all_stories = project.stories.all()
        total_stories = all_stories.count()
        completed_stories = all_stories.filter(status__category='completed').count()

        all_tasks = ProjectTask.objects.filter(story__project=project)
        total_tasks = all_tasks.count()
        completed_tasks = all_tasks.filter(status__category='completed').count()
        pending_tasks = total_tasks - completed_tasks

        overall_completion = round(project.progress, 1) if project.progress else (
            round((completed_stories / total_stories * 100), 1) if total_stories > 0 else 0
        )

        from datetime import date
        health = "On Track"
        if project.end_date and project.end_date < date.today() and overall_completion < 100:
            health = "Delayed"
        elif overall_completion < 30 and project.start_date and (date.today() - project.start_date).days > 30:
            health = "At Risk"

        my_tasks = all_tasks.filter(assigned_to=user)
        my_assigned_tasks_count = my_tasks.count()
        my_completed_tasks_count = my_tasks.filter(status__category='completed').count()
        my_in_progress_tasks_count = my_tasks.filter(status__category='active').count()
        my_blocked_tasks_count = my_tasks.filter(Q(status__category='pending') | Q(status__name__icontains='blocked')).count()
        my_pending_review_tasks_count = my_tasks.filter(status__name__icontains='review').count()
        
        my_logged_hours = sum(t.logged_hours for t in my_tasks)
        my_estimated_hours = sum(t.estimated_hours for t in my_tasks)
        my_completion_percent = round((my_completed_tasks_count / my_assigned_tasks_count * 100), 1) if my_assigned_tasks_count > 0 else 0

        my_assigned_stories_count = all_stories.filter(story_members__member__user=user).distinct().count()

        project_members = project.members.filter(is_active=True).select_related('user')
        team_members_data = []
        for pm in project_members:
            emp = pm.user
            full_name = f"{emp.first_name} {emp.last_name}".strip() or emp.email
            team_members_data.append({
                'id': pm.id,
                'user_id': emp.id,
                'name': full_name,
                'email': emp.email,
                'profile_photo': getattr(emp, 'profilePhoto', None),
                'designation': emp.designation or emp.role_title or 'Employee',
                'project_role': pm.project_role or 'Developer',
                'department': pm.department or getattr(emp, 'department', None),
                'is_online': getattr(emp, 'is_active', True)
            })

        recent_activities = ProjectActivity.objects.filter(project=project).select_related('user').order_by('-created_at')[:10]
        recent_activity_data = []
        for act in recent_activities:
            user_name = (f"{act.user.first_name} {act.user.last_name}".strip() or act.user.email) if act.user else "System"
            user_photo = getattr(act.user, 'profilePhoto', None) if act.user else None
            recent_activity_data.append({
                'id': act.id,
                'action': act.action,
                'entity_type': act.entity_type,
                'entity_id': act.entity_id,
                'details': act.details,
                'created_at': act.created_at,
                'user_name': user_name,
                'user_photo': user_photo,
            })

        my_recent_tasks = my_tasks.select_related('status', 'story').order_by('-id')[:5]
        my_recent_tasks_data = []
        for t in my_recent_tasks:
            my_recent_tasks_data.append({
                'id': t.id,
                'task_key': t.task_key or f"TSK-{t.id}",
                'title': t.title,
                'priority': t.priority,
                'status': ProjectStatusOptionSerializer(t.status).data if t.status else None,
                'due_date': t.due_date,
                'story_id': t.story_id,
                'story_title': t.story.title if t.story else '',
            })

        attachments = ProjectAttachment.objects.filter(story__project=project) | ProjectAttachment.objects.filter(task__story__project=project)
        doc_attachments = [{
            'id': att.id,
            'file_name': att.file_name,
            'file_url': att.file.url if att.file else None,
            'file_size': att.file_size,
            'created_at': att.created_at
        } for att in attachments.distinct()[:5]]

        pm_name = (f"{project.project_manager.first_name} {project.project_manager.last_name}".strip() or project.project_manager.email) if project.project_manager else None
        tl_name = (f"{project.team_lead.first_name} {project.team_lead.last_name}".strip() or project.team_lead.email) if project.team_lead else None

        return Response({
            'project_header': {
                'id': project.id,
                'name': project.name,
                'key': project.key,
                'description': project.description,
                'project_type': project.project_type,
                'status': ProjectStatusOptionSerializer(project.status).data if project.status else None,
                'start_date': project.start_date,
                'end_date': project.end_date,
                'progress': overall_completion,
                'health': health,
                'project_manager_name': pm_name,
                'team_lead_name': tl_name,
                'active_sprint_name': active_sprint_data['name'] if active_sprint_data else None,
            },
            'summary_cards': {
                'total_stories': total_stories,
                'completed_stories': completed_stories,
                'total_tasks': total_tasks,
                'completed_tasks': completed_tasks,
                'my_assigned_tasks': my_assigned_tasks_count,
                'pending_tasks': pending_tasks,
                'sprint_progress': active_sprint_data['progress_percent'] if active_sprint_data else 0,
                'overall_completion': overall_completion,
            },
            'current_sprint': active_sprint_data,
            'my_contribution': {
                'assigned_stories_count': my_assigned_stories_count,
                'assigned_tasks_count': my_assigned_tasks_count,
                'completed_tasks_count': my_completed_tasks_count,
                'in_progress_tasks_count': my_in_progress_tasks_count,
                'blocked_tasks_count': my_blocked_tasks_count,
                'pending_review_tasks_count': my_pending_review_tasks_count,
                'logged_hours': my_logged_hours,
                'estimated_hours': my_estimated_hours,
                'completion_percent': my_completion_percent,
            },
            'team_members': team_members_data,
            'recent_activity': recent_activity_data,
            'my_recent_tasks': my_recent_tasks_data,
            'project_progress': {
                'stories_completed': completed_stories,
                'total_stories': total_stories,
                'tasks_completed': completed_tasks,
                'total_tasks': total_tasks,
                'sprint_completion': active_sprint_data['progress_percent'] if active_sprint_data else 0,
                'overall_completion': overall_completion,
            },
            'documentation': {
                'description': project.description or 'No project description provided.',
                'acceptance_criteria': [s.acceptance_criteria for s in all_stories if s.acceptance_criteria],
                'attachments': doc_attachments,
                'links': []
            },
            'user_role': user_role,
        })


# --------------------------------------------------------------------------------
# ProjectEpicViewSet
# --------------------------------------------------------------------------------
class ProjectEpicViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectEpicSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = [
                'projects.epic.view',
                'projects.overview.view',
                'projects:view',
                'project_epics:view',
                'project_epics:create',
                'project_epics:update',
                'project_epics:delete'
            ]
        elif self.action == 'create':
            self.required_permission = ['projects.epic.create', 'project_epics:create']
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['projects.epic.edit', 'project_epics:update']
        elif self.action == 'destroy':
            self.required_permission = ['projects.epic.delete', 'project_epics:delete']
        else:
            self.required_permission = ['projects.epic.view', 'projects:view', 'project_epics:view']
        return super().get_permissions()

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        project = get_object_or_404(Project, id=project_id, company=self.request.user.organization) if project_id else None
        return epics_for_user(self.request.user, project=project)

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project')
        project = get_object_or_404(Project, id=project_id, company=request.user.organization)
        epic = create_epic(
            project=project,
            title=request.data.get('title'),
            description=request.data.get('description'),
            color=request.data.get('color', '#3b82f6'),
            priority=request.data.get('priority', 'Medium'),
            start_date=request.data.get('start_date'),
            due_date=request.data.get('due_date'),
            user=request.user,
        )
        return Response(ProjectEpicSerializer(epic).data, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------------
# ProjectSprintViewSet: Sprint lifecycle operations & burndown endpoint
# --------------------------------------------------------------------------------
class ProjectSprintViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'start': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'complete': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'cancel': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'reopen': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'burndown': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'add_stories': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'remove_story': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectSprintSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'burndown']:
            self.required_permission = [
                'projects.sprint.view',
                'projects.board.view',
                'projects.overview.view',
                'projects:view',
                'project_sprints:view',
                'project_sprints:manage',
                'project_sprints:create',
                'project_sprints:update',
                'project_sprints:delete'
            ]
        elif self.action == 'create':
            self.required_permission = ['projects.sprint.create', 'project_sprints:create', 'project_sprints:manage']
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['projects.sprint.edit', 'project_sprints:update', 'project_sprints:manage']
        elif self.action == 'destroy':
            self.required_permission = ['projects.sprint.delete', 'project_sprints:delete', 'project_sprints:manage']
        elif self.action in ['start', 'complete', 'cancel', 'reopen', 'add_stories', 'remove_story']:
            self.required_permission = [
                'projects.sprint.start',
                'projects.sprint.complete',
                'projects.sprint.cancel',
                'projects.sprint.reopen',
                'projects.sprint.move_stories',
                'projects.sprint.edit',
                'project_sprints:manage'
            ]
        else:
            self.required_permission = ['projects.sprint.view', 'projects:view', 'project_sprints:view']
        return super().get_permissions()

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        project = get_object_or_404(Project, id=project_id, company=self.request.user.organization) if project_id else None
        return sprints_for_user(self.request.user, project=project)

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project')
        project = get_object_or_404(Project, id=project_id, company=request.user.organization)
        try:
            sprint = create_sprint(
                project=project,
                name=request.data.get('name'),
                goal=request.data.get('goal'),
                start_date=request.data.get('start_date'),
                end_date=request.data.get('end_date'),
                capacity=request.data.get('capacity', 0),
                user=request.user,
            )
            return Response(ProjectSprintSerializer(sprint).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages if hasattr(e, 'messages') else str(e)}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        sprint = self.get_object()
        if sprint.status == 'completed':
            return Response({'detail': 'Completed Sprints cannot be deleted.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            sprint_name = sprint.name
            delete_sprint(sprint, user=request.user)
            return Response({'detail': f"Sprint '{sprint_name}' deleted successfully."}, status=status.HTTP_200_OK)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages if hasattr(e, 'messages') else str(e)}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='start')
    def start(self, request, pk=None):
        sprint = self.get_object()
        try:
            started = start_sprint(sprint, user=request.user)
            return Response(ProjectSprintSerializer(started).data)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages if hasattr(e, 'messages') else str(e)}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='add-stories')
    def add_stories(self, request, pk=None):
        sprint = self.get_object()
        story_ids = request.data.get('story_ids', [])
        if not isinstance(story_ids, list) or not story_ids:
            return Response({'detail': 'story_ids must be a non-empty list.'}, status=status.HTTP_400_BAD_REQUEST)

        stories = ProjectStory.objects.filter(id__in=story_ids, project=sprint.project)
        updated_count = stories.update(sprint=sprint)

        if request.user:
            ProjectActivity.objects.create(
                project=sprint.project,
                user=request.user,
                action="Stories Added to Sprint",
                entity_type="Sprint",
                entity_id=sprint.id,
                details={"sprint_name": sprint.name, "added_story_count": updated_count}
            )

        return Response({
            'detail': f'Successfully added {updated_count} story/stories to sprint.',
            'sprint': ProjectSprintSerializer(sprint).data
        })

    @action(detail=True, methods=['post'], url_path='remove-story')
    def remove_story(self, request, pk=None):
        sprint = self.get_object()
        story_id = request.data.get('story_id')
        story = get_object_or_404(ProjectStory, id=story_id, project=sprint.project, sprint=sprint)
        story.sprint = None
        story.save()

        if request.user:
            ProjectActivity.objects.create(
                project=sprint.project,
                user=request.user,
                action="Story Removed from Sprint",
                entity_type="Sprint",
                entity_id=sprint.id,
                details={"sprint_name": sprint.name, "story_title": story.title}
            )

        return Response({
            'detail': f'Successfully removed story from sprint.',
            'sprint': ProjectSprintSerializer(sprint).data
        })

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        sprint = self.get_object()
        move_to_sprint_id = request.data.get('move_uncompleted_to_sprint_id')
        target_sprint = ProjectSprint.objects.filter(id=move_to_sprint_id, project=sprint.project).first() if move_to_sprint_id else None
        completed = complete_sprint(sprint, move_uncompleted_to_sprint=target_sprint, user=request.user)
        return Response(ProjectSprintSerializer(completed).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        sprint = self.get_object()
        reason = request.data.get('reason')
        move_incomplete_to = request.data.get('move_incomplete_to', 'backlog')
        target_sprint_id = request.data.get('target_sprint_id')
        try:
            cancelled = cancel_sprint(
                sprint=sprint,
                reason=reason,
                move_incomplete_to=move_incomplete_to,
                target_sprint_id=target_sprint_id,
                user=request.user
            )
            return Response(ProjectSprintSerializer(cancelled).data)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages if hasattr(e, 'messages') else str(e)}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reopen')
    def reopen(self, request, pk=None):
        sprint = self.get_object()
        if sprint.status == 'completed':
            return Response({'detail': 'Completed Sprints cannot be reopened.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            reopened = reopen_sprint(sprint, user=request.user)
            return Response(ProjectSprintSerializer(reopened).data)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages if hasattr(e, 'messages') else str(e)}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='burndown')
    def burndown(self, request, pk=None):
        sprint = self.get_object()
        burndown_data = calculate_sprint_burndown(sprint)
        return Response(burndown_data)


# --------------------------------------------------------------------------------
# ProjectStoryViewSet
# --------------------------------------------------------------------------------
class ProjectStoryViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'move_sprint': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectStorySerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = [
                'projects.story.view',
                'projects.backlog.view',
                'projects.board.view',
                'projects.sprint.view',
                'projects:view',
                'project_stories:view',
                'project_stories:create',
                'project_stories:update',
                'project_stories:delete'
            ]
        elif self.action == 'create':
            self.required_permission = ['projects.story.create', 'projects.backlog.create', 'project_stories:create']
        elif self.action in ['update', 'partial_update']:
            self.required_permission = [
                'projects.story.edit',
                'projects.story.status',
                'projects.backlog.edit',
                'projects.board.move_cards',
                'projects.board.update_status',
                'project_stories:update'
            ]
        elif self.action == 'move_sprint':
            self.required_permission = ['projects.story.move', 'projects.sprint.move_stories', 'projects.backlog.move', 'project_stories:update']
        elif self.action == 'destroy':
            self.required_permission = ['projects.story.delete', 'projects.backlog.delete', 'project_stories:delete']
        else:
            self.required_permission = ['projects.story.view', 'projects:view', 'project_stories:view']
        return super().get_permissions()

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        project = get_object_or_404(Project, id=project_id, company=self.request.user.organization) if project_id else None
        qs = stories_for_user(self.request.user, project=project)
        return qs.prefetch_related(
            'story_members__member__user',
            'tasks__status',
            'tasks__assigned_to',
            'tasks__subtasks',
        )

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project')
        org = getattr(request.user, 'organization', None)
        if request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False):
            project = get_object_or_404(Project, id=project_id)
        else:
            project = get_object_or_404(Project, id=project_id, company=org)

        epic_id = request.data.get('epic')
        epic = None
        if epic_id:
            epic = ProjectEpic.objects.filter(id=epic_id, project=project).first()
            if not epic:
                return Response({'epic': 'Epic does not belong to the selected project.'}, status=status.HTTP_400_BAD_REQUEST)

        # For backlog story creation, sprint is always null
        sprint_id = request.data.get('sprint')
        sprint = None
        if sprint_id:
            sprint = ProjectSprint.objects.filter(id=sprint_id, project=project).first()
            if not sprint:
                return Response({'sprint': 'Sprint does not belong to the selected project.'}, status=status.HTTP_400_BAD_REQUEST)

        status_input = request.data.get('status')
        status_obj = None
        if status_input:
            if isinstance(status_input, (int, str)) and (isinstance(status_input, int) or status_input.isdigit()):
                status_obj = ProjectStatusOption.objects.filter(id=int(status_input), company=request.user.organization).first()
            else:
                status_obj = ProjectStatusOption.objects.filter(company=request.user.organization, name__iexact=str(status_input)).first()
            if not status_obj:
                return Response({'status': 'Status option not found or invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            story = create_story(
                project=project,
                title=request.data.get('title'),
                description=request.data.get('description'),
                acceptance_criteria=request.data.get('acceptance_criteria'),
                epic=epic,
                sprint=sprint,
                work_type=request.data.get('work_type', 'Feature'),
                priority=request.data.get('priority', 'Medium'),
                story_points=request.data.get('story_points', 0),
                due_date=request.data.get('due_date'),
                labels=request.data.get('labels', []),
                department=request.data.get('department'),
                status=status_obj,
                user=request.user,
                member_ids=request.data.get('members') or request.data.get('member_ids'),
            )
            return Response(ProjectStorySerializer(story).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='move-sprint')
    def move_sprint(self, request, pk=None):
        story = self.get_object()
        sprint_id = request.data.get('sprint_id')
        sprint = ProjectSprint.objects.filter(id=sprint_id, project=story.project).first() if sprint_id else None
        updated_story = assign_story_to_sprint(story, sprint, user=request.user)
        return Response(ProjectStorySerializer(updated_story).data)

    def update(self, request, *args, **kwargs):
        story = self.get_object()
        user = request.user
        is_manage_all = (
            user.is_superuser or getattr(user, 'isSuperAdmin', False) or
            has_fine_grained_permission(user, 'projects.board.manage') or
            has_fine_grained_permission(user, 'projects:update') or
            story.project.project_manager == user or story.project.team_lead == user
        )
        if not is_manage_all:
            is_assigned = (
                story.story_members.filter(member__user=user).exists() or
                ProjectTask.objects.filter(story=story, assigned_to=user).exists()
            )
            if not is_assigned:
                return Response({'detail': 'Permission denied: Developers can move assigned cards only.'}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        story = serializer.save()
        member_ids = self.request.data.get('members') or self.request.data.get('member_ids')
        if member_ids is not None and isinstance(member_ids, list):
            from projects.models import ProjectMember, ProjectStoryMember
            from users.models import Employee
            ProjectStoryMember.objects.filter(story=story).delete()
            for mid in member_ids:
                pm = ProjectMember.objects.filter(project=story.project, id=mid).first()
                if not pm:
                    pm = ProjectMember.objects.filter(project=story.project, user_id=mid).first()
                if not pm and Employee.objects.filter(id=mid, organization=story.project.company, is_active=True).exists():
                    pm, _ = ProjectMember.objects.get_or_create(
                        project=story.project, user_id=mid,
                        defaults={'project_role': 'Developer', 'is_active': True}
                    )
                if pm:
                    ProjectStoryMember.objects.get_or_create(story=story, member=pm, defaults={'assigned_by': self.request.user})


# --------------------------------------------------------------------------------
# ProjectTaskViewSet
# --------------------------------------------------------------------------------
class ProjectTaskViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'status_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'my_tasks': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectTaskSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'my_tasks']:
            assigned_to = self.request.query_params.get('assigned_to')
            if assigned_to == 'me' or self.action == 'my_tasks':
                self.required_permission = [
                    'projects.my_tasks.view',
                    'projects.task.view',
                    'project_tasks:view_own',
                    'project_tasks:view_all',
                    'projects:view'
                ]
            else:
                self.required_permission = [
                    'projects.task.view',
                    'projects.my_tasks.view',
                    'project_tasks:view_all',
                    'project_tasks:view_own',
                    'projects:view'
                ]
        elif self.action == 'create':
            self.required_permission = ['projects.task.create', 'project_tasks:create']
        elif self.action in ['update', 'partial_update', 'status_update']:
            self.required_permission = [
                'projects.task.edit',
                'projects.task.update_status',
                'projects.my_tasks.update',
                'projects.my_tasks.update_status',
                'project_tasks:update_all',
                'project_tasks:update_own'
            ]
        elif self.action == 'destroy':
            self.required_permission = ['projects.task.delete', 'project_tasks:delete']
        else:
            self.required_permission = [
                'projects.my_tasks.view',
                'projects.task.view',
                'project_tasks:view_all',
                'project_tasks:view_own',
                'projects:view'
            ]
        return super().get_permissions()

    def get_queryset(self):
        story_id = self.request.query_params.get('story_id')
        project_id = self.request.query_params.get('project_id')
        story = get_object_or_404(ProjectStory, id=story_id) if story_id else None
        project = get_object_or_404(Project, id=project_id) if project_id else None

        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to == 'me' or self.action == 'my_tasks':
            qs = ProjectTask.objects.filter(
                assigned_to=self.request.user,
                is_deleted=False
            )
            if project:
                qs = qs.filter(story__project=project)
            if story:
                qs = qs.filter(story=story)
            return qs.select_related('story', 'status', 'assigned_to').prefetch_related('subtasks')

        qs = tasks_for_user(self.request.user, story=story, project=project)
        if assigned_to:
            try:
                qs = qs.filter(assigned_to_id=int(assigned_to))
            except ValueError:
                pass
        return qs.prefetch_related('subtasks')

    @action(detail=False, methods=['get'], url_path='my-tasks')
    def my_tasks(self, request):
        project_id = request.query_params.get('project_id')
        project = get_object_or_404(Project, id=project_id, company=request.user.organization) if project_id else None

        if project and not (request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False)):
            if not project.members.filter(user=request.user, is_active=True).exists() and project.team_lead != request.user and project.project_manager != request.user:
                return Response({'detail': 'You do not have membership in this project.'}, status=status.HTTP_403_FORBIDDEN)

        qs = ProjectTask.objects.filter(
            assigned_to=request.user,
            is_deleted=False
        )
        if project:
            qs = qs.filter(story__project=project)

        serializer = self.get_serializer(qs.select_related('story', 'status', 'assigned_to'), many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        story_id = request.data.get('story')
        story = get_object_or_404(stories_for_user(request.user), id=story_id)

        assigned_to_id = request.data.get('assigned_to')
        from users.models import Employee
        assigned_to = None
        if assigned_to_id:
            assigned_to = Employee.objects.filter(id=assigned_to_id, organization=request.user.organization, is_active=True).first()
            if not assigned_to:
                return Response({'error': 'Assigned employee not found or invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)
            project = story.project
            if not (project.members.filter(user=assigned_to, is_active=True).exists() or 
                    project.project_manager == assigned_to or 
                    project.team_lead == assigned_to):
                return Response({'error': 'Assigned employee must be a member of the project.'}, status=status.HTTP_400_BAD_REQUEST)

        status_input = request.data.get('status')
        status_obj = None
        if status_input:
            if isinstance(status_input, (int, str)) and (isinstance(status_input, int) or status_input.isdigit()):
                status_obj = ProjectStatusOption.objects.filter(id=int(status_input), company=request.user.organization).first()
                if not status_obj:
                    return Response({'error': 'Status option not found or invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)
            elif isinstance(status_input, str):
                status_obj = ProjectStatusOption.objects.filter(company=request.user.organization, name__iexact=status_input).first()
                if not status_obj:
                    return Response({'error': 'Status option not found or invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = create_task(
                story=story,
                title=request.data.get('title'),
                description=request.data.get('description'),
                assigned_to=assigned_to,
                priority=request.data.get('priority', 'Medium'),
                status=status_obj,
                estimated_hours=request.data.get('estimated_hours', 0.0),
                start_date=request.data.get('start_date'),
                due_date=request.data.get('due_date'),
                user=request.user,
            )
            draft_token = request.data.get('draft_token')
            if draft_token:
                from projects.models import ProjectAttachment
                ProjectAttachment.objects.filter(draft_token=draft_token, uploaded_by=request.user).update(task=task, draft_token=None)
            return Response(ProjectTaskSerializer(task).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    def _check_task_update_permission(self, request, task):
        user = request.user
        if user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'project_tasks:update_all'):
            return True
        project = task.story.project if task.story else None
        if project and (project.project_manager == user or project.team_lead == user):
            return True
        return task.assigned_to == user

    def update(self, request, *args, **kwargs):
        task = self.get_object()
        if not self._check_task_update_permission(request, task):
            return Response({'detail': 'Permission denied: You can only update tasks assigned to you.'}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        task = self.get_object()
        if not self._check_task_update_permission(request, task):
            return Response({'detail': 'Permission denied: You can only update tasks assigned to you.'}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=['patch'], url_path='status')
    def status_update(self, request, pk=None):
        task = self.get_object()
        if not self._check_task_update_permission(request, task):
            return Response({'detail': 'Permission denied: You can only update status of tasks assigned to you.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ProjectTaskStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_task = update_task_status(task, serializer.validated_data['status'])
        return Response(ProjectTaskSerializer(updated_task).data)


# --------------------------------------------------------------------------------
# ProjectSubtaskViewSet
# --------------------------------------------------------------------------------
class ProjectSubtaskViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectSubtaskSerializer

    def get_permissions(self):
        if self.action == 'list':
            self.required_permission = ['project_tasks:view_all', 'project_tasks:view_own', 'project_tasks:update_all', 'project_tasks:update_own', 'project_tasks:create', 'project_tasks:delete']
        elif self.action == 'create':
            self.required_permission = ['projects.subtask.create', 'project_tasks:create']
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['project_tasks:update_all', 'project_tasks:update_own']
        elif self.action == 'destroy':
            self.required_permission = 'project_tasks:delete'
        else:
            self.required_permission = ['project_tasks:view_all', 'project_tasks:view_own', 'project_tasks:update_all', 'project_tasks:update_own', 'project_tasks:create', 'project_tasks:delete']
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        task_id = self.request.query_params.get('task_id')
        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            qs = ProjectSubtask.objects.all()
        else:
            qs = ProjectSubtask.objects.filter(task__story__project__company=user.organization)
        if task_id:
            qs = qs.filter(task_id=task_id)
        return qs

    def create(self, request, *args, **kwargs):
        task_id = request.data.get('task')
        user = request.user
        if user.is_superuser or getattr(user, 'isSuperAdmin', False):
            task = get_object_or_404(ProjectTask, id=task_id)
        else:
            task = get_object_or_404(ProjectTask, id=task_id, story__project__company=user.organization)

        assigned_to_id = request.data.get('assigned_to')
        from users.models import Employee
        assigned_to = None
        if assigned_to_id:
            assigned_to = Employee.objects.filter(id=assigned_to_id, organization=request.user.organization, is_active=True).first()
            if not assigned_to:
                return Response({'error': 'Assigned employee not found or invalid scope.'}, status=status.HTTP_400_BAD_REQUEST)
            project = task.story.project
            if not (project.members.filter(user=assigned_to, is_active=True).exists() or 
                    project.project_manager == assigned_to or 
                    project.team_lead == assigned_to):
                return Response({'error': 'Assigned employee must be a member of the project.'}, status=status.HTTP_400_BAD_REQUEST)

        subtask = create_subtask(
            task=task,
            title=request.data.get('title'),
            assigned_to=assigned_to,
            estimated_hours=request.data.get('estimated_hours', 0.0),
            user=request.user,
        )
        return Response(ProjectSubtaskSerializer(subtask).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        subtask = self.get_object()
        is_completed = request.data.get('is_completed')
        if is_completed is not None:
            from projects.services.subtasks import toggle_subtask_completion
            subtask, task_progress = toggle_subtask_completion(subtask, is_completed, user=request.user)
            data = ProjectSubtaskSerializer(subtask).data
            data['task_progress'] = task_progress
            return Response(data)
        return super().partial_update(request, *args, **kwargs)


# --------------------------------------------------------------------------------
# ProjectCommentViewSet & ProjectAttachmentViewSet
# --------------------------------------------------------------------------------
class ProjectCommentViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    throttle_scope = 'burst'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectCommentSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['projects.comment.view', 'projects.comment.create', 'projects.comment.edit', 'projects.comment.delete', 'projects:view']
        elif self.action == 'create':
            self.required_permission = 'projects.comment.create'
        elif self.action in ['update', 'partial_update']:
            self.required_permission = ['projects.comment.edit', 'projects.comment.view']
        elif self.action == 'destroy':
            self.required_permission = ['projects.comment.delete', 'projects:delete']
        else:
            self.required_permission = ['projects.comment.view', 'projects.comment.create', 'projects:view']
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        permitted_projects = projects_for_user(user)
        
        from django.db.models import Q
        qs = ProjectComment.objects.filter(
            Q(epic__project__in=permitted_projects) |
            Q(story__project__in=permitted_projects) |
            Q(task__story__project__in=permitted_projects) |
            Q(subtask__task__story__project__in=permitted_projects)
        ).select_related('user', 'story', 'task', 'epic').prefetch_related('attachments', 'attachments__uploaded_by')
        
        story_id = self.request.query_params.get('story_id')
        task_id = self.request.query_params.get('task_id')
        epic_id = self.request.query_params.get('epic_id')

        if story_id:
            qs = qs.filter(story_id=story_id)
        elif task_id:
            qs = qs.filter(task_id=task_id)
        elif epic_id:
            qs = qs.filter(epic_id=epic_id)

        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        limit_param = request.query_params.get('limit')
        before_id = request.query_params.get('before_id')
        before_created_at = request.query_params.get('before_created_at')

        if limit_param or before_id or before_created_at:
            try:
                limit = int(limit_param) if limit_param else 60
            except ValueError:
                limit = 60

            if before_created_at and before_id:
                qs = qs.filter(
                    Q(created_at__lt=before_created_at) |
                    Q(created_at=before_created_at, id__lt=int(before_id))
                )
            elif before_id:
                qs = qs.filter(id__lt=int(before_id))

            qs = qs.order_by('-created_at', '-id')
            raw_items = list(qs[:limit + 1])
            has_more = len(raw_items) > limit
            page_items = raw_items[:limit]
            page_items.reverse()  # Re-order to ascending chronological order

            serializer = self.get_serializer(page_items, many=True)
            return Response({
                'results': serializer.data,
                'has_more': has_more,
                'count': len(serializer.data)
            })

        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        epic = serializer.validated_data.get('epic')
        story = serializer.validated_data.get('story')
        task = serializer.validated_data.get('task')
        subtask = serializer.validated_data.get('subtask')
        attachment_ids = request.data.get('attachment_ids')
        draft_token = request.data.get('draft_token')
        if draft_token:
            try:
                import uuid
                uuid.UUID(str(draft_token))
            except (ValueError, TypeError, AttributeError):
                return Response({'detail': 'Invalid draft_token. Must be a valid UUID.'}, status=status.HTTP_400_BAD_REQUEST)

        client_message_id = request.data.get('client_message_id')
        
        permitted_projects = projects_for_user(request.user)
        project_access_ok = False
        if epic and epic.project in permitted_projects:
            project_access_ok = True
        elif story and story.project in permitted_projects:
            project_access_ok = True
        elif task and task.story.project in permitted_projects:
            project_access_ok = True
        elif subtask and subtask.task.story.project in permitted_projects:
            project_access_ok = True
            
        if not project_access_ok:
            return Response({'detail': 'Permission denied: no access to project.'}, status=status.HTTP_403_FORBIDDEN)

        comment = create_comment(
            user=request.user,
            comment_text=serializer.validated_data.get('comment', ''),
            epic=epic,
            story=story,
            task=task,
            subtask=subtask,
            attachment_ids=attachment_ids,
            draft_token=draft_token,
            client_message_id=client_message_id,
        )
        return Response(ProjectCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        comment = serializer.save()
        try:
            from projects.services.comments import broadcast_comment_event
            comment_data = ProjectCommentSerializer(comment).data
            broadcast_comment_event(
                'message_updated',
                comment_data,
                epic=comment.epic,
                story=comment.story,
                task=comment.task,
                subtask=comment.subtask
            )
        except Exception:
            pass

    def perform_destroy(self, instance):
        epic = instance.epic
        story = instance.story
        task = instance.task
        subtask = instance.subtask
        comment_id = instance.id
        story_id = instance.story_id
        task_id = instance.task_id
        epic_id = instance.epic_id
        instance.delete()
        try:
            from projects.services.comments import broadcast_comment_event
            data = {'id': comment_id, 'story': story_id, 'task': task_id, 'epic': epic_id}
            broadcast_comment_event(
                'message_deleted',
                data,
                epic=epic,
                story=story,
                task=task,
                subtask=subtask
            )
        except Exception:
            pass


class ProjectAttachmentViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    throttle_scope = 'uploads'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectAttachmentSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'download']:
            self.required_permission = ['projects.attachment.view', 'projects.attachment.download', 'projects.attachment.upload', 'projects:view']
        elif self.action == 'create':
            self.required_permission = 'projects.attachment.upload'
        elif self.action == 'destroy':
            self.required_permission = ['projects.attachment.delete', 'projects:delete']
        else:
            self.required_permission = ['projects.attachment.view', 'projects.attachment.upload', 'projects:view']
        return super().get_permissions()

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id') or self.request.query_params.get('project')
        epic_id = self.request.query_params.get('epic_id') or self.request.query_params.get('epic')
        story_id = self.request.query_params.get('story_id') or self.request.query_params.get('story')
        task_id = self.request.query_params.get('task_id') or self.request.query_params.get('task')
        draft_token_param = self.request.query_params.get('draft_token')
        is_inline_param = self.request.query_params.get('is_inline')

        user = self.request.user

        if draft_token_param:
            try:
                import uuid
                uuid.UUID(draft_token_param)
            except (ValueError, TypeError, AttributeError):
                return ProjectAttachment.objects.none()
            return ProjectAttachment.objects.filter(
                draft_token=draft_token_param,
                uploaded_by=user,
                company=getattr(user, 'organization', None)
            )

        permitted_projects = projects_for_user(user)

        from django.db.models import Q
        qs = ProjectAttachment.objects.filter(
            Q(project__in=permitted_projects) |
            Q(epic__project__in=permitted_projects) |
            Q(story__project__in=permitted_projects) |
            Q(task__story__project__in=permitted_projects) |
            Q(comment__epic__project__in=permitted_projects) |
            Q(comment__story__project__in=permitted_projects) |
            Q(comment__task__story__project__in=permitted_projects) |
            Q(comment__subtask__task__story__project__in=permitted_projects) |
            Q(is_temporary=True, uploaded_by=user)
        )

        if project_id:
            qs = qs.filter(project_id=project_id)
        elif epic_id:
            qs = qs.filter(epic_id=epic_id)
        elif story_id:
            qs = qs.filter(story_id=story_id)
        elif task_id:
            qs = qs.filter(task_id=task_id)

        if is_inline_param is not None:
            is_inline_bool = is_inline_param.lower() in ('true', '1')
            qs = qs.filter(is_inline=is_inline_bool)

        return qs

    def create(self, request, *args, **kwargs):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'detail': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        project_id = request.data.get('project') or request.data.get('project_id')
        epic_id = request.data.get('epic') or request.data.get('epic_id')
        story_id = request.data.get('story') or request.data.get('story_id')
        task_id = request.data.get('task') or request.data.get('task_id')
        draft_token = request.data.get('draft_token')
        if draft_token:
            try:
                import uuid
                uuid.UUID(str(draft_token))
            except (ValueError, TypeError, AttributeError):
                return Response({'detail': 'Invalid draft_token. Must be a valid UUID.'}, status=status.HTTP_400_BAD_REQUEST)

        is_inline = str(request.data.get('is_inline', 'false')).lower() in ('true', '1')

        persisted_targets = [bool(project_id), bool(epic_id), bool(story_id), bool(task_id)]
        persisted_count = sum(persisted_targets)
        has_draft = bool(draft_token)

        if not ((persisted_count == 1 and not has_draft) or (persisted_count == 0 and has_draft)):
            return Response(
                {'detail': 'Attachment must target exactly one persisted entity (project, epic, story, task) or one valid temporary draft token.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        project = Project.objects.filter(id=project_id).first() if project_id else None
        epic = ProjectEpic.objects.filter(id=epic_id).first() if epic_id else None
        story = ProjectStory.objects.filter(id=story_id).first() if story_id else None
        task = ProjectTask.objects.filter(id=task_id).first() if task_id else None

        if persisted_count == 1:
            permitted_projects = projects_for_user(request.user)
            project_access_ok = False
            if project and project in permitted_projects:
                project_access_ok = True
            elif epic and epic.project in permitted_projects:
                project_access_ok = True
            elif story and story.project in permitted_projects:
                project_access_ok = True
            elif task and task.story.project in permitted_projects:
                project_access_ok = True

            if not project_access_ok:
                return Response({'detail': 'Permission denied: no access to project.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from projects.services.comments import create_attachment
            attachment = create_attachment(
                user=request.user,
                file_obj=file_obj,
                project=project,
                epic=epic,
                story=story,
                task=task,
                draft_token=draft_token if has_draft else None,
                is_inline=is_inline,
            )
            return Response(ProjectAttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            err_dict = e.message_dict if hasattr(e, 'message_dict') else {'detail': e.messages}
            return Response(err_dict, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        attachment = self.get_object()
        from django.http import FileResponse
        import mimetypes
        mime, _ = mimetypes.guess_type(attachment.file_name)
        content_type = mime or 'application/octet-stream'
        
        response = FileResponse(attachment.file.open('rb'), content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{attachment.file_name}"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response




# --------------------------------------------------------------------------------
# EmployeeAssignmentsAPIView
# --------------------------------------------------------------------------------
class EmployeeAssignmentsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired]
    required_plan_feature = 'is_project_enabled'

    def get(self, request, employee_id):
        user = request.user
        if int(employee_id) != user.id:
            if not (user.is_superuser or getattr(user, 'isSuperAdmin', False) or has_fine_grained_permission(user, 'admin:employees')):
                return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)
        org = getattr(user, 'organization', None)
        if not org:
            return Response({'detail': 'User has no organization.'}, status=status.HTTP_400_BAD_REQUEST)

        from users.models import Employee
        try:
            target_employee = Employee.objects.get(id=employee_id, organization=org)
        except Employee.DoesNotExist:
            return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)

        pm_projects = Project.objects.filter(company=org, project_manager=target_employee, is_deleted=False)
        tl_projects = Project.objects.filter(company=org, team_lead=target_employee, is_deleted=False)
        member_projects = Project.objects.filter(company=org, members__user=target_employee, members__is_active=True, is_deleted=False)

        all_projects = (pm_projects | tl_projects | member_projects).distinct()
        assigned_tasks = ProjectTask.objects.filter(assigned_to=target_employee, story__project__company=org, story__project__is_deleted=False, is_deleted=False)

        return Response({
            'employee_id': target_employee.id,
            'projects_count': all_projects.count(),
            'tasks_count': assigned_tasks.count(),
            'projects': ProjectListSerializer(all_projects, many=True).data,
            'tasks': ProjectTaskSerializer(assigned_tasks, many=True).data,
        })


# --------------------------------------------------------------------------------
# ProjectRetrospectiveViewSet
# --------------------------------------------------------------------------------
class ProjectRetrospectiveViewSet(ActionPermissionMixin, viewsets.ModelViewSet):
    required_plan_feature = 'is_project_enabled'
    permission_classes_by_action = {
        'list': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'retrieve': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'create': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'partial_update': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'destroy': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'add_item': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'vote': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'convert_to_story': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
        'close': [permissions.IsAuthenticated, DRFCheckModePermission, DRFPlanPermissionRequired, HasProjectPermission],
    }
    serializer_class = ProjectRetrospectiveSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.required_permission = ['projects.retrospective.view', 'projects:view']
        elif self.action in ['create', 'add_item', 'vote']:
            self.required_permission = 'projects.retrospective.create'
        elif self.action in ['update', 'partial_update', 'close']:
            self.required_permission = ['projects.retrospective.close', 'projects.retrospective.edit', 'projects:view']
        elif self.action == 'convert_to_story':
            self.required_permission = ['projects.backlog.create', 'projects.retrospective.edit', 'projects:view']
        else:
            self.required_permission = ['projects.retrospective.view', 'projects:view']
        return super().get_permissions()

    def get_queryset(self):
        project_id = self.request.query_params.get('project_id')
        sprint_id = self.request.query_params.get('sprint_id')
        allowed_projects = projects_for_user(self.request.user)
        from django.db.models import Prefetch
        from projects.models import ProjectRetrospectiveItem
        qs = ProjectRetrospective.objects.filter(project__in=allowed_projects).select_related('project', 'sprint', 'created_by').prefetch_related(
            Prefetch('items', queryset=ProjectRetrospectiveItem.objects.select_related('created_by'))
        )
        if project_id:
            qs = qs.filter(project_id=project_id)
        if sprint_id:
            qs = qs.filter(sprint_id=sprint_id)
        return qs

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project')
        sprint_id = request.data.get('sprint')
        project = get_object_or_404(Project, id=project_id, company=request.user.organization)
        sprint = get_object_or_404(ProjectSprint, id=sprint_id, project=project)

        retro, created = ProjectRetrospective.objects.get_or_create(
            project=project,
            sprint=sprint,
            defaults={
                'happiness_score': request.data.get('happiness_score', 3.5),
                'is_anonymous': request.data.get('is_anonymous', False),
                'created_by': request.user
            }
        )
        return Response(ProjectRetrospectiveSerializer(retro).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        retro = self.get_object()
        category = request.data.get('category', 'went_well')
        text = request.data.get('text', '').strip()
        if not text:
            return Response({'detail': 'Text content is required.'}, status=status.HTTP_400_BAD_REQUEST)

        item = ProjectRetrospectiveItem.objects.create(
            retrospective=retro,
            category=category,
            text=text,
            created_by=request.user
        )
        return Response(ProjectRetrospectiveItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='vote')
    def vote(self, request):
        item_id = request.data.get('item_id')
        item = get_object_or_404(ProjectRetrospectiveItem, id=item_id, retrospective__project__company=request.user.organization)
        if item.votes >= 5:
            return Response({'detail': 'Maximum 5 votes allowed per item.'}, status=status.HTTP_400_BAD_REQUEST)
        item.votes += 1
        item.save()
        return Response(ProjectRetrospectiveItemSerializer(item).data)

    @action(detail=False, methods=['post'], url_path='convert-to-story')
    def convert_to_story(self, request):
        item_id = request.data.get('item_id')
        item = get_object_or_404(ProjectRetrospectiveItem, id=item_id, retrospective__project__company=request.user.organization)
        project = item.retrospective.project

        is_authorized = (
            request.user.is_superuser or getattr(request.user, 'isSuperAdmin', False) or
            has_fine_grained_permission(request.user, 'projects.retrospective.manage') or
            has_fine_grained_permission(request.user, 'projects:update') or
            project.project_manager == request.user or project.team_lead == request.user
        )
        if not is_authorized:
            return Response({'detail': 'Permission denied: PM approval required to convert action item to backlog story.'}, status=status.HTTP_403_FORBIDDEN)

        if item.converted_story:
            return Response(ProjectStorySerializer(item.converted_story).data)

        story = create_story(
            project=project,
            title=f"[Retro Action] {item.text[:100]}",
            description=f"Action item generated from Retrospective ({item.retrospective.sprint.name}):\n\n{item.text}",
            acceptance_criteria="Approved retro action item implementation verified by QA.",
            work_type='Improvement',
            priority='High',
            story_points=2,
            user=request.user,
        )
        item.converted_story = story
        item.approved_by = request.user
        item.save()

        # Audit log creation
        from core.models import AuditLog
        user = request.user
        AuditLog.objects.create(
            organization=user.organization,
            employee=user,
            employeeName=f"{user.first_name} {user.last_name}".strip() or user.email,
            action="PM Approved Retro Action Item",
            ipAddress=request.META.get('REMOTE_ADDR'),
            details=f"Approved retro item #{item.id} converted to Story #{story.id} ({story.title})"
        )

        return Response(ProjectStorySerializer(story).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        retro = self.get_object()
        retro.status = 'completed'
        retro.closed_at = timezone.now()
        retro.save()
        return Response(ProjectRetrospectiveSerializer(retro).data)

