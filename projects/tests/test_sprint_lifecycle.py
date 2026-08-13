from django.test import TestCase, Client
from django.utils import timezone
from django.core.exceptions import ValidationError
from rest_framework import status

from core.models import Organization
from users.models import Employee
from projects.models import Project, ProjectSprint, ProjectStory, ProjectActivity, ProjectStatusOption
from projects.services.sprints import create_sprint, start_sprint, complete_sprint, cancel_sprint, reopen_sprint, delete_sprint


class SprintLifecycleTestCase(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Company A", subdomain="companya")
        self.org2 = Organization.objects.create(name="Company B", subdomain="companyb")

        # Admin user for org1
        self.admin1 = Employee.objects.create_user(
            email="admin1@companya.com",
            username="admin1@companya.com",
            password="password123",
            first_name="Admin",
            last_name="One",
            designation="Admin",
            organization=self.org1,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            isSuperAdmin=True,
            permissions=['project_sprints:view', 'project_sprints:create', 'project_sprints:update', 'project_sprints:manage', 'project_sprints:delete', 'projects:view']
        )

        # PM user for org1
        self.pm1 = Employee.objects.create_user(
            email="pm1@companya.com",
            username="pm1@companya.com",
            password="password123",
            first_name="PM",
            last_name="One",
            designation="Project Manager",
            organization=self.org1,
            is_active=True,
            permissions=['project_sprints:view', 'project_sprints:create', 'project_sprints:update', 'project_sprints:manage', 'project_sprints:delete', 'projects:view']
        )

        # Developer user for org1
        self.dev1 = Employee.objects.create_user(
            email="dev1@companya.com",
            username="dev1@companya.com",
            password="password123",
            first_name="Dev",
            last_name="One",
            designation="Developer",
            organization=self.org1,
            is_active=True,
            permissions=['project_sprints:view', 'projects:view']
        )

        # Admin user for org2
        self.admin2 = Employee.objects.create_user(
            email="admin2@companyb.com",
            username="admin2@companyb.com",
            password="password123",
            first_name="Admin",
            last_name="Two",
            designation="Admin",
            organization=self.org2,
            is_active=True,
            isSuperAdmin=True,
            permissions=['project_sprints:view', 'project_sprints:create', 'project_sprints:update', 'project_sprints:manage', 'project_sprints:delete', 'projects:view']
        )

        # Project for org1
        self.project1 = Project.objects.create(
            company=self.org1,
            name="Project One",
            key="PRJ1",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=60)
        )

        # Project for org2
        self.project2 = Project.objects.create(
            company=self.org2,
            name="Project Two",
            key="PRJ2",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=60)
        )

        # Statuses
        self.status_completed = ProjectStatusOption.objects.filter(company=self.org1, category='completed').first()
        if not self.status_completed:
            self.status_completed = ProjectStatusOption.objects.create(
                company=self.org1, code="done_custom", name="Done", category="completed", progress_percentage=100, is_active=True
            )

        self.status_in_progress = ProjectStatusOption.objects.filter(company=self.org1, category='active').first()
        if not self.status_in_progress:
            self.status_in_progress = ProjectStatusOption.objects.create(
                company=self.org1, code="in_progress_custom", name="In Progress", category="active", is_active=True
            )

    # --------------------------------------------------------------------------
    # 1. DELETE SPRINT TESTS
    # --------------------------------------------------------------------------
    def test_delete_empty_planning_sprint(self):
        sprint = create_sprint(
            project=self.project1,
            name="Planning Sprint 1",
            goal="Test Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        sprint_id = sprint.id
        result = delete_sprint(sprint, user=self.pm1)
        self.assertTrue(result)
        self.assertFalse(ProjectSprint.objects.filter(id=sprint_id, is_deleted=False).exists())

    def test_delete_planning_sprint_returns_stories_to_backlog(self):
        sprint = create_sprint(
            project=self.project1,
            name="Planning Sprint 2",
            goal="Test Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        story1 = ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 1", story_points=3)
        story2 = ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 2", story_points=5)

        delete_sprint(sprint, user=self.pm1)

        story1.refresh_from_db()
        story2.refresh_from_db()
        self.assertIsNone(story1.sprint)
        self.assertIsNone(story2.sprint)
        self.assertTrue(ProjectStory.objects.filter(id=story1.id).exists())
        self.assertTrue(ProjectStory.objects.filter(id=story2.id).exists())

    def test_delete_active_sprint_blocked(self):
        sprint = create_sprint(
            project=self.project1,
            name="Active Sprint",
            goal="Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 1", story_points=2)
        start_sprint(sprint, user=self.pm1)

        with self.assertRaises(ValidationError) as ctx:
            delete_sprint(sprint, user=self.pm1)
        self.assertIn("Only Planning Sprints can be deleted", str(ctx.exception))

    def test_delete_completed_sprint_blocked(self):
        sprint = create_sprint(
            project=self.project1,
            name="Completed Sprint",
            goal="Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 1", story_points=2)
        start_sprint(sprint, user=self.pm1)
        complete_sprint(sprint, user=self.pm1)

        with self.assertRaises(ValidationError) as ctx:
            delete_sprint(sprint, user=self.pm1)
        self.assertIn("Only Planning Sprints can be deleted", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 2. CANCEL SPRINT TESTS
    # --------------------------------------------------------------------------
    def test_cancel_planning_sprint(self):
        sprint = create_sprint(
            project=self.project1,
            name="Cancel Sprint",
            goal="Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        story1 = ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Incomplete 1", story_points=3)

        cancelled = cancel_sprint(sprint, reason="Scope changed", move_incomplete_to='backlog', user=self.pm1)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertEqual(cancelled.cancellation_reason, "Scope changed")
        self.assertEqual(cancelled.cancelled_by, self.pm1)
        self.assertIsNotNone(cancelled.cancelled_at)

        story1.refresh_from_db()
        self.assertIsNone(story1.sprint)

    def test_cancel_active_sprint_retains_completed_stories(self):
        sprint = create_sprint(
            project=self.project1,
            name="Active Cancel Sprint",
            goal="Goal",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=14),
            user=self.pm1
        )
        story_done = ProjectStory.objects.create(
            project=self.project1, sprint=sprint, title="Done Story", story_points=5, status=self.status_completed
        )
        story_todo = ProjectStory.objects.create(
            project=self.project1, sprint=sprint, title="Incomplete Story", story_points=3, status=self.status_in_progress
        )
        start_sprint(sprint, user=self.pm1)

        cancelled = cancel_sprint(sprint, reason="Client Priority Shift", move_incomplete_to='backlog', user=self.pm1)
        self.assertEqual(cancelled.status, 'cancelled')

        story_done.refresh_from_db()
        story_todo.refresh_from_db()
        self.assertEqual(story_done.sprint_id, sprint.id)
        self.assertIsNone(story_todo.sprint)

    def test_cancel_sprint_move_incomplete_to_target_planning_sprint(self):
        sprint1 = create_sprint(
            project=self.project1, name="Sprint To Cancel", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )
        target_sprint = create_sprint(
            project=self.project1, name="Target Planning Sprint", goal="Goal",
            start_date=timezone.now().date() + timezone.timedelta(days=15), end_date=timezone.now().date() + timezone.timedelta(days=29), user=self.pm1
        )

        story = ProjectStory.objects.create(project=self.project1, sprint=sprint1, title="Story Move", story_points=3)

        cancel_sprint(sprint1, reason="Rescheduled", move_incomplete_to='sprint', target_sprint_id=target_sprint.id, user=self.pm1)

        story.refresh_from_db()
        self.assertEqual(story.sprint_id, target_sprint.id)

    def test_cancel_completed_sprint_blocked(self):
        sprint = create_sprint(
            project=self.project1, name="Completed Sprint", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )
        ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 1", story_points=2)
        start_sprint(sprint, user=self.pm1)
        complete_sprint(sprint, user=self.pm1)

        with self.assertRaises(ValidationError) as ctx:
            cancel_sprint(sprint, reason="Cancel test", user=self.pm1)
        self.assertIn("Completed sprints cannot be cancelled", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 3. REOPEN SPRINT TESTS
    # --------------------------------------------------------------------------
    def test_reopen_cancelled_sprint(self):
        sprint = create_sprint(
            project=self.project1, name="Cancelled Sprint", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )
        cancel_sprint(sprint, reason="Temporary hold", user=self.pm1)

        reopened = reopen_sprint(sprint, user=self.pm1)
        self.assertEqual(reopened.status, 'planning')
        self.assertIsNone(reopened.cancelled_at)
        self.assertIsNone(reopened.cancelled_by)
        self.assertIsNone(reopened.cancellation_reason)

    def test_reopen_completed_sprint_blocked(self):
        sprint = create_sprint(
            project=self.project1, name="Completed Sprint", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )
        ProjectStory.objects.create(project=self.project1, sprint=sprint, title="Story 1", story_points=2)
        start_sprint(sprint, user=self.pm1)
        complete_sprint(sprint, user=self.pm1)

        with self.assertRaises(ValidationError) as ctx:
            reopen_sprint(sprint, user=self.pm1)
        self.assertIn("Only Cancelled Sprints can be reopened", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 4. CSRF SECURITY & ENDPOINT TESTS
    # --------------------------------------------------------------------------
    def test_csrf_token_required_for_unsafe_requests(self):
        client = Client(enforce_csrf_checks=True)

        sprint = create_sprint(
            project=self.project1, name="CSRF Test Sprint", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )

        client.login(username="admin1@companya.com", password="password123")

        # 1. Unsafe DELETE request WITHOUT X-CSRFToken header -> Expect 403 Forbidden
        resp_no_csrf = client.delete(f"/api/v1/project-sprints/{sprint.id}/")
        self.assertEqual(resp_no_csrf.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Unsafe DELETE request WITH valid X-CSRFToken header -> Expect 200 OK
        # Fetch csrftoken cookie via /api/auth/me/
        me_resp = client.get("/api/auth/me/")
        csrf_token = client.cookies.get('csrftoken').value

        resp_with_csrf = client.delete(
            f"/api/v1/project-sprints/{sprint.id}/",
            HTTP_X_CSRFTOKEN=csrf_token
        )
        self.assertEqual(resp_with_csrf.status_code, status.HTTP_200_OK)
        self.assertFalse(ProjectSprint.objects.filter(id=sprint.id, is_deleted=False).exists())

    def test_cross_company_delete_blocked(self):
        client = Client(enforce_csrf_checks=False)
        # Login as Admin 2 (Company B)
        client.login(username="admin2@companyb.com", password="password123")

        sprint_comp_a = create_sprint(
            project=self.project1, name="Company A Sprint", goal="Goal",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timezone.timedelta(days=14), user=self.pm1
        )

        # Try deleting Company A sprint as Company B user -> Expect 404
        resp = client.delete(f"/api/v1/project-sprints/{sprint_comp_a.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(ProjectSprint.objects.filter(id=sprint_comp_a.id).exists())
