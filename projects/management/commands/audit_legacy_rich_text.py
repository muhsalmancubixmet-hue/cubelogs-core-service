from django.core.management.base import BaseCommand
from projects.models import Project, ProjectEpic, ProjectStory, ProjectTask, ProjectSprint, ProjectComment
from core.rich_text import sanitize_rich_text_html

class Command(BaseCommand):
    help = "Audits all database records containing legacy or raw HTML Rich Text content."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run audit without modifying anything and print report summary.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== CubeLogs Legacy Rich Text Audit ==="))

        fields_to_audit = [
            (Project, 'description', 'Project Description'),
            (ProjectEpic, 'description', 'Epic Description'),
            (ProjectStory, 'description', 'Story Description'),
            (ProjectStory, 'acceptance_criteria', 'Story Acceptance Criteria'),
            (ProjectTask, 'description', 'Task Description'),
            (ProjectSprint, 'goal', 'Sprint Goal'),
            (ProjectComment, 'comment', 'Comment Content'),
        ]

        total_inspected = 0
        escaped_html_count = 0
        blob_urls_count = 0
        records_to_normalize = 0

        for model_cls, field_name, label in fields_to_audit:
            qs = model_cls.objects.all()
            for obj in qs:
                total_inspected += 1
                val = getattr(obj, field_name) or ""
                if not val.strip():
                    continue

                is_escaped = '&lt;' in val or '&gt;' in val
                has_blob = 'blob:' in val

                if is_escaped:
                    escaped_html_count += 1
                if has_blob:
                    blob_urls_count += 1

                sanitized = sanitize_rich_text_html(val)
                if sanitized != val:
                    records_to_normalize += 1

        self.stdout.write(self.style.SUCCESS("\n=== Audit Results Summary ==="))
        self.stdout.write(f"Total Rich Text Records Inspected: {total_inspected}")
        self.stdout.write(f"Records with Escaped HTML (&lt;p&gt;): {escaped_html_count}")
        self.stdout.write(f"Records with Invalid Blob URLs: {blob_urls_count}")
        self.stdout.write(f"Records Pending Normalization: {records_to_normalize}")
