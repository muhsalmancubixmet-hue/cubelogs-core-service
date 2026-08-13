from django.test import TestCase
from django.core.management import call_command
from core.models import Organization
from users.models import Employee
from projects.models import Project, ProjectStory, ProjectTask, ProjectSprint
from projects.rich_text_utils import normalize_to_canonical_html

class RichTextNormalizationTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", subdomain="test-org")
        self.user = Employee.objects.create_user(
            email="test@example.com",
            password="password123",
            first_name="Test",
            last_name="User",
            organization=self.org
        )

        # Create test records with legacy / mixed / escaped content
        self.project = Project.objects.create(
            company=self.org,
            name="Legacy Project",
            key="LEG",
            project_manager=self.user,
            description="&lt;p&gt;&lt;strong&gt;Escaped HTML Description&lt;/strong&gt;&lt;/p&gt;"
        )

        self.story = ProjectStory.objects.create(
            project=self.project,
            created_by=self.user,
            title="Legacy Story",
            description="- [ ] Unchecked Task\n- [x] Checked Task\n> **bold quote**<span style=\"color:#ef4444\">red text</span>",
            acceptance_criteria="# Criteria 1\n- Rule A\n- Rule B"
        )

        self.task = ProjectTask.objects.create(
            story=self.story,
            title="Legacy Task",
            description="```python\ndef foo():\n    return 'bar'\n```\nPlain text details"
        )

        self.sprint = ProjectSprint.objects.create(
            project=self.project,
            name="Sprint 1",
            goal='"<p>Double Encoded Goal</p>"'
        )

    def test_normalization_utils(self):
        # 1. Escaped HTML
        raw1 = "&lt;p&gt;&lt;strong&gt;Hello&lt;/strong&gt;&lt;/p&gt;"
        norm1 = normalize_to_canonical_html(raw1)
        self.assertEqual(norm1, "<p><strong>Hello</strong></p>")

        # 2. Mixed Markdown + HTML
        raw2 = "- [ ] Task\n> **bold**<span style=\"color:#ef4444\">red</span>"
        norm2 = normalize_to_canonical_html(raw2)
        self.assertIn('<ul class="task-list"', norm2)
        self.assertIn('data-checked="false"', norm2)
        self.assertIn('<strong>bold</strong>', norm2)
        self.assertIn('<span style="color: #ef4444">red</span>', norm2)

        # 3. Idempotency Check
        self.assertEqual(norm2, normalize_to_canonical_html(norm2))

    def test_management_command_dry_run_and_confirm(self):
        # Dry run check
        call_command('normalize_project_rich_text', '--dry-run')
        self.project.refresh_from_db()
        self.assertIn('&lt;p&gt;', self.project.description) # unchanged in dry run

        # Confirm run
        call_command('normalize_project_rich_text', '--confirm')

        self.project.refresh_from_db()
        self.story.refresh_from_db()
        self.task.refresh_from_db()
        self.sprint.refresh_from_db()

        # Verify normalized HTML in DB
        self.assertEqual(self.project.description, "<p><strong>Escaped HTML Description</strong></p>")
        self.assertIn('<ul class="task-list"', self.story.description)
        self.assertIn('data-checked="true"', self.story.description)
        self.assertIn('<h1>Criteria 1</h1>', self.story.acceptance_criteria)
        self.assertIn('<pre><code>', self.task.description)
        self.assertEqual(self.sprint.goal, "<p>Double Encoded Goal</p>")

        # Second confirm run (idempotency check)
        call_command('normalize_project_rich_text', '--confirm')
        self.project.refresh_from_db()
        self.assertEqual(self.project.description, "<p><strong>Escaped HTML Description</strong></p>")

    def test_unsafe_script_removal(self):
        unsafe = "<p>Safe</p><script>alert('xss')</script><a href=\"javascript:alert(1)\">Click</a>"
        norm = normalize_to_canonical_html(unsafe)
        self.assertNotIn("<script>", norm)
        self.assertNotIn("alert", norm)
        self.assertNotIn("javascript:", norm)
        self.assertIn("<p>Safe</p>", norm)
