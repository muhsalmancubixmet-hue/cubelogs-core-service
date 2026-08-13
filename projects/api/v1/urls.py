# --------------------------------------------------------------------------------
#       Projects API v1 URLs
# --------------------------------------------------------------------------------

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from projects.api.v1.views import (
    ProjectViewSet, ProjectEpicViewSet, ProjectSprintViewSet, ProjectStoryViewSet,
    ProjectTaskViewSet, ProjectSubtaskViewSet, ProjectStatusOptionViewSet,
    ProjectCommentViewSet, ProjectAttachmentViewSet, EmployeeAssignmentsAPIView,
    ProjectRetrospectiveViewSet
)

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'epics', ProjectEpicViewSet, basename='project-epic')
router.register(r'project-sprints', ProjectSprintViewSet, basename='project-sprint')
router.register(r'stories', ProjectStoryViewSet, basename='project-story')
router.register(r'project-tasks', ProjectTaskViewSet, basename='project-task')
router.register(r'subtasks', ProjectSubtaskViewSet, basename='project-subtask')
router.register(r'project-statuses', ProjectStatusOptionViewSet, basename='project-status')
router.register(r'comments', ProjectCommentViewSet, basename='project-comment')
router.register(r'attachments', ProjectAttachmentViewSet, basename='project-attachment')
router.register(r'retrospectives', ProjectRetrospectiveViewSet, basename='project-retrospective')

urlpatterns = [
    path('employee-assignments/<int:employee_id>/', EmployeeAssignmentsAPIView.as_view(), name='employee-assignments'),
    path('', include(router.urls)),
]
