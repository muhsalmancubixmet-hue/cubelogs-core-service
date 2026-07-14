# --------------------------------------------------------------------------------
#       Attendance API Routing
# --------------------------------------------------------------------------------

# STANDARD LIBRARY

# DJANGO
from django.urls import path, include

# THIRD PARTY
from rest_framework.routers import DefaultRouter

# APPLICATION SPECIFIC
from attendance.api.v1.views import (
    AttendanceLogViewSet, LeaveTypeViewSet, LeaveViewSet,
    HolidayViewSet, TemplateViewSet, OfficeLocationViewSet, ScheduleViewSet,
    OrgSettingsViewSet, AuditLogViewSet, HolidaySettingsView,
    AttendanceApprovalView, HRAttendanceDashboardView
)

router = DefaultRouter()
router.register('attendance', AttendanceLogViewSet, basename='attendance')
router.register('leave-types', LeaveTypeViewSet, basename='leave-type')
router.register('leaves', LeaveViewSet, basename='leave')
router.register('holidays', HolidayViewSet, basename='holiday')
router.register('templates', TemplateViewSet, basename='template')
router.register('locations', OfficeLocationViewSet, basename='location')
router.register('schedules', ScheduleViewSet, basename='schedule')
router.register('audit-logs', AuditLogViewSet, basename='audit-log')

urlpatterns = [
    # HR Attendance Management
    path('attendance/hr-dashboard/', HRAttendanceDashboardView.as_view(), name='attendance-hr-dashboard'),
    path('attendance/<int:pk>/approve/', AttendanceApprovalView.as_view(), name='attendance-approve'),

    # Org Settings
    path('settings/', OrgSettingsViewSet.as_view({'get': 'list', 'put': 'update', 'patch': 'partial_update'}), name='org-settings'),
    path('settings/current/', OrgSettingsViewSet.as_view({'get': 'current_settings', 'put': 'current_settings', 'patch': 'current_settings'}), name='org-settings-current'),
    path('settings/holidays/', HolidaySettingsView.as_view(), name='holiday-settings'),

    path('', include(router.urls)),
]
