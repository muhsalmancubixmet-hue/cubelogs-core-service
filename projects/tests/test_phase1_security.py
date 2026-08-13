import hmac
import hashlib
import json
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule, AuditLog
from users.models import Employee, Role, PermissionFlag
from projects.models import (
    Project, ProjectMember, ProjectStory, ProjectStoryMember, ProjectTask,
    ProjectStatusOption, ProjectRetrospective, ProjectRetrospectiveItem, ProjectSprint
)
from projects.services.statuses import initialize_default_statuses


class Phase1SecurityTestCase(TestCase):
    def setUp(self):
        # 1. Setup Organization A
        self.settings_a = OrgSettings.objects.create(is_project_enabled=True)
        self.org_a = Organization.objects.create(name="Company A", subdomain="comp-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org_a)
        self.status_pending_a = ProjectStatusOption.objects.get(company=self.org_a, code='pending')
        self.status_testing_a = ProjectStatusOption.objects.get(company=self.org_a, code='testing')

        # 2. Setup Organization B
        self.settings_b = OrgSettings.objects.create(is_project_enabled=True)
        self.org_b = Organization.objects.create(name="Company B", subdomain="comp-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org_b)
        self.status_pending_b = ProjectStatusOption.objects.get(company=self.org_b, code='pending')

        # 3. Setup Users for Company A
        self.user_a_pm = Employee.objects.create_user(
            email="pm@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="PM",
            permissions=["projects:view", "projects:update", "project_stories:view", "project_tasks:view_all", "project_tasks:create", "project_tasks:update_all", "project_tasks:delete", "roles.view", "roles.create", "roles.edit", "roles.delete"]
        )
        self.user_a_member = Employee.objects.create_user(
            email="member@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="Member",
            permissions=["projects:view", "project_stories:view", "project_tasks:view_own", "project_tasks:update_own"]
        )
        # Setup User A without task create permission
        self.user_a_no_perm = Employee.objects.create_user(
            email="noperm@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="NoPerm",
            permissions=["projects:view", "project_stories:view"]
        )

        # 4. Setup User for Company B
        self.user_b_pm = Employee.objects.create_user(
            email="pm@compb.com", password="password123", organization=self.org_b,
            first_name="B", last_name="PM",
            permissions=["projects:view", "projects:update", "project_stories:view", "project_tasks:view_all", "project_tasks:create", "project_tasks:update_all", "project_tasks:delete"]
        )

        # 5. Setup Projects and Stories for Company A
        self.project_a = Project.objects.create(company=self.org_a, name="Project A", project_manager=self.user_a_pm, status=self.status_pending_a)
        self.member_a_pm = ProjectMember.objects.create(project=self.project_a, user=self.user_a_pm, is_active=True)
        self.member_a_user = ProjectMember.objects.create(project=self.project_a, user=self.user_a_member, is_active=True)
        
        self.story_a = ProjectStory.objects.create(project=self.project_a, title="Story A", status=self.status_pending_a, story_key="PROJA-1")
        ProjectStoryMember.objects.create(story=self.story_a, member=self.member_a_user, assigned_by=self.user_a_pm)

        # 6. Setup Projects and Stories for Company B
        self.project_b = Project.objects.create(company=self.org_b, name="Project B", project_manager=self.user_b_pm, status=self.status_pending_b)
        self.story_b = ProjectStory.objects.create(project=self.project_b, title="Story B", status=self.status_pending_b, story_key="PROJB-1")

        # Clients
        self.client_a_pm = APIClient()
        self.client_a_pm.force_authenticate(user=self.user_a_pm)

        self.client_a_no_perm = APIClient()
        self.client_a_no_perm.force_authenticate(user=self.user_a_no_perm)

    def test_task_creation_idor_prevention(self):
        # 1. Company A user + Company A story -> 201 Created
        initial_tasks_count = ProjectTask.objects.count()
        response = self.client_a_pm.post(
            "/api/v1/project-tasks/",
            {
                "story": self.story_a.id,
                "title": "Valid Task",
                "assigned_to": self.user_a_member.id,
                "status": self.status_pending_a.id
            },
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProjectTask.objects.count(), initial_tasks_count + 1)

        # 2. Company A user + Company B story -> 404 Not Found (blocked)
        response_blocked = self.client_a_pm.post(
            "/api/v1/project-tasks/",
            {
                "story": self.story_b.id,
                "title": "Vulnerable Task",
                "assigned_to": self.user_a_member.id,
                "status": self.status_pending_a.id
            },
            format="json"
        )
        self.assertEqual(response_blocked.status_code, status.HTTP_404_NOT_FOUND)
        
        # 3. Blocked request creates zero tasks
        # Total task count should remain initial + 1 (only the first one succeeded)
        self.assertEqual(ProjectTask.objects.count(), initial_tasks_count + 1)

        # 4. User without task-create permission -> blocked (403 or 404)
        response_no_perm = self.client_a_no_perm.post(
            "/api/v1/project-tasks/",
            {
                "story": self.story_a.id,
                "title": "No Permission Task",
                "status": self.status_pending_a.id
            },
            format="json"
        )
        self.assertIn(response_no_perm.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

        # 5. Assigned employee from another company -> 400 Bad Request
        response_wrong_emp = self.client_a_pm.post(
            "/api/v1/project-tasks/",
            {
                "story": self.story_a.id,
                "title": "Wrong Employee Task",
                "assigned_to": self.user_b_pm.id,
                "status": self.status_pending_a.id
            },
            format="json"
        )
        self.assertEqual(response_wrong_emp.status_code, status.HTTP_400_BAD_REQUEST)

        # 6. Status option from another company -> 400 Bad Request
        response_wrong_status = self.client_a_pm.post(
            "/api/v1/project-tasks/",
            {
                "story": self.story_a.id,
                "title": "Wrong Status Task",
                "status": self.status_pending_b.id
            },
            format="json"
        )
        self.assertEqual(response_wrong_status.status_code, status.HTTP_400_BAD_REQUEST)



    def test_auditlog_crashes_prevention(self):
        # 1. Custom Role ViewSet create action should not crash
        response_create = self.client_a_pm.post(
            "/api/roles/",
            {
                "name": "Custom Tester",
                "slug": "custom-tester",
                "label": "Tester",
                "description": "Custom role for testing",
                "organization": self.org_a.id,
                "permissions": []
            },
            format="json"
        )
        self.assertEqual(response_create.status_code, status.HTTP_201_CREATED)
        
        # Verify AuditLog created
        role_id = response_create.data["id"]
        audit_create = AuditLog.objects.filter(action="ROLE_CREATED", employee=self.user_a_pm).first()
        self.assertIsNotNone(audit_create)
        self.assertEqual(audit_create.employeeName, "A PM")

        # 2. Custom Role ViewSet update action should not crash
        response_update = self.client_a_pm.put(
            f"/api/roles/{role_id}/",
            {
                "name": "Custom Tester Updated",
                "slug": "custom-tester-updated",
                "label": "Tester Updated",
                "organization": self.org_a.id,
                "permissions": []
            },
            format="json"
        )
        self.assertEqual(response_update.status_code, status.HTTP_200_OK)
        audit_update = AuditLog.objects.filter(action="ROLE_UPDATED", employee=self.user_a_pm).first()
        self.assertIsNotNone(audit_update)

        # 3. Custom Role ViewSet duplicate action should not crash
        response_dup = self.client_a_pm.post(
            f"/api/roles/{role_id}/duplicate/",
            {
                "name": "Custom Tester Duplicate",
                "label": "Duplicate"
            },
            format="json"
        )
        self.assertEqual(response_dup.status_code, status.HTTP_201_CREATED)
        audit_dup = AuditLog.objects.filter(action="ROLE_DUPLICATED", employee=self.user_a_pm).first()
        self.assertIsNotNone(audit_dup)

        # 4. Custom Role ViewSet delete action should not crash
        response_del = self.client_a_pm.delete(f"/api/roles/{role_id}/")
        self.assertEqual(response_del.status_code, status.HTTP_204_NO_CONTENT)
        audit_del = AuditLog.objects.filter(action="ROLE_DELETED", employee=self.user_a_pm).first()
        self.assertIsNotNone(audit_del)

        # 5. Project Retrospective convert_to_story action should not crash
        sprint = ProjectSprint.objects.create(
            project=self.project_a,
            name="Sprint 1",
            start_date=timezone.now().date(),
            end_date=(timezone.now() + timezone.timedelta(days=14)).date()
        )
        retro = ProjectRetrospective.objects.create(
            project=self.project_a,
            sprint=sprint,
            created_by=self.user_a_pm
        )
        retro_item = ProjectRetrospectiveItem.objects.create(
            retrospective=retro,
            category="action_item",
            text="Create more tests",
            created_by=self.user_a_pm
        )
        response_convert = self.client_a_pm.post(
            "/api/v1/retrospectives/convert-to-story/",
            {
                "item_id": retro_item.id
            },
            format="json"
        )
        self.assertEqual(response_convert.status_code, status.HTTP_201_CREATED)
        audit_convert = AuditLog.objects.filter(action="PM Approved Retro Action Item", employee=self.user_a_pm).first()
        self.assertIsNotNone(audit_convert)
