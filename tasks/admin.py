# --------------------------------------------------------------------------------
#       Tasks Admin
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin

# THIRD PARTY

# APPLICATION SPECIFIC
from tasks.models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'assignedName', 'dueDate', 'status')
    list_filter = ('status', 'dueDate')
    search_fields = ('title', 'assignedName')
