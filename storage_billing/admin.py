from django.contrib import admin
from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage


@admin.register(StorageFile)
class StorageFileAdmin(admin.ModelAdmin):
    list_display = ('id', 'original_filename', 'organization', 'size_bytes', 'status', 'uploaded_at', 'deleted_at')
    list_filter = ('status', 'source_module', 'storage_backend')
    search_fields = ('original_filename', 'file_path', 'source_object_id', 'organization__name')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(StorageEvent)
class StorageEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'storage_file', 'organization', 'event_type', 'size_bytes', 'occurred_at', 'actor')
    list_filter = ('event_type',)
    search_fields = ('storage_file__id', 'organization__name')
    readonly_fields = ('id', 'storage_file', 'organization', 'event_type', 'size_bytes', 'occurred_at', 'actor', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StorageDailyUsage)
class StorageDailyUsageAdmin(admin.ModelAdmin):
    list_display = (
        'organization', 'usage_date', 'billable_bytes', 'billable_gb',
        'storage_credits', 'storage_charge', 'is_finalized', 'calculated_at'
    )
    list_filter = ('is_finalized', 'usage_date')
    search_fields = ('organization__name',)
    readonly_fields = ('id', 'created_at', 'updated_at')
