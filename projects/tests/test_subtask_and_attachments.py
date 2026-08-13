from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, OrganizationModule
from projects.models import Project, ProjectStory, ProjectTask, ProjectSubtask, ProjectAttachment
from projects.services.stories import create_story
from projects.services.tasks import create_task
from projects.services.subtasks import create_subtask

User = get_user_model()


class SubtaskAndAttachmentAPITestCase(TestCase):
    def setUp(self):
        settings1 = OrgSettings.objects.create()
        self.org = Organization.objects.create(name='Test Corp', subdomain='subtasktest', settings=settings1)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)

        settings2 = OrgSettings.objects.create()
        self.other_org = Organization.objects.create(name='Other Corp', subdomain='othertest', settings=settings2)
        OrganizationModule.objects.create(organization=self.other_org, module_id='project_management', enabled=True)

        self.user = User.objects.create_user(
            username='subtask_tester',
            email='subtask_tester@test.com',
            password='Password123!',
            organization=self.org,
            permissions=["projects:view", "project_tasks:update_own", "project_tasks:create"]
        )

        self.other_user = User.objects.create_user(
            username='other_tester',
            email='other@other.com',
            password='Password123!',
            organization=self.other_org,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.project = Project.objects.create(
            company=self.org,
            name='Scrum Subtask Test Project',
            team_lead=self.user,
        )

        self.story = create_story(
            project=self.project,
            title='Subtask Test Story',
            user=self.user,
        )

        self.task = create_task(
            story=self.story,
            title='Subtask Test Task',
            user=self.user,
        )

        self.subtask = create_subtask(
            task=self.task,
            title='Child Subtask 1',
            user=self.user,
        )

    def test_complete_subtask_successfully(self):
        url = f'/api/v1/subtasks/{self.subtask.id}/'
        response = self.client.patch(url, {'is_completed': True}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.subtask.refresh_from_db()
        self.assertTrue(self.subtask.is_completed)
        self.assertEqual(response.data['task_progress']['completed_subtasks'], 1)

    def test_reopen_subtask_successfully(self):
        # Complete first
        self.subtask.is_completed = True
        self.subtask.save()

        url = f'/api/v1/subtasks/{self.subtask.id}/'
        response = self.client.patch(url, {'is_completed': False}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.subtask.refresh_from_db()
        self.assertFalse(self.subtask.is_completed)
        self.assertEqual(response.data['task_progress']['completed_subtasks'], 0)

    def test_cross_company_subtask_blocked(self):
        other_client = APIClient()
        other_client.force_authenticate(user=self.other_user)

        url = f'/api/v1/subtasks/{self.subtask.id}/'
        response = other_client.patch(url, {'is_completed': True}, format='json')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_valid_task_attachment_upload(self):
        file_data = SimpleUploadedFile("test_doc.pdf", b"%PDF-1.4\n%PDF content bytes", content_type="application/pdf")
        url = '/api/v1/attachments/'
        response = self.client.post(url, {'file': file_data, 'task': self.task.id}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProjectAttachment.objects.filter(task=self.task).count(), 1)
 
    def test_valid_story_attachment_upload(self):
        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        file_data = SimpleUploadedFile("mock_ui.png", valid_png, content_type="image/png")
        url = '/api/v1/attachments/'
        response = self.client.post(url, {'file': file_data, 'story': self.story.id}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProjectAttachment.objects.filter(story=self.story).count(), 1)

    def test_invalid_file_type_blocked(self):
        file_data = SimpleUploadedFile("script.sh", b"#!/bin/bash\necho bad", content_type="application/x-sh")
        url = '/api/v1/attachments/'
        response = self.client.post(url, {'file': file_data, 'task': self.task.id}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not supported", str(response.data))

    def test_attachment_multiple_targets_blocked(self):
        file_data = SimpleUploadedFile("doc.txt", b"Hello", content_type="text/plain")
        url = '/api/v1/attachments/'
        response = self.client.post(url, {'file': file_data, 'task': self.task.id, 'story': self.story.id}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_attachment_no_target_blocked(self):
        file_data = SimpleUploadedFile("doc.txt", b"Hello", content_type="text/plain")
        url = '/api/v1/attachments/'
        response = self.client.post(url, {'file': file_data}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
