from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from projects.models import Project, ProjectMember, ProjectStatusOption
from projects.services.statuses import initialize_default_statuses


class ProjectAPIPermissionTestCase(TestCase):
    def setUp(self):
        # Organization A - CubeLogs Inc
        settings_organization_a = OrgSettings.objects.create()
        self.organization_a = Organization.objects.create(name="CubeLogs Inc", subdomain="cubelogs", settings=settings_organization_a)
        OrganizationModule.objects.create(organization=self.organization_a, module_id='project_management', enabled=True)
        initialize_default_statuses(self.organization_a)
        self.pending_status_a = ProjectStatusOption.objects.get(company=self.organization_a, code='pending')

        self.project_manager_salman = Employee.objects.create_user(
            email="salman.pm@cubelogs.com", password="password123",
            first_name="Salman", last_name="Manager",
            organization=self.organization_a,
            permissions=["projects:view", "projects:create", "projects:update", "projects:delete", "projects:members_manage"]
        )
        self.team_lead_akhil = Employee.objects.create_user(
            email="akhil.tl@cubelogs.com", password="password123",
            first_name="Akhil", last_name="Lead",
            organization=self.organization_a,
            permissions=["projects:view", "project_stories:view", "project_stories:create", "project_tasks:view_all", "project_tasks:create"]
        )
        self.developer_arjun = Employee.objects.create_user(
            email="arjun.dev@cubelogs.com", password="password123",
            first_name="Arjun", last_name="Developer",
            organization=self.organization_a,
            permissions=["projects:view", "project_stories:view", "project_tasks:view_own", "project_tasks:update_own"]
        )

        # Organization B - Acme Corp
        settings_organization_b = OrgSettings.objects.create()
        self.organization_b = Organization.objects.create(name="Acme Corp", subdomain="acme", settings=settings_organization_b)
        OrganizationModule.objects.create(organization=self.organization_b, module_id='project_management', enabled=True)
        self.project_manager_external = Employee.objects.create_user(
            email="pm@acme.com", password="password123",
            organization=self.organization_b,
            permissions=["projects:view", "projects:create", "projects:update"]
        )

        # Create Project in Organization A led by team_lead_akhil
        self.project_hrms = Project.objects.create(
            company=self.organization_a, name="CubeLogs HRMS",
            project_manager=self.project_manager_salman, team_lead=self.team_lead_akhil,
            status=self.pending_status_a
        )
        ProjectMember.objects.create(project=self.project_hrms, user=self.developer_arjun, is_active=True)

        # Create Project in Organization A NOT led by team_lead_akhil
        self.project_attendance = Project.objects.create(
            company=self.organization_a, name="Attendance Management",
            project_manager=self.project_manager_salman, status=self.pending_status_a
        )

    def test_pm_can_list_all_company_projects(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        response = api_client.get("/api/v1/projects/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 2)

    def test_pm_cannot_access_other_company_project(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_external)
        response = api_client.get(f"/api/v1/projects/{self.project_hrms.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_team_lead_sees_only_assigned_projects(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.team_lead_akhil)
        response = api_client.get("/api/v1/projects/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.project_hrms.id)

    def test_team_lead_cannot_create_project(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.team_lead_akhil)
        payload = {"name": "Unauthorized Project Creation"}
        response = api_client.post("/api/v1/projects/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_sees_only_member_projects(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.developer_arjun)
        response = api_client.get("/api/v1/projects/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.project_hrms.id)

    def test_eligible_members_returns_same_company_active_employees(self):
        Employee.objects.create_user(
            email="inactive@cubelogs.com", password="password123",
            first_name="Inactive", last_name="User", is_active=False,
            organization=self.organization_a
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        response = api_client.get("/api/v1/projects/eligible-members/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['results'] if 'results' in response.data else response.data
        emails = [emp['email'] for emp in data]
        self.assertIn("salman.pm@cubelogs.com", emails)
        self.assertIn("akhil.tl@cubelogs.com", emails)
        self.assertIn("arjun.dev@cubelogs.com", emails)
        self.assertNotIn("pm@acme.com", emails)
        self.assertNotIn("inactive@cubelogs.com", emails)

    def test_eligible_members_search_filter(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        response = api_client.get("/api/v1/projects/eligible-members/?search=Akhil")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['email'], "akhil.tl@cubelogs.com")

    def test_create_project_with_team_lead(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        payload = {
            "name": "New Portal",
            "project_type": "Internal",
            "team_lead": self.team_lead_akhil.id
        }
        response = api_client.post("/api/v1/projects/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['team_lead'], self.team_lead_akhil.id)
        self.assertEqual(response.data['team_lead_name'], "Akhil Lead")

    def test_cannot_assign_cross_company_team_lead(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        payload = {
            "name": "Invalid Project",
            "team_lead": self.project_manager_external.id
        }
        response = api_client.post("/api/v1/projects/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_project_detail_and_overview_returns_200(self):
        """Newly created empty project must return HTTP 200 for detail and overview APIs without 500 exceptions."""
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)
        
        # 1. Create empty project
        payload = {"name": "Empty Scrum Project", "project_type": "Internal"}
        create_resp = api_client.post("/api/v1/projects/", payload, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        project_id = create_resp.data['id']

        # 2. Fetch Project Detail
        detail_resp = api_client.get(f"/api/v1/projects/{project_id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data['name'], "Empty Scrum Project")

        # 3. Fetch Project Overview
        overview_resp = api_client.get(f"/api/v1/projects/{project_id}/overview/")
        self.assertEqual(overview_resp.status_code, status.HTTP_200_OK)
        
        # Verify structure and default empty values
        data = overview_resp.data
        self.assertIn('project_header', data)
        self.assertIn('summary_cards', data)
        self.assertIsNone(data['current_sprint'])
        self.assertEqual(data['summary_cards']['total_stories'], 0)
        self.assertEqual(data['summary_cards']['total_tasks'], 0)
        self.assertEqual(data['summary_cards']['sprint_progress'], 0)
        self.assertEqual(data['project_header']['progress'], 0)
        self.assertEqual(data['project_header']['health'], "On Track")
        self.assertIsNone(data['project_header']['team_lead_name'])
        self.assertEqual(len(data['recent_activity']), 0)
        self.assertEqual(len(data['my_recent_tasks']), 0)

    def test_delete_project_soft_deletes_and_excludes_from_list_and_detail(self):
        """Deleting a project marks is_deleted=True, excludes it from list, returns 404 on detail/actions."""
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)

        project_id = self.project_attendance.id

        # 1. Ensure project exists and is in list
        list_before = api_client.get("/api/v1/projects/")
        self.assertEqual(list_before.status_code, status.HTTP_200_OK)
        results_before = list_before.data['results'] if 'results' in list_before.data else list_before.data
        self.assertIn(project_id, [p['id'] for p in results_before])

        # 2. DELETE project
        delete_resp = api_client.delete(f"/api/v1/projects/{project_id}/")
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)

        # 3. Verify in database: is_deleted is True (soft delete)
        self.project_attendance.refresh_from_db()
        self.assertTrue(self.project_attendance.is_deleted)

        # 4. Verify project list excludes soft-deleted project
        list_after = api_client.get("/api/v1/projects/")
        self.assertEqual(list_after.status_code, status.HTTP_200_OK)
        results_after = list_after.data['results'] if 'results' in list_after.data else list_after.data
        self.assertNotIn(project_id, [p['id'] for p in results_after])

        # 5. Verify GET detail returns 404
        detail_resp = api_client.get(f"/api/v1/projects/{project_id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_404_NOT_FOUND)

        # 6. Verify PATCH / update returns 404
        patch_resp = api_client.patch(f"/api/v1/projects/{project_id}/", {"name": "Renamed"}, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_404_NOT_FOUND)

        # 7. Verify action endpoint (members) returns 404
        members_resp = api_client.get(f"/api/v1/projects/{project_id}/members/")
        self.assertEqual(members_resp.status_code, status.HTTP_404_NOT_FOUND)

