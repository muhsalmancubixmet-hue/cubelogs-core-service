from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import Project, ProjectEpic, ProjectSprint, ProjectStory, ProjectTask
from projects.rich_text_utils import normalize_to_canonical_html

class Command(BaseCommand):
    help = "Normalizes all Project Management rich text fields into canonical HTML."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run normalization in dry-run mode and display before/after diffs without saving.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Execute normalization and save changes to the database within an atomic transaction.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        confirm = options['confirm']

        if not dry_run and not confirm:
            self.stdout.write(self.style.WARNING(
                "Please specify either --dry-run to preview changes or --confirm to apply changes."
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("=== Project Management Rich Text Normalization ==="))
        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY-RUN mode. No changes will be saved."))

        fields_to_check = [
            (Project, 'description', 'Project Description'),
            (ProjectEpic, 'description', 'Epic Description'),
            (ProjectStory, 'description', 'Story Description'),
            (ProjectStory, 'acceptance_criteria', 'Story Acceptance Criteria'),
            (ProjectTask, 'description', 'Task Description'),
            (ProjectSprint, 'goal', 'Sprint Goal'),
        ]

        total_inspected = 0
        total_normalized = 0
        samples = []

        def process_field(model_cls, field_name, label):
            nonlocal total_inspected, total_normalized
            qs = model_cls.objects.all()
            for obj in qs:
                total_inspected += 1
                raw_val = getattr(obj, field_name) or ""
                if not raw_val.strip():
                    continue

                norm_val = normalize_to_canonical_html(raw_val)

                if raw_val != norm_val:
                    total_normalized += 1
                    if len(samples) < 10:
                        samples.append({
                            'model': model_cls.__name__,
                            'id': obj.id,
                            'field': field_name,
                            'label': label,
                            'before': repr(raw_val),
                            'after': repr(norm_val),
                        })
                    if confirm and not dry_run:
                        setattr(obj, field_name, norm_val)
                        obj.save(update_fields=[field_name])

        if confirm and not dry_run:
            with transaction.atomic():
                for model_cls, field_name, label in fields_to_check:
                    process_field(model_cls, field_name, label)
        else:
            for model_cls, field_name, label in fields_to_check:
                process_field(model_cls, field_name, label)

        self.stdout.write("\n=== Normalization Summary ===")
        self.stdout.write(f"Total Rich Text Fields Inspected: {total_inspected}")
        self.stdout.write(f"Records Needing Normalization: {total_normalized}")

        if samples:
            self.stdout.write("\n=== Sample Before / After Conversions ===")
            for idx, s in enumerate(samples, 1):
                self.stdout.write(self.style.SUCCESS(f"\nSample #{idx} [{s['model']} ID:{s['id']} Field:{s['field']}]"))
                self.stdout.write(self.style.WARNING(f" BEFORE: {s['before']}"))
                self.stdout.write(self.style.SUCCESS(f" AFTER : {s['after']}"))

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry-run completed successfully. Run with --confirm to apply changes."))
        elif confirm:
            self.stdout.write(self.style.SUCCESS(f"\nSuccessfully normalized {total_normalized} records in the database."))
