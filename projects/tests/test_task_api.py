from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from projects.models import Project, ProjectMember, ProjectStory, ProjectStoryMember, ProjectTask, ProjectStatusOption
from projects.services.statuses import initialize_default_statuses


class TaskAPIPermissionTestCase(TestCase):
    def setUp(self):
        settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Task Test Org", subdomain="tasktest", settings=settings)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)

        # Seed status options for the org
        initialize_default_statuses(self.org)
        self.status_pending = ProjectStatusOption.objects.get(company=self.org, code='pending')
        self.status_in_progress = ProjectStatusOption.objects.get(company=self.org, code='in_progress')
        self.status_completed = ProjectStatusOption.objects.get(company=self.org, code='completed')

        self.pm = Employee.objects.create_user(
            email="pm@tasktest.com", password="password123", organization=self.org,
            permissions=["projects:view", "projects:update", "project_stories:view", "project_tasks:view_all", "project_tasks:create", "project_tasks:update_all", "project_tasks:delete"]
        )

        self.emp1 = Employee.objects.create_user(
            email="emp1@tasktest.com", password="password123", organization=self.org,
            permissions=["projects:view", "project_stories:view", "project_tasks:view_own", "project_tasks:update_own"]
        )

        self.emp2 = Employee.objects.create_user(
            email="emp2@tasktest.com", password="password123", organization=self.org,
            permissions=["projects:view", "project_stories:view", "project_tasks:view_own", "project_tasks:update_own"]
        )

        self.project = Project.objects.create(company=self.org, name="Project T", project_manager=self.pm, status=self.status_pending)
        self.member1 = ProjectMember.objects.create(project=self.project, user=self.emp1, is_active=True)
        self.member2 = ProjectMember.objects.create(project=self.project, user=self.emp2, is_active=True)

        self.story = ProjectStory.objects.create(project=self.project, title="Frontend Section", status=self.status_pending)
        # Assign emp1 to story so they can see tasks
        ProjectStoryMember.objects.create(story=self.story, member=self.member1, assigned_by=self.pm)

        self.task1 = ProjectTask.objects.create(
            story=self.story, title="Task 1", assigned_to=self.emp1, status=self.status_pending
        )

    def test_employee_can_update_own_task_status(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.patch(
            f"/api/v1/project-tasks/{self.task1.id}/status/",
            {"status": self.status_in_progress.id},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status_id, self.status_in_progress.id)

    def test_employee_cannot_update_another_users_task_status(self):
        client = APIClient()
        client.force_authenticate(user=self.emp2)
        response = client.patch(
            f"/api/v1/project-tasks/{self.task1.id}/status/",
            {"status": self.status_completed.id},
            format="json"
        )
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

    def test_employee_status_endpoint_rejects_extra_field_mutations(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        payload = {"status": self.status_completed.id, "title": "Hacked Title Attempt"}
        response = client.patch(
            f"/api/v1/project-tasks/{self.task1.id}/status/",
            payload,
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task1.refresh_from_db()
        # Title remains unchanged
        self.assertEqual(self.task1.title, "Task 1")
        self.assertEqual(self.task1.status_id, self.status_completed.id)

    def test_list_project_tasks_assigned_to_me(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get("/api/v1/project-tasks/?assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task1.id)

    def test_list_project_tasks_by_project_id(self):
        client = APIClient()
        client.force_authenticate(user=self.pm)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task1.id)
