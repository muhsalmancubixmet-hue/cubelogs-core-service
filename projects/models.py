# --------------------------------------------------------------------------------
#       Projects Models
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import BaseModel
from projects.constants import FIBONACCI_STORY_POINTS, SPRINT_STATUS_CHOICES, SPRINT_STATUS_PLANNING


# --------------------------------------------------------------------------------
# ProjectStatusOption Model: Company-scoped reusable status pool
# --------------------------------------------------------------------------------
class ProjectStatusOption(models.Model):
    """
    Company-level reusable status records.
    - System statuses (is_system=True) cannot be renamed, deleted or deactivated.
    - Category field drives progress calculation:
        pending   → 0%
        active    → 50%
        completed → 100%
    """
    CATEGORY_CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('completed', 'Completed'),
    ]
    SCOPE_CHOICES = [
        ('project', 'Project'),
        ('epic', 'Epic'),
        ('story', 'Story'),
        ('task', 'Task'),
        ('all', 'All'),
    ]

    company = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='project_status_options'
    )
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='pending')
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='all')
    order = models.PositiveIntegerField(default=0)
    progress_percentage = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'api_project_status_option'
        ordering = ['order', 'id']
        unique_together = ('company', 'code')
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'scope']),
            models.Index(fields=['is_system']),
        ]

    def clean(self):
        super().clean()
        if self.progress_percentage < 0 or self.progress_percentage > 100:
            raise ValidationError({'progress_percentage': 'Progress percentage must be between 0 and 100.'})
        if self.is_system:
            if self.category == 'pending' and self.progress_percentage != 0:
                raise ValidationError({'progress_percentage': 'System status for Pending category must remain 0%.'})
            if self.category == 'completed' and self.progress_percentage != 100:
                raise ValidationError({'progress_percentage': 'System status for Completed category must remain 100%.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.company.name})"


# --------------------------------------------------------------------------------
# Project Model: Core project entity with organization scope & assigned leaders
# --------------------------------------------------------------------------------
class Project(BaseModel):
    PROJECT_TYPE_CHOICES = (
        ('Internal', 'Internal'),
        ('Client', 'Client'),
        ('Maintenance', 'Maintenance'),
        ('Research', 'Research'),
    )

    company = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=255)
    key = models.CharField(max_length=10, blank=True, null=True)  # e.g., 'ATT-0001'
    project_type = models.CharField(max_length=50, choices=PROJECT_TYPE_CHOICES, default='Internal')
    description = models.TextField(blank=True, null=True)
    project_manager = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects'
    )
    team_lead = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='led_projects'
    )
    status = models.ForeignKey(
        ProjectStatusOption, on_delete=models.PROTECT, null=True, blank=True, related_name='projects'
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    progress = models.FloatField(default=0.0)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_projects'
    )

    class Meta:
        db_table = 'api_project'
        ordering = ['-id']
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['team_lead']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.key and self.name and self.company_id:
            from projects.utils import generate_unique_project_key
            self.key = generate_unique_project_key(self.company, self.name)
        if not self.status_id and self.company_id:
            from projects.services.statuses import get_default_status
            self.status = get_default_status(self.company)
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------------
# ProjectMember Model: Maps employee memberships and roles within a project
# --------------------------------------------------------------------------------
class ProjectMember(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='project_memberships')
    project_role = models.CharField(max_length=100, default='Developer')
    department = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_member'
        unique_together = ('project', 'user')
        indexes = [
            models.Index(fields=['project', 'user']),
            models.Index(fields=['is_active']),
            models.Index(fields=['department']),
        ]

    def __str__(self):
        return f"{self.user.email} in {self.project.name} ({self.project_role})"


# --------------------------------------------------------------------------------
# ProjectEpic Model: Higher-level feature groupings attached to a project
# --------------------------------------------------------------------------------
class ProjectEpic(BaseModel):
    class PriorityChoices(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'
        URGENT = 'Urgent', 'Urgent'

    company = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='epics')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='epics')
    title = models.CharField(max_length=255)
    key = models.CharField(max_length=20, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=20, default='#3b82f6')
    status = models.ForeignKey(
        ProjectStatusOption, on_delete=models.PROTECT, null=True, blank=True, related_name='epics'
    )
    priority = models.CharField(max_length=50, choices=PriorityChoices.choices, default=PriorityChoices.MEDIUM)
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    order = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_epics'
    )

    class Meta:
        db_table = 'api_project_epic'
        ordering = ['order', '-id']
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['company']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.project.name} - Epic: {self.title}"


# --------------------------------------------------------------------------------
# ProjectSprint Model: Timeboxed Scrum iterations for a project
# --------------------------------------------------------------------------------
class ProjectSprint(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sprints')
    name = models.CharField(max_length=255)
    goal = models.TextField(blank=True, null=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_sprints'
    )
    cancellation_reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=SPRINT_STATUS_CHOICES, default=SPRINT_STATUS_PLANNING)
    capacity = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_sprints'
    )

    class Meta:
        db_table = 'api_project_sprint'
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(
                fields=['project'],
                condition=models.Q(status='active'),
                name='unique_active_sprint_per_project'
            )
        ]
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.project.name} - {self.name} ({self.get_status_display()})"


# --------------------------------------------------------------------------------
# ProjectStory Model: User stories belonging to a project, optional epic & sprint
# --------------------------------------------------------------------------------
class ProjectStory(BaseModel):
    class PriorityChoices(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'
        URGENT = 'Urgent', 'Urgent'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='stories')
    epic = models.ForeignKey(ProjectEpic, on_delete=models.SET_NULL, null=True, blank=True, related_name='stories')
    sprint = models.ForeignKey(ProjectSprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='stories')
    story_key = models.CharField(max_length=30, blank=True, null=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    acceptance_criteria = models.TextField(blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    status = models.ForeignKey(
        ProjectStatusOption, on_delete=models.PROTECT, null=True, blank=True, related_name='stories'
    )
    priority = models.CharField(max_length=50, choices=PriorityChoices.choices, default=PriorityChoices.MEDIUM)
    story_points = models.PositiveIntegerField(default=0)
    order = models.IntegerField(default=0)
    progress = models.FloatField(default=0.0)
    work_type = models.CharField(max_length=50, default='Feature')
    due_date = models.DateField(null=True, blank=True)
    labels = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_stories'
    )

    class Meta:
        db_table = 'api_project_story'
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['sprint']),
            models.Index(fields=['epic']),
            models.Index(fields=['status']),
            # Composite indexes for board column grouping (project+status) and sprint backlog (project+sprint)
            models.Index(fields=['project', 'status'], name='story_project_status_idx'),
            models.Index(fields=['project', 'sprint'], name='story_project_sprint_idx'),
        ]

    def __str__(self):
        return f"{self.project.name} - {self.title}"

    def clean(self):
        if self.story_points and self.story_points not in FIBONACCI_STORY_POINTS:
            raise ValidationError({'story_points': f"Story points must be one of {FIBONACCI_STORY_POINTS}"})


# --------------------------------------------------------------------------------
# ProjectStoryMember Model: Explicit assignment of project members to a story
# --------------------------------------------------------------------------------
class ProjectStoryMember(models.Model):
    story = models.ForeignKey(
        ProjectStory, on_delete=models.CASCADE, related_name='story_members'
    )
    member = models.ForeignKey(
        ProjectMember, on_delete=models.CASCADE, related_name='story_assignments'
    )
    assigned_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, related_name='assigned_story_members'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_story_member'
        constraints = [
            models.UniqueConstraint(fields=['story', 'member'], name='unique_story_member')
        ]
        indexes = [
            models.Index(fields=['story']),
            models.Index(fields=['member']),
        ]

    def __str__(self):
        return f"{self.member.user.email} → {self.story.title}"


# --------------------------------------------------------------------------------
# ProjectTask Model: Tasks belonging to a user story
# --------------------------------------------------------------------------------
class ProjectTask(BaseModel):
    class PriorityChoices(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'
        URGENT = 'Urgent', 'Urgent'

    story = models.ForeignKey(ProjectStory, on_delete=models.CASCADE, related_name='tasks')
    task_key = models.CharField(max_length=40, blank=True, null=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    assigned_to = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='project_tasks'
    )
    priority = models.CharField(max_length=50, choices=PriorityChoices.choices, default=PriorityChoices.MEDIUM)
    status = models.ForeignKey(
        ProjectStatusOption, on_delete=models.PROTECT, null=True, blank=True, related_name='tasks'
    )
    estimated_hours = models.FloatField(default=0.0)
    logged_hours = models.FloatField(default=0.0)
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_project_tasks'
    )

    class Meta:
        db_table = 'api_project_task'
        ordering = ['-id']
        indexes = [
            models.Index(fields=['story']),
            models.Index(fields=['assigned_to']),
            models.Index(fields=['status']),
            models.Index(fields=['due_date']),
            # Composite indexes for Scrum board task column grouping and backlog assignment queries
            models.Index(fields=['story', 'status'], name='task_story_status_idx'),
            models.Index(fields=['story', 'assigned_to'], name='task_story_assignee_idx'),
        ]

    def __str__(self):
        return self.title


# --------------------------------------------------------------------------------
# ProjectSubtask Model: Granular task breakdowns
# --------------------------------------------------------------------------------
class ProjectSubtask(BaseModel):
    task = models.ForeignKey(ProjectTask, on_delete=models.CASCADE, related_name='subtasks')
    title = models.CharField(max_length=255)
    is_completed = models.BooleanField(default=False)
    assigned_to = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='project_subtasks'
    )
    estimated_hours = models.FloatField(default=0.0)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_subtasks'
    )

    class Meta:
        db_table = 'api_project_subtask'
        ordering = ['id']
        indexes = [
            models.Index(fields=['task']),
            models.Index(fields=['is_completed']),
        ]

    def __str__(self):
        return f"{self.task.title} - Subtask: {self.title}"


# --------------------------------------------------------------------------------
# ProjectComment Model: Explicit nullable FK relations to Epic, Story, Task, Subtask
# --------------------------------------------------------------------------------
class ProjectComment(models.Model):
    epic = models.ForeignKey(ProjectEpic, on_delete=models.CASCADE, null=True, blank=True, related_name='comments')
    story = models.ForeignKey(ProjectStory, on_delete=models.CASCADE, null=True, blank=True, related_name='comments')
    task = models.ForeignKey(ProjectTask, on_delete=models.CASCADE, null=True, blank=True, related_name='comments')
    subtask = models.ForeignKey(ProjectSubtask, on_delete=models.CASCADE, null=True, blank=True, related_name='comments')
    user = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='project_comments')
    comment = models.TextField()
    client_message_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'api_project_comment'
        ordering = ['created_at']
        indexes = [
            # Composite indexes for chronological comment timeline retrieval per story/task
            models.Index(fields=['story', 'created_at'], name='comment_story_created_idx'),
            models.Index(fields=['task', 'created_at'], name='comment_task_created_idx'),
        ]

    def clean(self):
        targets = [bool(self.epic_id), bool(self.story_id), bool(self.task_id), bool(self.subtask_id)]
        if sum(targets) != 1:
            raise ValidationError("A comment must be linked to exactly one entity (epic, story, task, or subtask).")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Comment by {self.user.email} at {self.created_at}"


from django.core.files.storage import FileSystemStorage
from django.conf import settings

private_storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


# --------------------------------------------------------------------------------
# ProjectAttachment Model: Explicit nullable FK relations to Project, Epic, Story or Task
# --------------------------------------------------------------------------------
class ProjectAttachment(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    epic = models.ForeignKey(ProjectEpic, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    story = models.ForeignKey(ProjectStory, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    task = models.ForeignKey(ProjectTask, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    comment = models.ForeignKey(ProjectComment, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')

    # Temporary draft upload fields
    draft_token = models.UUIDField(null=True, blank=True, db_index=True)
    company = models.ForeignKey('core.Organization', on_delete=models.CASCADE, null=True, blank=True, related_name='project_attachments')
    is_temporary = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    is_inline = models.BooleanField(default=False)
    file = models.FileField(upload_to='project_attachments/%Y/%m/', storage=private_storage)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey('users.Employee', on_delete=models.CASCADE, related_name='project_attachments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_attachment'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['draft_token']),
            models.Index(fields=['is_temporary', 'expires_at']),
        ]

    def clean(self):
        persisted_targets = [bool(self.project_id), bool(self.epic_id), bool(self.story_id), bool(self.task_id)]
        persisted_count = sum(persisted_targets)
        has_draft = bool(self.draft_token)
        has_comment = bool(self.comment_id)

        if (persisted_count == 1 or has_comment) and not has_draft:
            pass
        elif persisted_count == 0 and has_draft:
            pass
        else:
            raise ValidationError("An attachment must target exactly one persisted entity (project, epic, story, task), comment, or one valid temporary draft token.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.file:
            try:
                self.file.delete(save=False)
            except Exception:
                pass
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"Attachment {self.file_name} by {self.uploaded_by.email}"



# --------------------------------------------------------------------------------
# ProjectActivity Model: Project activity log
# --------------------------------------------------------------------------------
class ProjectActivity(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='activities')
    user = models.ForeignKey('users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='project_activities')
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=50)
    entity_id = models.PositiveIntegerField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_activity'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['created_at']),
            # Composite index eliminates filesort when loading sorted project activity feed
            models.Index(fields=['project', 'created_at'], name='activity_project_created_idx'),
        ]

    def __str__(self):
        return f"{self.project.name} - {self.action} at {self.created_at}"


# --------------------------------------------------------------------------------
# ProjectSprintEvent Model: Historical event & snapshot records for Burndown & Velocity
# --------------------------------------------------------------------------------
class ProjectSprintEvent(models.Model):
    EVENT_TYPE_CHOICES = [
        ('sprint_started', 'Sprint Started'),
        ('story_added', 'Story Added'),
        ('story_removed', 'Story Removed'),
        ('points_changed', 'Points Changed'),
        ('story_completed', 'Story Completed'),
        ('sprint_completed', 'Sprint Completed'),
        ('daily_snapshot', 'Daily Snapshot'),
    ]

    sprint = models.ForeignKey(ProjectSprint, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES)
    story = models.ForeignKey(ProjectStory, on_delete=models.SET_NULL, null=True, blank=True, related_name='sprint_events')
    points_delta = models.IntegerField(default=0)
    total_points = models.IntegerField(default=0)
    completed_points = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_sprint_event'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['sprint']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.sprint.name} - {self.get_event_type_display()} ({self.created_at})"


# --------------------------------------------------------------------------------
# ProjectRetrospective Model: Sprint Retrospective Session
# --------------------------------------------------------------------------------
class ProjectRetrospective(BaseModel):
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('completed', 'Completed'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='retrospectives')
    sprint = models.ForeignKey(ProjectSprint, on_delete=models.CASCADE, related_name='retrospectives')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    happiness_score = models.FloatField(default=3.5)  # 1.0 to 5.0
    is_anonymous = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_retrospectives'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'api_project_retrospective'
        ordering = ['-created_at']

    def __str__(self):
        return f"Retrospective: {self.sprint.name}"


class ProjectRetrospectiveItem(models.Model):
    CATEGORY_CHOICES = [
        ('went_well', 'What Went Well'),
        ('didnt_go_well', 'What Didn\'t Go Well'),
        ('action_item', 'Action Item'),
        ('lesson_learned', 'Lesson Learned'),
    ]

    retrospective = models.ForeignKey(ProjectRetrospective, on_delete=models.CASCADE, related_name='items')
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    text = models.TextField()
    votes = models.PositiveIntegerField(default=0)
    converted_story = models.ForeignKey(
        ProjectStory, on_delete=models.SET_NULL, null=True, blank=True, related_name='retrospective_action_items'
    )
    approved_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_retro_items'
    )
    created_by = models.ForeignKey(
        'users.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_retro_items'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_project_retrospective_item'
        ordering = ['-votes', 'id']

    def __str__(self):
        return f"{self.get_category_display()}: {self.text[:30]}"



