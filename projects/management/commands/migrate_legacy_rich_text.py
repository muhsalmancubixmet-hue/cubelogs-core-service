from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import Project, ProjectEpic, ProjectStory, ProjectTask, ProjectSprint, ProjectComment
from core.rich_text import sanitize_rich_text_html

class Command(BaseCommand):
    help = "Migrates and normalizes legacy rich text content into canonical Tiptap HTML."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without modifying the database.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Apply normalization within an atomic database transaction.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        confirm = options['confirm']

        if not dry_run and not confirm:
            self.stdout.write(self.style.WARNING("Specify --dry-run to preview or --confirm to apply changes."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("=== Legacy Rich Text Migration ==="))
        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY-RUN mode."))

        fields = [
            (Project, 'description'),
            (ProjectEpic, 'description'),
            (ProjectStory, 'description'),
            (ProjectStory, 'acceptance_criteria'),
            (ProjectTask, 'description'),
            (ProjectSprint, 'goal'),
            (ProjectComment, 'comment'),
        ]

        total = 0
        changed = 0

        def process():
            nonlocal total, changed
            for model_cls, f_name in fields:
                for obj in model_cls.objects.all():
                    total += 1
                    raw = getattr(obj, f_name) or ""
                    if not raw.strip():
                        continue
                    clean = sanitize_rich_text_html(raw)
                    if clean != raw:
                        changed += 1
                        if confirm and not dry_run:
                            setattr(obj, f_name, clean)
                            obj.save(update_fields=[f_name])

        if confirm and not dry_run:
            with transaction.atomic():
                process()
        else:
            process()

        self.stdout.write(self.style.SUCCESS(f"\nInspected {total} records. Normalized {changed} records."))
