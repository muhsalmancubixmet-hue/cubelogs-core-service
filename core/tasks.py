# --------------------------------------------------------------------------------
#       Core Tasks (including Email dispatch)
# --------------------------------------------------------------------------------

import logging
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

import re

def sanitize_email_log_content(text):
    if not text or not isinstance(text, str):
        return text
    text = re.sub(r'(<strong>\s*Password:\s*</strong>\s*)([^\s<]+)', r'\1[PROTECTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'(Password:\s*)([^\s<]+)', r'\1[PROTECTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'(token=)[A-Za-z0-9._:\-]+', r'\1[PROTECTED_TOKEN]', text)
    return text

@shared_task
def send_email_task(recipient, subject, body, from_email=None, html_body=None):
    from core.models import EmailLog
    log = EmailLog.objects.create(
        recipient=recipient,
        subject=subject,
        body=sanitize_email_log_content(body or html_body),
        from_email=from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        status='PENDING'
    )
    try:
        send_mail(
            subject=subject,
            message=body or '',
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_body,
        )
        log.status = 'SENT'
        log.sent_at = timezone.now()
        log.save()
        logger.info(f"Email successfully sent to {recipient}")
    except Exception as exc:
        log.status = 'FAILED'
        log.error_message = str(exc)
        log.save()
        logger.error(f"Failed to send email to {recipient}. Error: {exc}")
        raise exc

@shared_task
def send_transactional_email_task(recipient, subject, html_content):
    from core.models import EmailLog
    log = EmailLog.objects.create(
        recipient=recipient,
        subject=subject,
        body=sanitize_email_log_content(html_content),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        status='PENDING'
    )
    try:
        send_mail(
            subject=subject,
            message="Please view this email in an HTML-compatible client.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_content,
        )
        log.status = 'SENT'
        log.sent_at = timezone.now()
        log.save()
        logger.info(f"Transactional email successfully sent to {recipient}")
    except Exception as exc:
        log.status = 'FAILED'
        log.error_message = str(exc)
        log.save()
        logger.error(f"Failed to send transactional email to {recipient}. Error: {exc}")
        raise exc

class EmailService:
    @staticmethod
    def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
        try:
            send_email_task.delay(recipient, subject, body, from_email, html_body)
        except Exception as e:
            logger.error(f"Failed to queue celery email task: {e}")
            from core.models import EmailLog
            EmailLog.objects.create(
                recipient=recipient,
                subject=subject,
                body=body or html_body,
                from_email=from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                status='FAILED',
                error_message=f"Queue failure: {str(e)}"
            )

    @staticmethod
    def send_transactional_email(recipient, subject, html_content, template_type=None, password=None, synchronous=False):
        if synchronous or password is not None:
            from core.models import EmailLog
            sanitized_body = sanitize_email_log_content(html_content)
            log = EmailLog.objects.create(
                recipient=recipient,
                subject=subject,
                body=sanitized_body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                status='PENDING'
            )
            try:
                send_mail(
                    subject=subject,
                    message="Please view this email in an HTML-compatible client.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                    html_message=html_content,
                )
                log.status = 'SENT'
                log.sent_at = timezone.now()
                log.save()
            except Exception as e:
                log.status = 'FAILED'
                log.error_message = str(e)
                log.save()
                logger.error(f"Failed to send synchronous credential email: {e}")
                raise e
        else:
            try:
                send_transactional_email_task.delay(recipient, subject, html_content)
            except Exception as e:
                logger.error(f"Failed to queue transactional email task: {e}")

# Maintain compatibility for modules importing queue_and_send_email directly
def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
    return EmailService.queue_and_send_email(recipient, subject, body, from_email, html_body)

