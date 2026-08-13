from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from projects.models import Project

class ProjectModuleAccessTestCase(TestCase):
    def setUp(self):
        # Create Org with module disabled
        self.settings_disabled = OrgSettings.objects.create()
        self.org_disabled = Organization.objects.create(name="Disabled Org", subdomain="disabled_org", settings=self.settings_disabled)
        OrganizationModule.objects.create(organization=self.org_disabled, module_id='project_management', enabled=False)

        self.pm_disabled = Employee.objects.create_user(
            email="pm@disabled.com", password="password123",
            organization=self.org_disabled,
            permissions=["projects:view", "projects:create", "projects:update"]
        )

        # Create Org with module enabled
        self.settings_enabled = OrgSettings.objects.create()
        self.org_enabled = Organization.objects.create(name="Enabled Org", subdomain="enabled_org", settings=self.settings_enabled)
        OrganizationModule.objects.create(organization=self.org_enabled, module_id='project_management', enabled=True)

        self.pm_enabled = Employee.objects.create_user(
            email="pm@enabled.com", password="password123",
            organization=self.org_enabled,
            permissions=["projects:view", "projects:create", "projects:update"]
        )

    def test_module_disabled_returns_403_for_all_roles(self):
        client = APIClient()
        client.force_authenticate(user=self.pm_disabled)

        response = client.get("/api/v1/projects/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_module_enabled_allows_authorized_access(self):
        client = APIClient()
        client.force_authenticate(user=self.pm_enabled)

        response = client.get("/api/v1/projects/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
