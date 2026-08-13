# --------------------------------------------------------------------------------
#       Projects Serializers
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO

# THIRD PARTY
from rest_framework import serializers

# APPLICATION SPECIFIC
from projects.models import (
    Project, ProjectMember, ProjectEpic, ProjectSprint, ProjectStory,
    ProjectStoryMember, ProjectTask, ProjectSubtask, ProjectStatusOption,
    ProjectComment, ProjectAttachment, ProjectActivity, ProjectSprintEvent,
    ProjectRetrospective, ProjectRetrospectiveItem
)
from projects.constants import FIBONACCI_STORY_POINTS
from users.models import Employee
from django.db.models import Q
from projects.selectors.projects import projects_for_user
from projects.selectors.stories import stories_for_user
from projects.selectors.tasks import tasks_for_user
from projects.selectors.epics import epics_for_user
from projects.selectors.sprints import sprints_for_user


# --------------------------------------------------------------------------------
# ProjectStatusOption Serializers
# --------------------------------------------------------------------------------
class ProjectStatusOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectStatusOption
        fields = [
            'id', 'name', 'code', 'category', 'scope',
            'order', 'progress_percentage', 'is_default', 'is_system', 'is_active'
        ]
        read_only_fields = ['id', 'is_system']

    def validate_name(self, value):
        if self.instance and self.instance.is_system:
            raise serializers.ValidationError("System statuses cannot be renamed.")
        return value

    def validate_is_active(self, value):
        if self.instance and self.instance.is_system and not value:
            raise serializers.ValidationError("System statuses cannot be deactivated.")
        return value

    def validate_progress_percentage(self, value):
        if value is None:
            raise serializers.ValidationError("Progress percentage is required.")
        if not isinstance(value, int) or isinstance(value, bool):
            raise serializers.ValidationError("Progress percentage must be an integer.")
        if value < 0 or value > 100:
            raise serializers.ValidationError("Progress percentage must be between 0 and 100.")
        if self.instance and self.instance.is_system:
            if self.instance.category == 'pending' and value != 0:
                raise serializers.ValidationError("System status for Pending category must remain 0%.")
            if self.instance.category == 'completed' and value != 100:
                raise serializers.ValidationError("System status for Completed category must remain 100%.")
        return value

    def validate(self, attrs):
        request = self.context.get('request')
        if request and not self.instance:
            company = request.user.organization
            code = attrs.get('code', '')
            if ProjectStatusOption.objects.filter(company=company, code=code).exists():
                raise serializers.ValidationError({'code': 'A status with this code already exists for your company.'})
        return attrs


# --------------------------------------------------------------------------------
# Project Member Serializers
# --------------------------------------------------------------------------------
class ProjectMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField(method_name='get_user_name', read_only=True)
    user_photo = serializers.SerializerMethodField(method_name='get_user_photo', read_only=True)
    designation = serializers.SerializerMethodField(method_name='get_designation', read_only=True)

    class Meta:
        model = ProjectMember
        fields = [
            'id', 'project', 'user', 'user_email', 'user_name', 'user_photo', 'designation',
            'project_role', 'department', 'is_active', 'joined_at'
        ]
        read_only_fields = ['id', 'joined_at']

    def get_user_name(self, obj):
        name = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return name if name else obj.user.email

    def get_user_photo(self, obj):
        return getattr(obj.user, 'profilePhoto', None)

    def get_designation(self, obj):
        if not obj.user:
            return obj.project_role or 'Developer'
        user_role = getattr(obj.user, 'role', None)
        if user_role:
            return getattr(user_role, 'name', str(user_role))
        return obj.project_role or 'Developer'


class ProjectMemberCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMember
        fields = ['user', 'project_role', 'department', 'is_active']

    def validate_user(self, value):
        request = self.context.get('request')
        if request and request.user:
            if value.organization != request.user.organization:
                raise serializers.ValidationError("User does not belong to your company.")
        return value

    def validate(self, attrs):
        project = self.context.get('project')
        user = attrs.get('user')
        if project and user:
            existing = ProjectMember.objects.filter(project=project, user=user).first()
            if existing and existing.is_active:
                raise serializers.ValidationError({"user": "User is already an active member of this project."})
        return attrs


class ProjectMemberUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMember
        fields = ['project_role', 'department', 'is_active']


# --------------------------------------------------------------------------------
# Project Epic Serializers
# --------------------------------------------------------------------------------
class ProjectEpicSerializer(serializers.ModelSerializer):
    status_detail = ProjectStatusOptionSerializer(source='status', read_only=True)

    class Meta:
        model = ProjectEpic
        fields = [
            'id', 'project', 'company', 'title', 'key', 'description',
            'color', 'status', 'status_detail', 'priority',
            'start_date', 'due_date', 'order', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'key', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['project'].queryset = projects_for_user(user)
            self.fields['status'].queryset = ProjectStatusOption.objects.filter(company=user.organization, is_active=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user if request else None
        if user:
            project = attrs.get('project') or (self.instance.project if self.instance else None)
            if project:
                if not projects_for_user(user).filter(id=project.id).exists():
                    raise serializers.ValidationError({"project": "Project not found or inaccessible."})
        return attrs


# --------------------------------------------------------------------------------
# Project Sprint Serializers
# --------------------------------------------------------------------------------
class ProjectSprintSerializer(serializers.ModelSerializer):
    sprint_key = serializers.SerializerMethodField(read_only=True)
    stories_count = serializers.SerializerMethodField()
    total_story_points = serializers.SerializerMethodField()
    completed_story_points = serializers.SerializerMethodField()
    remaining_capacity = serializers.SerializerMethodField(read_only=True)
    duration_days = serializers.SerializerMethodField(read_only=True)

    cancelled_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProjectSprint
        fields = [
            'id', 'project', 'sprint_key', 'name', 'goal', 'start_date', 'end_date',
            'duration_days', 'started_at', 'completed_at', 'cancelled_at', 'cancelled_by',
            'cancelled_by_name', 'cancellation_reason', 'status', 'capacity',
            'remaining_capacity', 'stories_count', 'total_story_points',
            'completed_story_points', 'created_at'
        ]
        read_only_fields = ['id', 'status', 'started_at', 'completed_at', 'cancelled_at', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['project'].queryset = projects_for_user(user)

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user if request else None
        if user:
            project = attrs.get('project') or (self.instance.project if self.instance else None)
            if project:
                if not projects_for_user(user).filter(id=project.id).exists():
                    raise serializers.ValidationError({"project": "Project not found or inaccessible."})
        return attrs

    def get_cancelled_by_name(self, obj):
        if obj.cancelled_by:
            name = f"{obj.cancelled_by.first_name} {obj.cancelled_by.last_name}".strip()
            return name if name else obj.cancelled_by.email
        return None

    def get_sprint_key(self, obj):
        sprints = list(obj.project.sprints.order_by('id').values_list('id', flat=True))
        try:
            seq = sprints.index(obj.id) + 1
        except ValueError:
            seq = obj.id
        return f"SPR-{seq:03d}"

    def get_stories_count(self, obj):
        return obj.stories.count()

    def get_total_story_points(self, obj):
        return sum(s.story_points for s in obj.stories.all())

    def get_completed_story_points(self, obj):
        return sum(s.story_points for s in obj.stories.all() if s.status and s.status.category == 'completed')

    def get_remaining_capacity(self, obj):
        used = self.get_total_story_points(obj)
        cap = obj.capacity or 0
        return max(0, cap - used)

    def get_duration_days(self, obj):
        if obj.start_date and obj.end_date:
            return (obj.end_date - obj.start_date).days
        return None


# --------------------------------------------------------------------------------
# Project Story Member Serializers
# --------------------------------------------------------------------------------
class ProjectStoryMemberSerializer(serializers.ModelSerializer):
    member_user_email = serializers.EmailField(source='member.user.email', read_only=True)
    member_user_name = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    user = serializers.IntegerField(source='member.user.id', read_only=True)

    class Meta:
        model = ProjectStoryMember
        fields = ['id', 'story', 'member', 'user', 'user_name', 'member_user_email', 'member_user_name', 'assigned_at']
        read_only_fields = ['id', 'assigned_at']

    def get_member_user_name(self, obj):
        if not obj.member or not obj.member.user:
            return ""
        name = f"{obj.member.user.first_name} {obj.member.user.last_name}".strip()
        return name if name else obj.member.user.email

    def get_user_name(self, obj):
        return self.get_member_user_name(obj)


class ProjectStoryMemberCreateSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()

    def validate_member_id(self, value):
        story = self.context.get('story')
        if not story:
            raise serializers.ValidationError("Story context is missing.")

        try:
            member = ProjectMember.objects.get(id=value, project=story.project, is_active=True)
        except ProjectMember.DoesNotExist:
            raise serializers.ValidationError("Member not found or is not an active member of this project.")

        if ProjectStoryMember.objects.filter(story=story, member=member).exists():
            raise serializers.ValidationError("This member is already assigned to this story.")

        self.context['resolved_member'] = member
        return value


# --------------------------------------------------------------------------------
# Project Subtask Serializer
# --------------------------------------------------------------------------------
class ProjectSubtaskSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProjectSubtask
        fields = [
            'id', 'task', 'title', 'is_completed', 'assigned_to',
            'assigned_to_name', 'estimated_hours', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['task'].queryset = tasks_for_user(user)
            self.fields['assigned_to'].queryset = Employee.objects.filter(organization=user.organization, is_active=True)

    def validate(self, attrs):
        task = attrs.get('task') or getattr(self.instance, 'task', None)
        if not task:
            raise serializers.ValidationError({"task": "Subtask must belong to a parent Task."})

        request = self.context.get('request')
        user = request.user if request else None
        if user:
            if not tasks_for_user(user).filter(id=task.id).exists():
                raise serializers.ValidationError({"task": "Task not found or inaccessible."})
            
            project = task.story.project
            assigned_to = attrs.get('assigned_to')
            if assigned_to:
                if assigned_to.organization != user.organization:
                    raise serializers.ValidationError({"assigned_to": "Assigned employee does not belong to your company."})
                if not assigned_to.is_active:
                    raise serializers.ValidationError({"assigned_to": "Assigned employee is inactive."})
                if not (project.members.filter(user=assigned_to, is_active=True).exists() or 
                        project.project_manager == assigned_to or 
                        project.team_lead == assigned_to):
                    raise serializers.ValidationError({"assigned_to": "Assigned employee must be a member of the project."})

        return attrs

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to:
            return None
        name = f"{obj.assigned_to.first_name} {obj.assigned_to.last_name}".strip()
        return name if name else obj.assigned_to.email


# --------------------------------------------------------------------------------
# Project Task Serializer
# --------------------------------------------------------------------------------
class ProjectTaskSerializer(serializers.ModelSerializer):
    status_detail = ProjectStatusOptionSerializer(source='status', read_only=True)
    assigned_to_name = serializers.SerializerMethodField(read_only=True)
    assigned_to_photo = serializers.SerializerMethodField(read_only=True)
    subtasks = ProjectSubtaskSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectTask
        fields = [
            'id', 'story', 'task_key', 'title', 'description', 'assigned_to',
            'assigned_to_name', 'assigned_to_photo', 'priority', 'status', 'status_detail',
            'estimated_hours', 'logged_hours', 'start_date', 'due_date',
            'completed_at', 'subtasks', 'created_at'
        ]
        read_only_fields = ['id', 'task_key', 'completed_at', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['story'].queryset = stories_for_user(user)
            self.fields['status'].queryset = ProjectStatusOption.objects.filter(company=user.organization, is_active=True)
            self.fields['assigned_to'].queryset = Employee.objects.filter(organization=user.organization, is_active=True)

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to:
            return None
        name = f"{obj.assigned_to.first_name} {obj.assigned_to.last_name}".strip()
        return name if name else obj.assigned_to.email

    def get_assigned_to_photo(self, obj):
        if not obj.assigned_to:
            return None
        return getattr(obj.assigned_to, 'profilePhoto', None)

    def validate(self, attrs):
        story = attrs.get('story') or getattr(self.instance, 'story', None)
        if not story:
            raise serializers.ValidationError({"story": "Task must belong to a parent User Story."})

        request = self.context.get('request')
        user = request.user if request else None
        if user:
            if not stories_for_user(user).filter(id=story.id).exists():
                raise serializers.ValidationError({"story": "Story not found or inaccessible."})
            
            project = story.project
            assigned_to = attrs.get('assigned_to')
            if assigned_to:
                if assigned_to.organization != user.organization:
                    raise serializers.ValidationError({"assigned_to": "Assigned employee does not belong to your company."})
                if not assigned_to.is_active:
                    raise serializers.ValidationError({"assigned_to": "Assigned employee is inactive."})
                if not (project.members.filter(user=assigned_to, is_active=True).exists() or 
                        project.project_manager == assigned_to or 
                        project.team_lead == assigned_to):
                    raise serializers.ValidationError({"assigned_to": "Assigned employee must be a member of the project."})
            
            status_opt = attrs.get('status')
            if status_opt and status_opt.company != user.organization:
                raise serializers.ValidationError({"status": "Status option not found or invalid."})

        return attrs


class ProjectStoryMemberSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='member.user.id')
    user_name = serializers.SerializerMethodField(read_only=True)
    user_email = serializers.ReadOnlyField(source='member.user.email')
    user_photo = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProjectStoryMember
        fields = ['id', 'story', 'member', 'user', 'user_name', 'user_email', 'user_photo', 'assigned_at']
        read_only_fields = ['id', 'assigned_at']

    def get_user_name(self, obj):
        if not obj.member or not obj.member.user:
            return ''
        name = f"{obj.member.user.first_name} {obj.member.user.last_name}".strip()
        return name if name else obj.member.user.email

    def get_user_photo(self, obj):
        if not obj.member or not obj.member.user:
            return None
        return getattr(obj.member.user, 'profilePhoto', None)


# --------------------------------------------------------------------------------
# Project Story Serializer
# --------------------------------------------------------------------------------
class ProjectStorySerializer(serializers.ModelSerializer):
    status_detail = ProjectStatusOptionSerializer(source='status', read_only=True)
    epic_detail = ProjectEpicSerializer(source='epic', read_only=True)
    sprint_detail = ProjectSprintSerializer(source='sprint', read_only=True)
    story_members = ProjectStoryMemberSerializer(many=True, read_only=True)
    tasks = ProjectTaskSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectStory
        fields = [
            'id', 'project', 'epic', 'epic_detail', 'sprint', 'sprint_detail',
            'story_key', 'title', 'description', 'acceptance_criteria',
            'work_type', 'priority', 'story_points', 'due_date', 'labels',
            'department', 'status', 'status_detail', 'order', 'progress',
            'story_members', 'tasks', 'created_at'
        ]
        read_only_fields = ['id', 'story_key', 'progress', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['project'].queryset = projects_for_user(user)
            self.fields['epic'].queryset = epics_for_user(user)
            self.fields['sprint'].queryset = sprints_for_user(user)
            self.fields['status'].queryset = ProjectStatusOption.objects.filter(company=user.organization, is_active=True)

    def validate_story_points(self, value):
        if value is not None and value not in FIBONACCI_STORY_POINTS:
            raise serializers.ValidationError(f"Story points must be one of {FIBONACCI_STORY_POINTS}")
        return value

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user if request else None
        if user:
            project = attrs.get('project') or (self.instance.project if self.instance else None)
            if project:
                if not projects_for_user(user).filter(id=project.id).exists():
                    raise serializers.ValidationError({"project": "Project not found or inaccessible."})
                
                epic = attrs.get('epic')
                if epic and epic.project != project:
                    raise serializers.ValidationError({"epic": "Epic does not belong to the selected project."})
                
                sprint = attrs.get('sprint')
                if sprint and sprint.project != project:
                    raise serializers.ValidationError({"sprint": "Sprint does not belong to the selected project."})
                
                status_opt = attrs.get('status')
                if status_opt and status_opt.company != user.organization:
                    raise serializers.ValidationError({"status": "Status option not found or invalid."})

        if not self.instance:
            if not attrs.get('acceptance_criteria'):
                attrs['acceptance_criteria'] = 'Acceptance criteria to be verified by team.'
            if not attrs.get('priority'):
                attrs['priority'] = 'Medium'
            if not attrs.get('story_points') or attrs.get('story_points') not in FIBONACCI_STORY_POINTS:
                attrs['story_points'] = 2
        else:
            if 'acceptance_criteria' in attrs and (not attrs['acceptance_criteria'] or not str(attrs['acceptance_criteria']).strip()):
                raise serializers.ValidationError({"acceptance_criteria": "Acceptance Criteria cannot be empty."})
            if 'story_points' in attrs and attrs['story_points'] not in FIBONACCI_STORY_POINTS:
                raise serializers.ValidationError({"story_points": f"Valid Fibonacci Story Points required ({FIBONACCI_STORY_POINTS})."})
        return attrs


# --------------------------------------------------------------------------------
# Project Serializers
# --------------------------------------------------------------------------------
class ProjectListSerializer(serializers.ModelSerializer):
    project_manager_name = serializers.SerializerMethodField(read_only=True)
    project_manager_photo = serializers.SerializerMethodField(read_only=True)
    team_lead_name = serializers.SerializerMethodField(read_only=True)
    team_lead_photo = serializers.SerializerMethodField(read_only=True)
    status_detail = ProjectStatusOptionSerializer(source='status', read_only=True)
    active_sprint_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'company', 'name', 'key', 'project_type', 'description',
            'project_manager', 'project_manager_name', 'project_manager_photo',
            'team_lead', 'team_lead_name', 'team_lead_photo',
            'status', 'status_detail',
            'start_date', 'end_date', 'progress', 'active_sprint_name'
        ]
        read_only_fields = ['id', 'company', 'key', 'progress']

    def get_project_manager_name(self, obj):
        if not obj.project_manager:
            return None
        name = f"{obj.project_manager.first_name} {obj.project_manager.last_name}".strip()
        return name if name else obj.project_manager.email

    def get_project_manager_photo(self, obj):
        if not obj.project_manager:
            return None
        return getattr(obj.project_manager, 'profilePhoto', None)

    def get_team_lead_name(self, obj):
        if not obj.team_lead:
            return None
        name = f"{obj.team_lead.first_name} {obj.team_lead.last_name}".strip()
        return name if name else obj.team_lead.email

    def get_team_lead_photo(self, obj):
        if not obj.team_lead:
            return None
        return getattr(obj.team_lead, 'profilePhoto', None)

    def get_active_sprint_name(self, obj):
        active = None
        if hasattr(obj, '_prefetched_objects_cache') and 'sprints' in obj._prefetched_objects_cache:
            for s in obj.sprints.all():
                if s.status == 'active':
                    active = s
                    break
        else:
            active = obj.sprints.filter(status='active').first()
        return active.name if active else None


class ProjectDetailSerializer(ProjectListSerializer):
    members = ProjectMemberSerializer(many=True, read_only=True)
    epics = ProjectEpicSerializer(many=True, read_only=True)
    active_sprint = serializers.SerializerMethodField(read_only=True)

    class Meta(ProjectListSerializer.Meta):
        fields = ProjectListSerializer.Meta.fields + ['members', 'epics', 'active_sprint']

    def get_active_sprint(self, obj):
        active = None
        if hasattr(obj, '_prefetched_objects_cache') and 'sprints' in obj._prefetched_objects_cache:
            for s in obj.sprints.all():
                if s.status == 'active':
                    active = s
                    break
        else:
            active = obj.sprints.filter(status='active').first()
        return ProjectSprintSerializer(active, context=self.context).data if active else None


class ProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['name', 'key', 'project_type', 'description', 'start_date', 'end_date', 'project_manager', 'team_lead']
        read_only_fields = ['key']

    def validate_project_manager(self, value):
        if value:
            request = self.context.get('request')
            if request and request.user and request.user.organization:
                if value.organization != request.user.organization:
                    raise serializers.ValidationError("Selected Project Manager must belong to your company.")
                if not value.is_active:
                    raise serializers.ValidationError("Selected Project Manager is not an active employee.")
        return value

    def validate_team_lead(self, value):
        if value:
            request = self.context.get('request')
            if request and request.user and request.user.organization:
                if value.organization != request.user.organization:
                    raise serializers.ValidationError("Selected Team Lead must belong to your company.")
                if not value.is_active:
                    raise serializers.ValidationError("Selected Team Lead is not an active employee.")
        return value


class ProjectUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['name', 'key', 'project_type', 'description', 'status', 'start_date', 'end_date', 'project_manager', 'team_lead']
        read_only_fields = ['key']

    def validate_project_manager(self, value):
        if value:
            request = self.context.get('request')
            if request and request.user and request.user.organization:
                if value.organization != request.user.organization:
                    raise serializers.ValidationError("Selected Project Manager must belong to your company.")
                if not value.is_active:
                    raise serializers.ValidationError("Selected Project Manager is not an active employee.")
        return value

    def validate_team_lead(self, value):
        if value:
            request = self.context.get('request')
            if request and request.user and request.user.organization:
                if value.organization != request.user.organization:
                    raise serializers.ValidationError("Selected Team Lead must belong to your company.")
                if not value.is_active:
                    raise serializers.ValidationError("Selected Team Lead is not an active employee.")
        return value


# --------------------------------------------------------------------------------
# Project Attachment Serializer
# --------------------------------------------------------------------------------
class ProjectAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField(read_only=True)
    uploaded_by_info = serializers.SerializerMethodField(read_only=True)
    file_url = serializers.SerializerMethodField(read_only=True)
    url = serializers.SerializerMethodField(read_only=True)
    download_url = serializers.SerializerMethodField(read_only=True)
    mime_type = serializers.SerializerMethodField(read_only=True)
    size_bytes = serializers.IntegerField(source='file_size', read_only=True)
    name = serializers.CharField(source='file_name', read_only=True)

    class Meta:
        model = ProjectAttachment
        fields = [
            'id', 'project', 'epic', 'story', 'task', 'comment', 'draft_token', 'is_temporary',
            'is_inline', 'file', 'file_url', 'url', 'download_url', 'file_name',
            'name', 'file_size', 'size_bytes', 'mime_type', 'uploaded_by',
            'uploaded_by_name', 'uploaded_by_info', 'created_at'
        ]
        read_only_fields = ['id', 'file_name', 'file_size', 'uploaded_by', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            p_qs = projects_for_user(user)
            self.fields['project'].queryset = p_qs
            self.fields['epic'].queryset = epics_for_user(user)
            self.fields['story'].queryset = stories_for_user(user)
            self.fields['task'].queryset = tasks_for_user(user)
            self.fields['comment'].queryset = ProjectComment.objects.filter(
                Q(epic__project__in=p_qs) |
                Q(story__project__in=p_qs) |
                Q(task__story__project__in=p_qs) |
                Q(subtask__task__story__project__in=p_qs)
            )

    def validate_draft_token(self, value):
        if value:
            try:
                import uuid
                uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError):
                raise serializers.ValidationError("Must be a valid UUID.")
        return value

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by:
            return 'Unknown'
        name = f"{obj.uploaded_by.first_name} {obj.uploaded_by.last_name}".strip()
        return name if name else obj.uploaded_by.email

    def get_uploaded_by_info(self, obj):
        if not obj.uploaded_by:
            return {'id': None, 'name': 'Unknown'}
        return {
            'id': obj.uploaded_by.id,
            'name': self.get_uploaded_by_name(obj),
        }

    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        relative_url = f"/api/v1/attachments/{obj.id}/download/"
        if request:
            return request.build_absolute_uri(relative_url)
        return relative_url

    def get_url(self, obj):
        return self.get_file_url(obj)

    def get_download_url(self, obj):
        return self.get_file_url(obj)

    def get_mime_type(self, obj):
        import mimetypes
        if obj.file_name:
            mime, _ = mimetypes.guess_type(obj.file_name)
            if mime:
                return mime
        return 'application/octet-stream'

    def validate_file(self, value):
        if value and value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("File size exceeds maximum limit of 10MB.")
        return value

    def validate(self, attrs):
        project = attrs.get('project') or getattr(self.instance, 'project', None)
        epic = attrs.get('epic') or getattr(self.instance, 'epic', None)
        story = attrs.get('story') or getattr(self.instance, 'story', None)
        task = attrs.get('task') or getattr(self.instance, 'task', None)
        comment = attrs.get('comment') or getattr(self.instance, 'comment', None)

        targets = [bool(project), bool(epic), bool(story), bool(task), bool(comment)]
        if sum(targets) > 1:
            raise serializers.ValidationError("Attachment must target at most one entity (project, epic, story, task, or comment).")
        return attrs


# --------------------------------------------------------------------------------
# Project Comment Serializer
# --------------------------------------------------------------------------------
class ProjectCommentSerializer(serializers.ModelSerializer):
    comment = serializers.CharField(required=False, allow_blank=True, default='', allow_null=True)
    user_name = serializers.SerializerMethodField(read_only=True)
    attachments = ProjectAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectComment
        fields = [
            'id', 'epic', 'story', 'task', 'subtask', 'user',
            'user_name', 'comment', 'client_message_id', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']
        extra_kwargs = {
            'comment': {'required': False, 'allow_blank': True, 'allow_null': True}
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            p_qs = projects_for_user(user)
            self.fields['epic'].queryset = epics_for_user(user)
            self.fields['story'].queryset = stories_for_user(user)
            self.fields['task'].queryset = tasks_for_user(user)
            self.fields['subtask'].queryset = ProjectSubtask.objects.filter(task__story__project__in=p_qs)

    def get_user_name(self, obj):
        name = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return name if name else obj.user.email

    def validate(self, attrs):
        epic = attrs.get('epic') or getattr(self.instance, 'epic', None)
        story = attrs.get('story') or getattr(self.instance, 'story', None)
        task = attrs.get('task') or getattr(self.instance, 'task', None)
        subtask = attrs.get('subtask') or getattr(self.instance, 'subtask', None)

        targets = [bool(epic), bool(story), bool(task), bool(subtask)]
        if sum(targets) != 1:
            raise serializers.ValidationError("Comment must target exactly one entity (epic, story, task, or subtask).")

        comment_text = (attrs.get('comment') or '').strip()
        attachment_ids = None
        draft_token = None
        if hasattr(self, 'initial_data') and isinstance(self.initial_data, dict):
            attachment_ids = self.initial_data.get('attachment_ids')
            draft_token = self.initial_data.get('draft_token')

        has_attachments = False
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if attachment_ids and isinstance(attachment_ids, list) and len(attachment_ids) > 0:
            has_attachments = True
        elif draft_token and user:
            from projects.models import ProjectAttachment
            has_attachments = ProjectAttachment.objects.filter(draft_token=draft_token, uploaded_by=user).exists()

        if not comment_text and not has_attachments:
            raise serializers.ValidationError({"comment": ["Comment text or at least one attachment is required."]})

        return attrs



# Legacy compatibility aliases
ProjectStoryListSerializer = ProjectStorySerializer
ProjectStoryDetailSerializer = ProjectStorySerializer
ProjectStoryCreateSerializer = ProjectStorySerializer
ProjectStoryUpdateSerializer = ProjectStorySerializer

ProjectTaskListSerializer = ProjectTaskSerializer
ProjectTaskDetailSerializer = ProjectTaskSerializer
ProjectTaskCreateSerializer = ProjectTaskSerializer


# --------------------------------------------------------------------------------
# Retrospective & Daily Stand-up & VCS Serializers
# --------------------------------------------------------------------------------
class ProjectRetrospectiveItemSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProjectRetrospectiveItem
        fields = [
            'id', 'retrospective', 'category', 'text', 'votes',
            'converted_story', 'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['retrospective'].queryset = ProjectRetrospective.objects.filter(project__in=projects_for_user(user))
            self.fields['converted_story'].queryset = stories_for_user(user)

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return 'Anonymous' if getattr(obj.retrospective, 'is_anonymous', False) else 'Team Member'
        return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip() or obj.created_by.email


class ProjectRetrospectiveSerializer(serializers.ModelSerializer):
    items = ProjectRetrospectiveItemSerializer(many=True, read_only=True)
    sprint_name = serializers.ReadOnlyField(source='sprint.name')

    class Meta:
        model = ProjectRetrospective
        fields = [
            'id', 'project', 'sprint', 'sprint_name', 'status',
            'happiness_score', 'is_anonymous', 'created_by', 'created_at', 'closed_at', 'items'
        ]
        read_only_fields = ['id', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['project'].queryset = projects_for_user(user)
            self.fields['sprint'].queryset = sprints_for_user(user)

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user if request else None
        if user:
            project = attrs.get('project') or (self.instance.project if self.instance else None)
            if project:
                if not projects_for_user(user).filter(id=project.id).exists():
                    raise serializers.ValidationError({"project": "Project not found or inaccessible."})
                
                sprint = attrs.get('sprint')
                if sprint and sprint.project != project:
                    raise serializers.ValidationError({"sprint": "Sprint does not belong to the selected project."})
        return attrs


ProjectTaskUpdateSerializer = ProjectTaskSerializer

class ProjectTaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.PrimaryKeyRelatedField(queryset=ProjectStatusOption.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            self.fields['status'].queryset = ProjectStatusOption.objects.filter(company=user.organization, is_active=True)
