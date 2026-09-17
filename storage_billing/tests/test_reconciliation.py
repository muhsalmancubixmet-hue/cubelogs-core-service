import os
import shutil
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Organization, OrgSettings, OrganizationModule
from projects.models import Project, ProjectAttachment
from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage
from storage_billing.services import StorageReconciliationService
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles


class StorageReconciliationTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_media = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.temp_media, ignore_errors=True)

    def setUp(self):
        super().setUp()
        sync_default_roles()

        # Org A
        self.settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)

        # Org B
        self.settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)

        self.role_pm = Role.objects.get(slug='project-manager', organization__isnull=True)

        self.user_a = Employee.objects.create_user(
            email="user_a@org-a.com",
            password="password123",
            organization=self.org_a,
            role=self.role_pm,
            is_active=True,
        )
        self.membership_a = OrganizationMembership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=self.role_pm,
            is_active_in_org=True,
        )

        self.user_b = Employee.objects.create_user(
            email="user_b@org-b.com",
            password="password123",
            organization=self.org_b,
            role=self.role_pm,
            is_active=True,
        )
        self.membership_b = OrganizationMembership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=self.role_pm,
            is_active_in_org=True,
        )

        self.project_a = Project.objects.create(name="Project A", company=self.org_a, project_manager=self.user_a)
        self.project_b = Project.objects.create(name="Project B", company=self.org_b, project_manager=self.user_b)

    def _create_attachment_file(self, filename="sample.pdf", content=b"1234567890"):
        return SimpleUploadedFile(filename, content, content_type="application/pdf")

    def test_01_existing_attachment_creates_storage_file(self):
        """1. existing attachment creates StorageFile."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Reconciliation Test Content"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test1.pdf", content),
                file_name="test1.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["created"], 1)

            storage_file = StorageFile.objects.filter(
                organization=self.org_a,
                source_module="projects",
                source_model="ProjectAttachment",
                source_object_id=str(att.id),
            ).first()
            self.assertIsNotNone(storage_file)
            self.assertEqual(storage_file.status, "ACTIVE")

    def test_02_creates_exactly_one_upload_event(self):
        """2. creates exactly one UPLOAD event."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Event Test"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test2.pdf", content),
                file_name="test2.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["created"], 1)

            storage_file = StorageFile.objects.get(
                organization=self.org_a,
                source_object_id=str(att.id),
            )
            events = StorageEvent.objects.filter(storage_file=storage_file)
            self.assertEqual(events.count(), 1)
            event = events.first()
            self.assertEqual(event.event_type, "UPLOAD")
            self.assertIsNone(event.actor)
            self.assertTrue(event.metadata.get("is_backfill"))
            self.assertEqual(event.metadata.get("reconciliation_source"), "ProjectAttachment")

    def test_03_storage_file_organization_equals_attachment_company(self):
        """3. StorageFile organization equals attachment.company."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Org Test"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test3.pdf", content),
                file_name="test3.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            sf = StorageFile.objects.get(source_object_id=str(att.id))
            self.assertEqual(sf.organization, self.org_a)
            self.assertEqual(sf.organization, att.company)

    def test_04_source_identity_format(self):
        """4. source identity is: projects / ProjectAttachment / str(id)."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test4.pdf", b"Identity Test"),
                file_name="test4.pdf",
                file_size=13,
                uploaded_by=self.user_a,
            )

            StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            sf = StorageFile.objects.get(organization=self.org_a, source_object_id=str(att.id))
            self.assertEqual(sf.source_module, "projects")
            self.assertEqual(sf.source_model, "ProjectAttachment")
            self.assertEqual(sf.source_object_id, str(att.id))

    def test_05_uploaded_at_preserves_project_attachment_created_at(self):
        """5. uploaded_at preserves ProjectAttachment.created_at."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            past_time = timezone.now() - timedelta(days=45)
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test5.pdf", b"Timestamp Test"),
                file_name="test5.pdf",
                file_size=14,
                uploaded_by=self.user_a,
            )
            # Simulate historical created_at
            ProjectAttachment.objects.filter(id=att.id).update(created_at=past_time)
            att.refresh_from_db()

            StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            sf = StorageFile.objects.get(source_object_id=str(att.id))
            self.assertEqual(sf.uploaded_at, att.created_at)

    def test_06_actual_physical_storage_size_is_used(self):
        """6. actual physical storage size is used."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            actual_bytes = b"0123456789ABCDEF"  # 16 bytes
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test6.pdf", actual_bytes),
                file_name="test6.pdf",
                file_size=999999,  # Deliberately bogus size in metadata
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["size_mismatch"], 1)

            sf = StorageFile.objects.get(source_object_id=str(att.id))
            self.assertEqual(sf.size_bytes, 16)  # Real physical size
            self.assertNotEqual(sf.size_bytes, 999999)

    def test_07_size_mismatch_does_not_modify_project_attachment_file_size(self):
        """7. size mismatch does not modify ProjectAttachment.file_size."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Mismatch Test"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test7.pdf", content),
                file_name="test7.pdf",
                file_size=500,  # Intentional difference
                uploaded_by=self.user_a,
            )

            StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            att.refresh_from_db()
            self.assertEqual(att.file_size, 500)  # Preserved unchanged

    def test_08_missing_physical_file_is_skipped(self):
        """8. missing physical file is skipped."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file="projects/2026/09/non_existent_file.pdf",
                file_name="non_existent_file.pdf",
                file_size=100,
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["scanned"], 1)
            self.assertEqual(res["missing_file"], 1)
            self.assertEqual(res["created"], 0)
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att.id)).exists())
            self.assertEqual(StorageEvent.objects.count(), 0)

    def test_09_missing_organization_is_skipped_safely(self):
        """9. missing organization is skipped/fails safely."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"No Org Test"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test9.pdf", content),
                file_name="test9.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )
            # Direct DB update simulates legacy corrupted row without company
            ProjectAttachment.objects.filter(id=att.id).update(company=None)

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["scanned"], 1)
            self.assertEqual(res["missing_company"], 1)
            self.assertEqual(res["created"], 0)
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att.id)).exists())

    def test_10_company_parent_mismatch_is_skipped(self):
        """10. company/parent mismatch is skipped."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Mismatch Tenant"
            # Project belongs to org_a, but attachment claims company=org_b
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_b,
                file=self._create_attachment_file("test10.pdf", content),
                file_name="test10.pdf",
                file_size=len(content),
                uploaded_by=self.user_b,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["scanned"], 1)
            self.assertEqual(res["tenant_mismatch"], 1)
            self.assertEqual(res["created"], 0)
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att.id)).exists())

    def test_11_second_reconciliation_is_idempotent(self):
        """11. second reconciliation is idempotent."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Idempotency Test"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test11.pdf", content),
                file_name="test11.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res1 = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res1["created"], 1)
            self.assertEqual(res1["already_tracked"], 0)

            res2 = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res2["created"], 0)
            self.assertEqual(res2["already_tracked"], 1)

            self.assertEqual(StorageFile.objects.filter(source_object_id=str(att.id)).count(), 1)

    def test_12_second_run_creates_no_duplicate_upload_event(self):
        """12. second run creates no duplicate UPLOAD event."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"No Duplicate Event"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test12.pdf", content),
                file_name="test12.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            StorageReconciliationService.reconcile_project_attachments(dry_run=False)

            sf = StorageFile.objects.get(source_object_id=str(att.id))
            self.assertEqual(StorageEvent.objects.filter(storage_file=sf).count(), 1)

    def test_13_dry_run_creates_zero_storage_file_records(self):
        """13. dry-run creates zero StorageFile records."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Dry Run Test"
            ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test13.pdf", content),
                file_name="test13.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=True)
            self.assertEqual(res["scanned"], 1)
            self.assertEqual(res["created"], 1)  # would create
            self.assertEqual(StorageFile.objects.count(), 0)

    def test_14_dry_run_creates_zero_storage_event_records(self):
        """14. dry-run creates zero StorageEvent records."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Dry Run Event Test"
            ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test14.pdf", content),
                file_name="test14.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=True)
            self.assertEqual(StorageEvent.objects.count(), 0)

    def test_15_one_bad_attachment_does_not_abort_valid_attachments(self):
        """15. one bad attachment does not abort valid attachments."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            # 1. Valid attachment
            att1 = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("valid1.pdf", b"Valid 1"),
                file_name="valid1.pdf",
                file_size=7,
                uploaded_by=self.user_a,
            )
            # 2. Missing physical file
            att2 = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file="projects/2026/09/ghost.pdf",
                file_name="ghost.pdf",
                file_size=10,
                uploaded_by=self.user_a,
            )
            # 3. Missing company (simulated via update)
            att3 = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("valid_no_comp.pdf", b"No Company"),
                file_name="valid_no_comp.pdf",
                file_size=10,
                uploaded_by=self.user_a,
            )
            ProjectAttachment.objects.filter(id=att3.id).update(company=None)

            # 4. Another valid attachment
            att4 = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("valid2.pdf", b"Valid 2"),
                file_name="valid2.pdf",
                file_size=7,
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["scanned"], 4)
            self.assertEqual(res["created"], 2)
            self.assertEqual(res["missing_file"], 1)
            self.assertEqual(res["missing_company"], 1)

            self.assertTrue(StorageFile.objects.filter(source_object_id=str(att1.id)).exists())
            self.assertTrue(StorageFile.objects.filter(source_object_id=str(att4.id)).exists())
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att2.id)).exists())
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att3.id)).exists())

    def test_16_expired_temporary_physical_attachment_reconciled_without_deleting(self):
        """16. expired temporary physical attachment can be reconciled without deleting it."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"Expired Draft Content"
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("draft.pdf", content),
                file_name="draft.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
                is_temporary=True,
                expires_at=timezone.now() - timedelta(days=2),  # Expired
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["created"], 1)

            # Check StorageFile created
            sf = StorageFile.objects.filter(source_object_id=str(att.id)).first()
            self.assertIsNotNone(sf)
            self.assertEqual(sf.size_bytes, len(content))

            # Confirm physical file still exists (not deleted or moved)
            att.refresh_from_db()
            self.assertTrue(att.file.storage.exists(att.file.name))
            self.assertTrue(ProjectAttachment.objects.filter(id=att.id).exists())

    def test_17_no_storage_daily_usage_is_created(self):
        """17. no StorageDailyUsage is created."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            content = b"No Daily Usage Content"
            ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("test17.pdf", content),
                file_name="test17.pdf",
                file_size=len(content),
                uploaded_by=self.user_a,
            )

            res = StorageReconciliationService.reconcile_project_attachments(dry_run=False)
            self.assertEqual(res["created"], 1)
            self.assertEqual(StorageDailyUsage.objects.count(), 0)

    def test_18_organization_filtered_reconciliation_cannot_touch_other_tenant(self):
        """18. organization-filtered reconciliation cannot touch another tenant's rows."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            att_a = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("org_a.pdf", b"Org A Content"),
                file_name="org_a.pdf",
                file_size=13,
                uploaded_by=self.user_a,
            )
            att_b = ProjectAttachment.objects.create(
                project=self.project_b,
                company=self.org_b,
                file=self._create_attachment_file("org_b.pdf", b"Org B Content"),
                file_name="org_b.pdf",
                file_size=13,
                uploaded_by=self.user_b,
            )

            # Reconcile ONLY org_a
            res = StorageReconciliationService.reconcile_project_attachments(
                organization=self.org_a,
                dry_run=False,
            )
            self.assertEqual(res["scanned"], 1)
            self.assertEqual(res["created"], 1)

            # Org A is reconciled
            self.assertTrue(StorageFile.objects.filter(organization=self.org_a, source_object_id=str(att_a.id)).exists())

            # Org B is untouched
            self.assertFalse(StorageFile.objects.filter(organization=self.org_b).exists())
            self.assertFalse(StorageFile.objects.filter(source_object_id=str(att_b.id)).exists())

    def test_19_management_command_dry_run_and_apply(self):
        """Management command defaults to dry-run and applies writes only with --apply."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            att = ProjectAttachment.objects.create(
                project=self.project_a,
                company=self.org_a,
                file=self._create_attachment_file("cmd_test.pdf", b"Command Content"),
                file_name="cmd_test.pdf",
                file_size=15,
                uploaded_by=self.user_a,
            )

            # 1. Default dry-run via command
            out = StringIO()
            call_command("reconcile_project_attachments", stdout=out)
            output = out.getvalue()
            self.assertIn("DRY RUN", output)
            self.assertEqual(StorageFile.objects.count(), 0)

            # 2. Apply via command
            out_apply = StringIO()
            call_command("reconcile_project_attachments", "--apply", stdout=out_apply)
            output_apply = out_apply.getvalue()
            self.assertIn("APPLY", output_apply)
            self.assertEqual(StorageFile.objects.filter(source_object_id=str(att.id)).count(), 1)
