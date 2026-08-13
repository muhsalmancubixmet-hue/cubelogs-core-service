import datetime
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization
from users.models import Employee
from projects.models import Project, ProjectSprint, ProjectStory, ProjectActivity, ProjectStatusOption
from projects.services.sprints import create_sprint, start_sprint, complete_sprint


class SprintPlanningTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org Sprint")
        self.user = Employee.objects.create(
            username="sprint_planner",
            email="sprint_planner@example.com",
            organization=self.org,
            is_active=True,
            first_name="Sprint",
            last_name="Planner"
        )
        self.user.is_superuser = True
        self.user.set_password("password123")
        self.user.save()

        self.project = Project.objects.create(
            name="Scrum Mobile App",
            key="SMA-0001",
            company=self.org,
            project_manager=self.user,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31)
        )

        self.todo_status = ProjectStatusOption.objects.create(
            company=self.org,
            code="todo_plan",
            name="To Do",
            category="pending",
            order=1,
            is_active=True
        )

        self.story1 = ProjectStory.objects.create(
            project=self.project,
            story_key="STORY-001",
            title="User Registration",
            story_points=5,
            status=self.todo_status
        )
        self.story2 = ProjectStory.objects.create(
            project=self.project,
            story_key="STORY-002",
            title="User Login",
            story_points=3,
            status=self.todo_status
        )
        self.story3 = ProjectStory.objects.create(
            project=self.project,
            story_key="STORY-003",
            title="Restaurant Listing",
            story_points=8,
            status=self.todo_status
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_sprint_auto_planning_status_and_validations(self):
        """Verify sprint is created in 'planning' status with valid fields and validations."""
        sprint = create_sprint(
            project=self.project,
            name="Sprint 1",
            goal="User Authentication MVP",
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 15),
            capacity=20,
            user=self.user
        )
        self.assertEqual(sprint.status, "planning")
        self.assertEqual(sprint.name, "Sprint 1")
        self.assertEqual(sprint.goal, "User Authentication MVP")
        self.assertEqual(sprint.capacity, 20)
        self.assertTrue(ProjectActivity.objects.filter(project=self.project, action="Sprint Created").exists())

    def test_sprint_creation_date_and_field_validations(self):
        """Verify date and required field validations for sprint creation."""
        # Empty name
        with self.assertRaises(ValidationError):
            create_sprint(self.project, "", goal="Goal", start_date="2026-02-01", end_date="2026-02-15")

        # Empty goal
        with self.assertRaises(ValidationError):
            create_sprint(self.project, "Sprint X", goal="", start_date="2026-02-01", end_date="2026-02-15")

        # Invalid end date (before start date)
        with self.assertRaises(ValidationError):
            create_sprint(self.project, "Sprint X", goal="Goal", start_date="2026-02-15", end_date="2026-02-01")

        # Start date before project start date
        with self.assertRaises(ValidationError):
            create_sprint(self.project, "Sprint X", goal="Goal", start_date="2025-12-01", end_date="2026-02-15")

    def test_add_and_remove_stories_from_planning_sprint(self):
        """Verify adding backlog stories to planning sprint and removing them."""
        sprint = create_sprint(
            project=self.project,
            name="Sprint 1",
            goal="User Auth & Listing",
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 15),
            capacity=20,
            user=self.user
        )

        # Add 3 stories via API
        response = self.client.post(
            f"/api/v1/project-sprints/{sprint.id}/add-stories/",
            {"story_ids": [self.story1.id, self.story2.id, self.story3.id]},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.story1.refresh_from_db()
        self.story2.refresh_from_db()
        self.story3.refresh_from_db()

        self.assertEqual(self.story1.sprint_id, sprint.id)
        self.assertEqual(self.story2.sprint_id, sprint.id)
        self.assertEqual(self.story3.sprint_id, sprint.id)

        # Verify stories count and story points
        sprint_res = self.client.get(f"/api/v1/project-sprints/{sprint.id}/")
        self.assertEqual(sprint_res.data["stories_count"], 3)
        self.assertEqual(sprint_res.data["total_story_points"], 16)
        self.assertEqual(sprint_res.data["remaining_capacity"], 4)
        self.assertEqual(sprint_res.data["sprint_key"], "SPR-001")

        # Remove story1
        rem_res = self.client.post(
            f"/api/v1/project-sprints/{sprint.id}/remove-story/",
            {"story_id": self.story1.id},
            format="json"
        )
        self.assertEqual(rem_res.status_code, status.HTTP_200_OK)
        self.story1.refresh_from_db()
        self.assertIsNone(self.story1.sprint)

    def test_start_sprint_and_single_active_sprint_rule(self):
        """Verify starting sprint transitions to active and enforces single active sprint."""
        sprint1 = create_sprint(
            project=self.project,
            name="Sprint 1",
            goal="Authentication",
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 15),
            capacity=20,
            user=self.user
        )
        sprint2 = create_sprint(
            project=self.project,
            name="Sprint 2",
            goal="Payments",
            start_date=datetime.date(2026, 2, 16),
            end_date=datetime.date(2026, 3, 1),
            capacity=20,
            user=self.user
        )

        # Cannot start sprint without stories
        with self.assertRaises(ValidationError):
            start_sprint(sprint1, user=self.user)

        # Add story to sprint1
        self.story1.sprint = sprint1
        self.story1.save()

        # Start sprint1
        started1 = start_sprint(sprint1, user=self.user)
        self.assertEqual(started1.status, "active")

        # Add story to sprint2 and attempt to start -> fails because sprint1 is active
        self.story2.sprint = sprint2
        self.story2.save()

        with self.assertRaises(ValidationError):
            start_sprint(sprint2, user=self.user)

    def test_board_endpoint_returns_only_active_sprint_work(self):
        """Verify /api/v1/projects/{projectId}/board/ filters stories by active sprint."""
        sprint1 = create_sprint(
            project=self.project,
            name="Sprint 1",
            goal="Active Iteration",
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 15),
            capacity=20,
            user=self.user
        )
        self.story1.sprint = sprint1
        self.story1.save()
        start_sprint(sprint1, user=self.user)

        # Story 2 remains in backlog (sprint = null)
        board_res = self.client.get(f"/api/v1/projects/{self.project.id}/board/")
        self.assertEqual(board_res.status_code, status.HTTP_200_OK)
        columns = board_res.data.get("columns", [])
        todo_stories = [s for col in columns if col["status"]["category"] in ["pending", "todo"] for s in col["stories"]]

        # Only story1 (in active sprint) appears on board
        story_ids_on_board = [s["id"] for s in todo_stories]
        self.assertIn(self.story1.id, story_ids_on_board)
        self.assertNotIn(self.story2.id, story_ids_on_board)
