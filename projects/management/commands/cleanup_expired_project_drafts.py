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
            if att.file:
                try:
                    att.file.delete(save=False)
                except Exception as e:
                    self.stderr.write(f"Failed to delete file {att.file_name}: {e}")
        expired.delete()
        self.stdout.write(self.style.SUCCESS(f"Successfully cleaned up {count} expired draft attachments."))
