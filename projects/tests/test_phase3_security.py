import io
import zipfile
import urllib.parse
from django.test import TestCase, override_settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings, OrganizationModule
from users.models import Employee, Role, PermissionFlag
from users.roles import sync_default_roles
from projects.models import Project, ProjectMember, ProjectStory, ProjectTask, ProjectEpic, ProjectSprint, ProjectAttachment
from projects.services.comments import create_attachment, create_comment
from projects.services.stories import create_story
from projects.services.tasks import create_task
from core.rich_text import sanitize_rich_text_html


class Phase3SecurityTestCase(TestCase):
    def setUp(self):
        sync_default_roles()
        settings1 = OrgSettings.objects.create()
        self.org1 = Organization.objects.create(name="Phase3 Org 1", subdomain="p3-1", settings=settings1)
        OrganizationModule.objects.create(organization=self.org1, module_id='project_management', enabled=True)

        settings2 = OrgSettings.objects.create()
        self.org2 = Organization.objects.create(name="Phase3 Org 2", subdomain="p3-2", settings=settings2)
        OrganizationModule.objects.create(organization=self.org2, module_id='project_management', enabled=True)

        # Create PM role for Org 1
        self.pm_role = Role.objects.get(slug='project-manager', organization__isnull=True)
        self.emp_role = Role.objects.get(slug='employee', organization__isnull=True)

        self.user_a_pm = Employee.objects.create_user(
            email="pm@p3org1.com", password="Password123!", organization=self.org1,
            role=self.pm_role, is_active=True
        )
        self.user_a_member = Employee.objects.create_user(
            email="member@p3org1.com", password="Password123!", organization=self.org1,
            role=self.emp_role, is_active=True
        )
        self.user_b_pm = Employee.objects.create_user(
            email="pm@p3org2.com", password="Password123!", organization=self.org2,
            role=self.pm_role, is_active=True
        )

        self.project_a = Project.objects.create(company=self.org1, name="Project A1", project_manager=self.user_a_pm)
        ProjectMember.objects.create(project=self.project_a, user=self.user_a_member, is_active=True)

        self.story_a = create_story(project=self.project_a, title="Story A", user=self.user_a_pm)
        self.task_a = create_task(story=self.story_a, title="Task A", user=self.user_a_pm)

        self.client_a_pm = APIClient()
        self.client_a_pm.force_authenticate(user=self.user_a_pm)

        self.client_a_member = APIClient()
        self.client_a_member.force_authenticate(user=self.user_a_member)

        self.client_b_pm = APIClient()
        self.client_b_pm.force_authenticate(user=self.user_b_pm)

        # Helpers for creating zip bytes (for docx/xlsx/zip testing)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('word/document.xml', '<w:document></w:document>')
        self.valid_zip_bytes = zip_buffer.getvalue()

    # ─── 1. RATE LIMITING TESTS ────────────────────────────────────────────────
    def test_burst_rate_limiting_enforced(self):
        from django.core.cache import cache
        cache.clear()
        from rest_framework.throttling import SimpleRateThrottle
        original_rate = SimpleRateThrottle.THROTTLE_RATES.get('burst')
        SimpleRateThrottle.THROTTLE_RATES['burst'] = '2/minute'
        try:
            # We make requests to comments creation which has throttle_scope = 'burst'
            client = APIClient()
            client.force_authenticate(user=self.user_a_member)

            payload = {"task": self.task_a.id, "comment": "Burst 1"}
            # 1st request -> OK
            response = client.post("/api/v1/comments/", payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # 2nd request -> OK
            response = client.post("/api/v1/comments/", payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            # 3rd request within same minute -> 429 Too Many Requests
            response = client.post("/api/v1/comments/", payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        finally:
            if original_rate:
                SimpleRateThrottle.THROTTLE_RATES['burst'] = original_rate
            else:
                SimpleRateThrottle.THROTTLE_RATES.pop('burst', None)

    # ─── 2. PRIVATE MEDIA serving TESTS ──────────────────────────────────────────
    def test_direct_media_attachments_serving_is_blocked(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a_pm)

        # Try to access a path under media/project_attachments/ directly
        response = client.get("/media/project_attachments/2026/08/file.png")
        # Direct URL blockers throw PermissionDenied which resolves to 403 Forbidden
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_secure_download_api_enforces_permissions(self):
        # 1. Create a secure private attachment
        file_data = SimpleUploadedFile("test.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type="image/png")
        attachment = create_attachment(user=self.user_a_pm, file_obj=file_data, project=self.project_a)

        # 2. Member of same project can download
        response = self.client_a_member.get(f"/api/v1/attachments/{attachment.id}/download/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Content disposition must be set to attachment
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

        # 3. Foreign company user gets 404/403
        response = self.client_b_pm.get(f"/api/v1/attachments/{attachment.id}/download/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 4. Unauthenticated user gets 401/403
        anon_client = APIClient()
        response = anon_client.get(f"/api/v1/attachments/{attachment.id}/download/")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    # ─── 3. FILE UPLOAD Pipeline Hardening TESTS ────────────────────────────────
    def test_upload_allowed_extensions_and_formats(self):
        # Test valid PNG signature
        png_file = SimpleUploadedFile("img.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type="image/png")
        att = create_attachment(user=self.user_a_pm, file_obj=png_file, project=self.project_a)
        self.assertEqual(att.file_name, "img.png")

        # Test valid PDF signature
        pdf_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4\n%...", content_type="application/pdf")
        att = create_attachment(user=self.user_a_pm, file_obj=pdf_file, project=self.project_a)
        self.assertEqual(att.file_name, "doc.pdf")

        # Test valid DOCX container structure
        docx_file = SimpleUploadedFile("doc.docx", self.valid_zip_bytes, content_type="application/vnd.openxmlformats")
        att = create_attachment(user=self.user_a_pm, file_obj=docx_file, project=self.project_a)
        self.assertEqual(att.file_name, "doc.docx")

    def test_upload_executables_and_svg_denied(self):
        # 1. Deny executable file extensions
        exe_file = SimpleUploadedFile("virus.exe", b"MZ...", content_type="application/x-msdownload")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user_a_pm, file_obj=exe_file, project=self.project_a)

        # 2. Deny SVG format extension/keywords
        svg_file = SimpleUploadedFile("vector.svg", b"<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>", content_type="image/svg+xml")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user_a_pm, file_obj=svg_file, project=self.project_a)

    def test_upload_double_extension_attacks_denied(self):
        # Spoofed double extension attempts
        spoofed_file = SimpleUploadedFile("img.png.exe", b"MZ...", content_type="image/png")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user_a_pm, file_obj=spoofed_file, project=self.project_a)

        spoofed_file2 = SimpleUploadedFile("img.js.png", b"console.log('bad')", content_type="image/png")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user_a_pm, file_obj=spoofed_file2, project=self.project_a)

    def test_upload_spoofed_content_types_denied(self):
        # HTML/JS inside a TXT document is ALLOWED to be uploaded per the safeguard, but served safely
        spoofed_txt = SimpleUploadedFile("text.txt", b"<html><script>alert(1)</script></html>", content_type="text/plain")
        att = create_attachment(user=self.user_a_pm, file_obj=spoofed_txt, project=self.project_a)
        self.assertEqual(att.file_name, "text.txt")
        
        # Verify that downloading it enforces text/plain and nosniff
        response = self.client_a_member.get(f"/api/v1/attachments/{att.id}/download/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

        # Spoofed DOCX (plain HTML instead of zip archive) -> MUST raise ValidationError
        spoofed_docx = SimpleUploadedFile("doc.docx", b"<html>not a zip container</html>", content_type="application/vnd.openxmlformats")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user_a_pm, file_obj=spoofed_docx, project=self.project_a)

    # ─── 4. XSS SANITIZATION REGRESSION TESTS ──────────────────────────────────
    def test_rich_text_xss_payloads_sanitized(self):
        unsafe_payloads = [
            ("<script>alert('XSS')</script><p>Safe content</p>", "<p>Safe content</p>"),
            ("<img src=\"#\" onerror=\"alert('XSS')\" />", "<img src=\"#\">"),
            ("<a href=\"javascript:alert('XSS')\">Click me</a>", '<a>Click me</a>'),
            ("<iframe src=\"https://hacker.com\"></iframe>", ""),
            ("<object data=\"flash.swf\"></object>", ""),
            ("<embed src=\"bad.swf\" />", "")
        ]

        for unsafe, expected in unsafe_payloads:
            cleaned = sanitize_rich_text_html(unsafe)
            self.assertEqual(cleaned.strip(), expected.strip())
