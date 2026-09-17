import uuid
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles
from projects.models import (
    Project, ProjectEpic, ProjectStory, ProjectTask, ProjectAttachment,
    ProjectMember, ProjectStoryMember
)
from projects.services.comments import create_attachment, create_comment
from storage_billing.models import StorageFile, StorageEvent
from subscribers.models import GlobalBillingSettings


class RichTextMediaHardeningTestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        # Setup Org A
        self.settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)

        # Setup Org B
        self.settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)

        # Global billing settings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_billing_enabled = False
        self.g_settings.save()

        # Role and Users
        self.role_pm = Role.objects.get(slug='project-manager', organization__isnull=True)

        self.user_a = Employee.objects.create_user(
            email="pm@org-a.com",
            password="password123",
            organization=self.org_a,
            role=self.role_pm,
            is_active=True
        )
        self.membership_a = OrganizationMembership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=self.role_pm,
            is_active_in_org=True
        )

        self.user_b = Employee.objects.create_user(
            email="pm@org-b.com",
            password="password123",
            organization=self.org_b,
            role=self.role_pm,
            is_active=True
        )
        self.membership_b = OrganizationMembership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=self.role_pm,
            is_active_in_org=True
        )

        # Base project & story
        self.project_a = Project.objects.create(name="Project A", company=self.org_a, project_manager=self.user_a)
        self.story_a = ProjectStory.objects.create(project=self.project_a, title="Story A")
        self.task_a = ProjectTask.objects.create(story=self.story_a, title="Task A")

        self.project_b = Project.objects.create(name="Project B", company=self.org_b, project_manager=self.user_b)
        self.story_b = ProjectStory.objects.create(project=self.project_b, title="Story B")

        # Clients
        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.user_a)

        self.client_b = APIClient()
        self.client_b.force_authenticate(user=self.user_b)

        self.valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"

    def test_01_raw_base64_image_in_rich_text_api_save_rejected(self):
        """
        Prove: Direct API request containing raw base64 image data URI returns HTTP 400.
        """
        # Test Project description
        resp_proj = self.client_a.patch(
            f'/api/v1/projects/{self.project_a.id}/',
            {'description': '<p><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="></p>'},
            format='json'
        )
        self.assertEqual(resp_proj.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Raw base64 images are not allowed", str(resp_proj.data))

        # Test Story acceptance_criteria
        resp_story = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'acceptance_criteria': '<p>Here: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP...</p>'},
            format='json'
        )
        self.assertEqual(resp_story.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Raw base64 images are not allowed", str(resp_story.data))

        # Test Task description
        resp_task = self.client_a.patch(
            f'/api/v1/project-tasks/{self.task_a.id}/',
            {'description': '<p><img src="data:image/webp;base64,UklGRkAAAABXRUJQVlA4IDQAAADwAQCdASoBAAEAAQAcJaACdLoAAP7/2QAA"></p>'},
            format='json'
        )
        self.assertEqual(resp_task.status_code, status.HTTP_400_BAD_REQUEST)

    def test_02_normal_attachment_reference_accepted(self):
        """
        Prove: Normal attachment reference URL and data-attachment-id are accepted.
        """
        file_data = SimpleUploadedFile("spec.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=True)

        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><img src="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}"></p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_03_referenced_attachment_remains_active(self):
        """
        Prove: An attachment referenced in rich-text remains ACTIVE in ProjectAttachment and StorageFile.
        """
        file_data = SimpleUploadedFile("spec.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=True)

        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><img src="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}"></p>'},
            format='json'
        )

        att.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_04_to_07_removed_sole_attachment_is_orm_deleted_and_storage_marked_deleted(self):
        """
        Prove: When sole rich-text attachment reference is removed from entity,
        ProjectAttachment is deleted via ORM, StorageFile becomes DELETED, and DELETE StorageEvent is created.
        """
        file_data = SimpleUploadedFile("spec.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=True)
        att_id = att.id

        # 1. Initially reference the attachment
        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><img src="/api/v1/attachments/{att_id}/download/" data-attachment-id="{att_id}"></p>'},
            format='json'
        )

        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        # 2. Update description removing the image reference
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>No image here anymore.</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 4. ProjectAttachment is deleted via ORM
        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())

        # 5. StorageFile becomes DELETED
        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)

        # 6. DELETE StorageEvent created
        del_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertEqual(del_events.count(), 1)
        self.assertEqual(del_events.first().actor, self.user_a)

    def test_08_multi_field_union_attachment_in_second_field_not_deleted(self):
        """
        Prove: When an attachment is removed from description but still referenced in acceptance_criteria,
        it is NOT deleted.
        """
        file_data = SimpleUploadedFile("diagram.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=True)

        # Reference in both fields initially
        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {
                'description': f'<p>In desc: <img src="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}"></p>',
                'acceptance_criteria': f'<p>In AC: <img src="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}"></p>'
            },
            format='json'
        )

        # Remove from description only
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>Removed from desc</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Attachment must remain ACTIVE because acceptance_criteria still references it
        att.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_09_attachment_belonging_to_another_entity_not_deleted(self):
        """
        Prove: Deleting references on Story A never deletes attachments linked to Story B.
        """
        file_a = SimpleUploadedFile("a.png", self.valid_png, content_type="image/png")
        att_a = create_attachment(user=self.user_a, file_obj=file_a, story=self.story_a, organization=self.org_a, is_inline=True)

        story_other = ProjectStory.objects.create(project=self.project_a, title="Story Other")
        file_other = SimpleUploadedFile("other.png", self.valid_png, content_type="image/png")
        att_other = create_attachment(user=self.user_a, file_obj=file_other, story=story_other, organization=self.org_a, is_inline=True)

        # Remove reference on story_a
        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>Empty</p>'},
            format='json'
        )

        # att_a is deleted, but att_other is strictly untouched
        self.assertFalse(ProjectAttachment.objects.filter(id=att_a.id).exists())
        self.assertTrue(ProjectAttachment.objects.filter(id=att_other.id).exists())
        sf_other = StorageFile.objects.get(source_object_id=str(att_other.id))
        self.assertEqual(sf_other.status, 'ACTIVE')

    def test_10_cross_org_attachment_id_cannot_be_adopted_or_deleted(self):
        """
        Prove: Referencing an attachment belonging to another organization in rich-text is blocked.
        """
        file_b = SimpleUploadedFile("b.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        # User A tries to reference Org B's attachment in Story A
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        # Org B attachment must remain untouched
        att_b.refresh_from_db()
        self.assertEqual(att_b.company, self.org_b)

    def test_11_comment_attachment_not_touched_by_rich_text_reconciliation(self):
        """
        Prove: Story chat comment attachments are NEVER considered orphans by rich-text reconciliation.
        """
        comment = create_comment(user=self.user_a, comment_text="Discussion", story=self.story_a)
        file_comment = SimpleUploadedFile("chat.png", self.valid_png, content_type="image/png")
        att_comment = create_attachment(user=self.user_a, file_obj=file_comment, story=self.story_a, organization=self.org_a)
        att_comment.comment = comment
        att_comment.save(update_fields=['comment'])

        # Save story with empty description
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>No attachments here</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Comment attachment must remain intact
        att_comment.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att_comment.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att_comment.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_12_draft_attachment_linking_remains_valid(self):
        """
        Prove: When creating a story with draft_token and referencing that draft attachment in rich-text,
        the draft attachment is promoted to the story and retained as ACTIVE.
        """
        draft_token = str(uuid.uuid4())
        file_draft = SimpleUploadedFile("draft.png", self.valid_png, content_type="image/png")
        att_draft = create_attachment(user=self.user_a, file_obj=file_draft, draft_token=draft_token, organization=self.org_a, is_inline=True)
        self.assertTrue(att_draft.is_temporary)

        resp = self.client_a.post(
            '/api/v1/stories/',
            {
                'project': self.project_a.id,
                'title': 'New Story with Draft',
                'draft_token': draft_token,
                'description': f'<p><img src="/api/v1/attachments/{att_draft.id}/download/" data-attachment-id="{att_draft.id}"></p>',
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        att_draft.refresh_from_db()
        self.assertFalse(att_draft.is_temporary)
        self.assertEqual(att_draft.story_id, resp.data['id'])
        sf = StorageFile.objects.get(source_object_id=str(att_draft.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_13_malformed_unrelated_html_does_not_cause_mass_deletion(self):
        """
        Prove: Broken or malformed HTML safely retains valid data-attachment-id references without error.
        """
        file_data = SimpleUploadedFile("safe.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=True)

        malformed_html = f'<div><p>Broken markup <<<> <img src="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}"> <span>unclosed'
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': malformed_html},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Attachment is retained
        att.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())

    def test_14_rich_text_file_is_inline_false_remains_while_referenced(self):
        """
        Prove: A paperclip file attachment (is_inline=False) referenced in rich text remains ACTIVE.
        """
        file_data = SimpleUploadedFile("report.pdf", b"%PDF-1.4 test content", content_type="application/pdf")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=False)

        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><a href="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}">📎 report.pdf</a></p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_15_removing_rich_text_file_is_inline_false_deletes_attachment_and_marks_storage_deleted(self):
        """
        Prove: When an is_inline=False paperclip file is removed from all rich-text fields of the entity,
        it is deleted via ORM, StorageFile becomes DELETED, and DELETE StorageEvent is created.
        """
        file_data = SimpleUploadedFile("contract.pdf", b"%PDF-1.4 contract", content_type="application/pdf")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=False)
        att_id = att.id

        # 1. Initially reference the file in story description
        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': f'<p><a href="/api/v1/attachments/{att_id}/download/" data-attachment-id="{att_id}">📎 contract.pdf</a></p>'},
            format='json'
        )

        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        # 2. Update description removing the link
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>Contract removed.</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 3. ProjectAttachment is deleted via ORM
        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())

        # 4. StorageFile becomes DELETED
        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)

        # 5. DELETE StorageEvent created with actor
        del_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertEqual(del_events.count(), 1)
        self.assertEqual(del_events.first().actor, self.user_a)

    def test_16_comment_attachment_with_is_inline_false_remains_untouched(self):
        """
        Prove: Chat comment attachments with is_inline=False are never touched by rich-text reconciliation.
        """
        comment = create_comment(user=self.user_a, comment_text="Meeting file", story=self.story_a)
        file_comment = SimpleUploadedFile("chat_notes.pdf", b"%PDF-1.4 notes", content_type="application/pdf")
        att_comment = create_attachment(user=self.user_a, file_obj=file_comment, story=self.story_a, organization=self.org_a, is_inline=False)
        att_comment.comment = comment
        att_comment.save(update_fields=['comment'])

        # Save story with empty description
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>Clear description</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Comment attachment remains active and intact
        att_comment.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att_comment.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att_comment.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_17_file_referenced_in_second_story_rich_text_field_remains(self):
        """
        Prove: When an is_inline=False file is removed from description but still referenced
        in acceptance_criteria, it is NOT deleted.
        """
        file_data = SimpleUploadedFile("criteria.pdf", b"%PDF-1.4 criteria", content_type="application/pdf")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a, is_inline=False)

        # Reference in both fields initially
        self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {
                'description': f'<p><a href="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}">📎 criteria.pdf</a></p>',
                'acceptance_criteria': f'<p><a href="/api/v1/attachments/{att.id}/download/" data-attachment-id="{att.id}">📎 criteria.pdf</a></p>'
            },
            format='json'
        )

        # Remove from description only
        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {'description': '<p>Description text without file</p>'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Attachment must remain ACTIVE because acceptance_criteria still references it
        att.refresh_from_db()
        self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())
        sf = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(sf.status, 'ACTIVE')

    def test_18_uppercase_parameterized_and_mixed_case_base64_variants_rejected(self):
        """
        Prove: Robust base64 detection rejects:
        - Uppercase DATA:IMAGE/PNG;BASE64,...
        - Parameterized data:image/png;charset=utf-8;base64,...
        - Parameterized data:image/jpeg;name=x;base64,...
        - Mixed-case ;BaSe64,...
        """
        variants = [
            '<p><img src="DATA:IMAGE/PNG;BASE64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="></p>',
            '<p><img src="data:image/png;charset=utf-8;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="></p>',
            '<p><img src="data:image/jpeg;name=photo.jpg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP..."></p>',
            '<p><img src="data:image/webp;foo=bar;charset=utf-8;BaSe64,UklGRkAAAABXRUJQVlA4IDQAAADwAQCdASoBAAEAAQAcJaACdLoAAP7/2QAA"></p>',
        ]

        for content in variants:
            resp = self.client_a.patch(
                f'/api/v1/stories/{self.story_a.id}/',
                {'description': content},
                format='json'
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("Raw base64 images are not allowed", str(resp.data))

    def test_19_project_update_rollback_on_reconciliation_failure(self):
        """
        Prove: When updating a project with a rich-text cross-organization attachment reference,
        reconciliation validation fails (HTTP 400), and the description update is atomically rolled back.
        Foreign-organization attachment remains untouched.
        """
        project = Project.objects.create(
            name="Project Rollback Test",
            company=self.org_a,
            project_manager=self.user_a,
            description="<p>OLD PROJECT</p>"
        )
        file_b = SimpleUploadedFile("b_proj.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        resp = self.client_a.patch(
            f'/api/v1/projects/{project.id}/',
            {
                'description': f'<p>NEW PROJECT <img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>'
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        project.refresh_from_db()
        self.assertEqual(project.description, "<p>OLD PROJECT</p>")

        # Confirm foreign-org attachment remains untouched
        att_b.refresh_from_db()
        self.assertEqual(att_b.company, self.org_b)
        sf_b = StorageFile.objects.get(source_object_id=str(att_b.id))
        self.assertEqual(sf_b.status, 'ACTIVE')

    def test_20_epic_update_rollback_on_reconciliation_failure(self):
        """
        Prove: When updating an epic with a rich-text cross-organization attachment reference,
        reconciliation validation fails (HTTP 400), and the description update is atomically rolled back.
        Foreign-organization attachment remains untouched.
        """
        epic = ProjectEpic.objects.create(
            project=self.project_a,
            company=self.org_a,
            title="Epic Rollback Test",
            description="<p>OLD EPIC</p>",
            created_by=self.user_a
        )
        file_b = SimpleUploadedFile("b_epic.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        resp = self.client_a.patch(
            f'/api/v1/epics/{epic.id}/',
            {
                'description': f'<p>NEW EPIC <img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>'
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        epic.refresh_from_db()
        self.assertEqual(epic.description, "<p>OLD EPIC</p>")

        att_b.refresh_from_db()
        self.assertEqual(att_b.company, self.org_b)
        sf_b = StorageFile.objects.get(source_object_id=str(att_b.id))
        self.assertEqual(sf_b.status, 'ACTIVE')

    def test_21_story_update_full_rollback_on_reconciliation_failure(self):
        """
        Prove: When updating a story with an invalid cross-org attachment reference,
        all fields (description, acceptance_criteria) and related story members
        are rolled back to pre-request state upon HTTP 400 rejection.
        """
        self.story_a.description = "<p>OLD DESCRIPTION</p>"
        self.story_a.acceptance_criteria = "<p>OLD CRITERIA</p>"
        self.story_a.save(update_fields=['description', 'acceptance_criteria'])

        # Establish initial member state
        pm_a, _ = ProjectMember.objects.get_or_create(
            project=self.project_a, user=self.user_a,
            defaults={'project_role': 'Project Manager', 'is_active': True}
        )
        sm_a, _ = ProjectStoryMember.objects.get_or_create(
            story=self.story_a, member=pm_a,
            defaults={'assigned_by': self.user_a}
        )
        initial_member_count = self.story_a.story_members.count()
        self.assertEqual(initial_member_count, 1)

        file_b = SimpleUploadedFile("b_story.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        resp = self.client_a.patch(
            f'/api/v1/stories/{self.story_a.id}/',
            {
                'description': f'<p>NEW DESCRIPTION <img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>',
                'acceptance_criteria': '<p>NEW CRITERIA</p>',
                'members': []
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        self.story_a.refresh_from_db()
        self.assertEqual(self.story_a.description, "<p>OLD DESCRIPTION</p>")
        self.assertEqual(self.story_a.acceptance_criteria, "<p>OLD CRITERIA</p>")

        # StoryMember state remains rolled back / preserved
        self.assertEqual(self.story_a.story_members.count(), initial_member_count)
        self.assertTrue(self.story_a.story_members.filter(id=sm_a.id).exists())

        att_b.refresh_from_db()
        self.assertEqual(att_b.company, self.org_b)

    def test_22_task_update_rollback_on_reconciliation_failure(self):
        """
        Prove: When updating a task with a rich-text cross-organization attachment reference,
        reconciliation validation fails (HTTP 400), and the description update is atomically rolled back.
        Foreign-organization attachment remains untouched.
        """
        task = ProjectTask.objects.create(
            story=self.story_a,
            title="Task Update Rollback Test",
            description="<p>OLD TASK</p>",
            created_by=self.user_a,
            assigned_to=self.user_a
        )
        file_b = SimpleUploadedFile("b_task.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        resp = self.client_a.patch(
            f'/api/v1/project-tasks/{task.id}/',
            {
                'description': f'<p>NEW TASK <img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>'
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        task.refresh_from_db()
        self.assertEqual(task.description, "<p>OLD TASK</p>")

        att_b.refresh_from_db()
        self.assertEqual(att_b.company, self.org_b)
        sf_b = StorageFile.objects.get(source_object_id=str(att_b.id))
        self.assertEqual(sf_b.status, 'ACTIVE')

    def test_23_task_create_rollback_on_reconciliation_failure(self):
        """
        Prove: When creating a task with an invalid cross-organization rich-text reference,
        reconciliation validation fails (HTTP 400), and task creation is rolled back atomically.
        No partial or orphaned task remains in DB.
        """
        task_count_before = ProjectTask.objects.filter(story=self.story_a).count()
        file_b = SimpleUploadedFile("b_create.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        resp = self.client_a.post(
            '/api/v1/project-tasks/',
            {
                'story': self.story_a.id,
                'title': 'Task That Should Roll Back',
                'description': f'<p><img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}"></p>',
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        task_count_after = ProjectTask.objects.filter(story=self.story_a).count()
        self.assertEqual(task_count_after, task_count_before)
        self.assertFalse(ProjectTask.objects.filter(title='Task That Should Roll Back').exists())

    def test_24_draft_promotion_rollback_on_create_failure(self):
        """
        Prove: When task creation with a draft_token fails due to invalid cross-org attachment reference,
        the entire transaction rolls back:
        - The draft attachment remains temporary with original draft_token and task=None.
        - StorageFile remains ACTIVE and no DELETE event is emitted.
        """
        draft_token = str(uuid.uuid4())
        file_draft = SimpleUploadedFile("draft_rollback.png", self.valid_png, content_type="image/png")
        att_draft = create_attachment(
            user=self.user_a,
            file_obj=file_draft,
            draft_token=draft_token,
            organization=self.org_a,
            is_inline=True
        )
        self.assertTrue(att_draft.is_temporary)
        self.assertEqual(str(att_draft.draft_token), str(draft_token))
        self.assertIsNone(att_draft.task)

        file_b = SimpleUploadedFile("b_draft_fail.png", self.valid_png, content_type="image/png")
        att_b = create_attachment(user=self.user_b, file_obj=file_b, story=self.story_b, organization=self.org_b, is_inline=True)

        task_count_before = ProjectTask.objects.filter(story=self.story_a).count()

        resp = self.client_a.post(
            '/api/v1/project-tasks/',
            {
                'story': self.story_a.id,
                'title': 'Task With Draft Rollback',
                'draft_token': draft_token,
                'description': (
                    f'<p>'
                    f'<img src="/api/v1/attachments/{att_draft.id}/download/" data-attachment-id="{att_draft.id}">'
                    f'<img src="/api/v1/attachments/{att_b.id}/download/" data-attachment-id="{att_b.id}">'
                    f'</p>'
                ),
            },
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cross-organization", str(resp.data))

        # Task count unchanged and no task created
        task_count_after = ProjectTask.objects.filter(story=self.story_a).count()
        self.assertEqual(task_count_after, task_count_before)
        self.assertFalse(ProjectTask.objects.filter(title='Task With Draft Rollback').exists())

        # Draft attachment remains in its original temporary unpromoted state
        att_draft.refresh_from_db()
        self.assertTrue(att_draft.is_temporary)
        self.assertEqual(str(att_draft.draft_token), str(draft_token))
        self.assertIsNone(att_draft.task)

        # StorageFile remains ACTIVE and no DELETE event generated
        sf = StorageFile.objects.get(source_object_id=str(att_draft.id))
        self.assertEqual(sf.status, 'ACTIVE')
        delete_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertFalse(delete_events.exists())

    def test_25_positive_control_task_create_and_update_succeeds(self):
        """
        Prove (Positive Control): Normal task creation and update with valid rich-text
        persists successfully, proving the endpoints do not unconditionally reject.
        """
        resp_create = self.client_a.post(
            '/api/v1/project-tasks/',
            {
                'story': self.story_a.id,
                'title': 'Positive Control Task',
                'description': '<p>Valid initial description</p>',
            },
            format='json'
        )
        self.assertEqual(resp_create.status_code, status.HTTP_201_CREATED)
        task_id = resp_create.data['id']
        task_obj = ProjectTask.objects.get(id=task_id)
        self.assertEqual(task_obj.title, 'Positive Control Task')
        self.assertEqual(task_obj.description, '<p>Valid initial description</p>')

        # Update task
        resp_update = self.client_a.patch(
            f'/api/v1/project-tasks/{task_id}/',
            {
                'description': '<p>Updated positive control description</p>'
            },
            format='json'
        )
        self.assertEqual(resp_update.status_code, status.HTTP_200_OK)
        task_obj.refresh_from_db()
        self.assertEqual(task_obj.description, '<p>Updated positive control description</p>')

