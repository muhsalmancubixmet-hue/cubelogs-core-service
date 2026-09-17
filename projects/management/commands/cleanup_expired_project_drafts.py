from django.core.management.base import BaseCommand
from django.utils import timezone
from projects.models import ProjectAttachment


class Command(BaseCommand):
    help = "Deletes expired temporary rich text draft attachments."

    def handle(self, *args, **options):
        now = timezone.now()
        expired = ProjectAttachment.objects.filter(
            is_temporary=True,
            expires_at__lt=now,
            project__isnull=True,
            epic__isnull=True,
            story__isnull=True,
            task__isnull=True,
        )
        count = expired.count()
        for att in expired:
            att.delete()
        self.stdout.write(self.style.SUCCESS(f"Successfully cleaned up {count} expired draft attachments."))
