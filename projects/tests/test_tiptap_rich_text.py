from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from projects.models import Project, ProjectStory, ProjectTask, ProjectAttachment
from users.models import Employee
from core.models import Organization, OrgSettings
from core.rich_text import sanitize_rich_text_html, is_rich_text_empty
from projects.services.comments import create_attachment


class TiptapRichTextBackendTestCase(TestCase):
    def setUp(self):
        settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(
            name="Test Corp", subdomain="testcorp", settings=settings
        )
        self.user = Employee.objects.create_user(
            email="developer@testcorp.com",
            password="Password123!",
            first_name="Alex",
            last_name="Morgan",
            organization=self.org,
        )
        self.project = Project.objects.create(
            name="Alpha Project",
            company=self.org,
        )
        self.story = ProjectStory.objects.create(
            project=self.project,
            title="User Auth Story",
        )
        self.task = ProjectTask.objects.create(
            story=self.story,
            title="Implement Login",
        )

    def test_backend_sanitizer_removes_script_and_iframe(self):
        unsafe_html = "<h1>Welcome</h1><script>alert(1)</script><iframe src='http://evil.com'></iframe>"
        clean = sanitize_rich_text_html(unsafe_html)
        self.assertIn("<h1>Welcome</h1>", clean)
        self.assertNotIn("<script>", clean)
        self.assertNotIn("<iframe", clean)

    def test_backend_sanitizer_unescapes_legacy_escaped_html(self):
        escaped_html = "&lt;p&gt;&lt;strong&gt;Escaped&lt;/strong&gt;&lt;/p&gt;"
        clean = sanitize_rich_text_html(escaped_html)
        self.assertIn("<strong>Escaped</strong>", clean)
        self.assertNotIn("&lt;p&gt;", clean)

    def test_is_rich_text_empty_helper(self):
        self.assertTrue(is_rich_text_empty("<p></p>"))
        self.assertTrue(is_rich_text_empty("<p><br></p>"))
        self.assertFalse(is_rich_text_empty("<p>Valid Text</p>"))

    def test_attachment_single_target_enforcement(self):
        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        dummy_file = SimpleUploadedFile("test.png", valid_png, content_type="image/png")

        # Valid: one target
        att = create_attachment(user=self.user, file_obj=dummy_file, project=self.project)
        self.assertEqual(att.project, self.project)
        self.assertIsNone(att.story)

        # Invalid: two targets simultaneously
        dummy_file2 = SimpleUploadedFile("test2.png", valid_png, content_type="image/png")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user, file_obj=dummy_file2, project=self.project, story=self.story)

    def test_attachment_file_size_limit(self):
        # 11 MB — exceeds the 10 MB cap
        large_content = b"\x89PNG\r\n\x1a\n" + b"0" * (11 * 1024 * 1024)
        large_file = SimpleUploadedFile("large.png", large_content, content_type="image/png")
        with self.assertRaises(ValidationError):
            create_attachment(user=self.user, file_obj=large_file, project=self.project)
