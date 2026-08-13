# --------------------------------------------------------------------------------
#       Management Command: Delete Demo Scrum Project
# --------------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from projects.demo_seed import delete_demo_project


class Command(BaseCommand):
    help = "Completely deletes the demo Scrum project ('Food Delivery Mobile App', Key: FDA) and all associated demo data without touching real user data."

    def handle(self, *args, **options):
        try:
            count = delete_demo_project()
            if count > 0:
                self.stdout.write(self.style.SUCCESS(f"Successfully deleted {count} demo project(s) and all associated demo data."))
            else:
                self.stdout.write(self.style.NOTICE("No demo project was found to delete."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error deleting demo project: {str(e)}"))
            raise e
