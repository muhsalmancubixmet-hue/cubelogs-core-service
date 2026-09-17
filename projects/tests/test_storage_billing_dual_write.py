import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles
from projects.models import (
    Project, ProjectEpic, ProjectStory, ProjectTask, ProjectComment, ProjectAttachment, ProjectMember
)
from projects.services.comments import create_attachment, create_comment
from storage_billing.models import StorageFile, StorageEvent
from subscribers.models import GlobalBillingSettings
from storage_billing.services import StorageService


class ProjectAttachmentDualWriteTestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        # Setup Organization A
        self.settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)

        # Setup Organization B
        self.settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)

        # Global billing settings: verify storage_billing_enabled=False
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_billing_enabled = False
        self.g_settings.save()

        # Roles
        self.role_pm = Role.objects.get(slug='project-manager', organization__isnull=True)

        # Users
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

        # Multi-tenant user: legacy pointer is org_a, but active membership in org_b
        self.multi_user = Employee.objects.create_user(
            email="multi@org-a.com",
            password="password123",
            organization=self.org_a,
            role=self.role_pm,
            is_active=True
        )
        self.multi_membership_a = OrganizationMembership.objects.create(
            user=self.multi_user,
            organization=self.org_a,
            role=self.role_pm,
            is_active_in_org=True
        )
        self.multi_membership_b = OrganizationMembership.objects.create(
            user=self.multi_user,
            organization=self.org_b,
            role=self.role_pm,
            is_active_in_org=True
        )

        # Projects and hierarchies
        self.project_a = Project.objects.create(name="Project A", company=self.org_a, project_manager=self.user_a)
        self.story_a = ProjectStory.objects.create(project=self.project_a, title="Story A")
        self.task_a = ProjectTask.objects.create(story=self.story_a, title="Task A")

        self.project_b = Project.objects.create(name="Project B", company=self.org_b, project_manager=self.user_b)
        self.story_b = ProjectStory.objects.create(project=self.project_b, title="Story B")
        self.task_b = ProjectTask.objects.create(story=self.story_b, title="Task B")

        # Clients
        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.user_a)

        self.client_b = APIClient()
        self.client_b.force_authenticate(user=self.user_b)

        self.valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"

    def test_direct_attachment_upload_creates_storage_file_and_event(self):
        """
        Prove: Direct upload creates exactly 1 ProjectAttachment, 1 StorageFile, and 1 UPLOAD event
        with correct source identity and organization attribution.
        """
        file_data = SimpleUploadedFile("spec.png", self.valid_png, content_type="image/png")
        url = '/api/v1/attachments/'
        response = self.client_a.post(url, {'file': file_data, 'project': self.project_a.id}, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProjectAttachment.objects.count(), 1)
        att = ProjectAttachment.objects.first()

        # Check ProjectAttachment
        self.assertEqual(att.company, self.org_a)
        self.assertEqual(att.uploaded_by, self.user_a)

        # Check StorageFile
        sf = StorageFile.objects.filter(source_object_id=str(att.id)).first()
        self.assertIsNotNone(sf)
        self.assertEqual(sf.source_module, 'projects')
        self.assertEqual(sf.source_model, 'ProjectAttachment')
        self.assertEqual(sf.organization, self.org_a)
        self.assertEqual(sf.size_bytes, att.file_size)
        self.assertEqual(sf.file_path, att.file.name)
        self.assertEqual(sf.status, 'ACTIVE')
        self.assertEqual(sf.storage_backend, 'private_filesystem')

        # Check StorageEvent
        events = StorageEvent.objects.filter(storage_file=sf)
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.event_type, 'UPLOAD')
        self.assertEqual(event.organization, self.org_a)
        self.assertEqual(event.size_bytes, att.file_size)
        self.assertEqual(event.actor, self.user_a)

    def test_upload_tracks_when_storage_billing_enabled_is_false(self):
        """
        Prove: Even when storage_billing_enabled=False, StorageFile and UPLOAD event are still recorded.
        """
        self.assertFalse(self.g_settings.storage_billing_enabled)
        file_data = SimpleUploadedFile("doc.png", self.valid_png, content_type="image/png")
        url = '/api/v1/attachments/'
        response = self.client_a.post(url, {'file': file_data, 'task': self.task_a.id}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        att = ProjectAttachment.objects.get(id=response.data['id'])
        self.assertTrue(StorageFile.objects.filter(source_object_id=str(att.id)).exists())

    def test_duplicate_storage_service_registration_is_idempotent(self):
        """
        Prove: Calling StorageService.record_file_upload multiple times returns the same StorageFile without duplicate events.
        """
        file_data = SimpleUploadedFile("idempotent.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)

        sf1 = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf1, event_type='UPLOAD').count(), 1)

        # Call record_file_upload again with same source
        sf2 = StorageService.record_file_upload(
            organization=self.org_a,
            original_filename="idempotent.png",
            size_bytes=att.file_size,
            file_path=att.file.name,
            source_object_id=str(att.id),
            source_module='projects',
            source_model='ProjectAttachment',
            uploaded_by=self.user_a
        )
        self.assertEqual(sf1.id, sf2.id)
        self.assertEqual(StorageFile.objects.filter(source_object_id=str(att.id)).count(), 1)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf1, event_type='UPLOAD').count(), 1)

    def test_failed_storage_registration_rolls_back_db_and_cleans_up_file(self):
        """
        Prove: If StorageService registration fails, the DB insert rolls back and the physical file is deleted.
        """
        file_data = SimpleUploadedFile("fail.png", self.valid_png, content_type="image/png")
        with patch.object(StorageService, 'record_file_upload', side_effect=RuntimeError("Storage service crashed")):
            with self.assertRaises(RuntimeError):
                create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)

        self.assertEqual(ProjectAttachment.objects.count(), 0)
        self.assertEqual(StorageFile.objects.count(), 0)

    def test_active_organization_authoritative_over_legacy_employee_org(self):
        """
        Prove: When a user belongs to multiple organizations, active_organization is authoritative.
        Legacy user.organization (Org A) is NOT used when active_organization is Org B.
        """
        client_multi = APIClient()
        client_multi.force_authenticate(user=self.multi_user)

        draft_token = str(uuid.uuid4())
        file_data = SimpleUploadedFile("multi_org.png", self.valid_png, content_type="image/png")

        # In request context, active_organization and active_membership point to Org B
        with patch('core.tenant.TenantContext.get_active_organization', return_value=self.org_b), \
             patch('core.tenant.TenantContext.get_active_membership', return_value=self.multi_membership_b):
            response = client_multi.post('/api/v1/attachments/', {'file': file_data, 'draft_token': draft_token}, format='multipart')

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            att = ProjectAttachment.objects.get(id=response.data['id'])
            self.assertEqual(att.company, self.org_b)

            sf = StorageFile.objects.get(source_object_id=str(att.id))
            self.assertEqual(sf.organization, self.org_b)
            self.assertNotEqual(sf.organization, self.org_a)

        # Also prove direct service create_attachment with explicit organization=self.org_b
        file_data2 = SimpleUploadedFile("multi_direct.png", self.valid_png, content_type="image/png")
        att2 = create_attachment(user=self.multi_user, file_obj=file_data2, project=self.project_b, organization=self.org_b)
        self.assertEqual(att2.company, self.org_b)
        sf2 = StorageFile.objects.get(source_object_id=str(att2.id))
        self.assertEqual(sf2.organization, self.org_b)
        self.assertNotEqual(sf2.organization, self.org_a)

    def test_cross_tenant_access_and_delete_denied(self):
        """
        Prove: An attachment created in Org A cannot be read or deleted by an actor in Org B.
        """
        file_data = SimpleUploadedFile("secret_a.png", self.valid_png, content_type="image/png")
        att_a = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)

        # User B attempts to DELETE Org A attachment
        resp_del = self.client_b.delete(f'/api/v1/attachments/{att_a.id}/')
        self.assertIn(resp_del.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

        att_a.refresh_from_db()
        sf_a = StorageFile.objects.get(source_object_id=str(att_a.id))
        self.assertEqual(sf_a.status, 'ACTIVE')

    def test_cross_tenant_draft_promotion_blocked_in_tasks(self):
        """
        Prove: A draft attachment created in Org A cannot be bound to a task in Org B,
        even if uploaded by the same multi-tenant user.
        Also verify same-org draft promotion still succeeds.
        """
        # 1. Multi-user creates draft in Org A
        draft_token = str(uuid.uuid4())
        file_data = SimpleUploadedFile("draft_a.png", self.valid_png, content_type="image/png")
        att_draft = create_attachment(user=self.multi_user, file_obj=file_data, draft_token=draft_token, organization=self.org_a)
        self.assertEqual(att_draft.company, self.org_a)
        self.assertTrue(att_draft.is_temporary)

        # 2. Org B actor creates task in Org B passing the Org A draft token
        resp_task = self.client_b.post('/api/v1/project-tasks/', {
            'title': 'Task in Org B',
            'story': self.story_b.id,
            'draft_token': draft_token,
        }, format='json')
        self.assertEqual(resp_task.status_code, status.HTTP_201_CREATED)

        # Verify draft was NOT promoted to task in Org B
        att_draft.refresh_from_db()
        self.assertIsNone(att_draft.task)
        self.assertEqual(att_draft.company, self.org_a)
        self.assertTrue(att_draft.is_temporary)

        # 3. Same-org promotion succeeds
        draft_token_b = str(uuid.uuid4())
        file_data_b = SimpleUploadedFile("draft_b.png", self.valid_png, content_type="image/png")
        att_draft_b = create_attachment(user=self.user_b, file_obj=file_data_b, draft_token=draft_token_b, organization=self.org_b)
        self.assertEqual(att_draft_b.company, self.org_b)

        resp_task_b = self.client_b.post('/api/v1/project-tasks/', {
            'title': 'Task 2 in Org B',
            'story': self.story_b.id,
            'draft_token': draft_token_b,
        }, format='json')
        self.assertEqual(resp_task_b.status_code, status.HTTP_201_CREATED)

        att_draft_b.refresh_from_db()
        self.assertIsNotNone(att_draft_b.task)
        self.assertEqual(att_draft_b.task.id, resp_task_b.data['id'])
        self.assertEqual(att_draft_b.company, self.org_b)
        self.assertFalse(att_draft_b.is_temporary)
        self.assertIsNone(att_draft_b.draft_token)

    def test_cross_tenant_draft_promotion_blocked_in_comments(self):
        """
        Prove: A draft attachment created in Org A cannot be bound to a comment in Org B,
        even if created by the same multi-tenant user.
        """
        draft_token = str(uuid.uuid4())
        file_data = SimpleUploadedFile("draft_comment_a.png", self.valid_png, content_type="image/png")
        att_draft = create_attachment(user=self.multi_user, file_obj=file_data, draft_token=draft_token, organization=self.org_a)

        # Attempt to link in comment on story_b (Org B)
        comment_b = create_comment(user=self.multi_user, comment_text="Cross tenant comment", story=self.story_b, draft_token=draft_token)

        att_draft.refresh_from_db()
        self.assertIsNone(att_draft.comment)
        self.assertEqual(att_draft.company, self.org_a)
        self.assertTrue(att_draft.is_temporary)

    def test_direct_api_delete_records_delete_event_with_actor(self):
        """
        Prove: Direct API delete marks StorageFile DELETED, records DELETE event, and attributes actor.
        """
        file_data = SimpleUploadedFile("to_delete.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)
        att_id = att.id
        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        resp = self.client_a.delete(f'/api/v1/attachments/{att_id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())

        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)
        self.assertEqual(sf.deleted_by, self.user_a)

        del_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertEqual(del_events.count(), 1)
        self.assertEqual(del_events.first().actor, self.user_a)

    def test_cascade_delete_via_comment_records_delete_event_without_actor(self):
        """
        Prove: Cascade delete (e.g. deleting ProjectComment) fires post_delete signal on attachments,
        marking StorageFile DELETED and recording exactly 1 DELETE event with actor=None.
        """
        comment = create_comment(user=self.user_a, comment_text="Test comment", story=self.story_a)
        file_data = SimpleUploadedFile("comment_file.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, story=self.story_a, organization=self.org_a)
        att.comment = comment
        att.save(update_fields=['comment'])

        sf = StorageFile.objects.get(source_object_id=str(att.id))
        self.assertEqual(sf.status, 'ACTIVE')

        # Cascade delete via comment.delete()
        comment.delete()

        self.assertFalse(ProjectAttachment.objects.filter(id=att.id).exists())
        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)
        self.assertIsNone(sf.deleted_by)

        del_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertEqual(del_events.count(), 1)
        self.assertIsNone(del_events.first().actor)

    def test_idempotent_delete_creates_no_second_delete_event(self):
        """
        Prove: Deleting an already DELETED StorageFile does not generate a second DELETE event.
        """
        file_data = SimpleUploadedFile("idempotent_del.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)
        sf = StorageFile.objects.get(source_object_id=str(att.id))

        StorageService.record_file_deletion(sf, deleted_by=self.user_a)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf, event_type='DELETE').count(), 1)

        # Second deletion call
        StorageService.record_file_deletion(sf, deleted_by=self.user_a)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf, event_type='DELETE').count(), 1)

    def test_deletion_of_attachment_without_storage_file_does_not_crash(self):
        """
        Prove: Deleting a historical attachment without a StorageFile succeeds smoothly without crashing.
        """
        file_data = SimpleUploadedFile("historical.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)
        att_id = att.id

        # Delete the StorageEvent and StorageFile to simulate a historical attachment from before Phase 1B
        StorageEvent.objects.filter(storage_file__source_object_id=str(att_id)).delete()
        StorageFile.objects.filter(source_object_id=str(att_id)).delete()

        resp = self.client_a.delete(f'/api/v1/attachments/{att_id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())

    def test_service_fail_closed_without_organization_or_target(self):
        """
        Prove: create_attachment called without explicit organization and without target entity
        fails closed with ValidationError. It must NOT fall back to user.organization (Org A),
        must NOT set ProjectAttachment.company = Org A, and must NOT create StorageFile for Org A.
        """
        from django.core.exceptions import ValidationError
        self.assertEqual(self.user_a.organization, self.org_a)
        file_data = SimpleUploadedFile("no_org.png", self.valid_png, content_type="image/png")
        draft_token = str(uuid.uuid4())

        with self.assertRaises(ValidationError) as ctx:
            create_attachment(user=self.user_a, file_obj=file_data, draft_token=draft_token)

        self.assertIn("Organization context is required", str(ctx.exception))
        self.assertFalse(ProjectAttachment.objects.filter(draft_token=draft_token).exists())
        self.assertFalse(StorageFile.objects.filter(organization=self.org_a, original_filename="no_org.png").exists())

    def test_filesystem_commit_safety_on_delete(self):
        """
        Prove: On committed delete, transaction.on_commit deletes the physical file,
        StorageFile becomes DELETED, and exactly one DELETE event is recorded.
        """
        file_data = SimpleUploadedFile("commit_test.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)
        att_id = att.id
        storage = att.file.storage
        file_name = att.file.name

        self.assertTrue(storage.exists(file_name))
        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        with self.captureOnCommitCallbacks(execute=True):
            att.delete()

        self.assertFalse(storage.exists(file_name))
        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())

        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf, event_type='DELETE').count(), 1)

    def test_filesystem_commit_safety_on_rollback(self):
        """
        Prove: On rolled-back transaction, physical file remains intact and is NOT deleted.
        Database row is preserved, StorageFile remains ACTIVE, and no committed DELETE event exists.
        """
        file_data = SimpleUploadedFile("rollback_test.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)
        storage = att.file.storage
        file_name = att.file.name
        att_id = att.id

        self.assertTrue(storage.exists(file_name))
        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        try:
            with transaction.atomic():
                att.delete()
                raise RuntimeError("Force rollback")
        except RuntimeError:
            pass

        # Because transaction rolled back, on_commit did not run, file must still exist
        self.assertTrue(storage.exists(file_name))
        self.assertTrue(ProjectAttachment.objects.filter(id=att_id).exists())

        sf.refresh_from_db()
        self.assertEqual(sf.status, 'ACTIVE')
        self.assertIsNone(sf.deleted_at)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf, event_type='DELETE').count(), 0)

    def test_expired_draft_cleanup_lifecycle_and_commit_safety(self):
        """
        Prove: cleanup_expired_project_drafts command deletes expired drafts through ORM,
        which triggers post_delete, marks StorageFile DELETED, records 1 DELETE event with actor=None,
        and safely deletes the physical file via on_commit.
        """
        draft_token = str(uuid.uuid4())
        file_data = SimpleUploadedFile("expired_draft.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, draft_token=draft_token, organization=self.org_a)
        att_id = att.id
        storage = att.file.storage
        file_name = att.file.name

        self.assertTrue(storage.exists(file_name))
        sf = StorageFile.objects.get(source_object_id=str(att_id))
        self.assertEqual(sf.status, 'ACTIVE')

        # Expire the attachment
        ProjectAttachment.objects.filter(id=att_id).update(expires_at=timezone.now() - timedelta(hours=2))

        from django.core.management import call_command
        with self.captureOnCommitCallbacks(execute=True):
            call_command('cleanup_expired_project_drafts')

        self.assertFalse(ProjectAttachment.objects.filter(id=att_id).exists())
        self.assertFalse(storage.exists(file_name))

        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        self.assertIsNotNone(sf.deleted_at)
        self.assertIsNone(sf.deleted_by)

        del_events = StorageEvent.objects.filter(storage_file=sf, event_type='DELETE')
        self.assertEqual(del_events.count(), 1)
        self.assertIsNone(del_events.first().actor)

    def test_bulk_queryset_delete_triggers_post_delete_signal(self):
        """
        Prove: QuerySet bulk deletion (ProjectAttachment.objects.filter(...).delete())
        fires the post_delete signal for each deleted instance, marking StorageFile DELETED,
        recording exactly one DELETE event with actor=None, and executing on_commit physical file cleanup.
        """
        file_data1 = SimpleUploadedFile("bulk1.png", self.valid_png, content_type="image/png")
        file_data2 = SimpleUploadedFile("bulk2.png", self.valid_png, content_type="image/png")

        att1 = create_attachment(user=self.user_a, file_obj=file_data1, project=self.project_a, organization=self.org_a)
        att2 = create_attachment(user=self.user_a, file_obj=file_data2, project=self.project_a, organization=self.org_a)

        id1, file_name1, storage1 = att1.id, att1.file.name, att1.file.storage
        id2, file_name2, storage2 = att2.id, att2.file.name, att2.file.storage

        self.assertTrue(storage1.exists(file_name1))
        self.assertTrue(storage2.exists(file_name2))

        with self.captureOnCommitCallbacks(execute=True):
            ProjectAttachment.objects.filter(id__in=[id1, id2]).delete()

        self.assertFalse(ProjectAttachment.objects.filter(id__in=[id1, id2]).exists())
        self.assertFalse(storage1.exists(file_name1))
        self.assertFalse(storage2.exists(file_name2))

        sf1 = StorageFile.objects.get(source_object_id=str(id1))
        sf2 = StorageFile.objects.get(source_object_id=str(id2))

        self.assertEqual(sf1.status, 'DELETED')
        self.assertEqual(sf2.status, 'DELETED')
        self.assertIsNone(sf1.deleted_by)
        self.assertIsNone(sf2.deleted_by)

        self.assertEqual(StorageEvent.objects.filter(storage_file=sf1, event_type='DELETE').count(), 1)
        self.assertEqual(StorageEvent.objects.filter(storage_file=sf2, event_type='DELETE').count(), 1)

    def test_put_and_patch_return_405(self):
        """
        Prove: PUT and PATCH on ProjectAttachment return 405 Method Not Allowed (immutable attachments).
        """
        file_data = SimpleUploadedFile("immutable.png", self.valid_png, content_type="image/png")
        att = create_attachment(user=self.user_a, file_obj=file_data, project=self.project_a, organization=self.org_a)

        resp_put = self.client_a.put(f'/api/v1/attachments/{att.id}/', {'file_name': 'hack.png'}, format='json')
        self.assertEqual(resp_put.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

        resp_patch = self.client_a.patch(f'/api/v1/attachments/{att.id}/', {'file_name': 'hack.png'}, format='json')
        self.assertEqual(resp_patch.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
