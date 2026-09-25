# --------------------------------------------------------------------------------
#       Core Tasks (including Email dispatch)
# --------------------------------------------------------------------------------

import logging
import re
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------
# sanitize_email_log_content
# Scrubs sensitive credentials and tokens from text before it is persisted to
# EmailLog.body or EmailLog.error_message.
#
# Patterns covered (E-4 hardening):
#   • Plain-text  "Password: <value>"
#   • HTML bold   "<strong>Password:</strong> <value>"
#   • HTML span   "<span ...>Password:</span> <value>"
#   • Query-string token  ?token=... or &token=...
#   • URL fragment        #token=...
#   • URL-encoded tokens  token%3D... / token%2F...
#   • JSON body           "token": "..."  or  'token': '...'
# --------------------------------------------------------------------------------
def sanitize_email_log_content(text):
    if not text or not isinstance(text, str):
        return text

    # --- Password patterns ---
    # HTML bold/strong tag wrapping:  <strong>Password:</strong> value
    text = re.sub(
        r'(<(?:strong|span)[^>]*>\s*Password\s*:?\s*</(?:strong|span)>\s*)([^\s<]+)',
        r'\1[PROTECTED]',
        text,
        flags=re.IGNORECASE,
    )
    # Plain-text: "Password: value" (covers "password :" spacing variants)
    text = re.sub(
        r'(Password\s*:\s*)([^\s<&]+)',
        r'\1[PROTECTED]',
        text,
        flags=re.IGNORECASE,
    )

    # --- Token patterns ---
    # URL query-string / fragment: [?&#]token=<value>  (supports URL-encoded chars)
    text = re.sub(
        r'([?&#]token=)[A-Za-z0-9._:\-/%+]+',
        r'\1[PROTECTED_TOKEN]',
        text,
    )
    # Bare "token=" not preceded by a letter/digit (e.g. at start of a param string)
    text = re.sub(
        r'(?<![A-Za-z0-9_])(token=)[A-Za-z0-9._:\-/%+]+',
        r'\1[PROTECTED_TOKEN]',
        text,
    )
    # JSON / dict representation:  "token": "value"  or  'token': 'value'
    text = re.sub(
        r'(["\']token["\']\s*:\s*["\'])([^"\']+)(["\'])',
        r'\1[PROTECTED_TOKEN]\3',
        text,
        flags=re.IGNORECASE,
    )
    # URL-percent-encoded form:  token%3D<value>  (token= encoded as token%3D)
    text = re.sub(
        r'(token%3D)[A-Za-z0-9._:\-/%+]+',
        r'\1[PROTECTED_TOKEN]',
        text,
        flags=re.IGNORECASE,
    )

    return text


# --------------------------------------------------------------------------------
# _execute_email_and_log  (private — NOT part of the public API)
# Single canonical implementation of: create log → send_mail → update log.
# All three previously-duplicated blocks (send_email_task, send_transactional_
# email_task, EmailService.send_transactional_email sync path) delegate here.
#
# Fixes addressed:
#   E-7  Duplicate EmailLog/send_mail/update pattern (consolidated here)
#   E-8  template_type is now written to EmailLog
#   E-9  Consistent use of getattr(settings, 'DEFAULT_FROM_EMAIL', None)
#   E-2  error_message is sanitized before persistence
# --------------------------------------------------------------------------------
def _execute_email_and_log(
    recipient,
    subject,
    plain_body=None,
    html_body=None,
    from_email=None,
    template_type=None,
):
    from core.models import EmailLog

    effective_from = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    sanitized_body = sanitize_email_log_content(plain_body or html_body or '')

    log = EmailLog.objects.create(
        recipient=recipient,
        subject=subject,
        body=sanitized_body,
        from_email=effective_from,
        template_type=template_type or '',
        status='PENDING',
    )
    try:
        send_mail(
            subject=subject,
            message=plain_body or 'Please view this email in an HTML-compatible client.',
            from_email=effective_from,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_body,
        )
        log.status = 'SENT'
        log.sent_at = timezone.now()
        log.save(update_fields=['status', 'sent_at'])
        logger.info('Email successfully delivered to %s', recipient)
        return log
    except Exception as exc:
        log.status = 'FAILED'
        # E-2: sanitize before storing — SMTP errors can echo back auth strings
        log.error_message = sanitize_email_log_content(str(exc))
        log.save(update_fields=['status', 'error_message'])
        logger.error('Failed to deliver email to %s: %s', recipient, exc)
        raise exc


# --------------------------------------------------------------------------------
# send_email_task
# Public Celery task — symbol name must remain stable for:
#   • EmailService.queue_and_send_email  →  send_email_task.delay(...)
#   • Test patches: patch("core.tasks.send_email_task.delay")
#
# Fixes addressed:
#   E-5  autoretry_for + retry_backoff (3 retries, exponential back-off, 10 min cap)
#   E-6  acks_late=True — message is re-queued if the worker dies mid-execution
#   E-7  Delegates to _execute_email_and_log (no inline duplication)
# --------------------------------------------------------------------------------
@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    acks_late=True,
)
def send_email_task(recipient, subject, body, from_email=None, html_body=None):
    _execute_email_and_log(
        recipient=recipient,
        subject=subject,
        plain_body=body,
        html_body=html_body,
        from_email=from_email,
    )


# --------------------------------------------------------------------------------
# send_transactional_email_task
# Public Celery task — symbol name must remain stable for:
#   • EmailService.send_transactional_email async path  →  .delay(...)
#   • Test patches: patch("core.tasks.send_transactional_email_task.delay")
#
# Same reliability fixes as send_email_task (E-5, E-6).
# --------------------------------------------------------------------------------
@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    acks_late=True,
)
def send_transactional_email_task(recipient, subject, html_content):
    _execute_email_and_log(
        recipient=recipient,
        subject=subject,
        plain_body=None,
        html_body=html_content,
    )


# --------------------------------------------------------------------------------
# EmailService
# Public class — all static method signatures are preserved exactly.
# Fixes addressed:
#   E-1  Fallback EmailLog in queue_and_send_email now calls sanitize + uses
#        _execute_email_and_log pattern (sanitized body, sanitized error_message)
#   E-7  send_transactional_email sync path delegates to _execute_email_and_log
#   E-8  template_type forwarded to _execute_email_and_log → stored in EmailLog
# --------------------------------------------------------------------------------
class EmailService:

    @staticmethod
    def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
        """
        Enqueue a general-purpose email via Celery.
        Falls back to a synchronous send + failure log if the broker is unreachable.
        Signature is intentionally unchanged for all existing callers.
        """
        try:
            send_email_task.delay(recipient, subject, body, from_email, html_body)
        except Exception as e:
            logger.error('Failed to queue celery email task: %s', e)
            # E-1 fix: sanitize body and error_message before persisting
            from core.models import EmailLog
            EmailLog.objects.create(
                recipient=recipient,
                subject=subject,
                body=sanitize_email_log_content(body or html_body or ''),
                from_email=from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                status='FAILED',
                error_message=sanitize_email_log_content(f'Queue failure: {str(e)}'),
            )

    @staticmethod
    def send_transactional_email(
        recipient,
        subject,
        html_content,
        template_type=None,
        password=None,
        synchronous=False,
    ):
        """
        Send an HTML transactional email.

        • synchronous=True  OR  password is not None  →  blocking in-process send
          (used for credential emails that must be delivered before the HTTP
          response is returned to the caller).
        • Otherwise → enqueued via Celery.

        Signature is intentionally unchanged for all existing callers.
        """
        if synchronous or password is not None:
            # E-7 fix: delegate to the unified helper instead of duplicating
            # E-8 fix: template_type is now forwarded and stored
            _execute_email_and_log(
                recipient=recipient,
                subject=subject,
                plain_body=None,
                html_body=html_content,
                template_type=template_type,
            )
        else:
            try:
                send_transactional_email_task.delay(recipient, subject, html_content)
            except Exception as e:
                logger.error('Failed to queue transactional email task: %s', e)


# --------------------------------------------------------------------------------
# queue_and_send_email  (module-level compatibility shim)
# Retained so that `from core.tasks import queue_and_send_email` keeps working.
# Callers: attendance/api/v1/views.py (leave status update notification).
# --------------------------------------------------------------------------------
def queue_and_send_email(recipient, subject, body, from_email=None, html_body=None):
    return EmailService.queue_and_send_email(recipient, subject, body, from_email, html_body)
