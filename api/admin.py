from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from api.models import (
    Employee, AttendanceLog, Task, LeaveType, Leave,
    Holiday, Template, OfficeLocation, Schedule, OrgSettings,
    SubscriptionPackage, SubscriberAccount, Wallet, WalletTransaction,
    EmailQueue, EmailLog, PromoVideoSection, Testimonial
)

class CustomEmployeeAdmin(UserAdmin):
    model = Employee
    list_display = ('email', 'first_name', 'last_name', 'designation', 'raw_password', 'isSuperAdmin', 'is_staff', 'is_superuser')
    list_filter = ('isSuperAdmin', 'is_staff', 'is_superuser', 'designation')
    readonly_fields = ('raw_password',)
    fieldsets = UserAdmin.fieldsets + (
        ('Workforce Info', {'fields': ('phone', 'designation', 'raw_password', 'isSuperAdmin', 'useDefaultPermissions', 'permissions', 'profilePhoto')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Workforce Info', {
            'classes': ('wide',),
            'fields': ('email', 'phone', 'designation', 'raw_password', 'isSuperAdmin', 'useDefaultPermissions', 'permissions', 'profilePhoto')
        }),
    )
    ordering = ('email',)

admin.site.register(Employee, CustomEmployeeAdmin)
admin.site.register(AttendanceLog)
admin.site.register(Task)
admin.site.register(LeaveType)
admin.site.register(Leave)
admin.site.register(Holiday)
admin.site.register(Template)
admin.site.register(OfficeLocation)
admin.site.register(Schedule)
admin.site.register(OrgSettings)
admin.site.register(SubscriptionPackage)
admin.site.register(SubscriberAccount)
admin.site.register(Wallet)
admin.site.register(WalletTransaction)

@admin.register(EmailQueue)
class EmailQueueAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'task_id', 'created_at', 'sent_at')
    list_filter = ('status', 'created_at', 'sent_at')
    readonly_fields = ('task_id', 'created_at', 'sent_at', 'error_message')
    search_fields = ('recipient', 'subject', 'body', 'error_message')
    fieldsets = (
        ('Email Details', {
            'fields': ('recipient', 'from_email', 'subject', 'body', 'html_body')
        }),
        ('Execution Tracking', {
            'fields': ('status', 'task_id', 'created_at', 'sent_at', 'error_message')
        }),
    )


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'template_type', 'status', 'created_at')
    list_filter = ('status', 'template_type')
    search_fields = ('recipient', 'subject', 'password')

    def password(self, obj):
        return "********" if obj and obj.password else ""
    password.short_description = "Password"

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if obj:
            return [f for f in fields if f != 'password']
        return fields

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return [field.name for field in self.model._meta.fields if field.name not in ('status', 'password')]
        return self.readonly_fields


@admin.register(PromoVideoSection)
class PromoVideoSectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'youtube_url', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'description')


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'author_title', 'stars', 'is_approved', 'created_at')
    list_filter = ('is_approved', 'stars', 'created_at')
    search_fields = ('author_name', 'author_title', 'text')





