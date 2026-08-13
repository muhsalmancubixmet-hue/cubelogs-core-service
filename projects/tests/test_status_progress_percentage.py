from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role
from projects.models import ProjectStatusOption
from projects.services.statuses import initialize_default_statuses

User = get_user_model()


class StatusProgressPercentageTests(APITestCase):
    def setUp(self):
        settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Acme Corp", subdomain="acme-corp", settings=settings)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)
        initialize_default_statuses(self.org)

        admin_role = Role.objects.filter(slug='admin').first()
        self.employee = Employee.objects.create_user(
            email="admin@acme.com",
            password="password123",
            organization=self.org,
            role=admin_role,
            is_staff=True,
            is_superuser=True
        )
        self.client.force_authenticate(user=self.employee)

    def test_serializer_returns_progress_percentage(self):
        url = "/api/v1/project-statuses/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get('results', data) if isinstance(data, dict) else data
        self.assertTrue(len(results) > 0)
        self.assertIn('progress_percentage', results[0])

    def test_create_custom_status_with_80_percent(self):
        url = "/api/v1/project-statuses/"
        payload = {
            "name": "In QA Review",
            "code": "in_qa_review",
            "category": "active",
            "scope": "task",
            "progress_percentage": 80
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["progress_percentage"], 80)
        
        status_obj = ProjectStatusOption.objects.get(id=response.data["id"])
        self.assertEqual(status_obj.progress_percentage, 80)

    def test_update_custom_status_percentage(self):
        st = ProjectStatusOption.objects.create(
            company=self.org, name="Custom QA", code="custom_qa", category="active", progress_percentage=50
        )
        url = f"/api/v1/project-statuses/{st.id}/"
        response = self.client.patch(url, {"progress_percentage": 85})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        st.refresh_from_db()
        self.assertEqual(st.progress_percentage, 85)

    def test_reject_negative_percentage(self):
        url = "/api/v1/project-statuses/"
        payload = {
            "name": "Invalid Low",
            "code": "invalid_low",
            "category": "active",
            "progress_percentage": -1
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_greater_than_100_percentage(self):
        url = "/api/v1/project-statuses/"
        payload = {
            "name": "Invalid High",
            "code": "invalid_high",
            "category": "active",
            "progress_percentage": 101
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pending_system_status_remains_0_percent(self):
        pending = ProjectStatusOption.objects.get(company=self.org, code="pending", is_system=True)
        url = f"/api/v1/project-statuses/{pending.id}/"
        response = self.client.patch(url, {"progress_percentage": 50})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completed_system_status_remains_100_percent(self):
        completed = ProjectStatusOption.objects.get(company=self.org, code="completed", is_system=True)
        url = f"/api/v1/project-statuses/{completed.id}/"
        response = self.client.patch(url, {"progress_percentage": 50})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_qa_review_at_80_percent_is_not_completed_category(self):
        qa = ProjectStatusOption.objects.create(
            company=self.org, name="In QA Review", code="in_qa_review_test", category="active", progress_percentage=80
        )
        self.assertNotEqual(qa.category, "completed")
        self.assertEqual(qa.progress_percentage, 80)
