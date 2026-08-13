from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status

from core.models import Organization
from users.models import Employee
from projects.models import Project, ProjectSprint, ProjectStory, ProjectSprintEvent, ProjectStatusOption
from projects.services.sprints import create_sprint, start_sprint, complete_sprint, cancel_sprint
from projects.selectors.analytics import calculate_project_velocity


class VelocityAnalyticsTestCase(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Company A", subdomain="companya-vel")
        self.org2 = Organization.objects.create(name="Company B", subdomain="companyb-vel")

        self.admin1 = Employee.objects.create_user(
            email="admin1@vel.com",
            username="admin1@vel.com",
            password="password123",
            first_name="Admin",
            last_name="One",
            designation="Admin",
            organization=self.org1,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            isSuperAdmin=True,
            permissions=['project_sprints:view', 'projects:view']
        )

        self.admin2 = Employee.objects.create_user(
            email="admin2@vel.com",
            username="admin2@vel.com",
            password="password123",
            first_name="Admin",
            last_name="Two",
            designation="Admin",
            organization=self.org2,
            is_active=True,
            isSuperAdmin=True,
            permissions=['project_sprints:view', 'projects:view']
        )

        self.project1 = Project.objects.create(
            company=self.org1,
            name="Project Velocity Test",
            key="PVT1",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=60)
        )

        self.project2 = Project.objects.create(
            company=self.org2,
            name="Project Org2",
            key="POR2",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=60)
        )

        self.status_completed = ProjectStatusOption.objects.filter(company=self.org1, category='completed').first()
        if not self.status_completed:
            self.status_completed = ProjectStatusOption.objects.create(
                company=self.org1, code="done_vel", name="Done", category="completed", progress_percentage=100, is_active=True
            )

        self.status_in_progress = ProjectStatusOption.objects.filter(company=self.org1, category='active').first()
        if not self.status_in_progress:
            self.status_in_progress = ProjectStatusOption.objects.create(
                company=self.org1, code="in_prog_vel", name="In Progress", category="active", is_active=True
            )

    def test_empty_project_returns_zero_velocity(self):
        result = calculate_project_velocity(self.project1)
        self.assertEqual(result['project_id'], self.project1.id)
        self.assertEqual(result['average_velocity'], 0)
        self.assertEqual(len(result['sprints']), 0)

    def test_completed_sprints_included_planning_active_cancelled_excluded(self):
        # 1. Completed Sprint 1
        s1 = create_sprint(self.project1, "Sprint 1", "Goal 1", timezone.now().date(), timezone.now().date() + timezone.timedelta(days=14), user=self.admin1)
        ProjectStory.objects.create(project=self.project1, sprint=s1, title="S1 Story 1", story_points=5, status=self.status_completed)
        ProjectStory.objects.create(project=self.project1, sprint=s1, title="S1 Story 2", story_points=3, status=self.status_completed)
        start_sprint(s1, user=self.admin1)
        complete_sprint(s1, user=self.admin1)

        # 2. Completed Sprint 2
        s2 = create_sprint(self.project1, "Sprint 2", "Goal 2", timezone.now().date() + timezone.timedelta(days=15), timezone.now().date() + timezone.timedelta(days=29), user=self.admin1)
        ProjectStory.objects.create(project=self.project1, sprint=s2, title="S2 Story 1", story_points=8, status=self.status_completed)
        start_sprint(s2, user=self.admin1)
        complete_sprint(s2, user=self.admin1)

        # 3. Planning Sprint (should be excluded)
        create_sprint(self.project1, "Sprint 3 Planning", "Goal 3", timezone.now().date() + timezone.timedelta(days=30), timezone.now().date() + timezone.timedelta(days=44), user=self.admin1)

        # 4. Cancelled Sprint (should be excluded)
        s_cancel = create_sprint(self.project1, "Sprint Cancelled", "Goal Cancel", timezone.now().date() + timezone.timedelta(days=45), timezone.now().date() + timezone.timedelta(days=59), user=self.admin1)
        cancel_sprint(s_cancel, reason="Cancelled", user=self.admin1)

        result = calculate_project_velocity(self.project1)
        self.assertEqual(len(result['sprints']), 2)
        self.assertEqual(result['average_velocity'], 8)  # (8 + 8) / 2 = 8

        sp1_data = next(s for s in result['sprints'] if s['sprint_id'] == s1.id)
        self.assertEqual(sp1_data['committed_points'], 8)
        self.assertEqual(sp1_data['completed_points'], 8)

        sp2_data = next(s for s in result['sprints'] if s['sprint_id'] == s2.id)
        self.assertEqual(sp2_data['committed_points'], 8)
        self.assertEqual(sp2_data['completed_points'], 8)

    def test_sprint_with_zero_completed_points_returns_valid_data_point(self):
        s1 = create_sprint(self.project1, "Sprint Zero", "Goal Zero", timezone.now().date(), timezone.now().date() + timezone.timedelta(days=14), user=self.admin1)
        ProjectStory.objects.create(project=self.project1, sprint=s1, title="Unfinished Story", story_points=5, status=self.status_in_progress)
        start_sprint(s1, user=self.admin1)
        complete_sprint(s1, user=self.admin1)

        result = calculate_project_velocity(self.project1)
        self.assertEqual(len(result['sprints']), 1)
        self.assertEqual(result['sprints'][0]['committed_points'], 5)
        self.assertEqual(result['sprints'][0]['completed_points'], 0)
        self.assertEqual(result['average_velocity'], 0)

    def test_historical_snapshot_remains_immutable_after_story_edits(self):
        s1 = create_sprint(self.project1, "Immutable Sprint", "Goal", timezone.now().date(), timezone.now().date() + timezone.timedelta(days=14), user=self.admin1)
        story = ProjectStory.objects.create(project=self.project1, sprint=s1, title="Story Fixed", story_points=10, status=self.status_completed)
        start_sprint(s1, user=self.admin1)
        complete_sprint(s1, user=self.admin1)

        # Edit story after sprint completion
        story.story_points = 99
        story.save()

        result = calculate_project_velocity(self.project1)
        # Should still report committed 10, completed 10 from snapshot!
        self.assertEqual(result['sprints'][0]['committed_points'], 10)
        self.assertEqual(result['sprints'][0]['completed_points'], 10)

    def test_idempotent_backfill_for_completed_sprints_missing_snapshot(self):
        s1 = create_sprint(self.project1, "Legacy Completed Sprint", "Goal", timezone.now().date(), timezone.now().date() + timezone.timedelta(days=14), user=self.admin1)
        ProjectStory.objects.create(project=self.project1, sprint=s1, title="Legacy Story", story_points=7, status=self.status_completed)
        s1.status = 'completed'
        s1.completed_at = timezone.now()
        s1.save()

        # Confirm no event exists initially
        self.assertFalse(s1.events.filter(event_type='sprint_completed').exists())

        # First call to calculate_project_velocity triggers idempotent backfill
        res1 = calculate_project_velocity(self.project1)
        self.assertTrue(s1.events.filter(event_type='sprint_completed').exists())
        self.assertEqual(res1['sprints'][0]['completed_points'], 7)

        # Second call reuses the snapshot without creating duplicates
        res2 = calculate_project_velocity(self.project1)
        self.assertEqual(s1.events.filter(event_type='sprint_completed').count(), 1)
        self.assertEqual(res2['sprints'][0]['completed_points'], 7)

    def test_cross_company_velocity_blocked(self):
        client = Client(enforce_csrf_checks=False)
        client.login(username="admin2@vel.com", password="password123")

        # Try to access Company A project velocity as Company B user -> Expect 404
        resp = client.get(f"/api/v1/projects/{self.project1.id}/velocity/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
