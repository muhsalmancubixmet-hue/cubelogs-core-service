from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role
from projects.models import (
    Project, ProjectMember, ProjectEpic, ProjectSprint, ProjectStory,
    ProjectTask, ProjectSubtask, ProjectComment, ProjectAttachment,
    ProjectRetrospective, ProjectStatusOption
)
from projects.services.statuses import initialize_default_statuses


class Phase2SecurityTestCase(TestCase):
    def setUp(self):
        # 1. Setup Organization A
        self.settings_a = OrgSettings.objects.create(is_project_enabled=True)
        self.org_a = Organization.objects.create(name="Company A", subdomain="comp-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org_a)
        self.status_todo_a = ProjectStatusOption.objects.get(company=self.org_a, code='pending')
        self.status_completed_a = ProjectStatusOption.objects.get(company=self.org_a, code='completed')

        # 2. Setup Organization B
        self.settings_b = OrgSettings.objects.create(is_project_enabled=True)
        self.org_b = Organization.objects.create(name="Company B", subdomain="comp-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org_b)
        self.status_todo_b = ProjectStatusOption.objects.get(company=self.org_b, code='pending')

        # 3. Setup Users for Company A (all with global 'Employee' system role)
        # Note: permissions field maps to system role's effective permissions
        from users.roles import DEFAULT_ROLES
        employee_perms = DEFAULT_ROLES['Employee']['permissions']

        self.user_a_pm = Employee.objects.create_user(
            email="pm@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="PM", permissions=employee_perms
        )
        self.user_a_dev = Employee.objects.create_user(
            email="dev@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="Dev", permissions=employee_perms
        )
        self.user_a_viewer = Employee.objects.create_user(
            email="viewer@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="Viewer", permissions=employee_perms
        )
        self.user_a_other_proj_member = Employee.objects.create_user(
            email="other@compa.com", password="password123", organization=self.org_a,
            first_name="A", last_name="Other", permissions=employee_perms
        )

        # 4. Setup User for Company B
        self.user_b_pm = Employee.objects.create_user(
            email="pm@compb.com", password="password123", organization=self.org_b,
            first_name="B", last_name="PM", permissions=employee_perms
        )

        # 5. Setup Projects for Company A
        self.project_a1 = Project.objects.create(
            company=self.org_a, name="Project A1", project_manager=self.user_a_pm, status=self.status_todo_a
        )
        self.project_a2 = Project.objects.create(
            company=self.org_a, name="Project A2", project_manager=self.user_a_pm, status=self.status_todo_a
        )

        # Setup Project for Company B
        self.project_b = Project.objects.create(
            company=self.org_b, name="Project B", project_manager=self.user_b_pm, status=self.status_todo_b
        )

        # 6. Add Project Members to Project A1
        self.pm_member = ProjectMember.objects.create(project=self.project_a1, user=self.user_a_pm, project_role='Project Manager', is_active=True)
        self.dev_member = ProjectMember.objects.create(project=self.project_a1, user=self.user_a_dev, project_role='Developer', is_active=True)
        self.viewer_member = ProjectMember.objects.create(project=self.project_a1, user=self.user_a_viewer, project_role='Viewer', is_active=True)

        # Add Project Member to Project A2 only
        self.other_member = ProjectMember.objects.create(project=self.project_a2, user=self.user_a_other_proj_member, project_role='Developer', is_active=True)

        # Add Project Member to Project B
        self.pm_b_member = ProjectMember.objects.create(project=self.project_b, user=self.user_b_pm, project_role='Project Manager', is_active=True)

        # 7. Create Resources on Project A1
        self.epic_a1 = ProjectEpic.objects.create(project=self.project_a1, company=self.org_a, title="Epic A1", status=self.status_todo_a)
        self.sprint_a1 = ProjectSprint.objects.create(project=self.project_a1, name="Sprint A1", status="planning")
        self.story_a1 = ProjectStory.objects.create(project=self.project_a1, title="Story A1", status=self.status_todo_a)
        self.task_a1 = ProjectTask.objects.create(story=self.story_a1, title="Task A1", status=self.status_todo_a)
        self.subtask_a1 = ProjectSubtask.objects.create(task=self.task_a1, title="Subtask A1")
        self.comment_a1 = ProjectComment.objects.create(task=self.task_a1, user=self.user_a_pm, comment="Comment A1")
        self.retro_a1 = ProjectRetrospective.objects.create(project=self.project_a1, sprint=self.sprint_a1, status="draft")

        # 8. Create Resources on Project B
        self.epic_b = ProjectEpic.objects.create(project=self.project_b, company=self.org_b, title="Epic B", status=self.status_todo_b)
        self.sprint_b = ProjectSprint.objects.create(project=self.project_b, name="Sprint B", status="planning")
        self.story_b = ProjectStory.objects.create(project=self.project_b, title="Story B", status=self.status_todo_b)

        # Clients
        self.client_pm = APIClient()
        self.client_pm.force_authenticate(user=self.user_a_pm)

        self.client_dev = APIClient()
        self.client_dev.force_authenticate(user=self.user_a_dev)

        self.client_viewer = APIClient()
        self.client_viewer.force_authenticate(user=self.user_a_viewer)

        self.client_other_member = APIClient()
        self.client_other_member.force_authenticate(user=self.user_a_other_proj_member)

    def test_viewer_rbac_denied_writes(self):
        # 1. Viewer try to create story -> 403 Forbidden
        response = self.client_viewer.post(
            "/api/v1/stories/",
            {"project": self.project_a1.id, "title": "Viewer New Story", "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Viewer try to create task -> 403 Forbidden
        response = self.client_viewer.post(
            "/api/v1/project-tasks/",
            {"story": self.story_a1.id, "title": "Viewer New Task", "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Viewer try to update story -> 403 Forbidden
        response = self.client_viewer.patch(
            f"/api/v1/stories/{self.story_a1.id}/",
            {"title": "Updated by Viewer"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Viewer try to create comment -> 403 Forbidden
        response = self.client_viewer.post(
            "/api/v1/comments/",
            {"task": self.task_a1.id, "comment": "Viewer Comment"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_developer_rbac_restrictions(self):
        # 1. Developer can create comment -> 201 Created
        response = self.client_dev.post(
            "/api/v1/comments/",
            {"task": self.task_a1.id, "comment": "Dev Comment"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 2. Developer can create subtask -> 201 Created
        response = self.client_dev.post(
            "/api/v1/subtasks/",
            {"task": self.task_a1.id, "title": "Dev Subtask"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 3. Developer cannot create story -> 403 Forbidden
        response = self.client_dev.post(
            "/api/v1/stories/",
            {"project": self.project_a1.id, "title": "Dev Story", "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Developer cannot create epic -> 403 Forbidden
        response = self.client_dev.post(
            "/api/v1/epics/",
            {"project": self.project_a1.id, "title": "Dev Epic", "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_tenant_isolation_cross_company(self):
        # PM from Company A tries to get Company B's project story -> 404 Not Found
        response = self.client_pm.get(f"/api/v1/stories/{self.story_b.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # PM from Company A tries to update Company B's story -> 404 Not Found
        response = self.client_pm.patch(
            f"/api/v1/stories/{self.story_b.id}/",
            {"title": "Attacked"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # PM from Company A tries to create story linked to Company B's Epic -> 400 Bad Request
        response = self.client_pm.post(
            "/api/v1/stories/",
            {"project": self.project_a1.id, "title": "Hacked Story", "epic": self.epic_b.id, "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_company_cross_project_isolation(self):
        # 1. Member of A2 tries to read Project A1's story -> 404 Not Found (since not a member of A1)
        response = self.client_other_member.get(f"/api/v1/stories/{self.story_a1.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 2. Member of A2 tries to create story in Project A2, but linking Project A1's Epic -> 400 Bad Request (consistency validation)
        response = self.client_pm.post(
            "/api/v1/stories/",
            {"project": self.project_a2.id, "title": "Cross Project Story", "epic": self.epic_a1.id, "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. PM of A1 tries to create story in Project A2, but linking Project A1's Sprint -> 400 Bad Request (consistency validation)
        response = self.client_pm.post(
            "/api/v1/stories/",
            {"project": self.project_a2.id, "title": "Cross Project Story 2", "sprint": self.sprint_a1.id, "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Member of A2 tries to comment on a task of Project A1 -> 403 Forbidden (inaccessible parent task)
        response = self.client_other_member.post(
            "/api/v1/comments/",
            {"task": self.task_a1.id, "comment": "Hacked Comment"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Member of A2 tries to create subtask under a task of Project A1 -> 403 Forbidden (inaccessible parent task)
        response = self.client_other_member.post(
            "/api/v1/subtasks/",
            {"task": self.task_a1.id, "title": "Hacked Subtask"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 6. Member of A2 tries to mutate Epic of Project A1 -> 404 Not Found
        response = self.client_other_member.patch(
            f"/api/v1/epics/{self.epic_a1.id}/",
            {"title": "Hacked Epic"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_company_assignment_blocked(self):
        # PM from Company A tries to assign a Company B employee to a Project A1 task -> 400 Bad Request
        response = self.client_pm.post(
            "/api/v1/project-tasks/",
            {"story": self.story_a1.id, "title": "Cross Assignment Task", "assigned_to": self.user_b_pm.id, "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # PM from Company A tries to assign a Company A employee who is not a project member to Project A1 task -> 400 Bad Request
        response = self.client_pm.post(
            "/api/v1/project-tasks/",
            {"story": self.story_a1.id, "title": "Non-Member Assignment Task", "assigned_to": self.user_a_other_proj_member.id, "status": self.status_todo_a.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
