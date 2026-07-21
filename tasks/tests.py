from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings
from users.models import Employee
from tasks.models import Task

class TaskTenantIsolationTestCase(TestCase):
    def setUp(self):
        # Create Org A and manager
        self.org_a = Organization.objects.create(name="Org A", subdomain="orga_test")
        self.user_a_manager = Employee.objects.create_user(
            email="manager@orga.com", password="password123",
            first_name="Manager", last_name="A",
            organization=self.org_a, isSuperAdmin=False,
            permissions=["tasks:create", "tasks:view"]
        )

        # Create Org B and employee
        self.org_b = Organization.objects.create(name="Org B", subdomain="orgb_test")
        self.user_b_emp = Employee.objects.create_user(
            email="emp@orgb.com", password="password123",
            first_name="Employee", last_name="B",
            organization=self.org_b, isSuperAdmin=False,
            permissions=["tasks:view"]
        )

        # Create Org A employee
        self.user_a_emp = Employee.objects.create_user(
            email="emp@orga.com", password="password123",
            first_name="Employee", last_name="A",
            organization=self.org_a, isSuperAdmin=False,
            permissions=["tasks:view"]
        )

    def test_org_a_cannot_assign_task_to_org_b_employee(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a_manager)

        payload = {
            "title": "Cross-Tenant Task Attempt",
            "description": "Attempt to assign task to Org B employee",
            "assignedTo": self.user_b_emp.id,
            "assignedName": "Employee B",
            "dueDate": "2026-12-31",
            "status": "Pending"
        }

        response = client.post("/api/tasks/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assignedTo", response.data)
        self.assertEqual(Task.objects.filter(title="Cross-Tenant Task Attempt").count(), 0)

    def test_same_organization_assignment_succeeds(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a_manager)

        payload = {
            "title": "Valid Same-Org Task",
            "description": "Assign task to Org A employee",
            "assignedTo": self.user_a_emp.id,
            "assignedName": "Employee A",
            "dueDate": "2026-12-31",
            "status": "Pending"
        }

        response = client.post("/api/tasks/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Task.objects.filter(title="Valid Same-Org Task").count(), 1)

    def test_org_a_cannot_patch_task_to_org_b_employee(self):
        # First create a valid task in Org A
        task = Task.objects.create(
            title="Org A Task",
            assignedTo=self.user_a_emp,
            assignedName="Employee A",
            dueDate="2026-12-31",
            status="Pending"
        )

        client = APIClient()
        client.force_authenticate(user=self.user_a_manager)

        patch_payload = {
            "assignedTo": self.user_b_emp.id
        }

        response = client.patch(f"/api/tasks/{task.id}/", patch_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        task.refresh_from_db()
        self.assertEqual(task.assignedTo, self.user_a_emp)

    def test_org_b_cannot_read_or_modify_org_a_task(self):
        task = Task.objects.create(
            title="Org A Private Task",
            assignedTo=self.user_a_emp,
            assignedName="Employee A",
            dueDate="2026-12-31",
            status="Pending"
        )

        client_b = APIClient()
        client_b.force_authenticate(user=self.user_b_emp)

        # GET detail request
        response_get = client_b.get(f"/api/tasks/{task.id}/")
        self.assertEqual(response_get.status_code, status.HTTP_404_NOT_FOUND)

        # DELETE request
        response_del = client_b.delete(f"/api/tasks/{task.id}/")
        self.assertIn(response_del.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])
