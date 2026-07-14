from django.contrib import admin
from company.models import (
    CMSContent, LMSModule, PromoVideoSection, Testimonial, Lead, LeadHistory
)

# CMS Admin
admin.site.register(CMSContent)
admin.site.register(LMSModule)
admin.site.register(PromoVideoSection)
admin.site.register(Testimonial)

# CRM Admin
@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'companyName', 'status', 'assigned_staff', 'is_read')
    list_filter = ('status', 'is_read')
    search_fields = ('name', 'email', 'companyName')

@admin.register(LeadHistory)
class LeadHistoryAdmin(admin.ModelAdmin):
    list_display = ('lead', 'modified_by', 'action', 'timestamp')
    list_filter = ('action', 'timestamp')
    search_fields = ('lead__name', 'action')

