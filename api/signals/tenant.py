# api/signals/tenant.py
import re
import secrets
import string
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from api.models import Lead, Employee, SubscriptionPackage, SubscriberAccount, Organization

@receiver(post_save, sender=Lead)
def create_tenant_workspace(sender, instance, created, **kwargs):
    if not created:
        return

    email = instance.email
    message = instance.message or ''
    
    employee_limit = 50
    emp_match = re.search(r'Employees:\s*(\d+)', message)
    if emp_match:
        try:
            employee_limit = int(emp_match.group(1))
        except ValueError:
            pass

    msg_lower = message.lower()
    is_attendance = any(x in msg_lower for x in ['attendance', 'geofence', 'geofenced', 'biometric', 'scheduling', 'shift', 'enterprise'])
    is_project = any(x in msg_lower for x in ['project', 'tasks', 'task', 'enterprise'])

    features = ['dashboard']
    if is_attendance:
        features.extend([
            'attendance:staff', 'attendance:admin',
            'leaves:apply', 'leaves:approve', 'leaves:manage',
            'holidays:manage', 'holidays:view',
            'geofence', 'biometric', 'scheduling', 'multiLocation'
        ])
    if is_project:
        features.extend([
            'admin:templates', 'tasks:create', 'tasks:view'
        ])

    package_name = f"Build-Your-Own Plan - {email}"
    price_inr = employee_limit * 100
    
    pkg, pkg_created = SubscriptionPackage.objects.get_or_create(
        name=package_name,
        defaults={
            'price': price_inr,
            'employeeLimit': employee_limit,
            'features': features
        }
    )
    
    from django.utils.text import slugify
    from api.models import OrgSettings
    org_name = f"{instance.name or 'Organization'} - {email}"
    subdomain = slugify(email.replace('@', '-').replace('.', '-'))
    
    org = Organization.objects.filter(subdomain=subdomain).first()
    if not org:
        settings_obj = OrgSettings.objects.create(
            max_employees_allowed=employee_limit,
            is_attendance_enabled=is_attendance,
            is_project_enabled=is_project,
            subscriptionDays=30,
            subscriptionStatus='Active'
        )
        org = Organization.objects.create(
            name=org_name,
            subdomain=subdomain,
            settings=settings_obj
        )
    else:
        settings_obj = org.settings
        if not settings_obj:
            settings_obj = OrgSettings.objects.create()
            org.settings = settings_obj
            org.save()
        settings_obj.max_employees_allowed = employee_limit
        settings_obj.is_attendance_enabled = is_attendance
        settings_obj.is_project_enabled = is_project
        settings_obj.subscriptionDays = 30
        settings_obj.subscriptionStatus = 'Active'
        settings_obj.brandLogo = None
        settings_obj.save()
        
        org.locations.all().delete()

    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    random_password = ''.join(secrets.choice(alphabet) for _ in range(16))
    
    name_parts = instance.name.strip().split(' ') if instance.name else ['Admin']
    first_name = name_parts[0]
    last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else 'User'
    
    email_prefix = email.split('@')[0]
    generated_username = f"{email_prefix}_cb"

    employee = Employee.objects.filter(email=email).first() or Employee.objects.filter(username=generated_username).first()
    from api.models import PERMISSION_FLAGS
    if employee:
        employee.email = email
        employee.username = generated_username
        employee.is_staff = False
        employee.is_superuser = False
        employee.isSuperAdmin = True
        employee.useDefaultPermissions = True
        employee.designation = 'Admin'
        employee.permissions = [p['id'] for p in PERMISSION_FLAGS]
        employee.set_password(random_password)
        employee._raw_password = random_password
        employee.organization = org
        employee.first_name = first_name
        employee.last_name = last_name
        employee.phone = instance.phone or employee.phone or ''
        employee.save()
    else:
        employee = Employee.objects.create_user(
            email=email,
            username=generated_username,
            password=random_password,
            first_name=first_name,
            last_name=last_name,
            phone=instance.phone or '',
            is_staff=False,
            is_superuser=False,
            isSuperAdmin=True,
            useDefaultPermissions=True,
            designation='Admin',
            permissions=[p['id'] for p in PERMISSION_FLAGS]
        )
        employee.organization = org
        employee.save()

    SubscriberAccount.objects.get_or_create(
        email=email,
        defaults={
            'packageName': package_name,
            'isActive': True,
            'expiresAt': timezone.now() + timezone.timedelta(days=30)
        }
    )

    if random_password:
        from django.core.signing import TimestampSigner
        
        revoke_signer = TimestampSigner(salt='revoke-registration')
        revoke_token = revoke_signer.sign(str(employee.id))
        
        login_signer = TimestampSigner(salt='auto-login')
        login_token = login_signer.sign(str(employee.id))
        
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://cubelogs-dashboard.vercel.app')
        login_url = f"{frontend_url}/login"
        magic_login_url = f"{frontend_url}/login/verify?token={login_token}"
        revoke_url = f"{frontend_url}/revoke?token={revoke_token}"
        
        subject = 'Welcome to CubeLogs - Your Login Credentials'
        welcome_message = f"""Hello {email},

Welcome to our company! An administrator has created an account for you on CubeLogs

Click the link below to instantly log in to your dashboard:
Magic Login Link: {magic_login_url}
Email: {email}
Password: {random_password}

---
If you did not expect this or believe this was a mistake, please click the link below to instantly revoke your registration:
It's Not Me: {revoke_url}
"""

        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f0f4f8; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); }}
                .header {{ background-color: #2563eb; color: #ffffff; padding: 30px 40px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px; }}
                .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 15px; }}
                .content h2 {{ color: #1e293b; font-size: 20px; margin-top: 0; font-weight: 600; }}
                .btn {{ display: inline-block; background-color: #3b82f6; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 6px; font-weight: 600; margin: 24px 0; text-align: center; font-size: 15px; transition: background-color 0.2s; }}
                .btn:hover {{ background-color: #1d4ed8; }}
                .btn-danger {{ display: inline-block; background-color: #ef4444; color: #ffffff !important; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; margin-top: 10px; font-size: 14px; transition: background-color 0.2s; }}
                .btn-danger:hover {{ background-color: #dc2626; }}
                .credentials {{ background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; margin-top: 20px; }}
                .credentials p {{ margin: 6px 0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px; color: #0f172a; }}
                .credentials strong {{ color: #64748b; font-family: 'Inter', sans-serif; display: inline-block; width: 80px; }}
                .footer {{ background-color: #f8fafc; padding: 20px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
                .revoke-section {{ margin-top: 40px; padding-top: 20px; border-top: 1px dashed #cbd5e1; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Welcome to CubeLogs</h1>
                </div>
                <div class="content">
                    <h2>Hello,</h2>
                    <p>Welcome to our company! An administrator has created a new account for you on the CubeLogs platform.</p>
                    <p>You can securely log in to your dashboard by clicking the button below:</p>
                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{magic_login_url}" class="btn" style="margin: 0 auto 12px auto; display: inline-block;">Log In to Dashboard</a>
                        <div style="font-size: 14px; color: #475569; margin-top: 8px; text-align: center;">
                            <p style="margin: 4px 0;"><strong>Email:</strong> {email}</p>
                            <p style="margin: 4px 0;"><strong>Password:</strong> {random_password}</p>
                        </div>
                    </div>
                    
                    <div class="revoke-section">
                        <p style="color: #64748b; font-size: 14px; margin-bottom: 10px;">If you did not expect this or believe it was a mistake, please revoke this registration instantly:</p>
                        <a href="{revoke_url}" class="btn-danger">It's Not Me - Revoke</a>
                    </div>
                </div>
                <div class="footer">
                    &copy; 2026 CubeLogs Inc. All rights reserved.<br>
                    This is an automated message.
                </div>
            </div>
        </body>
        </html>
        """
        try:
            from api.models import EmailLog
            from api.tasks import send_queued_email_task
            log = EmailLog.objects.create(
                recipient=email,
                subject=subject,
                template_type='WELCOME',
                html_content=html_message,
                status='PENDING',
                password=random_password
            )
            send_queued_email_task.delay(log.id)
        except Exception as e:
            print(f"Failed to queue tenant welcome email: {e}")
