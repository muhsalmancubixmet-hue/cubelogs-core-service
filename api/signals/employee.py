# api/signals/employee.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from api.models import Employee

@receiver(post_save, sender=Employee)
def send_employee_registration_email(sender, instance, created, **kwargs):
    if not created or not instance.email or instance.isSuperAdmin:
        return
    
    from api.models import EmailLog
    from api.tasks import send_queued_email_task
    
    raw_password = getattr(instance, '_raw_password', None)
    password_val = raw_password if raw_password else "Use your existing password"

    subject = "Welcome to CubeLogs!"
    dashboard_url = f"{getattr(settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')}/login"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }}
            .header {{ background: linear-gradient(135deg, #1e3a8a, #3b82f6); color: #ffffff; padding: 40px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
            .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 16px; }}
            .content h2 {{ color: #0f172a; font-size: 20px; font-weight: 600; margin-top: 0; }}
            .btn {{ display: inline-block; background-color: #2563eb; color: #ffffff !important; text-decoration: none; padding: 14px 30px; border-radius: 8px; font-weight: 600; margin: 24px 0; text-align: center; font-size: 16px; transition: background-color 0.2s; box-shadow: 0 4px 6px rgba(37, 99, 235, 0.2); }}
            .btn:hover {{ background-color: #1d4ed8; }}
            .footer {{ background-color: #f1f5f9; padding: 24px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to CubeLogs</h1>
            </div>
            <div class="content">
                <h2>Hello {instance.first_name or 'User'},</h2>
                <p>Your CubeLogs account has been successfully created. We are excited to have you on board!</p>
                <div style="text-align: center; margin: 24px 0;">
                    <a href="{dashboard_url}" class="btn" style="margin: 0 auto 12px auto; display: inline-block;">Go to Dashboard</a>
                    <div style="font-size: 14px; color: #475569; margin-top: 8px; text-align: center;">
                        <p style="margin: 4px 0;"><strong>Email:</strong> {instance.email}</p>
                        <p style="margin: 4px 0;"><strong>Password:</strong> {password_val}</p>
                    </div>
                </div>
                <p style="margin-top: 32px; color: #475569;">Best regards,<br><strong style="color: #0f172a;">The CubeLogs Team</strong></p>
            </div>
            <div class="footer">
                &copy; 2026 CubeLogs. All rights reserved.<br>
                This is an automated transactional message.
            </div>
        </div>
    </body>
    </html>
    """
    
    log = EmailLog.objects.create(
        recipient=instance.email,
        subject=subject,
        template_type='WELCOME',
        html_content=html_content,
        status='PENDING',
        password=raw_password
    )
    send_queued_email_task.delay(log.id)
