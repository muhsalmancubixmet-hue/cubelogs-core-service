import io
import zipfile
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles
from projects.models import Project, ProjectStory, ProjectMember
from projects.services.comments import create_attachment
from storage_billing.models import StorageFile, StorageEvent


def make_zip(file_dict):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in file_dict.items():
            zf.writestr(arcname, data)
    buf.seek(0)
    return buf.getvalue()


class ZipMemberSecurityTestCase(TestCase):
    def setUp(self):
        sync_default_roles()
        self.settings = OrgSettings.objects.create(is_project_enabled=True)
        self.org = Organization.objects.create(name="Security Org", subdomain="sec-org", settings=self.settings)
        OrganizationModule.objects.create(organization=self.org, module_id='project_management', enabled=True)

        self.role_pm = Role.objects.get(slug='project-manager', organization__isnull=True)
        self.user = Employee.objects.create_user(
            email="secpm@test.com",
            password="password123",
            organization=self.org,
            role=self.role_pm,
            is_active=True
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.org,
            role=self.role_pm,
            is_active_in_org=True
        )

        self.project = Project.objects.create(
            name="Sec Project",
            key="SP",
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
            title="Sec Story",
            project=self.project,
            created_by=self.user
        )

    def test_1_valid_zip_accepted(self):
        zip_bytes = make_zip({"readme.txt": b"Hello world"})
        f = SimpleUploadedFile("archive.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertIsNotNone(att.id)
        self.assertEqual(att.file_name, "archive.zip")

    def test_2_nested_valid_zip_accepted(self):
        zip_bytes = make_zip({
            "ProjectDocs/design/logo.png": b"\x89PNG\r\n\x1a\nfake-png-data",
            "ProjectDocs/specs/api.pdf": b"%PDF-fake-pdf-data",
        })
        f = SimpleUploadedFile("ProjectDocs.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertIsNotNone(att.id)
        self.assertEqual(att.file_name, "ProjectDocs.zip")

    def test_3_zip_containing_malware_exe_rejected(self):
        zip_bytes = make_zip({"payload/malware.exe": b"MZfakebinary"})
        f = SimpleUploadedFile("folder.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("prohibited file type", str(ctx.exception).lower())

    def test_4_zip_containing_test_PY_rejected_case_insensitively(self):
        zip_bytes = make_zip({"scripts/test.PY": b"print('hacked')"})
        f = SimpleUploadedFile("scripts.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("prohibited file type", str(ctx.exception).lower())

    def test_5_zip_containing_svg_rejected(self):
        zip_bytes = make_zip({"images/icon.svg": b"<svg></svg>"})
        f = SimpleUploadedFile("icons.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("prohibited file type", str(ctx.exception).lower())

    def test_6_zip_member_dot_dot_slash_evil_rejected(self):
        zip_bytes = make_zip({"../evil.txt": b"escape"})
        f = SimpleUploadedFile("traversal.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("unsafe path traversal", str(ctx.exception).lower())

    def test_7_zip_member_dot_dot_backslash_evil_rejected(self):
        zip_bytes = make_zip({"..\\evil.txt": b"escape_win"})
        f = SimpleUploadedFile("win_traversal.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("unsafe path traversal", str(ctx.exception).lower())

    def test_8_absolute_path_member_rejected(self):
        zip_bytes = make_zip({"/etc/shadow": b"root:x"})
        f = SimpleUploadedFile("abs.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("unsafe path traversal", str(ctx.exception).lower())

    def test_9_corrupt_zip_rejected(self):
        corrupt_bytes = b"PK\x03\x04corrupted-archive-garbage-bytes"
        f = SimpleUploadedFile("corrupted.zip", corrupt_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertTrue(
            "corrupted" in str(ctx.exception).lower() or
            "invalid" in str(ctx.exception).lower()
        )

    def test_10_archive_abuse_guard_rejects_unreasonable_archive(self):
        # Create a mock zip with 1001 members (exceeding MAX_ZIP_MEMBERS=1000)
        many_files = {f"file_{i}.txt": b"x" for i in range(1005)}
        zip_bytes = make_zip(many_files)
        f = SimpleUploadedFile("bomb.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("too many files", str(ctx.exception).lower())

    def test_11_normal_existing_zip_upload_still_works(self):
        zip_bytes = make_zip({"report.csv": b"id,val\n1,100"})
        f = SimpleUploadedFile("financials.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertEqual(att.file_name, "financials.zip")
        self.assertGreater(att.file_size, 0)

    def test_12_accepted_zip_still_creates_storage_file_and_upload_event(self):
        zip_bytes = make_zip({"data.txt": b"1234567890"})
        f = SimpleUploadedFile("project_archive.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )

        storage_file = StorageFile.objects.filter(
            source_object_id=str(att.id),
            source_model="ProjectAttachment"
        ).first()
        self.assertIsNotNone(storage_file)
        self.assertEqual(storage_file.size_bytes, att.file_size)
        self.assertEqual(storage_file.status, 'ACTIVE')

        upload_event = StorageEvent.objects.filter(
            storage_file=storage_file,
            event_type="UPLOAD"
        ).first()
        self.assertIsNotNone(upload_event)
        self.assertEqual(upload_event.size_bytes, att.file_size)

    def test_13_zip_containing_ps1_rejected(self):
        zip_bytes = make_zip({"scripts/deploy.ps1": b"Write-Host 'deploy'"})
        f = SimpleUploadedFile("deploy.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_14_zip_containing_vbs_rejected(self):
        zip_bytes = make_zip({"macro.vbs": b"MsgBox 'hello'"})
        f = SimpleUploadedFile("macro.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_15_zip_containing_msi_rejected(self):
        zip_bytes = make_zip({"installers/setup.msi": b"msi-installer-bytes"})
        f = SimpleUploadedFile("setup.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_16_zip_containing_jar_rejected(self):
        zip_bytes = make_zip({"app.jar": b"jar-archive-bytes"})
        f = SimpleUploadedFile("app.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_17_zip_containing_uppercase_unsupported_ext_rejected(self):
        zip_bytes = make_zip({"tools/plugin.DLL": b"binary-data"})
        f = SimpleUploadedFile("tools.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_18_zip_containing_valid_inner_mp4_accepted(self):
        zip_bytes = make_zip({"media/demo.mp4": b"\x00\x00\x00\x20ftypisomdemo"})
        f = SimpleUploadedFile("bundle.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertEqual(att.file_name, "bundle.zip")
        self.assertIsNotNone(att.id)

    def test_19_zip_containing_valid_inner_pdf_accepted(self):
        zip_bytes = make_zip({"docs/guide.pdf": b"%PDF-1.4 guide content"})
        f = SimpleUploadedFile("docs.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertEqual(att.file_name, "docs.zip")
        self.assertIsNotNone(att.id)

    def test_20_normal_direct_upload_policy_unchanged(self):
        # Direct txt upload accepted
        txt_f = SimpleUploadedFile("valid.txt", b"plain text", content_type="text/plain")
        att = create_attachment(
            user=self.user,
            file_obj=txt_f,
            story=self.story,
            organization=self.org
        )
        self.assertEqual(att.file_name, "valid.txt")

        # Direct ps1 upload rejected
        ps1_f = SimpleUploadedFile("script.ps1", b"Write-Host 1", content_type="text/plain")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=ps1_f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_21_zip_containing_inner_zip_rejected(self):
        nested_bytes = make_zip({"data.txt": b"inner content"})
        zip_bytes = make_zip({
            "image.png": b"\x89PNG\r\n\x1a\nfake",
            "inner.zip": nested_bytes,
        })
        f = SimpleUploadedFile("outer.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("nested zip files are not allowed", str(ctx.exception).lower())

    def test_22_zip_containing_uppercase_inner_zip_rejected(self):
        zip_bytes = make_zip({
            "subfolder/ARCHIVE.ZIP": b"PK\x03\x04fake",
        })
        f = SimpleUploadedFile("outer.zip", zip_bytes, content_type="application/zip")
        with self.assertRaises(ValidationError) as ctx:
            create_attachment(
                user=self.user,
                file_obj=f,
                story=self.story,
                organization=self.org
            )
        self.assertIn("nested zip files are not allowed", str(ctx.exception).lower())

    def test_23_standalone_direct_zip_remains_accepted(self):
        zip_bytes = make_zip({"notes.txt": b"safe notes"})
        f = SimpleUploadedFile("standalone_project.zip", zip_bytes, content_type="application/zip")
        att = create_attachment(
            user=self.user,
            file_obj=f,
            story=self.story,
            organization=self.org
        )
        self.assertEqual(att.file_name, "standalone_project.zip")
        self.assertIsNotNone(att.id)
