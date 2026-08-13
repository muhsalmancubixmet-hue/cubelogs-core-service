from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from users.roles import sync_default_roles
from projects.models import Project, ProjectMember, ProjectStory, ProjectTask, ProjectStatusOption
from projects.services.statuses import initialize_default_statuses


class MyTasksPermissionAndSecurityTestCase(TestCase):
    def setUp(self):
        sync_default_roles()
        settings1 = OrgSettings.objects.create()
        self.org1 = Organization.objects.create(name="MyTasks Org 1", subdomain="mytasks1", settings=settings1)
        OrganizationModule.objects.create(organization=self.org1, module_id='project_management', enabled=True)

        settings2 = OrgSettings.objects.create()
        self.org2 = Organization.objects.create(name="MyTasks Org 2", subdomain="mytasks2", settings=settings2)
        OrganizationModule.objects.create(organization=self.org2, module_id='project_management', enabled=True)

        initialize_default_statuses(self.org1)
        initialize_default_statuses(self.org2)
        self.status1 = ProjectStatusOption.objects.get(company=self.org1, code='pending')

        # Employee 1 with canonical permission key projects.my_tasks.view
        self.emp1 = Employee.objects.create_user(
            email="emp1@mytasks.com", password="password123", organization=self.org1,
            permissions=["projects.my_tasks.view", "projects:view"]
        )

        # Employee 2 with projects.my_tasks.view
        self.emp2 = Employee.objects.create_user(
            email="emp2@mytasks.com", password="password123", organization=self.org1,
            permissions=["projects.my_tasks.view", "projects:view"]
        )

        # Employee 3 without projects.my_tasks.view or any task permissions
        self.emp_no_perm = Employee.objects.create_user(
            email="noperm@mytasks.com", password="password123", organization=self.org1,
            permissions=["dashboard"]
        )

        # Project Manager
        self.pm = Employee.objects.create_user(
            email="pm@mytasks.com", password="password123", organization=self.org1,
            permissions=["projects.my_tasks.view", "projects.task.view", "projects:view", "project_tasks:view_all"]
        )

        # Other Org Employee
        self.other_org_emp = Employee.objects.create_user(
            email="other@mytasks2.com", password="password123", organization=self.org2,
            permissions=["projects.my_tasks.view", "projects:view"]
        )

        self.project1 = Project.objects.create(company=self.org1, name="Project Alpha", project_manager=self.pm, status=self.status1)
        ProjectMember.objects.create(project=self.project1, user=self.emp1, is_active=True)
        ProjectMember.objects.create(project=self.project1, user=self.emp2, is_active=True)
        ProjectMember.objects.create(project=self.project1, user=self.emp_no_perm, is_active=True)

        self.story1 = ProjectStory.objects.create(project=self.project1, title="Story Alpha", status=self.status1)

        self.task1_emp1 = ProjectTask.objects.create(
            story=self.story1, title="Task for Emp 1", assigned_to=self.emp1, status=self.status1
        )
        self.task2_emp2 = ProjectTask.objects.create(
            story=self.story1, title="Task for Emp 2", assigned_to=self.emp2, status=self.status1
        )
        self.task3_pm = ProjectTask.objects.create(
            story=self.story1, title="Task for PM", assigned_to=self.pm, status=self.status1
        )

    def test_employee_with_canonical_permission_can_fetch_my_tasks(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task1_emp1.id)

    def test_employee_only_receives_assigned_tasks(self):
        client = APIClient()
        client.force_authenticate(user=self.emp2)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task2_emp2.id)

    def test_project_manager_my_tasks_only_returns_own_tasks(self):
        client = APIClient()
        client.force_authenticate(user=self.pm)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task3_pm.id)

    def test_user_without_my_tasks_permission_is_forbidden(self):
        client = APIClient()
        client.force_authenticate(user=self.emp_no_perm)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_request_returns_401_or_403(self):
        client = APIClient()
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_empty_assigned_task_list_returns_200_with_empty_array(self):
        emp_no_tasks = Employee.objects.create_user(
            email="notasks@mytasks.com", password="password123", organization=self.org1,
            permissions=["projects.my_tasks.view", "projects:view"]
        )
        ProjectMember.objects.create(project=self.project1, user=emp_no_tasks, is_active=True)

        client = APIClient()
        client.force_authenticate(user=emp_no_tasks)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(results, [])

    def test_cross_company_access_returns_empty_or_forbidden(self):
        client = APIClient()
        client.force_authenticate(user=self.other_org_emp)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(results, [])
