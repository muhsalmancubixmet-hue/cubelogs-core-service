# --------------------------------------------------------------------------------
#       Users Services
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
import logging

# DJANGO
from django.conf import settings
from django.core.signing import TimestampSigner
from django.template.loader import render_to_string

# THIRD PARTY

# APPLICATION SPECIFIC
from core.utils import generate_secure_password
from core.tasks import EmailService

logger = logging.getLogger(__name__)


class UserService:
    @staticmethod
    def send_welcome_email(employee):
        """Send a standard welcome email to a newly created employee."""
        if not employee.email or employee.isSuperAdmin:
            return

        raw_password = getattr(employee, '_raw_password', None)
        password_val = raw_password if raw_password else "Use your existing password"

        subject = "Welcome to CubeLogs!"
        dashboard_url = f"{settings.FRONTEND_URL}/login"

        html_content = render_to_string(
            "emails/employee/welcome.html",
            {
                "employee_name": employee.first_name or 'User',
                "dashboard_url": dashboard_url,
                "email": employee.email,
                "password": password_val,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(employee.email, subject, html_content, 'WELCOME', raw_password)

    @staticmethod
    def send_admin_onboarding_email(employee, raw_password, synchronous=False):
        """
        Send a magic-login welcome email for admin-created accounts.
        Includes a one-click login link and a revoke link.
        """
        login_token = TimestampSigner(salt='auto-login').sign(str(employee.id))
        revoke_token = TimestampSigner(salt='revoke-registration').sign(str(employee.id))

        frontend_url = settings.FRONTEND_URL
        magic_login_url = f"{frontend_url}/login/verify?token={login_token}"
        revoke_url = f"{frontend_url}/revoke?token={revoke_token}"

        password_val = raw_password if raw_password else "Use your existing password"
        subject = 'Welcome to CubeLogs - Your Login Credentials'

        html_message = render_to_string(
            "emails/employee/admin_onboarding.html",
            {
                "magic_login_url": magic_login_url,
                "email": employee.email,
                "password": password_val,
                "revoke_url": revoke_url,
                "product_support_email": settings.SUPPORT_EMAIL,
                "product_website": settings.COMPANY_WEBSITE,
                "product_company_name": settings.COMPANY_NAME,
            }
        )
        EmailService.send_transactional_email(employee.email, subject, html_message, 'WELCOME', raw_password, synchronous=synchronous)

    @staticmethod
    def provision_employee(employee, request_user=None, raw_password=None, synchronous=False):
        """
        Post-creation hook: set organisation, generate password if needed,
        and send the admin onboarding email.

        This is called from EmployeeSerializer.create() to keep the serializer thin.
        """
        if request_user and request_user.is_authenticated:
            if not (request_user.isSuperAdmin and request_user.organization is None):
                employee.organization = request_user.organization
                employee.save(update_fields=['organization'])

        if raw_password:
            UserService.send_admin_onboarding_email(employee, raw_password, synchronous=synchronous)
