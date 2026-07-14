# --------------------------------------------------------------------------------
#       Users Admin
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

# THIRD PARTY
from .models import Employee, Template

# APPLICATION SPECIFIC

@admin.register(Employee)
class EmployeeAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'designation', 'organization', 'is_staff', 'isSuperAdmin')
    list_filter = ('is_staff', 'is_superuser', 'isSuperAdmin', 'organization')
    search_fields = ('email', 'first_name', 'last_name', 'designation')
    ordering = ('email',)
    
    # Define custom fieldsets to display our extra fields
    fieldsets = UserAdmin.fieldsets + (
        ('Custom Profile Info', {
            'fields': ('phone', 'designation', 'profilePhoto', 'organization'),
        }),
        ('Custom Permissions', {
            'fields': ('isSuperAdmin', 'useDefaultPermissions', 'permissions'),
        }),
    )

@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
