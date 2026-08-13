import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role
from projects.models import Project, ProjectAttachment
from projects.services.comments import create_attachment
from projects.services.projects import create_project


class TempAttachmentsTestCase(TestCase):
    def setUp(self):
        settings_a = OrgSettings.objects.create()
        self.org1 = Organization.objects.create(name="Company Alpha", subdomain="alpha", settings=settings_a)
        OrganizationModule.objects.create(organization=self.org1, module_id='project_management', enabled=True)

        settings_b = OrgSettings.objects.create()
        self.org2 = Organization.objects.create(name="Company Beta", subdomain="beta", settings=settings_b)
        OrganizationModule.objects.create(organization=self.org2, module_id='project_management', enabled=True)

        self.role_pm = Role.objects.create(name="Project Manager", slug="project-manager")

        self.emp1 = Employee.objects.create_user(email="user1@alpha.com", password="password123", organization=self.org1, role=self.role_pm, is_active=True)
        self.emp2 = Employee.objects.create_user(email="user2@alpha.com", password="password123", organization=self.org1, role=self.role_pm, is_active=True)
        self.emp_beta = Employee.objects.create_user(email="user@beta.com", password="password123", organization=self.org2, role=self.role_pm, is_active=True)

        self.draft_token = str(uuid.uuid4())
        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        self.test_image = SimpleUploadedFile("screenshot.png", valid_png, content_type="image/png")

    def test_1_temp_image_upload_succeeds_without_project_id(self):
        att = create_attachment(
            user=self.emp1,
            file_obj=self.test_image,
            draft_token=self.draft_token,
            is_inline=True
        )
        self.assertIsNotNone(att.id)
        self.assertEqual(str(att.draft_token), self.draft_token)
        self.assertTrue(att.is_temporary)
        self.assertIsNone(att.project)

    def test_2_temp_upload_requires_authenticated_user(self):
        with self.assertRaises(Exception):
            create_attachment(
                user=None,
                file_obj=self.test_image,
                draft_token=self.draft_token
            )

    def test_3_temp_upload_stores_organization_and_uploader(self):
        att = create_attachment(
            user=self.emp1,
            file_obj=self.test_image,
            draft_token=self.draft_token
        )
        self.assertEqual(att.uploaded_by, self.emp1)
        self.assertEqual(att.company, self.org1)

    def test_4_invalid_draft_token_rejected(self):
        with self.assertRaises(Exception):
            create_attachment(
                user=self.emp1,
                file_obj=self.test_image,
                draft_token=None,
                project=None
            )

    def test_4b_non_uuid_draft_token_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            create_attachment(
                user=self.emp1,
                file_obj=self.test_image,
                draft_token="draft-r0nykjsqao"
            )

    def test_5_cross_user_token_rejected_in_promotion(self):
        att = create_attachment(
            user=self.emp1,
            file_obj=self.test_image,
            draft_token=self.draft_token
        )
        p = create_project(
            company=self.org1,
            name="Emp2 Project",
            project_manager=self.emp2,
            user=self.emp2,
            draft_token=self.draft_token
        )
        att.refresh_from_db()
        self.assertIsNone(att.project)
        self.assertTrue(att.is_temporary)

    def test_6_cross_company_promotion_rejected(self):
        att = create_attachment(
            user=self.emp1,
            file_obj=self.test_image,
            draft_token=self.draft_token
        )
        p = create_project(
            company=self.org2,
            name="Beta Project",
            project_manager=self.emp_beta,
            user=self.emp_beta,
            draft_token=self.draft_token
        )
        att.refresh_from_db()
        self.assertIsNone(att.project)
        self.assertTrue(att.is_temporary)

    def test_7_project_create_promotes_matching_draft_attachments(self):
        att = create_attachment(
            user=self.emp1,
            file_obj=self.test_image,
            draft_token=self.draft_token
        )
        p = create_project(
            company=self.org1,
            name="Promoted Project",
            project_manager=self.emp1,
            user=self.emp1,
            draft_token=self.draft_token
        )
        att.refresh_from_db()
        self.assertEqual(att.project, p)
        self.assertIsNone(att.draft_token)
        self.assertFalse(att.is_temporary)

    def test_8_project_create_does_not_promote_other_users_drafts(self):
        att1 = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)
        token2 = str(uuid.uuid4())
        att2 = create_attachment(user=self.emp2, file_obj=self.test_image, draft_token=token2)

        p = create_project(
            company=self.org1,
            name="User1 Project",
            project_manager=self.emp1,
            user=self.emp1,
            draft_token=self.draft_token
        )
        att1.refresh_from_db()
        att2.refresh_from_db()

        self.assertEqual(att1.project, p)
        self.assertIsNone(att2.project)

    def test_9_failed_project_validation_preserves_drafts(self):
        att = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)
        try:
            create_project(
                company=self.org1,
                name="Valid Name",
                project_manager=self.emp_beta,  # Cross-company PM fails validation
                user=self.emp1,
                draft_token=self.draft_token
            )
        except Exception:
            pass

        att.refresh_from_db()
        self.assertIsNone(att.project)
        self.assertTrue(att.is_temporary)
        self.assertEqual(str(att.draft_token), self.draft_token)

    def test_10_successful_promotion_clears_temporary_state(self):
        att = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)
        p = create_project(company=self.org1, name="Clean Project", project_manager=self.emp1, user=self.emp1, draft_token=self.draft_token)
        att.refresh_from_db()
        self.assertEqual(att.project, p)
        self.assertFalse(att.is_temporary)
        self.assertIsNone(att.expires_at)

    def test_11_duplicate_submit_does_not_duplicate_attachments(self):
        att = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)
        p1 = create_project(company=self.org1, name="P1", project_manager=self.emp1, user=self.emp1, draft_token=self.draft_token)
        att.refresh_from_db()

        p2 = create_project(company=self.org1, name="P2", project_manager=self.emp1, user=self.emp1, draft_token=self.draft_token)
        self.assertEqual(p1.attachments.count(), 1)
        self.assertEqual(p2.attachments.count(), 0)

    def test_12_expired_draft_cleanup_works(self):
        att = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)
        att.expires_at = timezone.now() - timedelta(hours=2)
        att.save()

        from django.core.management import call_command
        call_command('cleanup_expired_project_drafts')

        self.assertFalse(ProjectAttachment.objects.filter(id=att.id).exists())

    def test_13_permanent_upload_target_validation_still_works(self):
        p = create_project(company=self.org1, name="Permanent P", project_manager=self.emp1, user=self.emp1)
        att = create_attachment(user=self.emp1, file_obj=self.test_image, project=p)
        self.assertEqual(att.project, p)
        self.assertFalse(att.is_temporary)

    def test_14_10mb_limit_works(self):
        big_file = SimpleUploadedFile("big.png", b"\x89PNG\r\n\x1a\n" + b"x" * (11 * 1024 * 1024), content_type="image/png")
        with self.assertRaises(Exception):
            create_attachment(user=self.emp1, file_obj=big_file, draft_token=self.draft_token)

    def test_15_mime_validation_works(self):
        bad_file = SimpleUploadedFile("evil.exe", b"binary", content_type="application/octet-stream")
        with self.assertRaises(Exception):
            create_attachment(user=self.emp1, file_obj=bad_file, draft_token=self.draft_token)

    def test_16_attachment_only_comment_allowed_and_empty_message_rejected(self):
        from projects.models import ProjectStory
        from projects.services.comments import create_comment
        from django.core.exceptions import ValidationError

        story = ProjectStory.objects.create(
            project=create_project(company=self.org1, name="Story P", project_manager=self.emp1, user=self.emp1),
            title="Test Story",
            created_by=self.emp1
        )
        att = create_attachment(user=self.emp1, file_obj=self.test_image, draft_token=self.draft_token)

        # 1. Blank comment with valid draft attachment -> Allowed
        comment_obj = create_comment(
            user=self.emp1,
            comment_text="",
            story=story,
            draft_token=self.draft_token
        )
        self.assertIsNotNone(comment_obj.id)
        att.refresh_from_db()
        self.assertEqual(att.comment, comment_obj)

        # 2. Blank comment with NO attachment -> Rejected
        with self.assertRaises(ValidationError):
            create_comment(
                user=self.emp1,
                comment_text="",
                story=story
            )
