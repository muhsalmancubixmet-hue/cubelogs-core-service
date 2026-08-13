# --------------------------------------------------------------------------------
#       Projects Admin Configuration
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin

# THIRD PARTY

# APPLICATION SPECIFIC
from projects.models import Project, ProjectMember, ProjectStory, ProjectStoryMember, ProjectTask, ProjectStatusOption


# --------------------------------------------------------------------------------
# ProjectStatusOptionAdmin: Django admin panel for company-level status options
# --------------------------------------------------------------------------------
@admin.register(ProjectStatusOption)
class ProjectStatusOptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'code', 'category', 'scope', 'order', 'is_system', 'is_default', 'is_active', 'company')
    list_filter = ('category', 'scope', 'is_system', 'is_active')
    search_fields = ('name', 'code', 'company__name')
    readonly_fields = ('is_system',)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)


# --------------------------------------------------------------------------------
# ProjectAdmin: Django admin panel configuration for Project models
# --------------------------------------------------------------------------------
@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'company', 'project_manager', 'team_lead', 'status', 'progress')
    list_filter = ('company', 'status')
    search_fields = ('name', 'description')


# --------------------------------------------------------------------------------
# ProjectMemberAdmin: Django admin panel configuration for ProjectMember models
# --------------------------------------------------------------------------------
@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'project', 'user', 'project_role', 'department', 'is_active')
    list_filter = ('project_role', 'is_active')
    search_fields = ('user__email', 'project__name')


# --------------------------------------------------------------------------------
# ProjectStoryAdmin: Django admin panel configuration for ProjectStory models
# --------------------------------------------------------------------------------
@admin.register(ProjectStory)
class ProjectStoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'department', 'status', 'progress', 'order')
    list_filter = ('status', 'department')
    search_fields = ('title', 'project__name')


# --------------------------------------------------------------------------------
# ProjectStoryMemberAdmin: Django admin panel for story-level member assignments
# --------------------------------------------------------------------------------
@admin.register(ProjectStoryMember)
class ProjectStoryMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'story', 'member', 'assigned_by', 'assigned_at')
    list_filter = ('story__project',)
    search_fields = ('story__title', 'member__user__email')


# --------------------------------------------------------------------------------
# ProjectTaskAdmin: Django admin panel configuration for ProjectTask models
# --------------------------------------------------------------------------------
@admin.register(ProjectTask)
class ProjectTaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'story', 'assigned_to', 'priority', 'status', 'due_date')
    list_filter = ('priority', 'status')
    search_fields = ('title', 'story__title')
