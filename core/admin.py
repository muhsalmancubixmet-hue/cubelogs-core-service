# --------------------------------------------------------------------------------
#       Core Admin
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.contrib import admin

# THIRD PARTY

# APPLICATION SPECIFIC
from core.models import OrgSettings, Organization, AuditLog, OrganizationModule, EmailLog, EmailQueue, EmailHistory


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


@admin.action(description="Send / Retry selected emails now")
def send_selected_emails(modeladmin, request, queryset):
    from django.core.mail import send_mail
    from django.conf import settings
    from django.utils import timezone

    success_count = 0
    fail_count = 0

    for item in queryset:
        try:
            send_mail(
                subject=item.subject,
                message=item.body or '',
                from_email=item.from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[item.recipient],
                fail_silently=False,
                html_message=item.body if '<' in (item.body or '') else None
            )
            item.status = 'SENT'
            item.sent_at = timezone.now()
            item.error_message = None
            item.save()
            success_count += 1
        except Exception as exc:
            item.status = 'FAILED'
            item.error_message = str(exc)
            item.save()
            fail_count += 1

    modeladmin.message_user(
        request,
        f"Email Dispatch Finished: {success_count} sent successfully, {fail_count} failed."
    )


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'created_at', 'sent_at')
    list_filter = ('status', 'created_at', 'sent_at')
    search_fields = ('recipient', 'subject', 'body', 'error_message')
    readonly_fields = ('created_at', 'updated_at', 'sent_at')
    actions = [send_selected_emails]


@admin.register(EmailQueue)
class EmailQueueAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'created_at')
    search_fields = ('recipient', 'subject', 'body')
    readonly_fields = ('created_at', 'updated_at', 'sent_at')
    actions = [send_selected_emails]


@admin.register(EmailHistory)
class EmailHistoryAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'created_at', 'sent_at')
    list_filter = ('status', 'created_at', 'sent_at')
    search_fields = ('recipient', 'subject', 'body', 'error_message')
    readonly_fields = ('created_at', 'updated_at', 'sent_at')
    actions = [send_selected_emails]


