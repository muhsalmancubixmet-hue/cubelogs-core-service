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
from core.tasks import EmailService

logger = logging.getLogger(__name__)


class UserService:
    @staticmethod
    def derive_employee_employment_access(status, lwd, is_active):
        """
        Single canonical truth helper to derive whether an employee has active employment access
        to an organization based on HR status, last working date, and global auth state.
        Returns True if actively employed; False if employment has ended or account is deactivated.
        """
        from django.utils import timezone
        today = timezone.now().date()

        if status == 'Deactivated' or not is_active:
            return False
        elif status in ['Resigned', 'Terminated']:
            if lwd is None or lwd <= today:
                return False
            else:  # lwd > today (serving notice period)
                return True
        return True

    @staticmethod
    def classify_employee_creation(email, target_org):
        """
        Centralized helper to classify employee creation intent into one of 4 cases:
        - ('NEW_GLOBAL_USER', None)
        - ('SAME_ORG_EXISTING', existing_employee)
        - ('OTHER_ORG_ACTIVE', existing_employee)
        - ('OTHER_ORG_EXITED', existing_employee)
        """
        from users.models import Employee

        if not email:
            return 'NEW_GLOBAL_USER', None

        email_clean = email.strip().lower()
        existing = (
            Employee.objects.filter(email__iexact=email_clean).first()
            or Employee.objects.filter(username__iexact=email_clean).first()
        )
        if not existing:
            return 'NEW_GLOBAL_USER', None

        target_org_id = target_org.id if target_org else None
        existing_org_id = existing.organization_id

        if existing_org_id == target_org_id:
            return 'SAME_ORG_EXISTING', existing

        has_access = UserService.derive_employee_employment_access(
            existing.employment_status,
            existing.last_working_date,
            existing.is_active
        )

        if not has_access:
            return 'OTHER_ORG_EXITED', existing
        else:
            return 'OTHER_ORG_ACTIVE', existing

    @staticmethod
    def sync_employee_shadow_membership(employee):
        """
        Phase 2A Shadow Synchronization Helper:
        Ensures that whenever an Employee is created or transferred in Phase 1 runtime,
        a corresponding OrganizationMembership shadow record is created or updated.
        Does NOT alter legacy Employee runtime source-of-truth.
        """
        if not employee or not employee.organization:
            return None

        from users.models import OrganizationMembership

        is_active_in_org = UserService.derive_employee_employment_access(
            employee.employment_status,
            employee.last_working_date,
            employee.is_active
        )

        # Safe role assignment: Ensure role belongs to current organization or is a system role
        role_to_assign = employee.role
        if role_to_assign and not (role_to_assign.organization_id == employee.organization_id or role_to_assign.is_system_role):
            role_to_assign = None

        membership, _ = OrganizationMembership.objects.update_or_create(
            user=employee,
            organization=employee.organization,
            defaults={
                'employee_code': employee.employee_code,
                'department': employee.department or '',
                'designation': employee.designation,
                'role': role_to_assign,
                'employment_status': employee.employment_status,
                'joining_date': employee.joining_date,
                'last_working_date': employee.last_working_date,
                'is_active_in_org': is_active_in_org,
            }
        )
        return membership

    @staticmethod
    def get_tokens_for_user(user, active_membership=None):
        """
        Phase 2C Token Generator:
        Generates RefreshToken and AccessToken for user with additive claims:
        - active_org_id
        - membership_id
        Preserves global user_id in refresh token for global identity.
        """
        from rest_framework_simplejwt.tokens import RefreshToken
        from core.tenant import TenantContext

        refresh = RefreshToken.for_user(user)

        if active_membership is None:
            fake_req = type('Req', (), {'user': user, 'is_authenticated': True})()
            active_membership = TenantContext.get_active_membership(fake_req)

        if active_membership:
            refresh['active_org_id'] = active_membership.organization_id
            refresh['membership_id'] = active_membership.id
            access_token = refresh.access_token
            access_token['active_org_id'] = active_membership.organization_id
            access_token['membership_id'] = active_membership.id
        else:
            refresh['active_org_id'] = getattr(user, 'organization_id', None)
            refresh['membership_id'] = None
            access_token = refresh.access_token
            access_token['active_org_id'] = getattr(user, 'organization_id', None)
            access_token['membership_id'] = None

        return refresh

    @staticmethod
    def send_welcome_email(employee, synchronous=False):
        """Send a standard welcome email to an existing employee joining a new organization."""
        if not employee.email or employee.isSuperAdmin:
            return

        password_val = "Use your existing password"
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
        EmailService.send_transactional_email(employee.email, subject, html_content, 'WELCOME', password=None, synchronous=synchronous)

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
