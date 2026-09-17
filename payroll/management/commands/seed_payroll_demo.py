# --------------------------------------------------------------------------------
#       Management Command: Seed / Remove Payroll & Attendance Demo Dataset
# --------------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from payroll.demo_seed import (
    seed_demo_dataset,
    remove_demo_dataset,
    DEMO_EMPLOYEES_CONFIG,
    DEMO_YEAR,
    DEMO_MONTH,
)


class Command(BaseCommand):
    help = (
        "Seeds a realistic, self-contained 5-employee Attendance & Payroll demo dataset "
        "for July 2026 into an existing organization. Use --remove to completely delete the demo data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--remove',
            action='store_true',
            dest='remove',
            default=False,
            help='Removes all demo dataset records (employees, logs, snapshots, salary structures, payslips).'
        )
        parser.add_argument(
            '--org-id',
            type=int,
            dest='org_id',
            default=None,
            help='Optional target organization ID. Defaults to the active dev organization.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Simulates the operation without making any database changes.'
        )

    def handle(self, *args, **options):
        is_remove = options.get('remove', False)
        org_id = options.get('org_id')
        dry_run = options.get('dry_run', False)

        if is_remove:
            self.stdout.write(self.style.NOTICE(f"Initiating demo dataset cleanup... (Dry Run: {dry_run})"))
            try:
                result = remove_demo_dataset(org_id=org_id, dry_run=dry_run)
                if result["status"] == "success":
                    self.stdout.write(self.style.SUCCESS(
                        f"\nDEMO DATASET REMOVED SUCCESSFULLY from organization '{result['organization'].name}' (ID: {result['organization'].id})\n"
                    ))
                    self.stdout.write("Deleted record counts:")
                    for k, v in result["counts"].items():
                        self.stdout.write(f"  - {k}: {v}")
                elif result["status"] == "dry_run":
                    self.stdout.write(self.style.WARNING(f"\n{result['message']}"))
                else:
                    self.stdout.write(self.style.WARNING(f"\n{result['message']}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error during demo dataset cleanup: {str(e)}"))
                raise e
        else:
            self.stdout.write(self.style.NOTICE(f"Initiating July {DEMO_YEAR} demo dataset creation... (Dry Run: {dry_run})"))
            try:
                result = seed_demo_dataset(org_id=org_id, dry_run=dry_run)
                org = result["organization"]

                if result["status"] == "already_seeded":
                    self.stdout.write(self.style.WARNING(f"\n{result['message']}"))
                    self.stdout.write(self.style.NOTICE("To refresh or reset, run with --remove first:\n  python manage.py seed_payroll_demo --remove\n"))
                    return

                if result["status"] == "dry_run":
                    self.stdout.write(self.style.WARNING(f"\n{result['message']}"))
                    return

                att_period = result["attendance_period"]
                payroll_period = result["payroll_period"]

                self.stdout.write("\n" + "=" * 65)
                self.stdout.write(self.style.SUCCESS("DEMO PAYROLL DATASET READY"))
                self.stdout.write("=" * 65)
                self.stdout.write(f"Organization: {org.name} (ID: {org.id})")
                self.stdout.write(f"Period: July {DEMO_YEAR}")
                self.stdout.write(f"Employees: 5")
                self.stdout.write(f"Attendance: Finalized Rev: {att_period.current_revision if hasattr(att_period, 'current_revision') else 1}")
                self.stdout.write(f"Payroll: Finalized Rev: {payroll_period.current_revision if hasattr(payroll_period, 'current_revision') else 1}")
                self.stdout.write(f"Payslips: Issued: {result['payslip_count']}")

                self.stdout.write("\nDemo employees seeded:")
                for cfg in DEMO_EMPLOYEES_CONFIG:
                    self.stdout.write(f"  [{cfg['code']}] {cfg['first_name']} {cfg['last_name']} ({cfg['designation']}) - {cfg['email']}")
                    self.stdout.write(f"       Scenario: {cfg['scenario']}")

                self.stdout.write("\nUseful pages:")
                self.stdout.write("  /attendance")
                self.stdout.write("  /attendance/management-portal")
                self.stdout.write("  /payroll")
                self.stdout.write("  /payroll/salaries")
                self.stdout.write("  /payroll/components")
                self.stdout.write("  /payroll/payslips")

                self.stdout.write("\nCleanup command:")
                self.stdout.write("  python manage.py seed_payroll_demo --remove\n")

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error during demo dataset creation: {str(e)}"))
                raise e
