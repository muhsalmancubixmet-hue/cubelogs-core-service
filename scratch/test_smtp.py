import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
django.setup()

from django.core.mail import send_mail
from django.conf import settings

print("DEFAULT_FROM_EMAIL:", settings.DEFAULT_FROM_EMAIL)
print("EMAIL_HOST:", settings.EMAIL_HOST)
print("EMAIL_PORT:", settings.EMAIL_PORT)
print("EMAIL_HOST_USER:", settings.EMAIL_HOST_USER)
print("EMAIL_HOST_PASSWORD length:", len(settings.EMAIL_HOST_PASSWORD) if settings.EMAIL_HOST_PASSWORD else 0)

try:
    send_mail(
        'Test SMTP connection',
        'If you receive this, the settings are 100% correct!',
        settings.DEFAULT_FROM_EMAIL,
        ['amnasali36@gmail.com'],
        fail_silently=False,
    )
    print("Email sent successfully!")
except Exception as e:
    print("Failed to send email:")
    import traceback
    traceback.print_exc()
