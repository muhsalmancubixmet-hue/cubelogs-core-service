import uuid
from decimal import Decimal
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles
from projects.models import Project, ProjectStory, ProjectComment, ProjectAttachment, ProjectMember
from projects.services.comments import create_attachment, create_comment
from storage_billing.models import StorageFile, StorageEvent


class ChatUploadPhaseTestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        self.settings = OrgSettings.objects.create(is_project_enabled=True)
        self.org = Organization.objects.create(name="Test Org", subdomain="test-org", settings=self.settings)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)

        self.role_pm = Role.objects.get(slug='project-manager', organization__isnull=True)
        self.user = Employee.objects.create_user(
            email="pm@test.com",
            password="password123",
            organization=self.org,
            role=self.role_pm,
            is_active=True
        )
        self.membership = OrganizationMembership.objects.create(
            user=self.user,
            organization=self.org,
            role=self.role_pm,
            is_active_in_org=True
        )

        self.project = Project.objects.create(
            name="Test Project",
            key="TP",
            company=self.org,
            created_by=self.user
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            project_role="Project Manager",
            is_active=True
        )

        self.story = ProjectStory.objects.create(
            title="Test Story",
            project=self.project,
            created_by=self.user
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.valid_mp4_bytes = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 64
        self.valid_webm_bytes = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x84webm\x42\x87\x81\x02\x42\x85\x81\x02" + b"\x00" * 32

    def _create_sample_attachment(self, name="file.png", draft_token=None):
        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        f = SimpleUploadedFile(name, valid_png, content_type="image/png")
        return create_attachment(
            user=self.user,
            file_obj=f,
            draft_token=draft_token or str(uuid.uuid4()),
            organization=self.org
        )

    def test_1_max_10_attachments_allowed_service(self):
        att_ids = [self._create_sample_attachment(f"file_{i}.png").id for i in range(10)]
        comment = create_comment(
            user=self.user,
            story=self.story,
            comment_text="Here are 10 files",
            attachment_ids=att_ids,
        )
        self.assertEqual(comment.attachments.count(), 10)

    def test_2_max_10_attachments_rejected_service_11(self):
        att_ids = [self._create_sample_attachment(f"file_{i}.png").id for i in range(11)]
        with self.assertRaises(ValidationError) as ctx:
            create_comment(
                user=self.user,
                story=self.story,
                comment_text="Here are 11 files",
                attachment_ids=att_ids,
            )
        self.assertIn("A maximum of 10 attachments is allowed per message.", str(ctx.exception))

    def test_3_max_10_attachments_combined_draft_and_ids_rejected(self):
        token = str(uuid.uuid4())
        draft_att_ids = [self._create_sample_attachment(f"draft_{i}.png", draft_token=token).id for i in range(6)]
        other_att_ids = [self._create_sample_attachment(f"other_{i}.png").id for i in range(5)]

        with self.assertRaises(ValidationError) as ctx:
            create_comment(
                user=self.user,
                story=self.story,
                comment_text="Total 11",
                attachment_ids=other_att_ids,
                draft_token=token,
            )
        self.assertIn("A maximum of 10 attachments is allowed per message.", str(ctx.exception))

    def test_4_max_10_attachments_api_rejected_with_400(self):
        att_ids = [self._create_sample_attachment(f"file_{i}.png").id for i in range(11)]
        response = self.client.post(
            "/api/v1/comments/",
            {
                "story": self.story.id,
                "comment": "11 attachments via API",
                "attachment_ids": att_ids
            },
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        detail = str(response.data)
        self.assertIn("A maximum of 10 attachments is allowed per message.", detail)

    def test_5_valid_mp4_upload_service_and_storage_dual_write(self):
        f = SimpleUploadedFile("demo.mp4", self.valid_mp4_bytes, content_type="video/mp4")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            project=self.project,
            organization=self.org
        )
        self.assertIsNotNone(att.id)

        # Verify dual-write to storage_billing
        sf = StorageFile.objects.filter(source_object_id=str(att.id), source_model="ProjectAttachment").first()
        self.assertIsNotNone(sf)
        self.assertEqual(sf.status, 'ACTIVE')
        self.assertEqual(sf.size_bytes, len(self.valid_mp4_bytes))
        self.assertEqual(sf.content_type, "video/mp4")

        # Check event
        ev = StorageEvent.objects.filter(storage_file=sf, event_type="UPLOAD").first()
        self.assertIsNotNone(ev)

    def test_6_valid_webm_upload_service_and_storage_dual_write(self):
        f = SimpleUploadedFile("demo.webm", self.valid_webm_bytes, content_type="video/webm")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            project=self.project,
            organization=self.org
        )
        self.assertIsNotNone(att.id)

        sf = StorageFile.objects.filter(source_object_id=str(att.id), source_model="ProjectAttachment").first()
        self.assertIsNotNone(sf)
        self.assertEqual(sf.status, 'ACTIVE')
        self.assertEqual(sf.content_type, "video/webm")

    def test_7_fake_mp4_rejected(self):
        fake_bytes = b"not an mp4 file content at all"
        f = SimpleUploadedFile("fake.mp4", fake_bytes, content_type="video/mp4")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                project=self.project,
                organization=self.org
            )
        self.assertIn("Invalid MP4 video file signature.", str(ctx.exception))

    def test_8_fake_webm_rejected(self):
        fake_bytes = b"not a webm file content at all"
        f = SimpleUploadedFile("fake.webm", fake_bytes, content_type="video/webm")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                project=self.project,
                organization=self.org
            )
        self.assertIn("Invalid WebM video file signature.", str(ctx.exception))

    def test_9_oversized_video_rejected(self):
        # 10MB + 1 byte
        oversized = SimpleUploadedFile("big.mp4", self.valid_mp4_bytes, content_type="video/mp4")
        oversized.size = 10 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=oversized,
                project=self.project,
                organization=self.org
            )
        self.assertIn("File size exceeds maximum allowed limit of 10MB.", str(ctx.exception))

    def test_10_delete_video_attachment_marks_storage_deleted(self):
        f = SimpleUploadedFile("delete_demo.mp4", self.valid_mp4_bytes, content_type="video/mp4")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            project=self.project,
            organization=self.org
        )
        sf = StorageFile.objects.filter(source_object_id=str(att.id), source_model="ProjectAttachment").first()
        self.assertIsNotNone(sf)
        self.assertEqual(sf.status, 'ACTIVE')

        # Delete attachment
        att.delete()

        sf.refresh_from_db()
        self.assertEqual(sf.status, 'DELETED')
        ev = StorageEvent.objects.filter(storage_file=sf, event_type="DELETE").first()
        self.assertIsNotNone(ev)
