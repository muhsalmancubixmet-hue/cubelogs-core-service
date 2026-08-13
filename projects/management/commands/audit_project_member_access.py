# --------------------------------------------------------------------------------
#       Management Command: Audit Project Member Access & Integrity
# --------------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from projects.models import Project, ProjectMember
from projects.constants import PROJECT_ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Audit ProjectMember records for missing PM/Team Lead memberships, invalid roles, duplicates, and cross-company violations."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            help='Run in dry-run mode without modifying data (default).'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Project Member Access Integrity Audit ==="))

        missing_pm_count = 0
        missing_lead_count = 0
        duplicate_count = 0
        invalid_role_count = 0
        cross_company_count = 0
        total_projects = Project.objects.count()
        total_members = ProjectMember.objects.count()

        valid_roles = list(PROJECT_ROLE_PERMISSIONS.keys())

        # 1. Audit Projects for PM and Team Lead Membership
        for project in Project.objects.select_related('project_manager', 'team_lead', 'company').all():
            if project.project_manager:
                pm_exists = ProjectMember.objects.filter(project=project, user=project.project_manager).exists()
                if not pm_exists:
                    missing_pm_count += 1
                    self.stdout.write(self.style.WARNING(
                        f"  [MISSING PM] Project '{project.name}' (ID: {project.id}) missing ProjectMember row for PM {project.project_manager.email}"
                    ))

            if project.team_lead:
                lead_exists = ProjectMember.objects.filter(project=project, user=project.team_lead).exists()
                if not lead_exists:
                    missing_lead_count += 1
                    self.stdout.write(self.style.WARNING(
                        f"  [MISSING TEAM LEAD] Project '{project.name}' (ID: {project.id}) missing ProjectMember row for Team Lead {project.team_lead.email}"
                    ))

        # 2. Audit ProjectMembers for invalid roles & cross-company leaks
        seen_pairs = set()
        for pm in ProjectMember.objects.select_related('project', 'user', 'project__company').all():
            pair = (pm.project_id, pm.user_id)
            if pair in seen_pairs:
                duplicate_count += 1
                self.stdout.write(self.style.ERROR(
                    f"  [DUPLICATE] Duplicate ProjectMember found for Project ID {pm.project_id}, User ID {pm.user_id}"
                ))
            else:
                seen_pairs.add(pair)

            if not pm.project_role or pm.project_role not in valid_roles:
                invalid_role_count += 1
                self.stdout.write(self.style.WARNING(
                    f"  [INVALID ROLE] ProjectMember ID {pm.id} ({pm.user.email} in '{pm.project.name}') has unstandardized role '{pm.project_role}'"
                ))

            if pm.project and pm.user and pm.project.company != pm.user.organization:
                cross_company_count += 1
                self.stdout.write(self.style.ERROR(
                    f"  [CROSS-COMPANY LEAK] ProjectMember ID {pm.id} ({pm.user.email}) belongs to org '{pm.user.organization}' but project belongs to '{pm.project.company}'"
                ))

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Audit Summary ==="))
        self.stdout.write(f"Total Projects Scanned: {total_projects}")
        self.stdout.write(f"Total Members Scanned: {total_members}")
        self.stdout.write(f"Missing PM Memberships: {missing_pm_count}")
        self.stdout.write(f"Missing Team Lead Memberships: {missing_lead_count}")
        self.stdout.write(f"Duplicate Member Records: {duplicate_count}")
        self.stdout.write(f"Unstandardized Role Titles: {invalid_role_count}")
        self.stdout.write(f"Cross-Company Relationships: {cross_company_count}")

        if any([missing_pm_count, missing_lead_count, duplicate_count, invalid_role_count, cross_company_count]):
            self.stdout.write(self.style.NOTICE("\nRun 'python manage.py sync_project_member_access --confirm' to automatically repair inconsistencies."))
        else:
            self.stdout.write(self.style.SUCCESS("\nAll Project Member relationships are 100% consistent and secure."))
