# --------------------------------------------------------------------------------
#       Management Command: Load Demo Scrum Project
# --------------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from projects.demo_seed import load_demo_project


class Command(BaseCommand):
    help = "Loads a complete, realistic demo Scrum project ('Food Delivery Mobile App', Key: FDA) to showcase full CubeLogs workflow."

    def handle(self, *args, **options):
        try:
            load_demo_project()
            self.stdout.write(self.style.SUCCESS("Successfully loaded demo Scrum project."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error loading demo project: {str(e)}"))
            raise e
