# --------------------------------------------------------------------------------
#       Core Tasks (including Email dispatch)
# --------------------------------------------------------------------------------

import logging
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

@shared_task
def send_email_task(recipient, subject, body, from_email=None, html_body=None):
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_body,
        )
        logger.info(f"Email successfully sent to {recipient}")
    except Exception as exc:
        logger.error(f"Failed to send email to {recipient}. Error: {exc}")
        raise exc

@shared_task
def send_transactional_email_task(recipient, subject, html_content):
    try:
        send_mail(
            subject=subject,
            message="Please view this email in an HTML-compatible client.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_content,
        )
        logger.info(f"Transactional email successfully sent to {recipient}")
    except Exception as exc:
        logger.error(f"Failed to send transactional email to {recipient}. Error: {exc}")
        raise exc

class EmailService:
    @staticmethod
    def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
        try:
            send_email_task.delay(recipient, subject, body, from_email, html_body)
        except Exception as e:
            logger.error(f"Failed to queue celery email task: {e}")

    @staticmethod
    def send_transactional_email(recipient, subject, html_content, template_type=None, password=None, synchronous=False):
        if synchronous:
            try:
                send_mail(
                    subject=subject,
                    message="Please view this email in an HTML-compatible client.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                    html_message=html_content,
                )
            except Exception as e:
                logger.error(f"Failed to send synchronous email: {e}")
                raise e
        else:
            try:
                send_transactional_email_task.delay(recipient, subject, html_content)
            except Exception as e:
                logger.error(f"Failed to queue transactional email task: {e}")

# Maintain compatibility for modules importing queue_and_send_email directly
def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
    return EmailService.queue_and_send_email(recipient, subject, body, from_email, html_body)
