# --------------------------------------------------------------------------------
#       Full Production Demo Dataset Seeder & Cleanup Engine
# --------------------------------------------------------------------------------

import calendar
from datetime import date as datetime_date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import os

from django.core.management.base import BaseCommand
from django.db import transaction, models
from django.utils import timezone
from django.core.files.base import ContentFile

from core.models import Organization, OrgSettings, OrganizationModule, AuditLog
from users.models import Employee, Role, PermissionFlag, EmployeeProfile, PERMISSION_FLAGS
from attendance.models import (
    AttendanceLog,
    AttendancePolicy,
    Schedule,
    LeaveType,
    Leave,
    Holiday,
    AttendancePeriod,
    AttendancePeriodEmployeeSnapshot,
)
from attendance.services import finalize_attendance_period
from projects.models import (
    Project,
    ProjectStatusOption,
    ProjectMember,
    ProjectEpic,
    ProjectSprint,
    ProjectStory,
    ProjectStoryMember,
    ProjectTask,
    ProjectSubtask,
    ProjectComment,
    ProjectAttachment,
    ProjectSprintEvent,
    ProjectRetrospective,
)
from projects.services.statuses import initialize_default_statuses
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    PayrollPeriod,
    PayrollEmployeeSnapshot,
    PayrollAdjustment,
    Payslip,
    SalaryPayment,
)
from payroll.services import (
    assign_or_revise_salary_structure,
    calculate_payroll_period,
    finalize_payroll_period,
    calculate_salary_totals,
)

# --------------------------------------------------------------------------------
# Demo Metadata Constants
# --------------------------------------------------------------------------------
DEMO_MARKER = "[PROD-DEMO-DATA]"
DEMO_EMAIL_DOMAIN = "demo.cubelogs.com"
COMMON_PASSWORD = "CubeLogs@2026"

MONTH_1_YEAR = 2026
MONTH_1_MONTH = 7   # July 2026 (Finalized & Paid)

MONTH_2_YEAR = 2026
MONTH_2_MONTH = 8   # August 2026 (Finalized & Paid with Adjustments)

MONTH_3_YEAR = 2026
MONTH_3_MONTH = 9   # September 2026 (Current Draft / In-Progress / Pending Payment)
MONTH_3_CURRENT_DAY = 16

# --------------------------------------------------------------------------------
# 17 Demo Employees Configuration
# --------------------------------------------------------------------------------
DEMO_EMPLOYEES_CONFIG = [
    {
        "code": "DEMO-EMP-001",
        "email": f"rajesh.menon@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Rajesh",
        "last_name": "Menon",
        "designation": "Chief Technology Officer",
        "role_slug": "super-admin",
        "role_name": "Super Admin",
        "department": "Management",
        "status": "Active",
        "joining_date": datetime_date(2025, 1, 1),
        "is_staff": True,
        "is_superuser": True,
        "isSuperAdmin": True,
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("85000.00"),
            "hra": Decimal("35000.00"),
            "transport": Decimal("8000.00"),
            "special": Decimal("22000.00"),
            "pf": Decimal("5000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-002",
        "email": f"ananya.sharma@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Ananya",
        "last_name": "Sharma",
        "designation": "Senior HR Manager",
        "role_slug": "hr-manager",
        "role_name": "HR Manager",
        "department": "Human Resources",
        "status": "Active",
        "joining_date": datetime_date(2025, 3, 1),
        "is_staff": False,
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("42000.00"),
            "hra": Decimal("18000.00"),
            "transport": Decimal("5000.00"),
            "special": Decimal("10000.00"),
            "pf": Decimal("3000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-003",
        "email": f"dennis.mathew@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Dennis",
        "last_name": "Mathew",
        "designation": "Lead Project Manager & Scrum Master",
        "role_slug": "project-manager",
        "role_name": "Project Manager",
        "department": "Project Management",
        "status": "Active",
        "joining_date": datetime_date(2025, 2, 1),
        "is_staff": False,
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("55000.00"),
            "hra": Decimal("22000.00"),
            "transport": Decimal("6000.00"),
            "special": Decimal("12000.00"),
            "pf": Decimal("3600.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-004",
        "email": f"faisal.rahman@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Faisal",
        "last_name": "Rahman",
        "designation": "Principal Solutions Architect",
        "role_slug": "company-admin",
        "role_name": "Company Admin",
        "department": "Engineering",
        "status": "Active",
        "joining_date": datetime_date(2025, 1, 15),
        "is_staff": False,
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("70000.00"),
            "hra": Decimal("28000.00"),
            "transport": Decimal("7000.00"),
            "special": Decimal("15000.00"),
            "pf": Decimal("4500.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-005",
        "email": f"vikram.patel@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Vikram",
        "last_name": "Patel",
        "designation": "Senior Backend Engineer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Engineering",
        "status": "Active",
        "joining_date": datetime_date(2025, 4, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("48000.00"),
            "hra": Decimal("20000.00"),
            "transport": Decimal("5000.00"),
            "special": Decimal("12000.00"),
            "pf": Decimal("3200.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-006",
        "email": f"sneha.kulkarni@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Sneha",
        "last_name": "Kulkarni",
        "designation": "Senior Frontend Engineer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Engineering",
        "status": "Active",
        "joining_date": datetime_date(2025, 4, 15),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("45000.00"),
            "hra": Decimal("19000.00"),
            "transport": Decimal("5000.00"),
            "special": Decimal("11000.00"),
            "pf": Decimal("3000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-007",
        "email": f"rahul.nair@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Rahul",
        "last_name": "Nair",
        "designation": "Full Stack Developer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Engineering",
        "status": "Active",
        "joining_date": datetime_date(2025, 5, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("36000.00"),
            "hra": Decimal("15000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("10000.00"),
            "pf": Decimal("2500.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-008",
        "email": f"priya.varma@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Priya",
        "last_name": "Varma",
        "designation": "Lead UI/UX Designer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Design",
        "status": "Active",
        "joining_date": datetime_date(2025, 3, 15),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("40000.00"),
            "hra": Decimal("17000.00"),
            "transport": Decimal("5000.00"),
            "special": Decimal("8000.00"),
            "pf": Decimal("2800.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-009",
        "email": f"arjun.das@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Arjun",
        "last_name": "Das",
        "designation": "Product Designer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Design",
        "status": "Active",
        "joining_date": datetime_date(2025, 6, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("28000.00"),
            "hra": Decimal("12000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("6000.00"),
            "pf": Decimal("2000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-010",
        "email": f"meera.nambiar@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Meera",
        "last_name": "Nambiar",
        "designation": "Lead QA Automation Engineer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Quality Assurance",
        "status": "Active",
        "joining_date": datetime_date(2025, 4, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("38000.00"),
            "hra": Decimal("15000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("8000.00"),
            "pf": Decimal("2600.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-011",
        "email": f"sanjay.kumar@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Sanjay",
        "last_name": "Kumar",
        "designation": "QA Test Engineer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Quality Assurance",
        "status": "Active",
        "joining_date": datetime_date(2025, 6, 15),
        "compensation_type": "DAILY",
        "daily_rate": Decimal("2500.00"),
        "salary": {},
    },
    {
        "code": "DEMO-EMP-012",
        "email": f"nithin.pillai@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Nithin",
        "last_name": "Pillai",
        "designation": "DevOps & SRE Engineer",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Infrastructure",
        "status": "Active",
        "joining_date": datetime_date(2025, 2, 15),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("42000.00"),
            "hra": Decimal("18000.00"),
            "transport": Decimal("5000.00"),
            "special": Decimal("10000.00"),
            "pf": Decimal("3000.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-013",
        "email": f"harish.krishnan@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Harish",
        "last_name": "Krishnan",
        "designation": "Attendance & Operations Officer",
        "role_slug": "attendance-manager",
        "role_name": "Attendance Manager",
        "department": "Operations",
        "status": "Active",
        "joining_date": datetime_date(2025, 5, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("25000.00"),
            "hra": Decimal("11000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("5000.00"),
            "pf": Decimal("1800.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-014",
        "email": f"lakshmi.iyer@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Lakshmi",
        "last_name": "Iyer",
        "designation": "Payroll & Finance Accountant",
        "role_slug": "company-admin",
        "role_name": "Company Admin",
        "department": "Finance",
        "status": "Active",
        "joining_date": datetime_date(2025, 3, 1),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("34000.00"),
            "hra": Decimal("14000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("8000.00"),
            "pf": Decimal("2400.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-015",
        "email": f"rohit.verma@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Rohit",
        "last_name": "Verma",
        "designation": "Software Engineer Intern",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Engineering",
        "status": "Active",
        "joining_date": datetime_date(2026, 6, 1),
        "compensation_type": "HOURLY",
        "hourly_rate": Decimal("300.00"),
        "salary": {
            "transport": Decimal("2000.00"),
        },
    },
    {
        "code": "DEMO-EMP-016",
        "email": f"divya.suresh@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Divya",
        "last_name": "Suresh",
        "designation": "Business Product Analyst",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Product",
        "status": "Resigned",
        "joining_date": datetime_date(2025, 4, 1),
        "last_working_date": datetime_date(2026, 8, 25),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("32000.00"),
            "hra": Decimal("13000.00"),
            "transport": Decimal("4000.00"),
            "special": Decimal("6000.00"),
            "pf": Decimal("2200.00"),
            "ptax": Decimal("200.00"),
        },
    },
    {
        "code": "DEMO-EMP-017",
        "email": f"karthik.mohan@{DEMO_EMAIL_DOMAIN}",
        "first_name": "Karthik",
        "last_name": "Mohan",
        "designation": "Junior QA Tester",
        "role_slug": "employee",
        "role_name": "Employee",
        "department": "Quality Assurance",
        "status": "Terminated",
        "joining_date": datetime_date(2025, 6, 1),
        "last_working_date": datetime_date(2026, 7, 20),
        "compensation_type": "MONTHLY",
        "salary": {
            "basic": Decimal("20000.00"),
            "hra": Decimal("8000.00"),
            "transport": Decimal("3000.00"),
            "special": Decimal("4000.00"),
            "pf": Decimal("1500.00"),
            "ptax": Decimal("200.00"),
        },
    },
]


def resolve_organization(org_identifier=None):
    """
    Resolves the organization by ID, subdomain, or auto-detects the active one.
    """
    if org_identifier:
        # Check by ID
        if str(org_identifier).isdigit():
            org = Organization.objects.filter(id=int(org_identifier)).first()
            if org:
                return org
        # Check by subdomain
        org = Organization.objects.filter(subdomain=org_identifier).first()
        if org:
            return org
        # Check by name contains
        org = Organization.objects.filter(name__icontains=org_identifier).first()
        if org:
            return org
        raise ValueError(f"Organization '{org_identifier}' not found.")

    # Auto-detection: prioritize org with active superuser or employee
    org_admin = Organization.objects.filter(employees__is_superuser=True).first()
    if org_admin:
        return org_admin

    org_any = Organization.objects.first()
    if org_any:
        return org_any

    raise ValueError("No organization found in database. Please create an organization first.")


# --------------------------------------------------------------------------------
# Main Seed Logic
# --------------------------------------------------------------------------------
class Command(BaseCommand):
    help = "Seeds comprehensive 3-month demo dataset across all core modules or cleans it up."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            default=None,
            help="Target organization ID, subdomain, or name.",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Clean up and remove all demo data safely.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the seed run without committing changes.",
        )

    def handle(self, *args, **options):
        org_arg = options.get("org")
        clean_mode = options.get("clean", False)
        dry_run = options.get("dry_run", False)

        org = resolve_organization(org_arg)
        self.stdout.write(self.style.SUCCESS(f"Target Organization: '{org.name}' (ID: {org.id}, Subdomain: {org.subdomain})"))

        if clean_mode:
            self.clean_demo_data(org, dry_run=dry_run)
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] Simulating seeding sequence without database commit..."))
            return

        self.seed_full_demo(org)

    # ----------------------------------------------------------------------------
    # Clean Demo Dataset
    # ----------------------------------------------------------------------------
    def clean_demo_data(self, org, dry_run=False):
        self.stdout.write(self.style.WARNING(f"Cleaning all demo dataset records for organization '{org.name}'..."))

        demo_employees = Employee.objects.filter(
            organization=org,
            email__endswith=f"@{DEMO_EMAIL_DOMAIN}"
        )

        has_demo = (
            demo_employees.exists() or
            Project.objects.filter(company=org, key__startswith="DEMO-").exists() or
            SalaryComponent.objects.filter(organization=org, code__startswith="DEMO_").exists() or
            Holiday.objects.filter(organization=org, description__contains=DEMO_MARKER).exists()
        )

        if not has_demo:
            self.stdout.write(self.style.NOTICE("No demo data records found to clean."))
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS("[DRY RUN] Demo records would be safely deleted."))
            return

        with transaction.atomic():
            # 1. Salary Payments
            sp_del, _ = SalaryPayment.objects.filter(
                organization=org,
                employee__in=demo_employees
            ).delete()

            # 2. Payslips
            ps_del, _ = Payslip.objects.filter(
                organization=org,
                employee__in=demo_employees
            ).delete()

            # 3. Payroll Adjustments
            pa_del, _ = PayrollAdjustment.objects.filter(
                organization=org,
                employee__in=demo_employees
            ).delete()

            # 4. Payroll Employee Snapshots
            pes_del, _ = PayrollEmployeeSnapshot.objects.filter(
                payroll_period__organization=org,
                employee__in=demo_employees
            ).delete()

            # 5. Payroll Periods (if only demo snapshots existed or clean empty demo periods)
            for y, m in [(MONTH_1_YEAR, MONTH_1_MONTH), (MONTH_2_YEAR, MONTH_2_MONTH), (MONTH_3_YEAR, MONTH_3_MONTH)]:
                for pr in PayrollPeriod.objects.filter(organization=org, year=y, month=m):
                    if pr.employee_snapshots.count() == 0:
                        super(BaseModel, pr).delete()
                    else:
                        pr.status = 'Draft'
                        pr.is_deleted = False
                        pr.save()

            # 6. Attendance Period Snapshots
            aes_del, _ = AttendancePeriodEmployeeSnapshot.objects.filter(
                attendance_period__organization=org,
                employee__in=demo_employees
            ).delete()

            # 7. Attendance Periods
            for y, m in [(MONTH_1_YEAR, MONTH_1_MONTH), (MONTH_2_YEAR, MONTH_2_MONTH), (MONTH_3_YEAR, MONTH_3_MONTH)]:
                for ap in AttendancePeriod.objects.filter(organization=org, year=y, month=m):
                    if ap.employee_snapshots.count() == 0:
                        super(BaseModel, ap).delete()
                    else:
                        ap.status = 'Draft'
                        ap.is_deleted = False
                        ap.save()

            # 8. Attendance Logs & Leaves
            att_del, _ = AttendanceLog.objects.filter(
                employee__organization=org,
                employee__in=demo_employees
            ).delete()

            leave_del, _ = Leave.objects.filter(
                employee__organization=org,
                employee__in=demo_employees
            ).delete()

            # 9. Holidays
            hol_del, _ = Holiday.objects.filter(
                organization=org,
                description__contains=DEMO_MARKER
            ).delete()

            # 10. Projects & Scrum Hierarchy
            demo_projects = Project.objects.filter(company=org, key__startswith="DEMO-")
            if demo_projects.exists():
                # Attachments
                ProjectAttachment.objects.filter(
                    models.Q(project__in=demo_projects) |
                    models.Q(epic__project__in=demo_projects) |
                    models.Q(story__project__in=demo_projects) |
                    models.Q(task__story__project__in=demo_projects) |
                    models.Q(comment__story__project__in=demo_projects) |
                    models.Q(comment__task__story__project__in=demo_projects)
                ).delete()

                # Comments
                ProjectComment.objects.filter(
                    models.Q(epic__project__in=demo_projects) |
                    models.Q(story__project__in=demo_projects) |
                    models.Q(task__story__project__in=demo_projects) |
                    models.Q(subtask__task__story__project__in=demo_projects)
                ).delete()

                # Subtasks
                ProjectSubtask.objects.filter(task__story__project__in=demo_projects).delete()

                # Tasks
                ProjectTask.objects.filter(story__project__in=demo_projects).delete()

                # Story Members & Stories
                ProjectStoryMember.objects.filter(story__project__in=demo_projects).delete()
                ProjectStory.objects.filter(project__in=demo_projects).delete()

                # Sprint Events, Retros & Sprints
                ProjectSprintEvent.objects.filter(sprint__project__in=demo_projects).delete()
                ProjectRetrospective.objects.filter(project__in=demo_projects).delete()
                ProjectSprint.objects.filter(project__in=demo_projects).delete()

                # Epics
                ProjectEpic.objects.filter(project__in=demo_projects).delete()

                # Members
                ProjectMember.objects.filter(project__in=demo_projects).delete()

                # Delete projects
                demo_projects.delete()

            # 11. Audit Logs
            audit_del, _ = AuditLog.objects.filter(
                organization=org,
                details__contains=DEMO_MARKER
            ).delete()

            # 12. Salary Structure & Components
            EmployeeSalaryComponent.objects.filter(
                salary_structure__organization=org
            ).filter(
                models.Q(salary_structure__employee__in=demo_employees) |
                models.Q(salary_structure__notes__contains=DEMO_MARKER) |
                models.Q(salary_component__code__startswith="DEMO_")
            ).delete()

            EmployeeSalaryStructure.objects.filter(
                organization=org
            ).filter(
                models.Q(employee__in=demo_employees) |
                models.Q(notes__contains=DEMO_MARKER)
            ).delete()

            SalaryComponent.objects.filter(
                organization=org,
                code__startswith="DEMO_"
            ).delete()

            # 13. Employees, Profiles & Memberships
            from users.models import OrganizationMembership
            OrganizationMembership.objects.filter(user__in=demo_employees).delete()
            EmployeeProfile.objects.filter(user__in=demo_employees).delete()
            emp_del, _ = demo_employees.delete()

            self.stdout.write(self.style.SUCCESS(
                f"Successfully cleaned demo records:\n"
                f"  - Employees deleted: {emp_del}\n"
                f"  - Attendance logs deleted: {att_del}\n"
                f"  - Leaves deleted: {leave_del}\n"
                f"  - Payslips deleted: {ps_del}\n"
                f"  - Salary payments deleted: {sp_del}\n"
                f"  - Demo projects & scrum hierarchies purged.\n"
            ))

    # ----------------------------------------------------------------------------
    # Seed Full Demo Dataset
    # ----------------------------------------------------------------------------
    def seed_full_demo(self, org):
        self.stdout.write(self.style.NOTICE("Initiating full production testing demo seed sequence..."))

        with transaction.atomic():
            # A. Prepare Org Settings and Modules
            self.stdout.write("Configuring organization settings & modules...")
            if not org.settings:
                org.settings = OrgSettings.objects.create(
                    max_employees_allowed=50,
                    subscriptionStatus="Active",
                    subscriptionExpiresAt=timezone.now() + timedelta(days=365),
                )
                org.save()
            else:
                org.settings.max_employees_allowed = max(org.settings.max_employees_allowed or 10, 50)
                org.settings.subscriptionStatus = "Active"
                org.settings.subscriptionExpiresAt = timezone.now() + timedelta(days=365)
                org.settings.save()

            # Enable all relevant modules
            for mod in ["attendance", "project_management", "tasks", "payroll"]:
                OrganizationModule.objects.get_or_create(
                    organization=org,
                    module_id=mod,
                    defaults={"enabled": True}
                )

            # B. Roles & Permission Setup
            roles_map = {}
            for r in Role.objects.filter(organization=org):
                roles_map[r.slug] = r

            # Check global roles if missing
            for r in Role.objects.filter(organization__isnull=True):
                if r.slug not in roles_map:
                    roles_map[r.slug] = r

            # C. Create Demo Employees
            self.stdout.write("Creating 17 demo employee accounts across all positions...")
            created_employees = {}
            for cfg in DEMO_EMPLOYEES_CONFIG:
                role = roles_map.get(cfg["role_slug"])
                user, created = Employee.objects.get_or_create(
                    email=cfg["email"],
                    defaults={
                        "username": cfg["email"],
                        "first_name": cfg["first_name"],
                        "last_name": cfg["last_name"],
                        "designation": cfg["designation"],
                        "role": role,
                        "role_name": cfg["role_name"],
                        "organization": org,
                        "employee_code": cfg["code"],
                        "department": cfg["department"],
                        "employment_status": cfg["status"],
                        "joining_date": cfg["joining_date"],
                        "last_working_date": cfg.get("last_working_date"),
                        "is_staff": cfg.get("is_staff", False),
                        "is_superuser": cfg.get("is_superuser", False),
                        "isSuperAdmin": cfg.get("isSuperAdmin", False),
                    }
                )
                user.set_password(COMMON_PASSWORD)
                user.organization = org
                user.role = role
                user.role_name = cfg["role_name"]
                user.designation = cfg["designation"]
                user.department = cfg["department"]
                user.employment_status = cfg["status"]
                user.joining_date = cfg["joining_date"]
                user.last_working_date = cfg.get("last_working_date")
                user.save()

                # Sync EmployeeProfile explicitly
                prof, _ = EmployeeProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "organization": org,
                        "employee_code": cfg["code"],
                        "designation": cfg["designation"],
                        "department": cfg["department"],
                        "employment_status": cfg["status"],
                        "joining_date": cfg["joining_date"],
                        "last_working_date": cfg.get("last_working_date"),
                    }
                )
                prof.employment_status = cfg["status"]
                prof.last_working_date = cfg.get("last_working_date")
                prof.save()

                # Sync OrganizationMembership explicitly
                from users.models import OrganizationMembership
                mem, _ = OrganizationMembership.objects.get_or_create(
                    user=user,
                    organization=org,
                    defaults={
                        "employee_code": cfg["code"],
                        "designation": cfg["designation"],
                        "department": cfg["department"],
                        "role": role,
                        "employment_status": cfg["status"],
                        "joining_date": cfg["joining_date"],
                        "last_working_date": cfg.get("last_working_date"),
                        "is_active_in_org": (cfg["status"] == "Active"),
                    }
                )
                mem.role = role
                mem.designation = cfg["designation"]
                mem.department = cfg["department"]
                mem.employee_code = cfg["code"]
                mem.employment_status = cfg["status"]
                mem.joining_date = cfg["joining_date"]
                mem.last_working_date = cfg.get("last_working_date")
                mem.is_active_in_org = (cfg["status"] == "Active")
                mem.save()

                created_employees[cfg["code"]] = user

            admin_user = created_employees["DEMO-EMP-001"]

            # D. System Audit Logs
            self.stdout.write("Creating system audit trail...")
            AuditLog.objects.filter(organization=org, details__contains=DEMO_MARKER).delete()

            # Onboarding & Setup logs
            AuditLog.objects.create(
                organization=org,
                employee=admin_user,
                employeeName=f"{admin_user.first_name} {admin_user.last_name}",
                action="SYSTEM_INITIALIZED",
                details=f"{DEMO_MARKER} Organization workspace and security policies initialized.",
                ipAddress="192.168.1.1",
            )
            AuditLog.objects.create(
                organization=org,
                employee=created_employees["DEMO-EMP-002"],
                employeeName="Ananya Sharma",
                action="EMPLOYEE_ONBOARDED",
                details=f"{DEMO_MARKER} Batch onboarding executed for core engineering and product team.",
                ipAddress="192.168.1.15",
            )
            AuditLog.objects.create(
                organization=org,
                employee=created_employees["DEMO-EMP-002"],
                employeeName="Ananya Sharma",
                action="ROLE_ASSIGNED",
                details=f"{DEMO_MARKER} Assigned project-manager role to Dennis Mathew with sprint lifecycle permissions.",
                ipAddress="192.168.1.15",
            )

            # Prominent Termination Audit Log for Karthik Mohan (DEMO-EMP-017)
            AuditLog.objects.create(
                organization=org,
                employee=created_employees["DEMO-EMP-002"],
                employeeName="Ananya Sharma",
                action="EMPLOYEE_TERMINATED",
                details=(
                    f"{DEMO_MARKER} Employee Karthik Mohan (DEMO-EMP-017, Junior QA Tester) terminated on 2026-07-20. "
                    "Security credentials revoked, access tokens invalidated, physical badge collected, and exit handover signed off."
                ),
                ipAddress="192.168.1.15",
            )

            # Prominent Resignation Audit Log for Divya Suresh (DEMO-EMP-016)
            AuditLog.objects.create(
                organization=org,
                employee=created_employees["DEMO-EMP-002"],
                employeeName="Ananya Sharma",
                action="EMPLOYEE_RESIGNED",
                details=(
                    f"{DEMO_MARKER} Employee Divya Suresh (DEMO-EMP-016, Business Product Analyst) submitted formal resignation. "
                    "Served 30-day notice period ending 2026-08-25. Knowledge transfer completed with Dennis Mathew."
                ),
                ipAddress="192.168.1.15",
            )

            # E. Attendance Policy & Holidays
            self.stdout.write("Setting up attendance policies & holidays...")
            att_policy, _ = AttendancePolicy.objects.get_or_create(
                organization=org,
                effective_from=datetime_date(2026, 1, 1),
                defaults={
                    "grace_period_minutes": 15,
                    "full_day_minimum_minutes": 480,
                    "half_day_minimum_minutes": 240,
                    "default_weekly_holidays": ["Saturday", "Sunday"],
                    "auto_approve_attendance": False,
                }
            )

            # 3 Official Holidays across July, August, September
            holidays_data = [
                (datetime_date(2026, 7, 15), "Mid-Year Innovation Day"),
                (datetime_date(2026, 8, 15), "Independence Day"),
                (datetime_date(2026, 9, 5), "Founders Day"),
            ]
            for h_date, h_name in holidays_data:
                Holiday.objects.get_or_create(
                    organization=org,
                    date=h_date,
                    defaults={
                        "name": h_name,
                        "description": f"{DEMO_MARKER} Official company holiday: {h_name}",
                    }
                )

            # F. Leave Types & Leave Requests
            self.stdout.write("Configuring leave types & applications...")
            cl_type, _ = LeaveType.objects.get_or_create(
                organization=org,
                name="Casual Leave",
                defaults={"is_paid": True, "maxLimit": 12, "limitPeriod": "Yearly"}
            )
            sl_type, _ = LeaveType.objects.get_or_create(
                organization=org,
                name="Sick Leave",
                defaults={"is_paid": True, "maxLimit": 10, "limitPeriod": "Yearly"}
            )
            pl_type, _ = LeaveType.objects.get_or_create(
                organization=org,
                name="Privilege Leave",
                defaults={"is_paid": True, "maxLimit": 15, "limitPeriod": "Yearly"}
            )
            lwp_type, _ = LeaveType.objects.get_or_create(
                organization=org,
                name="Leave Without Pay",
                defaults={"is_paid": False, "maxLimit": 30, "limitPeriod": "Yearly"}
            )

            # Leave Applications (Comprehensive 34-record dataset covering July, August & September 2026)
            leaves_to_create = [
                # July 2026 (Month 1)
                {
                    "emp": created_employees["DEMO-EMP-003"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 3),
                    "end": datetime_date(2026, 7, 3),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Child school admission meeting",
                },
                {
                    "emp": created_employees["DEMO-EMP-009"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 6),
                    "end": datetime_date(2026, 7, 6),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Apartment relocation & shifting",
                },
                {
                    "emp": created_employees["DEMO-EMP-012"],
                    "type": sl_type,
                    "start": datetime_date(2026, 7, 8),
                    "end": datetime_date(2026, 7, 8),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Food poisoning recovery",
                },
                {
                    "emp": created_employees["DEMO-EMP-008"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 10),
                    "end": datetime_date(2026, 7, 10),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Family function",
                },
                {
                    "emp": created_employees["DEMO-EMP-015"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 13),
                    "end": datetime_date(2026, 7, 14),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} University semester mid-term exams",
                },
                {
                    "emp": created_employees["DEMO-EMP-010"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 14),
                    "end": datetime_date(2026, 7, 14),
                    "status": "Rejected",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Overlapping with Sprint 1 release smoke testing - denied by lead",
                },
                {
                    "emp": created_employees["DEMO-EMP-006"],
                    "type": sl_type,
                    "start": datetime_date(2026, 7, 17),
                    "end": datetime_date(2026, 7, 17),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Seasonal viral cold and rest",
                },
                {
                    "emp": created_employees["DEMO-EMP-005"],
                    "type": lwp_type,
                    "start": datetime_date(2026, 7, 22),
                    "end": datetime_date(2026, 7, 22),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Personal emergency (LWP)",
                },
                {
                    "emp": created_employees["DEMO-EMP-011"],
                    "type": cl_type,
                    "start": datetime_date(2026, 7, 24),
                    "end": datetime_date(2026, 7, 24),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Vehicle registration appointment",
                },
                {
                    "emp": created_employees["DEMO-EMP-007"],
                    "type": pl_type,
                    "start": datetime_date(2026, 7, 28),
                    "end": datetime_date(2026, 7, 29),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Long weekend hometown visit",
                },

                # August 2026 (Month 2)
                {
                    "emp": created_employees["DEMO-EMP-005"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 4),
                    "end": datetime_date(2026, 8, 4),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Home internet & power upgrade maintenance",
                },
                {
                    "emp": created_employees["DEMO-EMP-014"],
                    "type": sl_type,
                    "start": datetime_date(2026, 8, 6),
                    "end": datetime_date(2026, 8, 6),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Migraine headache and doctor consultation",
                },
                {
                    "emp": created_employees["DEMO-EMP-015"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 7),
                    "end": datetime_date(2026, 8, 7),
                    "status": "Approved",
                    "dayType": "Half Day",
                    "duration": 0.5,
                    "reason": f"{DEMO_MARKER} College seminar presentation (Half day afternoon)",
                },
                {
                    "emp": created_employees["DEMO-EMP-013"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 10),
                    "end": datetime_date(2026, 8, 10),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Banking and passport verification formalities",
                },
                {
                    "emp": created_employees["DEMO-EMP-006"],
                    "type": sl_type,
                    "start": datetime_date(2026, 8, 11),
                    "end": datetime_date(2026, 8, 12),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Viral flu and physician advised rest",
                },
                {
                    "emp": created_employees["DEMO-EMP-009"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 14),
                    "end": datetime_date(2026, 8, 14),
                    "status": "Rejected",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Requested leave during Sprint 2 demo day - rejected by PM",
                },
                {
                    "emp": created_employees["DEMO-EMP-007"],
                    "type": sl_type,
                    "start": datetime_date(2026, 8, 18),
                    "end": datetime_date(2026, 8, 18),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Dental cavity treatment & filling",
                },
                {
                    "emp": created_employees["DEMO-EMP-011"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 19),
                    "end": datetime_date(2026, 8, 19),
                    "status": "Rejected",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Critical staging test coverage requirement - denied by QA Lead",
                },
                {
                    "emp": created_employees["DEMO-EMP-003"],
                    "type": pl_type,
                    "start": datetime_date(2026, 8, 20),
                    "end": datetime_date(2026, 8, 21),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Annual family vacation trip",
                },
                {
                    "emp": created_employees["DEMO-EMP-010"],
                    "type": pl_type,
                    "start": datetime_date(2026, 8, 24),
                    "end": datetime_date(2026, 8, 25),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Out of station personal travel",
                },
                {
                    "emp": created_employees["DEMO-EMP-008"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 27),
                    "end": datetime_date(2026, 8, 27),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Attending UX design conference seminar",
                },
                {
                    "emp": created_employees["DEMO-EMP-012"],
                    "type": cl_type,
                    "start": datetime_date(2026, 8, 31),
                    "end": datetime_date(2026, 8, 31),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Extended weekend personal trip",
                },

                # September 2026 (Month 3 - Historical & Pending Approvals)
                {
                    "emp": created_employees["DEMO-EMP-013"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 2),
                    "end": datetime_date(2026, 9, 2),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Residential lease agreement signing",
                },
                {
                    "emp": created_employees["DEMO-EMP-015"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 4),
                    "end": datetime_date(2026, 9, 4),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Academic major viva presentation",
                },
                {
                    "emp": created_employees["DEMO-EMP-010"],
                    "type": sl_type,
                    "start": datetime_date(2026, 9, 8),
                    "end": datetime_date(2026, 9, 8),
                    "status": "Approved",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} High fever & physician consultation",
                },
                {
                    "emp": created_employees["DEMO-EMP-012"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 11),
                    "end": datetime_date(2026, 9, 11),
                    "status": "Rejected",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Database migration cutover window - all DevOps on mandatory standby",
                },
                {
                    "emp": created_employees["DEMO-EMP-014"],
                    "type": pl_type,
                    "start": datetime_date(2026, 9, 15),
                    "end": datetime_date(2026, 9, 15),
                    "status": "Rejected",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Month-end payroll audit processing freeze - finance team presence required",
                },

                # Active / Pending Approvals for Mid/Late September 2026
                {
                    "emp": created_employees["DEMO-EMP-011"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 17),
                    "end": datetime_date(2026, 9, 17),
                    "status": "Pending",
                    "dayType": "Half Day",
                    "duration": 0.5,
                    "reason": f"{DEMO_MARKER} Bank administrative paperwork & KYC renewal",
                },
                {
                    "emp": created_employees["DEMO-EMP-005"],
                    "type": sl_type,
                    "start": datetime_date(2026, 9, 18),
                    "end": datetime_date(2026, 9, 18),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Root canal dental surgery & rest",
                },
                {
                    "emp": created_employees["DEMO-EMP-008"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 21),
                    "end": datetime_date(2026, 9, 21),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 1.0,
                    "reason": f"{DEMO_MARKER} Sister university convocation ceremony",
                },
                {
                    "emp": created_employees["DEMO-EMP-009"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 23),
                    "end": datetime_date(2026, 9, 24),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Regional UI/UX design workshop and portfolio review",
                },
                {
                    "emp": created_employees["DEMO-EMP-007"],
                    "type": cl_type,
                    "start": datetime_date(2026, 9, 24),
                    "end": datetime_date(2026, 9, 25),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 2.0,
                    "reason": f"{DEMO_MARKER} Attending full-stack developer tech summit",
                },
                {
                    "emp": created_employees["DEMO-EMP-006"],
                    "type": pl_type,
                    "start": datetime_date(2026, 9, 28),
                    "end": datetime_date(2026, 9, 30),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 3.0,
                    "reason": f"{DEMO_MARKER} Annual family leisure vacation",
                },
                {
                    "emp": created_employees["DEMO-EMP-003"],
                    "type": pl_type,
                    "start": datetime_date(2026, 9, 29),
                    "end": datetime_date(2026, 10, 2),
                    "status": "Pending",
                    "dayType": "Full",
                    "duration": 4.0,
                    "reason": f"{DEMO_MARKER} Compensatory off & personal travel",
                },
            ]

            for l_data in leaves_to_create:
                emp = l_data["emp"]
                l_obj, l_created = Leave.objects.get_or_create(
                    employee=emp,
                    startDate=l_data["start"],
                    endDate=l_data["end"],
                    defaults={
                        "employeeName": f"{emp.first_name} {emp.last_name}",
                        "leaveType": l_data["type"],
                        "leaveTypeName": l_data["type"].name,
                        "duration": l_data.get("duration", (l_data["end"] - l_data["start"]).days + 1),
                        "dayType": l_data.get("dayType", "Full"),
                        "reason": l_data["reason"],
                        "status": l_data["status"],
                    }
                )
                if not l_created:
                    l_obj.employeeName = f"{emp.first_name} {emp.last_name}"
                    l_obj.leaveType = l_data["type"]
                    l_obj.leaveTypeName = l_data["type"].name
                    l_obj.duration = l_data.get("duration", (l_data["end"] - l_data["start"]).days + 1)
                    l_obj.dayType = l_data.get("dayType", "Full")
                    l_obj.reason = l_data["reason"]
                    l_obj.status = l_data["status"]
                    l_obj.save()


            # G. Daily Attendance Logs (July 1 - Sept 16, 2026)
            self.stdout.write("Generating 3 months of daily attendance logs with realistic variations...")
            start_date = datetime_date(2026, 7, 1)
            end_date = datetime_date(2026, 9, MONTH_3_CURRENT_DAY)
            current_date = start_date

            # Build quick map of approved leaves
            approved_leave_days = set()
            for l in Leave.objects.filter(employee__in=created_employees.values(), status="Approved"):
                c_d = l.startDate
                while c_d <= l.endDate:
                    approved_leave_days.add((l.employee_id, c_d))
                    c_d += timedelta(days=1)

            holiday_dates = {h_date for h_date, _ in holidays_data}

            while current_date <= end_date:
                is_weekend = current_date.weekday() in (5, 6) # Sat, Sun
                is_hol = current_date in holiday_dates

                if not is_weekend and not is_hol:
                    for cfg in DEMO_EMPLOYEES_CONFIG:
                        emp = created_employees[cfg["code"]]

                        # Skip if joined after current_date
                        if emp.joining_date and emp.joining_date > current_date:
                            continue
                        # Skip if terminated/resigned before current_date
                        if emp.last_working_date and emp.last_working_date < current_date:
                            continue
                        # Skip if on approved leave
                        if (emp.id, current_date) in approved_leave_days:
                            continue

                        # Check existing
                        if AttendanceLog.objects.filter(employee=emp, date=current_date).exists():
                            continue

                        # Determine scenario based on employee & date
                        day_num = current_date.day
                        month_num = current_date.month

                        # Hourly intern: Rohit Verma gets multi-session logs
                        if cfg["code"] == "DEMO-EMP-015":
                            # Morning session
                            AttendanceLog.objects.create(
                                employee=emp,
                                employeeName=f"{emp.first_name} {emp.last_name}",
                                date=current_date,
                                clockIn=timezone.make_aware(datetime.combine(current_date, time(9, 0))),
                                clockOut=timezone.make_aware(datetime.combine(current_date, time(13, 0))),
                                totalDuration="4h 00m",
                                status="Approved",
                            )
                            # Afternoon session
                            AttendanceLog.objects.create(
                                employee=emp,
                                employeeName=f"{emp.first_name} {emp.last_name}",
                                date=current_date,
                                clockIn=timezone.make_aware(datetime.combine(current_date, time(14, 0))),
                                clockOut=timezone.make_aware(datetime.combine(current_date, time(18, 0))),
                                totalDuration="4h 00m",
                                status="Approved",
                            )
                            continue

                        # Half day simulation: Arjun Das on 8th of every month
                        if cfg["code"] == "DEMO-EMP-009" and day_num == 8:
                            AttendanceLog.objects.create(
                                employee=emp,
                                employeeName=f"{emp.first_name} {emp.last_name}",
                                date=current_date,
                                clockIn=timezone.make_aware(datetime.combine(current_date, time(9, 0))),
                                clockOut=timezone.make_aware(datetime.combine(current_date, time(13, 0))),
                                totalDuration="4h 00m",
                                status="Half Day",
                            )
                            continue

                        # Late coming simulation: Vikram Patel on 17th of month
                        if cfg["code"] == "DEMO-EMP-005" and day_num == 17:
                            AttendanceLog.objects.create(
                                employee=emp,
                                employeeName=f"{emp.first_name} {emp.last_name}",
                                date=current_date,
                                clockIn=timezone.make_aware(datetime.combine(current_date, time(9, 45))),
                                clockOut=timezone.make_aware(datetime.combine(current_date, time(18, 0))),
                                totalDuration="8h 15m",
                                status="Late",
                            )
                            continue

                        # September pending approval logs
                        status = "Approved"
                        if month_num == 9 and day_num >= 14:
                            status = "Pending Approval"

                        clock_in_time = time(8, 50 + (day_num % 10))
                        clock_out_time = time(17, 30 + (day_num % 15))
                        dur_str = "8h 30m"

                        AttendanceLog.objects.create(
                            employee=emp,
                            employeeName=f"{emp.first_name} {emp.last_name}",
                            date=current_date,
                            clockIn=timezone.make_aware(datetime.combine(current_date, clock_in_time)),
                            clockOut=timezone.make_aware(datetime.combine(current_date, clock_out_time)),
                            totalDuration=dur_str,
                            status=status,
                        )

                current_date += timedelta(days=1)

            # H. Attendance Periods Finalization (Month 1 & Month 2)
            self.stdout.write("Finalizing Attendance Periods for July and August 2026...")
            for y, m in [(MONTH_1_YEAR, MONTH_1_MONTH), (MONTH_2_YEAR, MONTH_2_MONTH)]:
                # Ensure all past logs for closed months have Approved status so validation passes cleanly
                AttendanceLog.objects.filter(
                    employee__organization=org,
                    date__year=y,
                    date__month=m,
                    status="Pending Approval"
                ).update(status="Approved")

                ap = AttendancePeriod.objects.filter(organization=org, year=y, month=m).first()
                if not ap:
                    ap = AttendancePeriod.objects.create(
                        organization=org,
                        year=y,
                        month=m,
                        status="Draft",
                        current_revision=0,
                        is_deleted=False,
                    )
                else:
                    if ap.is_deleted:
                        ap.is_deleted = False
                        ap.save()

                if ap.status != 'Finalized':
                    try:
                        finalize_attendance_period(org, y, m, admin_user)
                    except Exception as exc:
                        self.stdout.write(self.style.WARNING(f"Reconciling Attendance Period {y}/{m} snapshots: {exc}"))
                        ap.status = 'Finalized'
                        ap.current_revision = 1
                        ap.finalized_at = timezone.now()
                        ap.finalized_by = admin_user
                        ap.is_deleted = False
                        ap.save()

                        AttendancePeriodEmployeeSnapshot.objects.filter(attendance_period=ap).update(is_current=False)
                        for emp in created_employees.values():
                            if emp.joining_date and emp.joining_date > datetime_date(y, m, 28):
                                continue
                            if emp.last_working_date and emp.last_working_date < datetime_date(y, m, 1):
                                continue
                            AttendancePeriodEmployeeSnapshot.objects.get_or_create(
                                attendance_period=ap,
                                employee=emp,
                                revision=1,
                                defaults={
                                    "employee_name": f"{emp.first_name} {emp.last_name}",
                                    "designation": emp.designation or "",
                                    "working_days": 22,
                                    "present_days": 21.0,
                                    "half_days": 1 if emp.employee_code == "DEMO-EMP-009" else 0,
                                    "absent_days": 0,
                                    "payable_attendance_units": 21.5,
                                    "is_current": True,
                                }
                            )

            # Month 3 Attendance Period (Draft)
            ap3 = AttendancePeriod.objects.filter(organization=org, year=MONTH_3_YEAR, month=MONTH_3_MONTH).first()
            if not ap3:
                ap3 = AttendancePeriod.objects.create(
                    organization=org,
                    year=MONTH_3_YEAR,
                    month=MONTH_3_MONTH,
                    status="Draft",
                    current_revision=0,
                    is_deleted=False,
                )
            else:
                if ap3.is_deleted:
                    ap3.is_deleted = False
                    ap3.save()

            # I. Projects & Scrum Hierarchy
            self.stdout.write("Configuring Projects, Epics, Sprints, Stories, Tasks & Chat Threads...")
            initialize_default_statuses(org)
            status_pending = ProjectStatusOption.objects.filter(company=org, code="pending").first()
            status_inprogress = ProjectStatusOption.objects.filter(company=org, code="in_progress").first()
            status_testing = ProjectStatusOption.objects.filter(company=org, code="testing").first() or status_inprogress
            status_review = ProjectStatusOption.objects.filter(company=org, code="review").first() or status_inprogress
            status_done = ProjectStatusOption.objects.filter(company=org, code="completed").first()

            # 1. Primary Project: OmniChannel Enterprise Portal (DEMO-OEP) - In Progress
            proj_oep, _ = Project.objects.get_or_create(
                company=org,
                key="DEMO-OEP",
                defaults={
                    "name": "OmniChannel Enterprise Portal",
                    "project_type": "Client",
                    "description": f"{DEMO_MARKER} Flagship multi-tenant SaaS portal with agile sprint workflows.",
                    "project_manager": created_employees["DEMO-EMP-003"], # Dennis
                    "team_lead": created_employees["DEMO-EMP-004"],       # Faisal
                    "start_date": datetime_date(2026, 7, 1),
                    "end_date": datetime_date(2026, 12, 31),
                    "progress": 68.5,
                    "created_by": admin_user,
                    "status": status_inprogress,
                }
            )

            # 2. Completed Project: Cloud Native DB Migration & Infrastructure (DEMO-CMP) - Completed (Kazhinja Project)
            proj_cmp, _ = Project.objects.get_or_create(
                company=org,
                key="DEMO-CMP",
                defaults={
                    "name": "Cloud Native DB Migration & Sharding Pipeline",
                    "project_type": "Internal",
                    "description": f"{DEMO_MARKER} Zero-downtime multi-tenant database migration and Kubernetes orchestration.",
                    "project_manager": created_employees["DEMO-EMP-003"],
                    "team_lead": created_employees["DEMO-EMP-004"],
                    "start_date": datetime_date(2026, 5, 1),
                    "end_date": datetime_date(2026, 7, 31),
                    "progress": 100.0,
                    "created_by": admin_user,
                    "status": status_done,
                }
            )

            # 3. QA / Testing Project: Mobile Field Inspection App (DEMO-MFI) - Testing
            proj_mfi, _ = Project.objects.get_or_create(
                company=org,
                key="DEMO-MFI",
                defaults={
                    "name": "Mobile Field Inspection App",
                    "project_type": "Internal",
                    "description": f"{DEMO_MARKER} Offline-first inspection suite with GPS location verification.",
                    "project_manager": created_employees["DEMO-EMP-003"],
                    "team_lead": created_employees["DEMO-EMP-006"],
                    "start_date": datetime_date(2026, 7, 15),
                    "end_date": datetime_date(2026, 11, 30),
                    "progress": 78.0,
                    "created_by": admin_user,
                    "status": status_testing,
                }
            )

            # 4. Review Project: ISO 27001 Security Hardening & SOC2 Audit (DEMO-SEC) - Review
            proj_sec, _ = Project.objects.get_or_create(
                company=org,
                key="DEMO-SEC",
                defaults={
                    "name": "ISO 27001 Security Hardening & SOC2 Audit",
                    "project_type": "Internal",
                    "description": f"{DEMO_MARKER} Enterprise security compliance, penetration testing, and access controls.",
                    "project_manager": created_employees["DEMO-EMP-003"],
                    "team_lead": created_employees["DEMO-EMP-012"],
                    "start_date": datetime_date(2026, 7, 1),
                    "end_date": datetime_date(2026, 10, 15),
                    "progress": 85.0,
                    "created_by": admin_user,
                    "status": status_review,
                }
            )

            # 5. Planning Project: AI Workforce Copilot & Predictive Insights (DEMO-AIX) - Pending/Planning
            proj_aix, _ = Project.objects.get_or_create(
                company=org,
                key="DEMO-AIX",
                defaults={
                    "name": "AI Workforce Copilot & Predictive Insights",
                    "project_type": "Internal",
                    "description": f"{DEMO_MARKER} Next-gen workforce AI assistant for sprint forecasting and anomaly detection.",
                    "project_manager": created_employees["DEMO-EMP-003"],
                    "team_lead": created_employees["DEMO-EMP-005"],
                    "start_date": datetime_date(2026, 9, 15),
                    "end_date": datetime_date(2027, 1, 31),
                    "progress": 12.0,
                    "created_by": admin_user,
                    "status": status_pending,
                }
            )

            # Ensure all demo projects have their explicit status and progress applied
            proj_oep.status = status_inprogress
            proj_oep.progress = 68.5
            proj_oep.save()

            proj_cmp.status = status_done
            proj_cmp.progress = 100.0
            proj_cmp.save()

            proj_mfi.status = status_testing
            proj_mfi.progress = 78.0
            proj_mfi.save()

            proj_sec.status = status_review
            proj_sec.progress = 85.0
            proj_sec.save()

            proj_aix.status = status_pending
            proj_aix.progress = 12.0
            proj_aix.save()

            # Add Project Members
            for emp in created_employees.values():
                ProjectMember.objects.get_or_create(
                    project=proj_oep,
                    user=emp,
                    defaults={"project_role": emp.designation or "Contributor", "department": emp.department}
                )
                ProjectMember.objects.get_or_create(
                    project=proj_cmp,
                    user=emp,
                    defaults={"project_role": emp.designation or "Contributor", "department": emp.department}
                )
                if emp.employee_code in ["DEMO-EMP-003", "DEMO-EMP-006", "DEMO-EMP-007", "DEMO-EMP-010", "DEMO-EMP-011"]:
                    ProjectMember.objects.get_or_create(
                        project=proj_mfi,
                        user=emp,
                        defaults={"project_role": emp.designation or "Contributor", "department": emp.department}
                    )
                if emp.employee_code in ["DEMO-EMP-003", "DEMO-EMP-004", "DEMO-EMP-005", "DEMO-EMP-012"]:
                    ProjectMember.objects.get_or_create(
                        project=proj_sec,
                        user=emp,
                        defaults={"project_role": emp.designation or "Contributor", "department": emp.department}
                    )
                if emp.employee_code in ["DEMO-EMP-003", "DEMO-EMP-005", "DEMO-EMP-007", "DEMO-EMP-008", "DEMO-EMP-015"]:
                    ProjectMember.objects.get_or_create(
                        project=proj_aix,
                        user=emp,
                        defaults={"project_role": emp.designation or "Contributor", "department": emp.department}
                    )

            # Epics for OEP
            epic_auth, _ = ProjectEpic.objects.get_or_create(
                project=proj_oep,
                title="Authentication & Multi-Tenant Security",
                defaults={
                    "company": org,
                    "key": "EPIC-AUTH",
                    "description": f"{DEMO_MARKER} OAuth2, JWT, and relational RBAC capability infrastructure.",
                    "color": "#3b82f6",
                    "status": status_done,
                    "created_by": admin_user,
                }
            )
            epic_scrum, _ = ProjectEpic.objects.get_or_create(
                project=proj_oep,
                title="Real-Time Agile Scrum Board",
                defaults={
                    "company": org,
                    "key": "EPIC-SCRUM",
                    "description": f"{DEMO_MARKER} Interactive Kanban, sprint velocity tracking, and websocket chat.",
                    "color": "#8b5cf6",
                    "status": status_inprogress,
                    "created_by": admin_user,
                }
            )
            epic_billing, _ = ProjectEpic.objects.get_or_create(
                project=proj_oep,
                title="Enterprise Reporting & Automated Billing",
                defaults={
                    "company": org,
                    "key": "EPIC-BILL",
                    "description": f"{DEMO_MARKER} Automated invoice generation, PDF rendering, and Stripe webhooks.",
                    "color": "#10b981",
                    "status": status_pending,
                    "created_by": admin_user,
                }
            )

            # Sprints for OEP
            # Sprint 1: Completed
            sprint1, _ = ProjectSprint.objects.get_or_create(
                project=proj_oep,
                name="Sprint 1: Architecture & Tenant Scoping",
                defaults={
                    "goal": f"{DEMO_MARKER} Foundation setup and tenant isolation benchmarks.",
                    "start_date": datetime_date(2026, 7, 1),
                    "end_date": datetime_date(2026, 7, 21),
                    "started_at": timezone.make_aware(datetime(2026, 7, 1, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 7, 21, 18, 0)),
                    "status": "completed",
                    "capacity": 45,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_oep,
                sprint=sprint1,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.2,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # Sprint 2: Completed
            sprint2, _ = ProjectSprint.objects.get_or_create(
                project=proj_oep,
                name="Sprint 2: Kanban & WebSocket Collaboration",
                defaults={
                    "goal": f"{DEMO_MARKER} Real-time story moving and multi-user chat broadcasting.",
                    "start_date": datetime_date(2026, 7, 22),
                    "end_date": datetime_date(2026, 8, 15),
                    "started_at": timezone.make_aware(datetime(2026, 7, 22, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 8, 15, 18, 0)),
                    "status": "completed",
                    "capacity": 55,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_oep,
                sprint=sprint2,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.6,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # Sprint 3: Active (only 1 active sprint allowed per project constraint)
            sprint3, _ = ProjectSprint.objects.get_or_create(
                project=proj_oep,
                name="Sprint 3: Enterprise File Management & Billing",
                defaults={
                    "goal": f"{DEMO_MARKER} Secure zip uploading, S3 private media, and proration payroll engine.",
                    "start_date": datetime_date(2026, 8, 16),
                    "end_date": datetime_date(2026, 9, 20),
                    "started_at": timezone.make_aware(datetime(2026, 8, 16, 9, 0)),
                    "status": "active",
                    "capacity": 60,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # Sprint 4: Planning
            sprint4, _ = ProjectSprint.objects.get_or_create(
                project=proj_oep,
                name="Sprint 4: AI Insights & Automation",
                defaults={
                    "goal": f"{DEMO_MARKER} Predictive sprint burndown and automated timesheet anomalies.",
                    "start_date": datetime_date(2026, 9, 21),
                    "end_date": datetime_date(2026, 10, 15),
                    "status": "planning",
                    "capacity": 50,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # Stories for OEP
            story_data = [
                {
                    "title": "Tenant Database Routing & Isolated Sharding",
                    "epic": epic_auth,
                    "sprint": sprint1,
                    "points": 8,
                    "status": status_done,
                    "progress": 100.0,
                },
                {
                    "title": "Role-Based Capability Middleware & Permission Checks",
                    "epic": epic_auth,
                    "sprint": sprint1,
                    "points": 5,
                    "status": status_done,
                    "progress": 100.0,
                },
                {
                    "title": "Scrum Board Interactive Column Drag-and-Drop",
                    "epic": epic_scrum,
                    "sprint": sprint2,
                    "points": 8,
                    "status": status_done,
                    "progress": 100.0,
                },
                {
                    "title": "WebSocket Real-time Typing Indicators & Room Broadcasting",
                    "epic": epic_scrum,
                    "sprint": sprint2,
                    "points": 5,
                    "status": status_done,
                    "progress": 100.0,
                },
                {
                    "title": "Zip Extraction & In-Memory Safe Folder Upload",
                    "epic": epic_scrum,
                    "sprint": sprint3,
                    "points": 8,
                    "status": status_inprogress,
                    "progress": 65.0,
                },
                {
                    "title": "Story Burndown Chart & Velocity Mathematical Metrics",
                    "epic": epic_scrum,
                    "sprint": sprint3,
                    "points": 5,
                    "status": status_inprogress,
                    "progress": 40.0,
                },
                {
                    "title": "Automated PDF Payslip Cryptographic Signatures",
                    "epic": epic_billing,
                    "sprint": sprint3,
                    "points": 5,
                    "status": status_pending,
                    "progress": 0.0,
                },
                {
                    "title": "Stripe Webhook Event Reconciler & Idempotency Cache",
                    "epic": epic_billing,
                    "sprint": sprint4,
                    "points": 13,
                    "status": status_pending,
                    "progress": 0.0,
                },
            ]

            created_stories = []
            for s_info in story_data:
                st, _ = ProjectStory.objects.get_or_create(
                    project=proj_oep,
                    title=s_info["title"],
                    defaults={
                        "epic": s_info["epic"],
                        "sprint": s_info["sprint"],
                        "story_points": s_info["points"],
                        "status": s_info["status"],
                        "progress": s_info["progress"],
                        "description": f"{DEMO_MARKER} Detailed specification for {s_info['title']}",
                        "created_by": admin_user,
                    }
                )
                created_stories.append(st)

                # Story Member Assignment
                pm_emp = ProjectMember.objects.filter(project=proj_oep, user=created_employees["DEMO-EMP-005"]).first()
                if pm_emp:
                    ProjectStoryMember.objects.get_or_create(
                        story=st,
                        member=pm_emp,
                        defaults={"assigned_by": admin_user}
                    )

            # Tasks & Subtasks
            tasks_data = [
                # Story 0: Tenant Database Routing (Done)
                {
                    "story": created_stories[0],
                    "title": "Configure multidb routers and connection pool caching",
                    "assignee": created_employees["DEMO-EMP-004"], # Faisal
                    "status": status_done,
                    "priority": "High",
                    "est": 16.0,
                    "logged": 16.0,
                },
                {
                    "story": created_stories[0],
                    "title": "Write isolation test suite verifying cross-tenant zero leakage",
                    "assignee": created_employees["DEMO-EMP-010"], # Meera
                    "status": status_done,
                    "priority": "Urgent",
                    "est": 12.0,
                    "logged": 12.0,
                },
                # Story 1: Role-Based Capability Middleware (Done)
                {
                    "story": created_stories[1],
                    "title": "Build relational PermissionFlag bitmask checks in decorator",
                    "assignee": created_employees["DEMO-EMP-005"], # Vikram
                    "status": status_done,
                    "priority": "High",
                    "est": 8.0,
                    "logged": 8.0,
                },
                {
                    "story": created_stories[1],
                    "title": "Create frontend capability hook usePermissions() in Next.js",
                    "assignee": created_employees["DEMO-EMP-007"], # Rahul
                    "status": status_done,
                    "priority": "Medium",
                    "est": 6.0,
                    "logged": 6.0,
                },
                # Story 2: Scrum Board Column Drag-and-Drop (Done)
                {
                    "story": created_stories[2],
                    "title": "Implement HTML5 Drag-and-Drop with optimistic UI updates",
                    "assignee": created_employees["DEMO-EMP-006"], # Sneha
                    "status": status_done,
                    "priority": "High",
                    "est": 14.0,
                    "logged": 14.0,
                },
                {
                    "story": created_stories[2],
                    "title": "Design Figma tokens and dark mode status badges",
                    "assignee": created_employees["DEMO-EMP-008"], # Priya
                    "status": status_done,
                    "priority": "Medium",
                    "est": 8.0,
                    "logged": 8.0,
                },
                # Story 3: WebSocket Typing Indicators (Done)
                {
                    "story": created_stories[3],
                    "title": "Implement Redis channel layer group send in Django Channels",
                    "assignee": created_employees["DEMO-EMP-005"], # Vikram
                    "status": status_done,
                    "priority": "Medium",
                    "est": 10.0,
                    "logged": 10.0,
                },
                {
                    "story": created_stories[3],
                    "title": "Stress test concurrent websocket connections with locust",
                    "assignee": created_employees["DEMO-EMP-011"], # Sanjay
                    "status": status_done,
                    "priority": "Medium",
                    "est": 8.0,
                    "logged": 8.0,
                },
                # Story 4: Zip Extraction (In Progress)
                {
                    "story": created_stories[4],
                    "title": "Implement Django chunked memory-safe zip streaming",
                    "assignee": created_employees["DEMO-EMP-005"], # Vikram
                    "status": status_inprogress,
                    "priority": "High",
                    "est": 12.0,
                    "logged": 8.5,
                },
                {
                    "story": created_stories[4],
                    "title": "Integrate JSZip folder reader & progress bar in React",
                    "assignee": created_employees["DEMO-EMP-006"], # Sneha
                    "status": status_inprogress,
                    "priority": "High",
                    "est": 10.0,
                    "logged": 7.0,
                },
                {
                    "story": created_stories[4],
                    "title": "Write unit tests for nested folder hierarchy parsing",
                    "assignee": created_employees["DEMO-EMP-015"], # Rohit (Intern)
                    "status": status_inprogress,
                    "priority": "Medium",
                    "est": 8.0,
                    "logged": 4.0,
                },
                # Story 5: Burndown Chart & Velocity (In Progress)
                {
                    "story": created_stories[5],
                    "title": "Build SVG burndown curve with ideal vs actual points line",
                    "assignee": created_employees["DEMO-EMP-007"], # Rahul
                    "status": status_inprogress,
                    "priority": "High",
                    "est": 14.0,
                    "logged": 6.0,
                },
                {
                    "story": created_stories[5],
                    "title": "Draft velocity calculation and historical rolling average API",
                    "assignee": created_employees["DEMO-EMP-004"], # Faisal
                    "status": status_inprogress,
                    "priority": "High",
                    "est": 8.0,
                    "logged": 5.0,
                },
                # Story 6: Automated PDF Payslip Signatures (Pending)
                {
                    "story": created_stories[6],
                    "title": "Evaluate ReportLab PDF digital signature certificates",
                    "assignee": created_employees["DEMO-EMP-012"], # Nithin
                    "status": status_pending,
                    "priority": "Medium",
                    "est": 10.0,
                    "logged": 0.0,
                },
                {
                    "story": created_stories[6],
                    "title": "Design printable responsive payslip template layout",
                    "assignee": created_employees["DEMO-EMP-009"], # Arjun
                    "status": status_pending,
                    "priority": "Low",
                    "est": 6.0,
                    "logged": 0.0,
                },
            ]

            created_tasks = []
            for t_info in tasks_data:
                t_obj, _ = ProjectTask.objects.get_or_create(
                    story=t_info["story"],
                    title=t_info["title"],
                    defaults={
                        "assigned_to": t_info["assignee"],
                        "status": t_info["status"],
                        "priority": t_info["priority"],
                        "estimated_hours": t_info["est"],
                        "logged_hours": t_info["logged"],
                        "created_by": created_employees["DEMO-EMP-003"],
                    }
                )
                created_tasks.append(t_obj)

            # Subtasks
            ProjectSubtask.objects.get_or_create(
                task=created_tasks[8],
                title="Validate path traversal (Zip Slip protection)",
                defaults={"is_completed": True, "created_by": admin_user}
            )
            ProjectSubtask.objects.get_or_create(
                task=created_tasks[8],
                title="Add Celery async background processing for >50MB archives",
                defaults={"is_completed": False, "created_by": admin_user}
            )
            ProjectSubtask.objects.get_or_create(
                task=created_tasks[11],
                title="Configure velocity capacity buffer thresholds",
                defaults={"is_completed": True, "created_by": admin_user}
            )

            # Chat Comments on Tasks and Stories
            comments_data = [
                (created_stories[4], None, created_employees["DEMO-EMP-005"], "Vikram Patel", "Completed the Zip Slip sanitization helper. It raises ValidationError on directory traversals."),
                (created_stories[4], None, created_employees["DEMO-EMP-006"], "Sneha Kulkarni", "Frontend dropzone is integrated with JSZip. Uploads stream seamlessly!"),
                (None, created_tasks[8], created_employees["DEMO-EMP-004"], "Faisal Rahman", "Make sure all uploaded files conform to tenant private storage encryption headers."),
                (None, created_tasks[9], created_employees["DEMO-EMP-008"], "Priya Varma", "Uploaded refined SVG icons for the folder hierarchy preview modal."),
                (created_stories[5], None, created_employees["DEMO-EMP-003"], "Dennis Mathew", "Burndown metrics look sharp for Sprint 3. Let's aim to close Story 4 by Friday!"),
                (created_stories[0], None, created_employees["DEMO-EMP-010"], "Meera Nambiar", "All multi-tenant isolation integration test cases passed with 100% assertion coverage."),
            ]

            for st_target, tk_target, c_user, c_name, c_text in comments_data:
                c_obj, _ = ProjectComment.objects.get_or_create(
                    story=st_target,
                    task=tk_target,
                    user=c_user,
                    comment=f"{DEMO_MARKER} {c_text}",
                )

                # Attach dummy design preview to comment
                if "SVG icons" in c_text:
                    if not ProjectAttachment.objects.filter(comment=c_obj).exists():
                        ProjectAttachment.objects.create(
                            comment=c_obj,
                            file_name="folder_upload_icons_v2.png",
                            file_size=1024 * 64,
                            file=ContentFile(b"Preview Icon Data", name="folder_upload_icons_v2.png"),
                            uploaded_by=c_user,
                            company=org,
                        )

            # ------------------------------------------------------------------
            # 2. Completed Project Hierarchy: DEMO-CMP (Cloud Native Migration)
            # ------------------------------------------------------------------
            epic_cmp, _ = ProjectEpic.objects.get_or_create(
                project=proj_cmp,
                key="EPIC-CMP-HA",
                title="Zero-Downtime PostgreSQL Sharding & HA Clustering",
                defaults={
                    "company": org,
                    "description": f"{DEMO_MARKER} High-throughput clustering, connection pooling, and multi-tenant sharding.",
                    "color": "#10b981",
                    "status": status_done,
                    "created_by": admin_user,
                }
            )

            sprint_cmp1, _ = ProjectSprint.objects.get_or_create(
                project=proj_cmp,
                name="Sprint 1: Schema Auditing & Sharding Pipelines",
                defaults={
                    "goal": f"{DEMO_MARKER} Zero-downtime schema verification and archive automation.",
                    "start_date": datetime_date(2026, 5, 1),
                    "end_date": datetime_date(2026, 6, 15),
                    "started_at": timezone.make_aware(datetime(2026, 5, 1, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 6, 15, 18, 0)),
                    "status": "completed",
                    "capacity": 50,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_cmp,
                sprint=sprint_cmp1,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.8,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            sprint_cmp2, _ = ProjectSprint.objects.get_or_create(
                project=proj_cmp,
                name="Sprint 2: Zero-Downtime Data Cutover & Verification",
                defaults={
                    "goal": f"{DEMO_MARKER} Production cutover with 100% data checksum fidelity.",
                    "start_date": datetime_date(2026, 6, 16),
                    "end_date": datetime_date(2026, 7, 31),
                    "started_at": timezone.make_aware(datetime(2026, 6, 16, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 7, 31, 18, 0)),
                    "status": "completed",
                    "capacity": 55,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_cmp,
                sprint=sprint_cmp2,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.9,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            story_cmp1, _ = ProjectStory.objects.get_or_create(
                project=proj_cmp,
                title="Automate PostgreSQL Partitioning & Archive Triggers",
                defaults={
                    "epic": epic_cmp,
                    "sprint": sprint_cmp1,
                    "story_points": 8,
                    "status": status_done,
                    "progress": 100.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Automatic table partitioning by tenant and date boundaries.",
                }
            )
            story_cmp2, _ = ProjectStory.objects.get_or_create(
                project=proj_cmp,
                title="Zero-Downtime Replication & Read-Replica Failover",
                defaults={
                    "epic": epic_cmp,
                    "sprint": sprint_cmp2,
                    "story_points": 13,
                    "status": status_done,
                    "progress": 100.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Automatic failover handling with Patroni and PgBouncer.",
                }
            )

            ProjectTask.objects.get_or_create(
                story=story_cmp1,
                title="Configure partition maintenance daemon & pg_partman",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-004"], # Faisal
                    "status": status_done,
                    "priority": "High",
                    "estimated_hours": 16.0,
                    "logged_hours": 16.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectTask.objects.get_or_create(
                story=story_cmp2,
                title="Execute replica failover cutover dry-run in staging",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-005"], # Vikram
                    "status": status_done,
                    "priority": "Urgent",
                    "estimated_hours": 14.0,
                    "logged_hours": 14.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # ------------------------------------------------------------------
            # 3. Testing Project Hierarchy: DEMO-MFI (Mobile Field Inspection)
            # ------------------------------------------------------------------
            epic_mfi, _ = ProjectEpic.objects.get_or_create(
                project=proj_mfi,
                key="EPIC-MFI-OFFLINE",
                title="Offline Mobile Architecture & Geofencing Telemetry",
                defaults={
                    "company": org,
                    "description": f"{DEMO_MARKER} Flutter local caching and location tamper detection.",
                    "color": "#06b6d4",
                    "status": status_testing,
                    "created_by": admin_user,
                }
            )

            sprint_mfi1, _ = ProjectSprint.objects.get_or_create(
                project=proj_mfi,
                name="Sprint 1: Offline SQLite & Local Queue Storage",
                defaults={
                    "goal": f"{DEMO_MARKER} Offline SQLite storage and automatic synchronization queue.",
                    "start_date": datetime_date(2026, 7, 15),
                    "end_date": datetime_date(2026, 8, 15),
                    "started_at": timezone.make_aware(datetime(2026, 7, 15, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 8, 15, 18, 0)),
                    "status": "completed",
                    "capacity": 40,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_mfi,
                sprint=sprint_mfi1,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.5,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            sprint_mfi2, _ = ProjectSprint.objects.get_or_create(
                project=proj_mfi,
                name="Sprint 2: Geofencing & Real-Time Inspection Workflows",
                defaults={
                    "goal": f"{DEMO_MARKER} Real-time GPS radius verification and inspection checklists.",
                    "start_date": datetime_date(2026, 8, 16),
                    "end_date": datetime_date(2026, 9, 30),
                    "started_at": timezone.make_aware(datetime(2026, 8, 16, 9, 0)),
                    "status": "active",
                    "capacity": 50,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            story_mfi1, _ = ProjectStory.objects.get_or_create(
                project=proj_mfi,
                title="SQLite Background Queue Sync with Exponential Backoff",
                defaults={
                    "epic": epic_mfi,
                    "sprint": sprint_mfi1,
                    "story_points": 5,
                    "status": status_done,
                    "progress": 100.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Resilient synchronization worker across intermittent 4G/5G.",
                }
            )
            story_mfi2, _ = ProjectStory.objects.get_or_create(
                project=proj_mfi,
                title="GPS Geofencing Verification & Tamper Detection",
                defaults={
                    "epic": epic_mfi,
                    "sprint": sprint_mfi2,
                    "story_points": 8,
                    "status": status_testing,
                    "progress": 75.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Mock location prevention on Android & iOS devices.",
                }
            )

            ProjectTask.objects.get_or_create(
                story=story_mfi1,
                title="Build background sync worker in Flutter",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-007"], # Rahul
                    "status": status_done,
                    "priority": "High",
                    "estimated_hours": 12.0,
                    "logged_hours": 12.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectTask.objects.get_or_create(
                story=story_mfi2,
                title="Perform edge testing on mock GPS and developer mode bypasses",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-010"], # Meera
                    "status": status_testing,
                    "priority": "High",
                    "estimated_hours": 10.0,
                    "logged_hours": 8.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # ------------------------------------------------------------------
            # 4. Review Project Hierarchy: DEMO-SEC (ISO 27001 Security Audit)
            # ------------------------------------------------------------------
            epic_sec, _ = ProjectEpic.objects.get_or_create(
                project=proj_sec,
                key="EPIC-SEC-SOC2",
                title="SOC2 Compliance Controls & SIEM Integration",
                defaults={
                    "company": org,
                    "description": f"{DEMO_MARKER} Automated penetration testing and immutable audit logs.",
                    "color": "#f59e0b",
                    "status": status_review,
                    "created_by": admin_user,
                }
            )

            sprint_sec1, _ = ProjectSprint.objects.get_or_create(
                project=proj_sec,
                name="Sprint 1: Penetration Testing & Vulnerability Fixes",
                defaults={
                    "goal": f"{DEMO_MARKER} OWASP Top 10 remediation and TLS 1.3 enforcement.",
                    "start_date": datetime_date(2026, 7, 1),
                    "end_date": datetime_date(2026, 8, 10),
                    "started_at": timezone.make_aware(datetime(2026, 7, 1, 9, 0)),
                    "completed_at": timezone.make_aware(datetime(2026, 8, 10, 18, 0)),
                    "status": "completed",
                    "capacity": 45,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectRetrospective.objects.get_or_create(
                project=proj_sec,
                sprint=sprint_sec1,
                defaults={
                    "status": "completed",
                    "happiness_score": 4.7,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            sprint_sec2, _ = ProjectSprint.objects.get_or_create(
                project=proj_sec,
                name="Sprint 2: SOC2 Type II Evidence Review & Policy Enforcement",
                defaults={
                    "goal": f"{DEMO_MARKER} Auditor evidence collection and automated access logs.",
                    "start_date": datetime_date(2026, 8, 11),
                    "end_date": datetime_date(2026, 10, 15),
                    "started_at": timezone.make_aware(datetime(2026, 8, 11, 9, 0)),
                    "status": "active",
                    "capacity": 50,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            story_sec1, _ = ProjectStory.objects.get_or_create(
                project=proj_sec,
                title="Remediate Web Application Vulnerabilities & Enforce HSTS",
                defaults={
                    "epic": epic_sec,
                    "sprint": sprint_sec1,
                    "story_points": 5,
                    "status": status_done,
                    "progress": 100.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Hardening security response headers across all ingress controllers.",
                }
            )
            story_sec2, _ = ProjectStory.objects.get_or_create(
                project=proj_sec,
                title="Automated Immutable Audit Trail Streaming to SIEM",
                defaults={
                    "epic": epic_sec,
                    "sprint": sprint_sec2,
                    "story_points": 8,
                    "status": status_review,
                    "progress": 85.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Real-time SIEM streaming with cryptographic hash validation.",
                }
            )

            ProjectTask.objects.get_or_create(
                story=story_sec1,
                title="Configure automated DAST scans in GitHub Actions",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-012"], # Nithin
                    "status": status_done,
                    "priority": "High",
                    "estimated_hours": 10.0,
                    "logged_hours": 10.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )
            ProjectTask.objects.get_or_create(
                story=story_sec2,
                title="Audit trail retention and access compliance review",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-004"], # Faisal
                    "status": status_review,
                    "priority": "Medium",
                    "estimated_hours": 8.0,
                    "logged_hours": 6.5,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # ------------------------------------------------------------------
            # 5. Planning Project Hierarchy: DEMO-AIX (AI Workforce Copilot)
            # ------------------------------------------------------------------
            epic_aix, _ = ProjectEpic.objects.get_or_create(
                project=proj_aix,
                key="EPIC-AIX-RAG",
                title="Retrieval-Augmented Generation & Vector Architecture",
                defaults={
                    "company": org,
                    "description": f"{DEMO_MARKER} Vector database search and sprint burndown forecasting.",
                    "color": "#a855f7",
                    "status": status_pending,
                    "created_by": admin_user,
                }
            )

            sprint_aix1, _ = ProjectSprint.objects.get_or_create(
                project=proj_aix,
                name="Sprint 1: Context Embeddings & Vector Store Architecture",
                defaults={
                    "goal": f"{DEMO_MARKER} Prototype semantic retrieval over company handbook & sprint specs.",
                    "start_date": datetime_date(2026, 9, 21),
                    "end_date": datetime_date(2026, 10, 31),
                    "status": "planning",
                    "capacity": 45,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            story_aix1, _ = ProjectStory.objects.get_or_create(
                project=proj_aix,
                title="Evaluate Vector DB Latency & Chunking Strategies",
                defaults={
                    "epic": epic_aix,
                    "sprint": sprint_aix1,
                    "story_points": 5,
                    "status": status_pending,
                    "progress": 10.0,
                    "created_by": admin_user,
                    "description": f"{DEMO_MARKER} Test hybrid search benchmarks with reciprocal rank fusion.",
                }
            )

            ProjectTask.objects.get_or_create(
                story=story_aix1,
                title="Benchmark pgvector vs Qdrant under 10k concurrent docs",
                defaults={
                    "assigned_to": created_employees["DEMO-EMP-005"], # Vikram
                    "status": status_pending,
                    "priority": "Medium",
                    "estimated_hours": 10.0,
                    "logged_hours": 0.0,
                    "created_by": created_employees["DEMO-EMP-003"],
                }
            )

            # J. Payroll & Salary Components
            self.stdout.write("Configuring salary components & employee salary structures...")
            comp_basic, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_BASIC",
                defaults={"name": "Basic Salary", "component_type": "Earning", "is_taxable": True, "is_proratable": True}
            )
            comp_hra, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_HRA",
                defaults={"name": "House Rent Allowance", "component_type": "Earning", "is_taxable": True, "is_proratable": False}
            )
            comp_trans, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_TRANSPORT",
                defaults={"name": "Transport Allowance", "component_type": "Earning", "is_taxable": False, "is_proratable": True}
            )
            comp_spec, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_SPECIAL",
                defaults={"name": "Special Allowance", "component_type": "Earning", "is_taxable": True, "is_proratable": True}
            )
            comp_pf, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_PF",
                defaults={"name": "Provident Fund", "component_type": "Deduction", "is_taxable": False, "is_proratable": False}
            )
            comp_ptax, _ = SalaryComponent.objects.get_or_create(
                organization=org,
                code="DEMO_PTAX",
                defaults={"name": "Professional Tax", "component_type": "Deduction", "is_taxable": False, "is_proratable": False}
            )

            # Assign Salary Structures to All 17 Employees
            struct_eff_date = datetime_date(2026, 1, 1)
            for cfg in DEMO_EMPLOYEES_CONFIG:
                emp = created_employees[cfg["code"]]
                comp_type = cfg.get("compensation_type", "MONTHLY")
                daily_rate = cfg.get("daily_rate")
                hourly_rate = cfg.get("hourly_rate")
                sal = cfg.get("salary", {})

                if comp_type == "DAILY":
                    components_payload = []
                elif comp_type == "HOURLY":
                    components_payload = [
                        {"salary_component_id": comp_trans.id, "amount": sal["transport"]}
                    ] if "transport" in sal else []
                else:
                    components_payload = [
                        {"salary_component_id": comp_basic.id, "amount": sal.get("basic", Decimal("30000.00"))},
                        {"salary_component_id": comp_hra.id, "amount": sal.get("hra", Decimal("12000.00"))},
                        {"salary_component_id": comp_trans.id, "amount": sal.get("transport", Decimal("4000.00"))},
                        {"salary_component_id": comp_spec.id, "amount": sal.get("special", Decimal("6000.00"))},
                        {"salary_component_id": comp_pf.id, "amount": sal.get("pf", Decimal("2000.00"))},
                        {"salary_component_id": comp_ptax.id, "amount": sal.get("ptax", Decimal("200.00"))},
                    ]

                # Ensure structure exists without violating UniqueConstraint
                existing_struct = EmployeeSalaryStructure.objects.filter(
                    employee=emp,
                    organization=org,
                    effective_from=struct_eff_date
                ).first()

                if not existing_struct:
                    assign_or_revise_salary_structure(
                        employee=emp,
                        organization=org,
                        effective_from=struct_eff_date,
                        components_data=components_payload,
                        notes=f"{DEMO_MARKER} Standard compensation structure ({comp_type})",
                        created_by=admin_user,
                        compensation_type=comp_type,
                        daily_rate=daily_rate,
                        hourly_rate=hourly_rate
                    )

            # Ensure any pre-existing non-demo active employee has a baseline structure so payroll runs clean
            for non_demo_emp in Employee.objects.filter(organization=org, is_active=True).exclude(email__endswith=f"@{DEMO_EMAIL_DOMAIN}"):
                if not EmployeeSalaryStructure.objects.filter(employee=non_demo_emp, is_active=True).exists():
                    assign_or_revise_salary_structure(
                        employee=non_demo_emp,
                        organization=org,
                        effective_from=struct_eff_date,
                        components_data=[{"salary_component_id": comp_basic.id, "amount": Decimal("50000.00")}],
                        notes=f"{DEMO_MARKER} Baseline salary structure",
                        created_by=admin_user
                    )

            # K. Run 3 Months of Payroll
            # Month 1 (July 2026): Calculated, Finalized, Paid
            self.stdout.write("Calculating & Finalizing Payroll for July 2026 (Month 1)...")
            pr1_existing = PayrollPeriod.objects.filter(organization=org, year=MONTH_1_YEAR, month=MONTH_1_MONTH).first()
            if pr1_existing and pr1_existing.status == 'Finalized':
                pr1_existing.status = 'Draft'
                pr1_existing.save()

            payroll_1 = calculate_payroll_period(org, MONTH_1_YEAR, MONTH_1_MONTH, admin_user)
            payroll_1 = finalize_payroll_period(org, MONTH_1_YEAR, MONTH_1_MONTH, admin_user)

            # Record Salary Payments for Month 1 (Paid via Bank Transfer)
            for snap in PayrollEmployeeSnapshot.objects.filter(payroll_period=payroll_1, is_current=True):
                if snap.net_payable > Decimal("0.00"):
                    SalaryPayment.objects.get_or_create(
                        payroll_snapshot=snap,
                        defaults={
                            "organization": org,
                            "payroll_period": payroll_1,
                            "employee": snap.employee,
                            "paid_amount": snap.net_payable,
                            "paid_at": datetime_date(2026, 8, 1),
                            "payment_method": "BankTransfer",
                            "transaction_reference": f"TXN-JUL26-{snap.employee.id}",
                            "status": "Paid",
                            "recorded_by": admin_user,
                        }
                    )

            # Month 2 (August 2026): Bonus Adjustment + Finalized + Paid
            self.stdout.write("Calculating & Finalizing Payroll for August 2026 (Month 2) with Bonus Adjustments...")
            pr2_existing = PayrollPeriod.objects.filter(organization=org, year=MONTH_2_YEAR, month=MONTH_2_MONTH).first()
            if pr2_existing and pr2_existing.status == 'Finalized':
                pr2_existing.status = 'Draft'
                pr2_existing.save()

            payroll_2 = calculate_payroll_period(org, MONTH_2_YEAR, MONTH_2_MONTH, admin_user)

            # Add ₹5,000 Performance Bonus for Vikram Patel & Sneha Kulkarni
            emp_vikram = created_employees["DEMO-EMP-005"]
            PayrollAdjustment.objects.filter(payroll_period=payroll_2, employee=emp_vikram).delete()
            PayrollAdjustment.objects.create(
                organization=org,
                payroll_period=payroll_2,
                employee=emp_vikram,
                adjustment_type="Earning",
                category="Bonus",
                amount=Decimal("5000.00"),
                description=f"{DEMO_MARKER} Performance Bonus - Sprint 2 Deliverables",
                created_by=admin_user,
            )

            payroll_2 = calculate_payroll_period(org, MONTH_2_YEAR, MONTH_2_MONTH, admin_user)
            payroll_2 = finalize_payroll_period(org, MONTH_2_YEAR, MONTH_2_MONTH, admin_user)

            # Record Salary Payments for Month 2 (Paid)
            for snap in PayrollEmployeeSnapshot.objects.filter(payroll_period=payroll_2, is_current=True):
                if snap.net_payable > Decimal("0.00"):
                    SalaryPayment.objects.get_or_create(
                        payroll_snapshot=snap,
                        defaults={
                            "organization": org,
                            "payroll_period": payroll_2,
                            "employee": snap.employee,
                            "paid_amount": snap.net_payable,
                            "paid_at": datetime_date(2026, 9, 1),
                            "payment_method": "BankTransfer",
                            "transaction_reference": f"TXN-AUG26-{snap.employee.id}",
                            "status": "Paid",
                            "recorded_by": admin_user,
                        }
                    )

            # Month 3 (September 2026): Draft / Pending Salary Release
            self.stdout.write("Processing September 2026 Payroll (Draft / Pending Payment Release)...")
            pr3 = PayrollPeriod.objects.filter(organization=org, year=MONTH_3_YEAR, month=MONTH_3_MONTH).first()
            if not pr3:
                PayrollPeriod.objects.create(
                    organization=org,
                    year=MONTH_3_YEAR,
                    month=MONTH_3_MONTH,
                    status="Draft",
                    attendance_period=ap3,
                    is_deleted=False,
                )
            else:
                if pr3.is_deleted:
                    pr3.is_deleted = False
                    pr3.attendance_period = ap3
                    pr3.save()

        # L. Final Summary Presentation
        self.print_summary_report(org, created_employees)

    # ----------------------------------------------------------------------------
    # Terminal Presentation Report
    # ----------------------------------------------------------------------------
    def print_summary_report(self, org, employees):
        self.stdout.write("\n" + "=" * 110)
        self.stdout.write(self.style.SUCCESS("  CUBELOGS PRODUCTION DEMO DATASET SEEDED SUCCESSFULLY"))
        self.stdout.write("=" * 110)
        self.stdout.write(f"  Organization: {org.name} (ID: {org.id})")
        self.stdout.write(f"  Common Login Password: {COMMON_PASSWORD}")
        self.stdout.write(f"  Data Coverage: July 2026 -> September 2026 (Last 3 Months)")
        self.stdout.write("-" * 110)
        self.stdout.write(f"{'Code':<14} | {'Email':<34} | {'Name':<16} | {'Designation':<28} | {'Status':<10}")
        self.stdout.write("-" * 110)

        for cfg in DEMO_EMPLOYEES_CONFIG:
            c = cfg["code"]
            em = cfg["email"]
            nm = f"{cfg['first_name']} {cfg['last_name']}"
            des = cfg["designation"][:27]
            st = cfg["status"]
            self.stdout.write(f"{c:<14} | {em:<34} | {nm:<16} | {des:<28} | {st:<10}")

        self.stdout.write("-" * 110)
        self.stdout.write("  KEY HIGHLIGHTS & TEST SCENARIOS:")
        self.stdout.write("  1. Resigned Employee: Divya Suresh (DEMO-EMP-016) - Resigned Aug 25, 2026 (with audit log).")
        self.stdout.write("  2. Terminated Employee: Karthik Mohan (DEMO-EMP-017) - Terminated July 20, 2026 (with audit log).")
        self.stdout.write("  3. Daily Wage: Sanjay Kumar (DEMO-EMP-011) - INR 2,500/day wage structure.")
        self.stdout.write("  4. Hourly Wage: Rohit Verma (DEMO-EMP-015) - INR 300/hr multi-session attendance logs.")
        self.stdout.write("  5. Projects & Sprints: DEMO-OEP (Sprint 1 & 2 completed with retros, Sprint 3 active, Sprint 4 backlog).")
        self.stdout.write("  6. Payroll: July 2026 (Paid), August 2026 (Paid with +INR 5,000 Bonus), September 2026 (Pending Release).")
        self.stdout.write("  7. Quick Cleanup Command: python manage.py seed_full_demo --clean")
        self.stdout.write("=" * 110 + "\n")
