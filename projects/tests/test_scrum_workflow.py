from django.test import TestCase
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee
from projects.models import (
    Project, ProjectStatusOption, ProjectEpic, ProjectSprint,
    ProjectStory, ProjectTask, ProjectComment, ProjectAttachment
)
from projects.services.statuses import initialize_default_statuses
from projects.services.sprints import start_sprint, complete_sprint


class ScrumWorkflowTestCase(TestCase):
    def setUp(self):
        organization_settings = OrgSettings.objects.create()
        self.company_organization = Organization.objects.create(
            name="CubeLogs Inc", subdomain="cubelogs", settings=organization_settings
        )
        OrganizationModule.objects.create(
            organization=self.company_organization, module_id='project_management', enabled=True
        )
        initialize_default_statuses(self.company_organization)

        self.pending_status = ProjectStatusOption.objects.get(company=self.company_organization, code='pending')
        self.completed_status = ProjectStatusOption.objects.get(company=self.company_organization, code='completed')

        # Realistic Scrum team members
        self.project_manager_salman = Employee.objects.create_user(
            email="salman.pm@cubelogs.com", first_name="Salman", last_name="Manager",
            password="password123", organization=self.company_organization,
            permissions=[
                "projects:view", "projects:create", "projects:update", "projects:delete",
                "project_epics:create", "project_sprints:create", "project_sprints:manage",
                "project_stories:create", "project_tasks:create"
            ]
        )
        self.team_lead_akhil = Employee.objects.create_user(
            email="akhil.tl@cubelogs.com", first_name="Akhil", last_name="Lead",
            password="password123", organization=self.company_organization,
            permissions=["projects:view", "project_stories:view", "project_tasks:view_all"]
        )

        self.project_hrms = Project.objects.create(
            company=self.company_organization, name="CubeLogs HRMS",
            project_manager=self.project_manager_salman,
            team_lead=self.team_lead_akhil, status=self.pending_status
        )

    def test_configurable_project_key_generation(self):
        self.assertEqual(self.project_hrms.key, "HRM-0001")

        project_attendance = Project.objects.create(
            company=self.company_organization, name="Attendance Management",
            project_manager=self.project_manager_salman, status=self.pending_status
        )
        self.assertEqual(project_attendance.key, "ATT-0001")

    def test_fibonacci_story_points_validation(self):
        valid_story = ProjectStory(
            project=self.project_hrms, title="Implement Employee CRUD", story_points=8, status=self.pending_status
        )
        valid_story.full_clean()  # Should pass clean validation

        invalid_story = ProjectStory(
            project=self.project_hrms, title="Invalid Story Points", story_points=7, status=self.pending_status
        )
        with self.assertRaises(ValidationError):
            invalid_story.full_clean()

    def test_single_active_sprint_per_project_rule(self):
        sprint_one = ProjectSprint.objects.create(project=self.project_hrms, name="Sprint 1", status="planning")
        sprint_two = ProjectSprint.objects.create(project=self.project_hrms, name="Sprint 2", status="planning")
        ProjectStory.objects.create(project=self.project_hrms, sprint=sprint_one, title="Story 1", story_points=3, status=self.pending_status)
        ProjectStory.objects.create(project=self.project_hrms, sprint=sprint_two, title="Story 2", story_points=3, status=self.pending_status)

        start_sprint(sprint_one, user=self.project_manager_salman)
        self.assertEqual(sprint_one.status, "active")

        with self.assertRaises(ValidationError):
            start_sprint(sprint_two, user=self.project_manager_salman)

    def test_comment_explicit_target_validation(self):
        project_epic = ProjectEpic.objects.create(
            company=self.company_organization, project=self.project_hrms, title="Employee Management"
        )
        project_story = ProjectStory.objects.create(
            project=self.project_hrms, title="Implement Employee CRUD", status=self.pending_status
        )

        valid_comment = ProjectComment(user=self.project_manager_salman, comment="Great progress on Employee CRUD", story=project_story)
        valid_comment.full_clean()

        invalid_comment = ProjectComment(user=self.project_manager_salman, comment="Invalid double target", story=project_story, epic=project_epic)
        with self.assertRaises(ValidationError):
            invalid_comment.full_clean()

    def test_sprint_completion_moves_uncompleted_stories(self):
        sprint_one = ProjectSprint.objects.create(project=self.project_hrms, name="Sprint 1", status="planning")
        story_completed = ProjectStory.objects.create(
            project=self.project_hrms, sprint=sprint_one, title="Create Employee API", story_points=5, status=self.completed_status
        )
        story_pending = ProjectStory.objects.create(
            project=self.project_hrms, sprint=sprint_one, title="Design Profile UI", story_points=3, status=self.pending_status
        )

        start_sprint(sprint_one, user=self.project_manager_salman)
        complete_sprint(sprint_one, user=self.project_manager_salman)

        story_completed.refresh_from_db()
        story_pending.refresh_from_db()

        self.assertEqual(story_completed.sprint, sprint_one)
        self.assertIsNone(story_pending.sprint)  # Returned to Product Backlog

    def test_api_burndown_and_velocity(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.project_manager_salman)

        sprint_alpha = ProjectSprint.objects.create(project=self.project_hrms, name="Sprint 1", status="planning")
        ProjectStory.objects.create(project=self.project_hrms, sprint=sprint_alpha, title="Story Alpha", story_points=5, status=self.pending_status)
        start_sprint(sprint_alpha, user=self.project_manager_salman)

        response_burndown = api_client.get(f"/api/v1/project-sprints/{sprint_alpha.id}/burndown/")
        self.assertEqual(response_burndown.status_code, status.HTTP_200_OK)
        self.assertIn('timeline', response_burndown.data)

        response_velocity = api_client.get(f"/api/v1/projects/{self.project_hrms.id}/velocity/")
        self.assertEqual(response_velocity.status_code, status.HTTP_200_OK)
