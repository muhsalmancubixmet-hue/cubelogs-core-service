# --------------------------------------------------------------------------------
#       Management Command: Sync & Repair Project Member Access
# --------------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import Project, ProjectMember
from projects.constants import PROJECT_ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Idempotently syncs missing PM/Team Lead memberships, standardizes project roles to Contributor, and cleans up duplicates safely."

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            default=False,
            help='Execute actual DB repairs. Defaults to dry-run mode.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Run in dry-run mode without modifying data.'
        )

    def handle(self, *args, **options):
        confirm = options.get('confirm', False)
        dry_run = not confirm

        if dry_run:
            self.stdout.write(self.style.NOTICE("=== Running in DRY-RUN mode. Pass --confirm to apply changes ==="))
        else:
            self.stdout.write(self.style.MIGRATE_HEADING("=== Executing Database Repair & Sync ==="))

        repaired_pm = 0
        repaired_lead = 0
        cleaned_duplicates = 0
        standardized_roles = 0

        valid_roles = list(PROJECT_ROLE_PERMISSIONS.keys())

        with transaction.atomic():
            # 1. Add missing PM & Team Lead memberships
            for project in Project.objects.select_related('project_manager', 'team_lead').all():
                if project.project_manager:
                    pm_obj, created = ProjectMember.objects.get_or_create(
                        project=project, user=project.project_manager,
                        defaults={'project_role': 'Project Manager', 'is_active': True}
                    )
                    if created:
                        repaired_pm += 1
                        self.stdout.write(f"  [FIXED PM] Added ProjectMember row for PM {project.project_manager.email} in '{project.name}'")

                if project.team_lead:
                    lead_obj, created = ProjectMember.objects.get_or_create(
                        project=project, user=project.team_lead,
                        defaults={'project_role': 'Team Lead', 'is_active': True}
                    )
                    if created:
                        repaired_lead += 1
                        self.stdout.write(f"  [FIXED TEAM LEAD] Added ProjectMember row for Team Lead {project.team_lead.email} in '{project.name}'")

            # 2. Standardize unrecognised role names to Contributor
            role_map = {r.lower(): r for r in valid_roles}
            for pm in ProjectMember.objects.all():
                role_lower = (pm.project_role or '').strip().lower()
                if role_lower in role_map:
                    canonical = role_map[role_lower]
                    if pm.project_role != canonical:
                        old_role = pm.project_role
                        pm.project_role = canonical
                        if not dry_run:
                            pm.save(update_fields=['project_role'])
                        standardized_roles += 1
                        self.stdout.write(f"  [STANDARDIZED ROLE] Updated ProjectMember ID {pm.id} ({pm.user.email}) from '{old_role}' -> '{canonical}'")
                else:
                    old_role = pm.project_role
                    new_role = 'Team Lead' if (pm.project.team_lead_id == pm.user_id) else ('Project Manager' if (pm.project.project_manager_id == pm.user_id) else 'Contributor')
                    pm.project_role = new_role
                    if not dry_run:
                        pm.save(update_fields=['project_role'])
                    standardized_roles += 1
                    self.stdout.write(f"  [STANDARDIZED ROLE] Updated ProjectMember ID {pm.id} ({pm.user.email}) from '{old_role}' -> '{new_role}'")

            # 3. Clean duplicates safely
            seen_pairs = set()
            for pm in ProjectMember.objects.order_by('id').all():
                pair = (pm.project_id, pm.user_id)
                if pair in seen_pairs:
                    cleaned_duplicates += 1
                    self.stdout.write(f"  [REMOVED DUPLICATE] Removed duplicate ProjectMember ID {pm.id} for User ID {pm.user_id}")
                    if not dry_run:
                        pm.delete()
                else:
                    seen_pairs.add(pair)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS("\n=== Sync Summary ==="))
        self.stdout.write(f"Missing PM Memberships Repaired: {repaired_pm}")
        self.stdout.write(f"Missing Team Lead Memberships Repaired: {repaired_lead}")
        self.stdout.write(f"Project Roles Standardized: {standardized_roles}")
        self.stdout.write(f"Duplicate Members Cleaned: {cleaned_duplicates}")

        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDry-run completed successfully. Run with --confirm to persist changes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nDatabase successfully synchronized and repaired!"))
