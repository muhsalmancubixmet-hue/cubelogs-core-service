# api/signals/alerts.py
from django.utils import timezone
from django.conf import settings

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
