# api/tasks/email.py
try:
    from celery import shared_task
except ImportError:
    def shared_task(func):
        return func

from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail
from api.models import EmailQueue, EmailLog
import logging

logger = logging.getLogger(__name__)

def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
    """
    Helper function to log an email in EmailQueue and dispatch it via Celery task.
    """
    email_log = EmailQueue.objects.create(
        recipient=recipient,
        from_email=from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@cubelogs.com'),
        subject=subject,
        body=body,
        html_body=html_body,
        status='PENDING'
    )
    try:
        result = send_queued_emailqueue_task.delay(email_log.id)
        email_log.task_id = result.id
        email_log.save()
    except Exception as e:
        logger.error(f"Failed to queue email task: {e}")
        email_log.status = 'FAILED'
        email_log.error_message = f"Failed to queue celery task. Error: {e}"
        email_log.save()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_queued_emailqueue_task(self, email_log_id):
    """
    Asynchronously sends an email logged in the EmailQueue.
    Supports retries, tracks and updates status (SENT, FAILED, RETRYING).
    """
    logger.info(f"Starting email dispatch for EmailQueue ID: {email_log_id}")

    try:
        email_log = EmailQueue.objects.get(pk=email_log_id)
    except EmailQueue.DoesNotExist:
        logger.error(f"EmailQueue with ID {email_log_id} does not exist.")
        return

    email_log.task_id = self.request.id
    email_log.status = 'RETRYING' if self.request.retries > 0 else 'PENDING'
    email_log.save()

    try:
        send_mail(
            subject=email_log.subject,
            message=email_log.body,
            from_email=email_log.from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@cubelogs.com'),
            recipient_list=[email_log.recipient],
            fail_silently=False,
            html_message=email_log.html_body,
        )
        email_log.status = 'SENT'
        email_log.sent_at = timezone.now()
        email_log.error_message = None
        email_log.save()
        logger.info(f"EmailQueue ID {email_log_id} successfully sent to {email_log.recipient}")
    except Exception as exc:
        logger.warning(f"Failed to send EmailQueue ID {email_log_id} to {email_log.recipient} on attempt {self.request.retries + 1}. Error: {exc}")
        email_log.error_message = str(exc)
        email_log.save()

        from celery.exceptions import Retry
        try:
            self.retry(exc=exc)
        except Retry:
            raise
        except Exception as retry_exc:
            email_log.status = 'FAILED'
            email_log.error_message = f"Max retries reached. Last exception: {exc}"
            email_log.save()
            logger.error(f"EmailQueue ID {email_log_id} marked as FAILED. Max retries exceeded.")
            raise retry_exc


@shared_task
def send_queued_email_task(email_log_id):
    """
    Asynchronously sends a transactional HTML email logged in EmailLog.
    """
    logger.info(f"Starting email dispatch for EmailLog ID: {email_log_id}")

    try:
        email_log = EmailLog.objects.get(pk=email_log_id)
    except EmailLog.DoesNotExist:
        logger.error(f"EmailLog with ID {email_log_id} does not exist.")
        return

    try:
        send_mail(
            subject=email_log.subject,
            message="Please view this email in an HTML-compatible client.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email_log.recipient],
            fail_silently=False,
            html_message=email_log.html_content,
        )
        email_log.status = 'SENT'
        email_log.sent_at = timezone.now()
        email_log.error_message = None
        email_log.save()
        logger.info(f"EmailLog ID {email_log_id} successfully sent to {email_log.recipient}")
    except Exception as exc:
        logger.error(f"Failed to send EmailLog ID {email_log_id} to {email_log.recipient}. Error: {exc}")
        email_log.status = 'FAILED'
        email_log.error_message = str(exc)
        email_log.save()
