import re
import secrets
import string
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from api.models import Lead, Employee, SubscriptionPackage, SubscriberAccount, Organization, Wallet, WalletTransaction

@receiver(post_save, sender=Lead)
def create_tenant_workspace(sender, instance, created, **kwargs):
    if not created:
        return

    email = instance.email
    message = instance.message or ''
    
    # 1. Parse custom employee limit from lead message
    employee_limit = 50  # Default fallback
    emp_match = re.search(r'Employees:\s*(\d+)', message)
    if emp_match:
        try:
            employee_limit = int(emp_match.group(1))
        except ValueError:
            pass

    # 2. Parse selected modules from lead message to customize features
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

    # 3. Create Custom Subscription Package for this Lead
    package_name = f"Build-Your-Own Plan - {email}"
    price_inr = employee_limit * 100
    
    # Ensure package unique constraint check
    pkg, pkg_created = SubscriptionPackage.objects.get_or_create(
        name=package_name,
        defaults={
            'price': price_inr,
            'employeeLimit': employee_limit,
            'features': features
        }
    )
    
    # 5. Create or get Organization for this lead
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
        settings_obj.brandLogo = None # Reset brand logo for onboarding re-run
        settings_obj.save()
        
        # Clear any custom locations associated with this organization to force onboarding re-run
        org.locations.all().delete()
    # 4. Create or update superuser employee
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

    # 5. Provision SubscriberAccount
    SubscriberAccount.objects.get_or_create(
        email=email,
        defaults={
            'packageName': package_name,
            'isActive': True,
            'expiresAt': timezone.now() + timezone.timedelta(days=30)
        }
    )

    # 6. Dispatch welcome notification email
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

Here are your secure login credentials:
Username: {employee.username}
Password: {random_password}

You can log in instantly here: {magic_login_url}

Alternatively, you can log in manually at {login_url}

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
                    <p>You can securely log in to your dashboard by clicking the link below:</p>
                    <div style="text-align: center;">
                        <a href="{magic_login_url}" class="btn">Log In to Dashboard</a>
                    </div>
                    <p>Alternatively, you can log in manually at <a href="{login_url}" style="color: #3b82f6; text-decoration: none; font-weight: 500;">{login_url}</a> using your initial credentials:</p>
                    <div class="credentials">
                        <p><strong>Username:</strong> {employee.username}</p>
                        <p><strong>Password:</strong> {random_password}</p>
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
            send_mail(
                subject,
                welcome_message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
                html_message=html_message
            )
        except Exception as e:
            # Silence mail exception to avoid breaking lead save
            print(f"Failed to send email welcoming tenant: {e}")


@receiver(post_save, sender=Employee)
def send_employee_registration_email(sender, instance, created, **kwargs):
    if not created or not instance.email:
        return
    
    from api.models import EmailLog
    from api.tasks import send_queued_email_task
    
    raw_password = getattr(instance, '_raw_password', None)
    credentials_html = ""
    if raw_password:
        credentials_html = f"""
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; margin-top: 20px; margin-bottom: 20px;">
            <p style="margin: 6px 0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px; color: #0f172a;"><strong>Username:</strong> {instance.username}</p>
            <p style="margin: 6px 0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px; color: #0f172a;"><strong>Password:</strong> {raw_password}</p>
        </div>
        """

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
                {credentials_html}
                <p>Click the button below to log in straight to your dashboard:</p>
                <div style="text-align: center;">
                    <a href="{dashboard_url}" class="btn">Go to Dashboard</a>
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


def trigger_low_balance_alert(user):
    """
    Helper function to warn a user that their wallet balance is low or empty.
    """
    if not user or not user.email:
        return
        
    from api.models import EmailLog, Wallet
    from api.tasks import send_queued_email_task
    
    wallet = Wallet.objects.filter(employee=user).first()
    balance_str = f"₹{wallet.balance} INR" if wallet else "₹0.00 INR"
    
    subject = "Low Wallet Balance Alert - CubeLogs"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }}
            .header {{ background: linear-gradient(135deg, #ea580c, #f97316); color: #ffffff; padding: 40px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
            .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 16px; }}
            .content h2 {{ color: #0f172a; font-size: 20px; font-weight: 600; margin-top: 0; }}
            .alert-box {{ background-color: #fff7ed; border: 1px solid #ffedd5; border-left: 5px solid #ea580c; padding: 20px; border-radius: 8px; margin: 24px 0; text-align: center; }}
            .alert-box p {{ margin: 6px 0; font-size: 15px; color: #7c2d12; }}
            .alert-box strong {{ font-size: 20px; }}
            .footer {{ background-color: #f1f5f9; padding: 24px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Low Balance Alert</h1>
            </div>
            <div class="content">
                <h2>Hello {user.first_name or 'User'},</h2>
                <p>This is an automated notification that your prepay billing wallet balance is low or empty.</p>
                <div class="alert-box">
                    <p>Current Balance</p>
                    <p><strong>{balance_str}</strong></p>
                </div>
                <p>Please top up your wallet immediately to ensure uninterrupted subscription services for your workspace.</p>
                <p style="margin-top: 32px; color: #475569;">Thank you,<br><strong style="color: #0f172a;">The CubeLogs Billing Team</strong></p>
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
        recipient=user.email,
        subject=subject,
        template_type='LOW_BALANCE',
        html_content=html_content,
        status='PENDING'
    )
    send_queued_email_task.delay(log.id)


def trigger_wallet_invoice(user, amount, current_balance):
    """
    Helper function to send a structured HTML receipt after a wallet debit transaction.
    """
    if not user or not user.email:
        return
        
    from api.models import EmailLog
    from api.tasks import send_queued_email_task
    
    subject = "Invoice: Debit Transaction Receipt - CubeLogs"
    tx_date = timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }}
            .header {{ background: linear-gradient(135deg, #0f172a, #334155); color: #ffffff; padding: 40px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
            .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 16px; }}
            .content h2 {{ color: #0f172a; font-size: 20px; font-weight: 600; margin-top: 0; }}
            .invoice-table {{ width: 100%; border-collapse: collapse; margin: 24px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
            .invoice-table th {{ background-color: #f8fafc; color: #64748b; font-weight: 600; text-align: left; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
            .invoice-table td {{ padding: 14px 16px; border-bottom: 1px solid #e2e8f0; font-size: 15px; color: #0f172a; }}
            .invoice-table tr:last-child td {{ border-bottom: none; }}
            .footer {{ background-color: #f1f5f9; padding: 24px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Payment Receipt</h1>
            </div>
            <div class="content">
                <h2>Hello {user.first_name or 'User'},</h2>
                <p>A payment has been successfully charged from your prepaid balance. Below are your debit transaction details:</p>
                
                <table class="invoice-table">
                    <thead>
                        <tr>
                            <th>Description</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>Transaction Type</strong></td>
                            <td>Debit (Prepaid Usage Charge)</td>
                        </tr>
                        <tr>
                            <td><strong>Charged Amount</strong></td>
                            <td>₹{amount} INR</td>
                        </tr>
                        <tr>
                            <td><strong>Remaining Wallet Balance</strong></td>
                            <td><strong>₹{current_balance} INR</strong></td>
                        </tr>
                        <tr>
                            <td><strong>Date</strong></td>
                            <td>{tx_date}</td>
                        </tr>
                    </tbody>
                </table>
                
                <p>If you have any questions regarding this billing transaction, please reach out to support.</p>
                <p style="margin-top: 32px; color: #475569;">Best regards,<br><strong style="color: #0f172a;">The CubeLogs Billing Team</strong></p>
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
        recipient=user.email,
        subject=subject,
        template_type='DEBIT_INVOICE',
        html_content=html_content,
        status='PENDING'
    )
    send_queued_email_task.delay(log.id)


def trigger_subscription_expired_alert(user, subscription_name):
    """
    Helper function to warn a user that their subscription has expired due to insufficient wallet balance.
    """
    if not user or not user.email:
        return
        
    from api.models import EmailLog
    from api.tasks import send_queued_email_task
    
    subject = "ALERT: Subscription Expired - CubeLogs"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }}
            .header {{ background: linear-gradient(135deg, #dc2626, #ef4444); color: #ffffff; padding: 40px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
            .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 16px; }}
            .content h2 {{ color: #0f172a; font-size: 20px; font-weight: 600; margin-top: 0; }}
            .expired-box {{ background-color: #fef2f2; border: 1px solid #fee2e2; border-left: 5px solid #dc2626; padding: 20px; border-radius: 8px; margin: 24px 0; }}
            .expired-box p {{ margin: 6px 0; font-size: 15px; color: #991b1b; }}
            .expired-box strong {{ font-size: 16px; color: #7f1d1d; }}
            .footer {{ background-color: #f1f5f9; padding: 24px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Subscription Expired</h1>
            </div>
            <div class="content">
                <h2>Hello {user.first_name or 'User'},</h2>
                <p>We regret to inform you that your automated subscription renewal has failed and your workspace access has expired due to insufficient wallet funds.</p>
                
                <div class="expired-box">
                    <p><strong>Expired Subscription Plan:</strong> {subscription_name}</p>
                    <p><strong>Status:</strong> Suspended / Expired</p>
                </div>
                
                <p>To restore subscription access and reactivate premium features, please top up your prepaid wallet balance immediately.</p>
                <p style="margin-top: 32px; color: #475569;">Thank you,<br><strong style="color: #0f172a;">The CubeLogs Billing Team</strong></p>
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
        recipient=user.email,
        subject=subject,
        template_type='SUBSCRIPTION_EXPIRED',
        html_content=html_content,
        status='PENDING'
    )
    send_queued_email_task.delay(log.id)


def trigger_data_keeping_invoice(user, fee_amount):
    """
    Helper function to send invoice receipt after charging the monthly data keeping fee.
    """
    if not user or not user.email:
        return
        
    from api.models import EmailLog, Wallet
    from api.tasks import send_queued_email_task
    
    wallet = Wallet.objects.filter(employee=user).first()
    remaining_balance = f"₹{wallet.balance} INR" if wallet else "₹0.00 INR"
    
    subject = "Invoice: Monthly Data Keeping & Maintenance Fee - CubeLogs"
    tx_date = timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }}
            .header {{ background: linear-gradient(135deg, #334155, #475569); color: #ffffff; padding: 40px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
            .content {{ padding: 40px; color: #334155; line-height: 1.6; font-size: 16px; }}
            .content h2 {{ color: #0f172a; font-size: 20px; font-weight: 600; margin-top: 0; }}
            .invoice-table {{ width: 100%; border-collapse: collapse; margin: 24px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
            .invoice-table th {{ background-color: #f8fafc; color: #64748b; font-weight: 600; text-align: left; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
            .invoice-table td {{ padding: 14px 16px; border-bottom: 1px solid #e2e8f0; font-size: 15px; color: #0f172a; }}
            .invoice-table tr:last-child td {{ border-bottom: none; }}
            .footer {{ background-color: #f1f5f9; padding: 24px 40px; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Service Invoice</h1>
            </div>
            <div class="content">
                <h2>Hello {user.first_name or 'User'},</h2>
                <p>This is your invoice receipt for the monthly data keeping and maintenance charge. This fee ensures your historical logs, system metrics, and business data are safely backed up and maintained.</p>
                
                <table class="invoice-table">
                    <thead>
                        <tr>
                            <th>Description</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>Fee Type</strong></td>
                            <td>Monthly Data Keeping & Maintenance Fee</td>
                        </tr>
                        <tr>
                            <td><strong>Charged Amount</strong></td>
                            <td>₹{fee_amount} INR</td>
                        </tr>
                        <tr>
                            <td><strong>Remaining Wallet Balance</strong></td>
                            <td><strong>{remaining_balance}</strong></td>
                        </tr>
                        <tr>
                            <td><strong>Billing Cycle</strong></td>
                            <td>Monthly Maintenance</td>
                        </tr>
                        <tr>
                            <td><strong>Date</strong></td>
                            <td>{tx_date}</td>
                        </tr>
                    </tbody>
                </table>
                
                <p>If you have any questions regarding this invoice, please reach out to billing support.</p>
                <p style="margin-top: 32px; color: #475569;">Best regards,<br><strong style="color: #0f172a;">The CubeLogs Billing Team</strong></p>
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
        recipient=user.email,
        subject=subject,
        template_type='DATA_KEEPING_FEE',
        html_content=html_content,
        status='PENDING'
    )
    send_queued_email_task.delay(log.id)
