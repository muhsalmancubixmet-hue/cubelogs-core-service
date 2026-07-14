# --------------------------------------------------------------------------------
#       Core Admin
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import OrgSettings, Organization, AuditLog, OrganizationModule


@admin.register(OrgSettings)
class OrgSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'subscriptionStatus', 'subscriptionExpiresAt', 'max_employees_allowed')
    list_filter = ('subscriptionStatus',)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'subdomain', 'settings')
    search_fields = ('name', 'subdomain')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('employeeName', 'action', 'createdAt', 'ipAddress')
    list_filter = ('action', 'createdAt')
    search_fields = ('employeeName', 'action', 'details')


@admin.register(OrganizationModule)
class OrganizationModuleAdmin(admin.ModelAdmin):
    list_display = ('organization', 'module_id', 'enabled', 'activated_at')
    list_filter = ('module_id', 'enabled')
    search_fields = ('organization__name', 'module_id')
