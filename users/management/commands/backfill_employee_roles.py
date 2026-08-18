import logging
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils.text import slugify
from users.models import Employee, Role

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Safely backfills Employee.role ForeignKeys for legacy employees with unambiguous same-organization roles.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Persist matched role foreign keys to the database. Defaults to dry-run mode.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Simulate backfill without modifying database (default behavior).'
        )

    def handle(self, *args, **options):
        is_apply = options.get('apply', False)
        mode_str = "APPLY (writing to database)" if is_apply else "DRY-RUN (simulation only, no changes written)"

        self.stdout.write(self.style.NOTICE(f"=== Starting Employee Role Backfill [{mode_str}] ==="))

        legacy_employees = Employee.objects.filter(
            role__isnull=True,
            organization__isnull=False
        ).select_related('organization')

        total_candidates = legacy_employees.count()
        matched_count = 0
        unmatched_count = 0
        ambiguous_count = 0
        updated_count = 0

        self.stdout.write(f"Found {total_candidates} employees with role=NULL belonging to an organization.\n")

        for emp in legacy_employees:
            desig = (emp.designation or emp.role_name or '').strip()
            if not desig:
                self.stdout.write(self.style.WARNING(f"  [SKIPPED - EMPTY DESIGNATION] Employee {emp.id} ({emp.email}) has no designation."))
                unmatched_count += 1
                continue

            primary_desig = desig.split(',')[0].strip()
            desig_slug = slugify(primary_desig)

            # Query organization-scoped and system roles matching primary designation
            matched_roles = Role.objects.filter(is_active=True).filter(
                Q(organization=emp.organization) | Q(organization__isnull=True, is_system_role=True)
            ).filter(
                Q(name__iexact=primary_desig) | Q(label__iexact=primary_desig) | Q(slug__iexact=desig_slug)
            )

            match_count = matched_roles.count()

            if match_count == 1:
                role_obj = matched_roles.first()
                matched_count += 1
                if is_apply:
                    emp.role = role_obj
                    emp.role_name = role_obj.name
                    emp.save(update_fields=['role', 'role_name'])
                    updated_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  [UPDATED] Employee {emp.id} ({emp.email}) -> Role '{role_obj.name}' (id={role_obj.id})"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"  [MATCHED - DRY RUN] Employee {emp.id} ({emp.email}) -> Role '{role_obj.name}' (id={role_obj.id})"))
            elif match_count == 0:
                unmatched_count += 1
                self.stdout.write(self.style.WARNING(f"  [SKIPPED - UNMATCHED] Employee {emp.id} ({emp.email}) designation '{primary_desig}' matched 0 roles."))
            else:
                ambiguous_count += 1
                matching_names = [f"'{r.name}' (id={r.id})" for r in matched_roles]
                self.stdout.write(self.style.WARNING(f"  [SKIPPED - AMBIGUOUS] Employee {emp.id} ({emp.email}) matched multiple roles: {', '.join(matching_names)}"))

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.NOTICE("=== BACKFILL SUMMARY ==="))
        self.stdout.write(f"Total Candidates: {total_candidates}")
        self.stdout.write(self.style.SUCCESS(f"Matched:          {matched_count}"))
        self.stdout.write(self.style.WARNING(f"Unmatched:        {unmatched_count}"))
        self.stdout.write(self.style.WARNING(f"Ambiguous:        {ambiguous_count}"))
        self.stdout.write(f"Updated:          {updated_count}")
        self.stdout.write(f"Mode:             {mode_str}")
        self.stdout.write("=" * 50)
