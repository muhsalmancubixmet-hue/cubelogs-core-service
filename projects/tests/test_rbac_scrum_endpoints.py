from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, PermissionFlag
from users.roles import sync_default_roles
from projects.models import Project, ProjectMember, ProjectSprint, ProjectStory, ProjectTask, ProjectStatusOption
from projects.services.statuses import initialize_default_statuses


class ScrumEndpointsRBACAuthorizationTestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        settings1 = OrgSettings.objects.create()
        self.org1 = Organization.objects.create(name="Scrum Org 1", subdomain="scrum1", settings=settings1)
        OrganizationModule.objects.create(organization=self.org1, module_id='project_management', enabled=True)

        settings2 = OrgSettings.objects.create()
        self.org2 = Organization.objects.create(name="Scrum Org 2", subdomain="scrum2", settings=settings2)
        OrganizationModule.objects.create(organization=self.org2, module_id='project_management', enabled=True)

        initialize_default_statuses(self.org1)
        initialize_default_statuses(self.org2)
        self.status1 = ProjectStatusOption.objects.get(company=self.org1, code='pending')

        # Get system roles
        self.employee_role = Role.objects.get(slug='employee', organization__isnull=True)
        self.pm_role = Role.objects.get(slug='project-manager', organization__isnull=True)

        # 1. Standard Employee in Org 1 with Employee Role
        self.emp1 = Employee.objects.create_user(
            email="emp1@scrum1.com", password="password123", organization=self.org1,
            role=self.employee_role
        )

        # 2. Employee in Org 1 without board/sprint permissions (Custom role or denied perm)
        self.restricted_role = Role.objects.create(
            name="Restricted Role", slug="restricted-role", organization=self.org1
        )
        self.emp_restricted = Employee.objects.create_user(
            email="restricted@scrum1.com", password="password123", organization=self.org1,
            role=self.restricted_role
        )

        # 3. Project Manager in Org 1
        self.pm = Employee.objects.create_user(
            email="pm@scrum1.com", password="password123", organization=self.org1,
            role=self.pm_role
        )

        # 4. Other Org Employee
        self.other_emp = Employee.objects.create_user(
            email="other@scrum2.com", password="password123", organization=self.org2,
            role=self.employee_role
        )

        # Create Project
        self.project1 = Project.objects.create(company=self.org1, name="Scrum Project Alpha", project_manager=self.pm, status=self.status1)
        ProjectMember.objects.create(project=self.project1, user=self.emp1, is_active=True)
        ProjectMember.objects.create(project=self.project1, user=self.emp_restricted, is_active=True)

        # Create Sprint & Tasks
        self.sprint1 = ProjectSprint.objects.create(
            project=self.project1, name="Sprint 1", status="active", goal="Complete MVP"
        )
        self.story1 = ProjectStory.objects.create(
            project=self.project1, sprint=self.sprint1, title="Story 1", status=self.status1
        )
        self.task1_emp1 = ProjectTask.objects.create(
            story=self.story1, title="Task for Emp 1", assigned_to=self.emp1, status=self.status1
        )

    # ─── BOARD ENDPOINT TESTS ──────────────────────────────────────────────────
    def test_project_member_with_board_view_can_access_board(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get(f"/api/v1/projects/{self.project1.id}/board/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['project_id'], self.project1.id)

    def test_user_without_board_view_is_forbidden(self):
        client = APIClient()
        client.force_authenticate(user=self.emp_restricted)
        response = client.get(f"/api/v1/projects/{self.project1.id}/board/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_company_board_access_is_blocked(self):
        client = APIClient()
        client.force_authenticate(user=self.other_emp)
        response = client.get(f"/api/v1/projects/{self.project1.id}/board/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ─── SPRINT ENDPOINT TESTS ─────────────────────────────────────────────────
    def test_project_member_with_sprint_view_can_access_sprint_list(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get(f"/api/v1/project-sprints/?project_id={self.project1.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(len(results), 1)

    def test_read_only_employee_cannot_create_sprint(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.post(
            "/api/v1/project-sprints/",
            {"project": self.project1.id, "name": "Sprint 2"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_project_manager_can_create_sprint(self):
        client = APIClient()
        client.force_authenticate(user=self.pm)
        response = client.post(
            "/api/v1/project-sprints/",
            {
                "project": self.project1.id,
                "name": "Sprint 2",
                "goal": "Next Iteration",
                "start_date": "2026-08-01",
                "end_date": "2026-08-15",
                "capacity": 20
            },
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # ─── MY TASKS ENDPOINT TESTS ───────────────────────────────────────────────
    def test_member_with_my_tasks_view_can_access_assigned_tasks(self):
        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get(f"/api/v1/project-tasks/?project_id={self.project1.id}&assigned_to=me")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.task1_emp1.id)

    # ─── RBAC EXTRA & DENIED PERMISSION OVERRIDES ─────────────────────────────
    def test_extra_permission_grants_access(self):
        board_perm = PermissionFlag.objects.get(key='projects.board.view')
        self.emp_restricted.extra_permissions.add(board_perm)

        client = APIClient()
        client.force_authenticate(user=self.emp_restricted)
        response = client.get(f"/api/v1/projects/{self.project1.id}/board/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_denied_permission_overrides_role_permission(self):
        board_perm = PermissionFlag.objects.get(key='projects.board.view')
        overview_perm = PermissionFlag.objects.get(key='projects.overview.view')
        view_perm = PermissionFlag.objects.get(key='projects:view')
        self.emp1.denied_permissions.add(board_perm, overview_perm, view_perm)

        client = APIClient()
        client.force_authenticate(user=self.emp1)
        response = client.get(f"/api/v1/projects/{self.project1.id}/board/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
