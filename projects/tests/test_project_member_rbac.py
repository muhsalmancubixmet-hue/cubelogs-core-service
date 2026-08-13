# --------------------------------------------------------------------------------
#       Tests: Project Member RBAC & Authorization Overlay Test Suite
# --------------------------------------------------------------------------------

from django.test import TestCase
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization
from users.models import Employee, Role, PermissionFlag
from projects.models import Project, ProjectMember, ProjectSprint, ProjectStory, ProjectTask, ProjectStatusOption
from projects.services.projects import create_project
from projects.permissions import get_project_effective_permissions, has_project_permission


class ProjectMemberRBACTestCase(TestCase):
    def setUp(self):
        # Create Organizations
        self.org1 = Organization.objects.create(name="Org One", subdomain="org-one")
        self.org2 = Organization.objects.create(name="Org Two", subdomain="org-two")

        # Create System Roles
        self.admin_role = Role.objects.create(name="Company Admin", slug="company-admin", organization=self.org1)
        self.emp_role = Role.objects.create(name="Employee", slug="employee", organization=self.org1)

        # Create Employees in Org 1
        self.admin_user = Employee.objects.create_user(
            email="admin@org1.com", password="password123", first_name="Admin", last_name="User",
            organization=self.org1, role=self.admin_role, role_name="Company Admin", isSuperAdmin=True, is_active=True
        )
        self.pm_user = Employee.objects.create_user(
            email="pm@org1.com", password="password123", first_name="PM", last_name="User",
            organization=self.org1, role=self.emp_role, is_active=True
        )
        self.team_lead_user = Employee.objects.create_user(
            email="lead@org1.com", password="password123", first_name="Lead", last_name="User",
            organization=self.org1, role=self.emp_role, is_active=True
        )
        self.dev_user1 = Employee.objects.create_user(
            email="dev1@org1.com", password="password123", first_name="Dev", last_name="One",
            organization=self.org1, role=self.emp_role, is_active=True
        )
        self.dev_user2 = Employee.objects.create_user(
            email="dev2@org1.com", password="password123", first_name="Dev", last_name="Two",
            organization=self.org1, role=self.emp_role, is_active=True
        )
        self.inactive_user = Employee.objects.create_user(
            email="inactive@org1.com", password="password123", first_name="Inactive", last_name="User",
            organization=self.org1, role=self.emp_role, is_active=False
        )

        # Create Cross-Company User in Org 2
        self.cross_company_user = Employee.objects.create_user(
            email="user@org2.com", password="password123", first_name="Cross", last_name="Org",
            organization=self.org2, role=self.emp_role, is_active=True
        )

        # Initialize Default Status Options
        self.status_todo = ProjectStatusOption.objects.create(
            company=self.org1, name="To Do", code="TODO", category="pending", is_default=True, is_system=True
        )
        self.status_done = ProjectStatusOption.objects.create(
            company=self.org1, name="Done", code="DONE", category="completed", progress_percentage=100, is_system=True
        )

        # Create Project 1 via Service
        self.project1 = create_project(
            company=self.org1,
            name="Project One",
            project_manager=self.pm_user,
            team_lead=self.team_lead_user,
            user=self.admin_user,
        )

        self.client = APIClient()

    # 1. Creator becomes active ProjectMember
    def test_01_creator_becomes_active_project_member(self):
        pm = ProjectMember.objects.filter(project=self.project1, user=self.admin_user).first()
        self.assertIsNotNone(pm)
        self.assertTrue(pm.is_active)

    # 2. PM becomes ProjectMember with PROJECT_MANAGER role
    def test_02_pm_becomes_project_member(self):
        pm = ProjectMember.objects.filter(project=self.project1, user=self.pm_user).first()
        self.assertIsNotNone(pm)
        self.assertEqual(pm.project_role, "Project Manager")

    # 3. Team Lead becomes ProjectMember with TEAM_LEAD role
    def test_03_team_lead_becomes_project_member(self):
        pm = ProjectMember.objects.filter(project=self.project1, user=self.team_lead_user).first()
        self.assertIsNotNone(pm)
        self.assertEqual(pm.project_role, "Team Lead")

    # 4. Same person as creator/PM/Lead creates only one membership row
    def test_04_same_person_creates_only_one_membership_row(self):
        proj = create_project(
            company=self.org1,
            name="Solo Project",
            project_manager=self.pm_user,
            team_lead=self.pm_user,
            user=self.pm_user,
        )
        count = ProjectMember.objects.filter(project=proj, user=self.pm_user).count()
        self.assertEqual(count, 1)

    # 5. Team Lead from another company is rejected
    def test_05_cross_company_team_lead_rejected(self):
        with self.assertRaises(ValidationError):
            create_project(
                company=self.org1,
                name="Invalid Lead Project",
                project_manager=self.pm_user,
                team_lead=self.cross_company_user,
                user=self.admin_user,
            )

    # 6. Inactive Team Lead is rejected
    def test_06_inactive_team_lead_rejected(self):
        with self.assertRaises(ValidationError):
            create_project(
                company=self.org1,
                name="Inactive Lead Project",
                project_manager=self.pm_user,
                team_lead=self.inactive_user,
                user=self.admin_user,
            )

    # 7. Team Lead can perform approved Team Lead actions in assigned Project
    def test_07_team_lead_permissions_in_assigned_project(self):
        perms = get_project_effective_permissions(self.team_lead_user, self.project1)
        self.assertIn("projects.sprint.start", perms)
        self.assertIn("projects.board.manage", perms)
        self.assertIn("projects.retrospective.close", perms)

    # 8. Team Lead cannot perform Team Lead actions in another Project where they are Contributor
    def test_08_team_lead_in_proj1_is_contributor_in_proj2(self):
        project2 = create_project(
            company=self.org1,
            name="Project Two",
            project_manager=self.pm_user,
            team_lead=self.dev_user1,
            user=self.admin_user,
        )
        ProjectMember.objects.create(project=project2, user=self.team_lead_user, project_role="Contributor", is_active=True)

        perms_p1 = get_project_effective_permissions(self.team_lead_user, self.project1)
        perms_p2 = get_project_effective_permissions(self.team_lead_user, project2)

        self.assertIn("projects.board.manage", perms_p1)
        self.assertNotIn("projects.board.manage", perms_p2)
        self.assertNotIn("projects.sprint.start", perms_p2)

    # 9. Team Lead cannot delete Project unless separately permitted
    def test_09_team_lead_cannot_delete_project(self):
        perms = get_project_effective_permissions(self.team_lead_user, self.project1)
        self.assertNotIn("projects:delete", perms)

    # 10. Designation does not affect access
    def test_10_designation_does_not_affect_access(self):
        # Set HR designation to "CTO" or "Lead"
        self.dev_user1.designation = "Lead Architect"
        self.dev_user1.save()

        pm = ProjectMember.objects.create(project=self.project1, user=self.dev_user1, project_role="Contributor", is_active=True)
        self.assertEqual(pm.project_role, "Contributor")

        perms = get_project_effective_permissions(self.dev_user1, self.project1)
        self.assertNotIn("projects.board.manage", perms)

    # 11. Normal member defaults to CONTRIBUTOR
    def test_11_normal_member_defaults_to_contributor(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {
            "user_ids": [self.dev_user1.id]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pm = ProjectMember.objects.get(project=self.project1, user=self.dev_user1)
        self.assertEqual(pm.project_role, "Contributor")

    # 12. Bulk add preserves selected project roles
    def test_12_bulk_add_preserves_selected_project_role(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {
            "user_ids": [self.dev_user1.id, self.dev_user2.id],
            "project_role": "QA Engineer"
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pm1 = ProjectMember.objects.get(project=self.project1, user=self.dev_user1)
        pm2 = ProjectMember.objects.get(project=self.project1, user=self.dev_user2)
        self.assertEqual(pm1.project_role, "QA Engineer")
        self.assertEqual(pm2.project_role, "QA Engineer")

    # 13. Existing member is not duplicated
    def test_13_existing_member_not_duplicated(self):
        self.client.force_authenticate(user=self.admin_user)
        self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {"user_ids": [self.dev_user1.id]}, format='json')
        self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {"user_ids": [self.dev_user1.id]}, format='json')

        count = ProjectMember.objects.filter(project=self.project1, user=self.dev_user1).count()
        self.assertEqual(count, 1)

    # 14. Cross-company member is rejected
    def test_14_cross_company_member_rejected(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {
            "user_ids": [self.cross_company_user.id],
            "project_role": "Contributor"
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # 15. Completed Project blocks adding members
    def test_15_completed_project_blocks_adding_members(self):
        self.project1.status = self.status_done
        self.project1.save()

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {
            "user_ids": [self.dev_user1.id]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # 16. Unauthorized requester receives 403
    def test_16_unauthorized_requester_receives_403(self):
        self.client.force_authenticate(user=self.dev_user1)
        response = self.client.post(f"/api/v1/projects/{self.project1.id}/members/", {
            "user_ids": [self.dev_user2.id]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # 17. Contributor permissions check
    def test_17_contributor_permissions_check(self):
        ProjectMember.objects.create(project=self.project1, user=self.dev_user1, project_role="Contributor", is_active=True)

        perms = get_project_effective_permissions(self.dev_user1, self.project1)
        self.assertIn("projects.overview.view", perms)
        self.assertIn("projects.backlog.view", perms)
        self.assertIn("projects.board.view", perms)
        self.assertIn("projects.my_tasks.view", perms)
        self.assertIn("projects.comment.create", perms)

        self.assertNotIn("projects.sprint.create", perms)
        self.assertNotIn("projects.sprint.start", perms)
        self.assertNotIn("projects.members.manage", perms)
        self.assertNotIn("projects.board.manage", perms)

    # 18. Denied permissions override project role access
    def test_18_denied_permissions_override_project_role_access(self):
        flag_comment, _ = PermissionFlag.objects.get_or_create(key="projects.comment.create", defaults={"name": "Create Comment"})
        self.dev_user1.denied_permissions.add(flag_comment)

        ProjectMember.objects.create(project=self.project1, user=self.dev_user1, project_role="Contributor", is_active=True)

        perms = get_project_effective_permissions(self.dev_user1, self.project1)
        self.assertNotIn("projects.comment.create", perms)
