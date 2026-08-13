# --------------------------------------------------------------------------------
#       Projects Tests - ChatConsumer Room Permissions
# --------------------------------------------------------------------------------

from django.test import TestCase
from asgiref.sync import async_to_sync
from core.models import Organization
from users.models import Employee
from projects.models import Project, ProjectStory, ProjectTask, ProjectStoryMember, ProjectMember


class ChatConsumerPermissionsTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org")
        self.owner = Employee.objects.create_user(email="owner@test.com", password="password", username="owner", organization=self.org)
        self.pm = Employee.objects.create_user(email="pm@test.com", password="password", username="pm", organization=self.org)
        self.story_member = Employee.objects.create_user(email="smember@test.com", password="password", username="storymember", organization=self.org)
        self.task_member = Employee.objects.create_user(email="tmember@test.com", password="password", username="taskmember", organization=self.org)
        self.unauthorized_user = Employee.objects.create_user(email="unauth@test.com", password="password", username="unauth", organization=self.org)

        self.project = Project.objects.create(
            name="Test Project",
            key="TP",
            company=self.org,
            project_manager=self.pm
        )

        # Register project members
        ProjectMember.objects.create(project=self.project, user=self.pm, is_active=True)
        self.sm_pm = ProjectMember.objects.create(project=self.project, user=self.story_member, is_active=True)
        ProjectMember.objects.create(project=self.project, user=self.task_member, is_active=True)

        self.story = ProjectStory.objects.create(
            project=self.project,
            title="Test Story",
            created_by=self.pm
        )

        # Assign story member
        ProjectStoryMember.objects.create(story=self.story, member=self.sm_pm)

        # Assign task member under story
        self.task = ProjectTask.objects.create(
            story=self.story,
            title="Test Task",
            assigned_to=self.task_member
        )

    def test_pm_access_story_room(self):
        from projects.consumers import ChatConsumer
        consumer = ChatConsumer()
        has_perm, proj_id = async_to_sync(consumer.check_room_permission)(self.pm, 'story', str(self.story.id))
        self.assertTrue(has_perm)
        self.assertEqual(proj_id, self.project.id)

    def test_story_member_access_story_room(self):
        from projects.consumers import ChatConsumer
        consumer = ChatConsumer()
        has_perm, proj_id = async_to_sync(consumer.check_room_permission)(self.story_member, 'story', str(self.story.id))
        self.assertTrue(has_perm)
        self.assertEqual(proj_id, self.project.id)

    def test_task_assigned_member_access_story_room(self):
        from projects.consumers import ChatConsumer
        consumer = ChatConsumer()
        has_perm, proj_id = async_to_sync(consumer.check_room_permission)(self.task_member, 'story', str(self.story.id))
        self.assertTrue(has_perm)
        self.assertEqual(proj_id, self.project.id)

    def test_unauthorized_user_rejected_story_room(self):
        from projects.consumers import ChatConsumer
        consumer = ChatConsumer()
        has_perm, proj_id = async_to_sync(consumer.check_room_permission)(self.unauthorized_user, 'story', str(self.story.id))
        self.assertFalse(has_perm)
        self.assertIsNone(proj_id)
