from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from projects.models import Project, ProjectStatusOption, ProjectStory, ProjectStoryMember
from projects.services.statuses import initialize_default_statuses


class StoryMembersAPITestCase(TestCase):
    def setUp(self):
        # Organization A - CubeLogs Inc
        settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="CubeLogs Inc", subdomain="cubelogs", settings=settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org_a)
        self.pending_status_a = ProjectStatusOption.objects.get(company=self.org_a, code='pending')

        # Employees in Org A
        self.pm_salman = Employee.objects.create_user(
            email="salman.pm@cubelogs.com", password="password123",
            first_name="Salman", last_name="Manager", designation="Project Manager",
            organization=self.org_a,
            permissions=["projects:view", "projects:create", "projects:update", "projects:delete", "project_stories:view", "project_stories:create", "project_stories:update"]
        )
        self.tl_akhil = Employee.objects.create_user(
            email="akhil.tl@cubelogs.com", password="password123",
            first_name="Akhil", last_name="Lead", designation="Team Lead",
            organization=self.org_a,
            permissions=["projects:view", "project_stories:view", "project_stories:create"]
        )
        self.dev_arjun = Employee.objects.create_user(
            email="arjun.dev@cubelogs.com", password="password123",
            first_name="Arjun", last_name="Developer", designation="Developer",
            organization=self.org_a,
            permissions=["projects:view", "project_stories:view"]
        )
        self.qa_sarah = Employee.objects.create_user(
            email="sarah.qa@cubelogs.com", password="password123",
            first_name="Sarah", last_name="Tester", designation="QA Engineer",
            organization=self.org_a,
            permissions=["projects:view", "project_stories:view"]
        )
        self.inactive_user = Employee.objects.create_user(
            email="inactive@cubelogs.com", password="password123",
            first_name="Inactive", last_name="Account", is_active=False,
            organization=self.org_a
        )

        # Organization B - Acme Corp
        settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Acme Corp", subdomain="acme", settings=settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)
        self.external_user = Employee.objects.create_user(
            email="user@acme.com", password="password123",
            first_name="External", last_name="User",
            organization=self.org_b
        )

        # Create Project in Org A
        self.project = Project.objects.create(
            company=self.org_a, name="CubeLogs HRMS",
            project_manager=self.pm_salman, team_lead=self.tl_akhil,
            status=self.pending_status_a
        )

    def test_eligible_story_members_returns_same_company_active_employees(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.pm_salman)
        response = api_client.get(f"/api/v1/projects/{self.project.id}/eligible-story-members/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        emails = [emp['email'] for emp in results]

        self.assertIn("salman.pm@cubelogs.com", emails)
        self.assertIn("akhil.tl@cubelogs.com", emails)
        self.assertIn("arjun.dev@cubelogs.com", emails)
        self.assertIn("sarah.qa@cubelogs.com", emails)
        self.assertNotIn("user@acme.com", emails)
        self.assertNotIn("inactive@cubelogs.com", emails)

    def test_eligible_story_members_search_filtering(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.pm_salman)
        response = api_client.get(f"/api/v1/projects/{self.project.id}/eligible-story-members/?search=Tester")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['email'], "sarah.qa@cubelogs.com")

    def test_eligible_story_members_pagination(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.pm_salman)
        response = api_client.get(f"/api/v1/projects/{self.project.id}/eligible-story-members/?page=1&page_size=2")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 4)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['page'], 1)

    def test_story_creation_with_assigned_members(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.pm_salman)
        payload = {
            "project": self.project.id,
            "title": "User Registration Story",
            "work_type": "Feature",
            "priority": "High",
            "story_points": 3,
            "members": [self.dev_arjun.id, self.qa_sarah.id]
        }
        response = api_client.post("/api/v1/stories/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        story_id = response.data['id']
        assigned_user_ids = [m['user'] for m in response.data['story_members']]
        self.assertIn(self.dev_arjun.id, assigned_user_ids)
        self.assertIn(self.qa_sarah.id, assigned_user_ids)

    def test_story_update_with_assigned_members(self):
        story = ProjectStory.objects.create(
            project=self.project, title="Existing Story",
            status=self.pending_status_a, created_by=self.pm_salman
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.pm_salman)
        payload = {
            "title": "Updated Story Title",
            "members": [self.tl_akhil.id]
        }
        response = api_client.patch(f"/api/v1/stories/{story.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assigned_user_ids = [m['user'] for m in response.data['story_members']]
        self.assertEqual(assigned_user_ids, [self.tl_akhil.id])
