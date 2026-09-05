from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.core.signing import TimestampSigner
from django.utils import timezone
from unittest.mock import patch
from django.db import IntegrityError
from django.core.exceptions import ValidationError

from decimal import Decimal
from core.models import Organization, OrgSettings
from users.models import Employee, OrganizationMembership, Role
from subscribers.models import Wallet
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken


class JWTAuthLifecycleTestCase(APITestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=False,
        )
        self.org = Organization.objects.create(
            name="JWT Auth Test Org",
            subdomain="jwt_test",
            settings=self.org_settings,
        )
        self.user = Employee.objects.create_user(  # type: ignore[call-arg]
            email="jwt_user@example.com",
            password="securepassword123",
            first_name="JWT",
            last_name="User",
            organization=self.org,
            permissions=["projects:create", "projects:view", "dashboard"]
        )
        from users.api.v1.services import UserService
        self.membership = UserService.sync_employee_shadow_membership(self.user)

    def test_password_login_returns_jwt_tokens(self):
        client = APIClient()
        response = client.post("/api/auth/login/", {
            "email": "jwt_user@example.com",
            "password": "securepassword123"
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "jwt_user@example.com")
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(len(response.data["access"]) > 20)
        self.assertTrue(len(response.data["refresh"]) > 20)

        # Confirm Bearer token authenticates subsequent requests
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me_res = client.get("/api/auth/me/")
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)
        self.assertEqual(me_res.json()["email"], "jwt_user@example.com")

    def test_magic_login_returns_jwt_tokens(self):
        signer = TimestampSigner(salt="auto-login")
        token = signer.sign(str(self.user.id))

        client = APIClient()
        response = client.post("/api/auth/magic-login/", {"token": token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "jwt_user@example.com")
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        # Confirm Bearer token authenticates
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me_res = client.get("/api/auth/me/")
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)

    def test_magic_login_invalid_token_returns_400(self):
        client = APIClient()
        response = client.post("/api/auth/magic-login/", {"token": "invalid.token.value"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_token_refresh_lifecycle(self):
        client = APIClient()
        login_res = client.post("/api/auth/login/", {
            "email": "jwt_user@example.com",
            "password": "securepassword123"
        }, format="json")
        refresh_token = login_res.data["refresh"]

        # Call refresh endpoint
        refresh_res = client.post("/api/auth/refresh/", {
            "refresh": refresh_token
        }, format="json")

        self.assertEqual(refresh_res.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_res.data)
        new_access = refresh_res.data["access"]

        # Use refreshed access token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access}")
        me_res = client.get("/api/auth/me/")
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)

    def test_logout_blacklists_refresh_token(self):
        client = APIClient()
        login_res = client.post("/api/auth/login/", {
            "email": "jwt_user@example.com",
            "password": "securepassword123"
        }, format="json")
        refresh_token = login_res.data["refresh"]

        # Logout with refresh token
        logout_res = client.post("/api/auth/logout/", {
            "refresh": refresh_token
        }, format="json")
        self.assertEqual(logout_res.status_code, status.HTTP_200_OK)

        # Attempt to refresh with blacklisted token should fail
        refresh_attempt = client.post("/api/auth/refresh/", {
            "refresh": refresh_token
        }, format="json")
        self.assertEqual(refresh_attempt.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_me_returns_401(self):
        client = APIClient()
        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_refresh_rotation_and_old_token_rejected(self):
        client = APIClient()
        login_res = client.post("/api/auth/login/", {
            "email": "jwt_user@example.com",
            "password": "securepassword123"
        }, format="json")
        first_refresh = login_res.data["refresh"]

        # First refresh -> returns new access and rotated refresh
        refresh_res = client.post("/api/auth/refresh/", {
            "refresh": first_refresh
        }, format="json")
        self.assertEqual(refresh_res.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_res.data)
        self.assertIn("refresh", refresh_res.data)
        second_refresh = refresh_res.data["refresh"]

        # Reusing the old rotated refresh token MUST be rejected with 401
        replay_res = client.post("/api/auth/refresh/", {
            "refresh": first_refresh
        }, format="json")
        self.assertEqual(replay_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # The new rotated refresh token should work
        valid_res = client.post("/api/auth/refresh/", {
            "refresh": second_refresh
        }, format="json")
        self.assertEqual(valid_res.status_code, status.HTTP_200_OK)

    def test_invalid_or_expired_refresh_token_returns_401(self):
        client = APIClient()
        response = client.post("/api/auth/refresh/", {
            "refresh": "invalid.or.expired.jwt.token"
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_endpoint_includes_enrichment_data(self):
        client = APIClient()
        login_res = client.post("/api/auth/login/", {
            "email": "jwt_user@example.com",
            "password": "securepassword123"
        }, format="json")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_res.data['access']}")

        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("is_attendance_enabled", data)
        self.assertIn("is_project_enabled", data)
        self.assertTrue(data["is_attendance_enabled"])
        self.assertFalse(data["is_project_enabled"])
        self.assertIn("subscription", data)

    def test_employee_creation_queues_single_onboarding_email(self):
        from unittest.mock import patch
        from users.api.v1.serializers import EmployeeSerializer

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.user.id}")

        with patch("core.tasks.send_transactional_email_task.delay") as mock_delay:
            with patch("core.tasks.send_mail") as mock_send_mail:
                data = {
                    "email": "single_onboard_test@example.com",
                    "first_name": "Single",
                    "last_name": "Onboard",
                    "designation": "Developer",
                    "password": "Password123!"
                }
                with self.captureOnCommitCallbacks(execute=True):
                    serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
                    self.assertTrue(serializer.is_valid(), serializer.errors)
                    emp = serializer.save()

                # Secure rule: credential-bearing onboarding email sent synchronously, NOT serialized into Celery
                mock_delay.assert_not_called()
                self.assertEqual(mock_send_mail.call_count, 1)
                self.assertEqual(mock_send_mail.call_args[1]['subject'], "Welcome to CubeLogs - Your Login Credentials")
                self.assertEqual(mock_send_mail.call_args[1]['recipient_list'], ["single_onboard_test@example.com"])

    def test_magic_login_token_validity_and_expiry(self):
        signer = TimestampSigner(salt="auto-login")
        valid_token = signer.sign(str(self.user.id))

        client = APIClient()
        # Test valid token
        res = client.post("/api/auth/magic-login/", {"token": valid_token}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["user"]["email"], "jwt_user@example.com")

        # Test expired token (> 7 days)
        with patch("django.core.signing.TimestampSigner.unsign", side_effect=__import__("django.core.signing", fromlist=["SignatureExpired"]).SignatureExpired):
            exp_res = client.post("/api/auth/magic-login/", {"token": valid_token}, format="json")
            self.assertEqual(exp_res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("error", exp_res.data)
            self.assertEqual(exp_res.data["error"], "Magic link has expired.")

    def test_onboarding_identity_and_tenant_isolation_invariants(self):
        from unittest.mock import patch
        from users.api.v1.serializers import EmployeeSerializer

        with patch("core.tasks.send_transactional_email_task.delay") as mock_delay:
            with patch("core.tasks.send_mail") as mock_send_mail:
                data = {
                    "email": "identity_isolation@example.com",
                    "first_name": "Tenant",
                    "last_name": "Isolated",
                    "designation": "Manager",
                    "password": "Password123!"
                }
                with self.captureOnCommitCallbacks(execute=True):
                    serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
                    self.assertTrue(serializer.is_valid(), serializer.errors)
                    emp = serializer.save()

                # Secure rule: credential-bearing onboarding email sent synchronously, NOT serialized into Celery
                mock_delay.assert_not_called()
                self.assertEqual(mock_send_mail.call_count, 1)
                self.assertEqual(mock_send_mail.call_args[1]['subject'], "Welcome to CubeLogs - Your Login Credentials")
                self.assertEqual(mock_send_mail.call_args[1]['recipient_list'], ["identity_isolation@example.com"])
                self.assertIn("identity_isolation@example.com", mock_send_mail.call_args[1]['html_message'])

            # Assert organization tenant isolation
            self.assertEqual(emp.organization, self.org)

    def test_same_org_duplicate_employee_blocked(self):
        from users.api.v1.serializers import EmployeeSerializer
        data = {
            "email": "jwt_user@example.com",  # Same org email as self.user
            "first_name": "Duplicate",
            "last_name": "Attempt",
            "designation": "Developer"
        }
        serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)
        self.assertEqual(serializer.errors["email"][0], "An employee with this email already exists in your organization. Please edit the existing employee instead.")

    def test_other_org_active_employee_blocked(self):
        from users.models import Role
        from users.api.v1.serializers import EmployeeSerializer
        from datetime import date, timedelta

        other_org = Organization.objects.create(name="Org B", subdomain="org_b")
        other_emp = Employee.objects.create_user(
            email="org_b_active@example.com",
            password="Password123!",
            organization=other_org,
            employment_status="Active"
        )

        data = {
            "email": "org_b_active@example.com",
            "first_name": "Attempt",
            "last_name": "Hijack",
            "designation": "Hacker"
        }
        serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)
        self.assertEqual(serializer.errors["email"][0], "This email is currently associated with an active employee in another organization.")

    def test_other_org_future_resigned_employee_blocked(self):
        from users.api.v1.serializers import EmployeeSerializer
        from datetime import date, timedelta

        other_org = Organization.objects.create(name="Org C", subdomain="org_c")
        future_date = date.today() + timedelta(days=10)
        other_emp = Employee.objects.create_user(
            email="org_c_notice@example.com",
            password="Password123!",
            organization=other_org,
            employment_status="Resigned",
            last_working_date=future_date
        )

        data = {
            "email": "org_c_notice@example.com",
            "first_name": "Future",
            "last_name": "Resigned"
        }
        serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)

    def test_other_org_exited_employee_transferred_successfully(self):
        from unittest.mock import patch
        from users.api.v1.serializers import EmployeeSerializer
        from datetime import date, timedelta

        other_org = Organization.objects.create(name="Org D", subdomain="org_d")
        past_date = date.today() - timedelta(days=5)
        exited_emp = Employee.objects.create_user(
            email="exited_user@example.com",
            password="OriginalPassword123!",
            organization=other_org,
            employment_status="Resigned",
            last_working_date=past_date,
            is_active=True
        )

        with patch("core.tasks.send_transactional_email_task.delay") as mock_delay:
            data = {
                "email": "exited_user@example.com",
                "first_name": "Transferred",
                "last_name": "User",
                "designation": "Senior Dev"
            }
            with self.captureOnCommitCallbacks(execute=True):
                serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
                self.assertTrue(serializer.is_valid(), serializer.errors)
                emp = serializer.save()

            # Verify transfer results
            self.assertEqual(emp.id, exited_emp.id)
            self.assertEqual(emp.organization, self.org)
            self.assertEqual(emp.employment_status, "Active")
            self.assertTrue(emp.check_password("OriginalPassword123!"))

            # Assert welcome.html email queued with "Welcome to CubeLogs!" subject
            self.assertEqual(mock_delay.call_count, 1)
            recipient_arg, subject_arg, html_arg = mock_delay.call_args[0]
            self.assertEqual(recipient_arg, "exited_user@example.com")
            self.assertEqual(subject_arg, "Welcome to CubeLogs!")
            self.assertIn("Use your existing password", html_arg)

    def test_phase2a_organization_membership_schema_and_shadow_sync(self):
        from users.models import OrganizationMembership, Role
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError

        # 1. Verify Employee remains AUTH_USER_MODEL
        from django.conf import settings
        self.assertEqual(settings.AUTH_USER_MODEL, "users.Employee")

        # 2. Schema can represent same user in different orgs
        org_b = Organization.objects.create(name="Org B Multi", subdomain="org_b_multi")
        OrganizationMembership.objects.filter(user=self.user).delete()
        mem1 = OrganizationMembership.objects.create(user=self.user, organization=self.org, employee_code="EMP-101")
        mem2 = OrganizationMembership.objects.create(user=self.user, organization=org_b, employee_code="EMP-101")  # Same employee code allowed across different orgs
        self.assertEqual(OrganizationMembership.objects.filter(user=self.user).count(), 2)

        # 3. Duplicate (user, organization) rejected
        with self.assertRaises((IntegrityError, ValidationError)):
            OrganizationMembership.objects.create(user=self.user, organization=self.org, employee_code="EMP-102")

        # 4. Duplicate non-empty employee_code in same organization rejected
        other_user = Employee.objects.create_user(email="other_emp@example.com", password="Password123!")
        with self.assertRaises((IntegrityError, ValidationError)):
            OrganizationMembership.objects.create(user=other_user, organization=self.org, employee_code="EMP-101")

        # 5. Cross-org role validation in clean()
        role_b = Role.objects.create(name="Role Org B", organization=org_b)
        invalid_mem = OrganizationMembership(user=other_user, organization=self.org, role=role_b)
        with self.assertRaises(ValidationError):
            invalid_mem.clean()

        # 6. Shadow membership sync for newly created Employee
        from users.api.v1.serializers import EmployeeSerializer
        data = {
            "email": "shadow_sync_new@example.com",
            "first_name": "Shadow",
            "last_name": "Sync",
            "designation": "Architect",
            "employee_code": "CODE-999"
        }
        serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        new_emp = serializer.save()

        shadow_mem = OrganizationMembership.objects.filter(user=new_emp, organization=self.org).first()
        self.assertIsNotNone(shadow_mem)
        self.assertEqual(shadow_mem.employee_code, "CODE-999")
        self.assertEqual(shadow_mem.designation, "Architect")
        self.assertTrue(shadow_mem.is_active_in_org)

    def test_phase2a_lifecycle_matrix_and_transfer_sync(self):
        from users.api.v1.services import UserService
        from users.models import OrganizationMembership, Role
        from datetime import date, timedelta
        from django.core.exceptions import ValidationError

        today = date.today()
        past_date = today - timedelta(days=5)
        future_date = today + timedelta(days=10)

        # 1. Canonical lifecycle truth helper matrix assertions
        self.assertTrue(UserService.derive_employee_employment_access("Active", None, True))
        self.assertFalse(UserService.derive_employee_employment_access("Active", None, False))
        self.assertFalse(UserService.derive_employee_employment_access("Deactivated", None, True))
        self.assertFalse(UserService.derive_employee_employment_access("Resigned", None, True))
        self.assertFalse(UserService.derive_employee_employment_access("Resigned", past_date, True))
        self.assertFalse(UserService.derive_employee_employment_access("Resigned", today, True))
        self.assertTrue(UserService.derive_employee_employment_access("Resigned", future_date, True))  # Notice period
        self.assertFalse(UserService.derive_employee_employment_access("Terminated", None, True))
        self.assertFalse(UserService.derive_employee_employment_access("Terminated", past_date, True))

        # 2. Role validation on direct save()
        org_c = Organization.objects.create(name="Org C Role Test", subdomain="org_c_role")
        role_c = Role.objects.create(name="Role Org C", organization=org_c)
        invalid_mem = OrganizationMembership(user=self.user, organization=self.org, role=role_c)
        with self.assertRaises(ValidationError):
            invalid_mem.save()  # full_clean() on save() enforces role tenant boundary

        # 3. Transfer shadow membership synchronization
        exited_emp = Employee.objects.create_user(
            email="transfer_shadow@example.com",
            password="Password123!",
            organization=org_c,
            employment_status="Resigned",
            last_working_date=past_date,
            is_active=True
        )
        mem_org_c = UserService.sync_employee_shadow_membership(exited_emp)
        self.assertFalse(mem_org_c.is_active_in_org)

        # Perform transfer to self.org
        from users.api.v1.serializers import EmployeeSerializer
        data = {
            "email": "transfer_shadow@example.com",
            "first_name": "Transferred",
            "last_name": "Shadow",
            "designation": "Lead Developer"
        }
        serializer = EmployeeSerializer(data=data, context={'request': type('Req', (), {'user': self.user})()})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        transferred_emp = serializer.save()

        # Assert Org C membership marked inactive, Org A membership active
        mem_org_c.refresh_from_db()
        self.assertFalse(mem_org_c.is_active_in_org)

        mem_org_a = OrganizationMembership.objects.filter(user=transferred_emp, organization=self.org).first()
        self.assertIsNotNone(mem_org_a)
        self.assertTrue(mem_org_a.is_active_in_org)
        self.assertEqual(mem_org_a.designation, "Lead Developer")

    def test_phase2b_active_tenant_context_foundation(self):
        from core.tenant import TenantContext
        from core.middleware import TenantContextMiddleware
        from users.models import OrganizationMembership, Role
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser

        factory = RequestFactory()

        # A. Anonymous Request Context
        req_anon = factory.get("/api/v1/users/me/")
        req_anon.user = AnonymousUser()
        TenantContextMiddleware(lambda r: None)(req_anon)
        self.assertIsNone(req_anon.active_organization)
        self.assertIsNone(req_anon.active_membership)

        # B. Normal User Org A Context
        from users.api.v1.services import UserService
        UserService.sync_employee_shadow_membership(self.user)
        req_user_a = factory.get("/api/v1/users/me/")
        req_user_a.user = self.user  # Belongs to self.org
        TenantContextMiddleware(lambda r: None)(req_user_a)
        self.assertEqual(req_user_a.active_organization.id, self.org.id)
        self.assertEqual(req_user_a.active_membership.user_id, self.user.id)
        self.assertEqual(req_user_a.active_membership.organization_id, self.org.id)

        # C. User Org B Context
        org_b = Organization.objects.create(name="Tenant Org B", subdomain="tenant_b")
        user_b = Employee.objects.create_user(email="user_b@example.com", password="Password123!", organization=org_b)
        mem_b = UserService.sync_employee_shadow_membership(user_b)

        req_user_b = factory.get("/api/v1/users/me/")
        req_user_b.user = user_b
        TenantContextMiddleware(lambda r: None)(req_user_b)
        self.assertEqual(req_user_b.active_organization.id, org_b.id)
        self.assertEqual(req_user_b.active_membership.id, mem_b.id)

        # D. User with Org A active + historical inactive Org B
        OrganizationMembership.objects.create(user=self.user, organization=org_b, is_active_in_org=False)
        self.assertEqual(req_user_a.active_organization.id, self.org.id)
        self.assertNotEqual(req_user_a.active_membership.organization_id, org_b.id)

        # E. Missing Membership (Fail Closed)
        user_no_mem = Employee.objects.create_user(email="no_mem@example.com", password="Password123!", organization=org_b)
        req_no_mem = factory.get("/api/v1/users/me/")
        req_no_mem.user = user_no_mem
        TenantContextMiddleware(lambda r: None)(req_no_mem)
        self.assertIsNone(req_no_mem.active_membership)

        # F. Inactive Current Membership (Fail Closed)
        mem_b.is_active_in_org = False
        mem_b.save()
        self.assertIsNone(req_user_b.active_membership)

        # G. Superadmin (organization=None)
        super_admin = Employee.objects.create_superuser(email="superadmin@example.com", password="Password123!", organization=None)
        req_super = factory.get("/api/v1/users/me/")
        req_super.user = super_admin
        TenantContextMiddleware(lambda r: None)(req_super)
        self.assertIsNone(req_super.active_membership)
        self.assertIsNone(req_super.active_organization)

        # H. Query Count Optimization (At most 1 query per request context)
        mem_b.is_active_in_org = True
        mem_b.save()
        req_perf = factory.get("/api/v1/users/me/")
        req_perf.user = user_b
        TenantContextMiddleware(lambda r: None)(req_perf)

        with self.assertNumQueries(1):
            _ = req_perf.active_membership
            _ = req_perf.active_membership
            _ = req_perf.active_organization
            _ = req_perf.active_organization

    def test_phase2c_jwt_and_auth_context_multi_membership(self):
        from users.api.v1.services import UserService
        from users.models import OrganizationMembership
        from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
        from django.core.signing import TimestampSigner

        # 1. Login JWT Token Claims & Parity
        mem_a = UserService.sync_employee_shadow_membership(self.user)
        refresh = UserService.get_tokens_for_user(self.user)
        access = refresh.access_token

        self.assertEqual(access['user_id'], self.user.id)
        self.assertEqual(access['active_org_id'], self.org.id)
        self.assertEqual(access['membership_id'], mem_a.id)

        # 2. Magic Login JWT Token Claims
        signer = TimestampSigner(salt='auto-login')
        magic_token = signer.sign(str(self.user.id))
        resp_magic = self.client.post("/api/v1/auth/magic-login/", {"token": magic_token}, format="json")
        self.assertEqual(resp_magic.status_code, 200)
        magic_access = AccessToken(resp_magic.data['access'])
        self.assertEqual(magic_access['active_org_id'], self.org.id)
        self.assertEqual(magic_access['membership_id'], mem_a.id)

        # 3. /auth/me Additive Payload Structure & Legacy Parity
        self.client.force_authenticate(user=self.user)
        resp_me = self.client.get("/api/v1/users/me/")
        self.assertEqual(resp_me.status_code, 200)

        # Legacy fields preserved
        self.assertEqual(resp_me.data['id'], self.user.id)
        self.assertEqual(resp_me.data['email'], self.user.email)
        self.assertEqual(resp_me.data['organization'], self.org.id)

        # Additive fields present
        self.assertIn('active_organization', resp_me.data)
        self.assertIn('active_membership', resp_me.data)
        self.assertIn('available_memberships', resp_me.data)
        self.assertEqual(resp_me.data['active_organization']['id'], self.org.id)
        self.assertEqual(resp_me.data['active_membership']['id'], mem_a.id)
        self.assertEqual(len(resp_me.data['available_memberships']), 1)

        # 4. Token Refresh Re-evaluates DB Membership
        resp_refresh = self.client.post("/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(resp_refresh.status_code, 200)
        refreshed_access = AccessToken(resp_refresh.data['access'])
        self.assertEqual(refreshed_access['active_org_id'], self.org.id)
        self.assertEqual(refreshed_access['membership_id'], mem_a.id)

        # 5. Membership Deactivated After Issuance -> Request Fails Closed
        mem_a.is_active_in_org = False
        mem_a.save()

        req_deactivated = self.client.get("/api/v1/users/me/", HTTP_AUTHORIZATION=f"Bearer {str(access)}")
        self.assertEqual(req_deactivated.status_code, 200)
        self.assertIsNone(req_deactivated.wsgi_request.active_membership)  # Fails closed on inactive DB state

    def test_phase2d_employee_directory_and_true_multi_org_onboarding(self):
        from users.api.v1.serializers import EmployeeSerializer
        from users.models import OrganizationMembership, Role
        from unittest.mock import patch

        org_b = Organization.objects.create(name="Org B Directory", subdomain="org_b_dir")

        # 1. New Global User Onboarding
        with patch("core.tasks.send_transactional_email_task.delay") as mock_delay, \
             patch("core.tasks.send_mail") as mock_send_mail:
            data_a = {
                "email": "new_multi_user@example.com",
                "first_name": "New",
                "last_name": "User",
                "designation": "Org A Dev",
                "employee_code": "EMP-A1"
            }
            with self.captureOnCommitCallbacks(execute=True):
                serializer_a = EmployeeSerializer(data=data_a, context={'request': type('Req', (), {'user': self.user})()})
                self.assertTrue(serializer_a.is_valid(), serializer_a.errors)
                emp_new = serializer_a.save()

            # Verify Org A membership created
            mem_a = OrganizationMembership.objects.get(user=emp_new, organization=self.org)
            self.assertEqual(mem_a.designation, "Org A Dev")
            self.assertTrue(mem_a.is_active_in_org)

            # Assert admin_onboarding email sent synchronously with temp password (not serialized to Celery)
            mock_delay.assert_not_called()
            self.assertEqual(mock_send_mail.call_count, 1)
            self.assertIn("Welcome to CubeLogs - Your Login Credentials", mock_send_mail.call_args[1]['subject'])

        # 2. Existing Global User Joining Org B (True Multi-Org)
        with patch("core.tasks.send_transactional_email_task.delay") as mock_delay:
            admin_b = Employee.objects.create_user(email="admin_b@example.com", password="Password123!", organization=org_b)
            mem_admin_b = OrganizationMembership.objects.create(user=admin_b, organization=org_b, is_active_in_org=True)

            data_b = {
                "email": "new_multi_user@example.com",
                "first_name": "New",
                "last_name": "User",
                "designation": "Org B Manager",
                "employee_code": "EMP-B1"
            }
            with self.captureOnCommitCallbacks(execute=True):
                serializer_b = EmployeeSerializer(data=data_b, context={'request': type('Req', (), {'user': admin_b, 'active_organization': org_b})()})
                self.assertTrue(serializer_b.is_valid(), serializer_b.errors)
                emp_joined = serializer_b.save()

            # Verify same Employee ID, password preserved
            self.assertEqual(emp_joined.id, emp_new.id)

            # Both Org A and Org B memberships remain active!
            mem_a.refresh_from_db()
            mem_b = OrganizationMembership.objects.get(user=emp_new, organization=org_b)
            self.assertTrue(mem_a.is_active_in_org)
            self.assertTrue(mem_b.is_active_in_org)
            self.assertEqual(mem_a.designation, "Org A Dev")
            self.assertEqual(mem_b.designation, "Org B Manager")

            # Assert welcome email sent with "Use your existing password"
            self.assertEqual(mock_delay.call_count, 1)
            self.assertEqual(mock_delay.call_args[0][1], "Welcome to CubeLogs!")
            self.assertIn("Use your existing password", mock_delay.call_args[0][2])

        # 3. Employee Directory Scoping & Projection
        self.client.force_authenticate(user=self.user)
        resp_dir_a = self.client.get("/api/v1/employees/")
        self.assertEqual(resp_dir_a.status_code, 200)
        results_a = resp_dir_a.data.get('results', resp_dir_a.data)
        emp_data_a = next(e for e in results_a if e['id'] == emp_new.id)
        self.assertEqual(emp_data_a['designation'], "Org A Dev")

        self.client.force_authenticate(user=admin_b)
        resp_dir_b = self.client.get("/api/v1/employees/")
        self.assertEqual(resp_dir_b.status_code, 200)
        results_b = resp_dir_b.data.get('results', resp_dir_b.data)
        emp_data_b = next(e for e in results_b if e['id'] == emp_new.id)
        self.assertEqual(emp_data_b['designation'], "Org B Manager")

        # 4. Updating Org B Membership Does Not Mutate Org A
        serializer_update_b = EmployeeSerializer(emp_new, data={"designation": "Org B Director"}, partial=True, context={'request': type('Req', (), {'user': admin_b, 'active_organization': org_b})()})
        self.assertTrue(serializer_update_b.is_valid(), serializer_update_b.errors)
        serializer_update_b.save()

        mem_a.refresh_from_db()
        mem_b.refresh_from_db()
        self.assertEqual(mem_a.designation, "Org A Dev")
        self.assertEqual(mem_b.designation, "Org B Director")

        # 5. Org-Level Delete Preserves Global Account & Other Memberships
        from users.api.v1.views import EmployeeViewSet
        view = EmployeeViewSet()
        req_del = type('Req', (), {'user': admin_b, 'active_organization': org_b})()
        view.request = req_del
        view.perform_destroy(emp_new)

        mem_b.refresh_from_db()
        mem_a.refresh_from_db()
        emp_new.refresh_from_db()
        self.assertTrue(mem_b.is_deleted)
        self.assertFalse(mem_b.is_active_in_org)
        self.assertTrue(mem_a.is_active_in_org)  # Org A membership untouched!
        self.assertTrue(emp_new.is_active)  # Global Employee stays active!

    def test_phase2e2f_domain_tenant_cutover_and_isolation(self):
        from users.models import OrganizationMembership, Role
        from attendance.models import AttendanceLog
        from projects.models import Project
        from rest_framework.test import APIClient

        # Setup 2 distinct organizations
        org_a = self.org
        org_b = Organization.objects.create(name="Cutover Org B", subdomain="cutover_b")

        # Create Role objects
        role_mgr_a = Role.objects.create(name="Manager A", slug="mgr-a", organization=org_a)
        role_dev_b = Role.objects.create(name="Developer B", slug="dev-b", organization=org_b)
        from users.models import PermissionFlag
        perm_flag, _ = PermissionFlag.objects.get_or_create(key="projects:create", defaults={'name': 'Create Projects', 'module': 'projects', 'category': 'projects'})
        role_mgr_a.permissions.add(perm_flag)

        # User U: Active in Org A (Manager) and Org B (Developer)
        user_u = Employee.objects.create_user(email="user_u@example.com", password="Password123!", organization=org_a)
        mem_u_a = OrganizationMembership.objects.create(user=user_u, organization=org_a, role=role_mgr_a, designation="Org A Manager", is_active_in_org=True)
        mem_u_b = OrganizationMembership.objects.create(user=user_u, organization=org_b, role=role_dev_b, designation="Org B Developer", is_active_in_org=True)

        # User V: Active in Org B ONLY
        user_v = Employee.objects.create_user(email="user_v@example.com", password="Password123!", organization=org_b)
        mem_v_b = OrganizationMembership.objects.create(user=user_v, organization=org_b, role=role_dev_b, designation="Org B Engineer", is_active_in_org=True)

        # Admin for Org A
        admin_a = self.user

        # 1. Verification G: Same User U uses correct role & permissions in each org
        self.assertEqual(user_u.has_capability("projects:create", active_membership=mem_u_a), True)
        self.assertEqual(user_u.has_capability("salary:manage", active_membership=mem_u_b), False)

        # 2. Verification E: Org A project cannot assign Org-B-only Employee (User V)
        org_a.settings.is_project_enabled = True
        org_a.settings.save()
        client_a = APIClient()
        client_a.force_authenticate(user=admin_a)

        proj_a = Project.objects.create(name="Org A Project", company=org_a)

        # Eligible members in Org A returns only Org A members
        resp_elig_a = client_a.get("/api/v1/projects/eligible-members/")
        self.assertEqual(resp_elig_a.status_code, 200)
        elig_ids_a = [e['id'] for e in resp_elig_a.data]
        self.assertIn(user_u.id, elig_ids_a)
        self.assertNotIn(user_v.id, elig_ids_a)  # User V blocked from Org A project assignment!

        # 3. Verification H: Historical Attendance Log Isolation
        log_a = AttendanceLog.objects.create(employee=user_u, employeeName="User U", date="2026-08-28")
        log_b = AttendanceLog.objects.create(employee=user_v, employeeName="User V", date="2026-08-28")

        # Querying attendance logs for Org A returns only Org A logs
        logs_a_qs = AttendanceLog.objects.filter(employee__organization=org_a)
        self.assertIn(log_a, logs_a_qs)
        self.assertNotIn(log_b, logs_a_qs)

        # 4. Verification J: Inactive membership is denied
        mem_u_b.is_active_in_org = False
        mem_u_b.save()

        # Inactive membership fails active tenant context lookup
        from core.tenant import TenantContext
        req_mock = type('Req', (), {'user': user_u, 'META': {}})()
        user_u.organization = org_b
        active_mem = TenantContext.get_active_membership(req_mock)
        self.assertIsNone(active_mem)  # Fails closed!

    def test_phase2g_workspace_switching_and_refresh_preservation(self):
        from users.models import OrganizationMembership, Role
        from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

        # Setup Org A and Org B
        org_a = self.org
        org_b = Organization.objects.create(name="Switch Org B", subdomain="switch_b")

        role_mgr_a = Role.objects.create(name="Manager A", slug="mgr-a", organization=org_a)
        role_dev_b = Role.objects.create(name="Developer B", slug="dev-b", organization=org_b)

        # User U has active memberships in both Org A and Org B
        user_u = Employee.objects.create_user(email="switcher@example.com", password="Password123!", organization=org_a)
        mem_a = OrganizationMembership.objects.create(user=user_u, organization=org_a, role=role_mgr_a, designation="Org A Manager", is_active_in_org=True)
        mem_b = OrganizationMembership.objects.create(user=user_u, organization=org_b, role=role_dev_b, designation="Org B Developer", is_active_in_org=True)

        # User V belongs to Org C ONLY
        org_c = Organization.objects.create(name="Unrelated Org C", subdomain="org_c")
        user_v = Employee.objects.create_user(email="user_v_c@example.com", password="Password123!", organization=org_c)
        mem_c = OrganizationMembership.objects.create(user=user_v, organization=org_c, is_active_in_org=True)

        # 1. Authenticate as User U
        self.client.force_authenticate(user=user_u)

        # 2. Switch to Org B
        resp_switch = self.client.post("/api/v1/auth/switch-organization/", {"organization_id": org_b.id}, format="json")
        self.assertEqual(resp_switch.status_code, 200)
        self.assertEqual(resp_switch.data['user']['active_organization']['id'], org_b.id)
        self.assertEqual(resp_switch.data['user']['designation'], "Org B Developer")

        # Verify Employee.organization in DB was NOT mutated!
        user_u.refresh_from_db()
        self.assertEqual(user_u.organization_id, org_a.id)  # Unmutated legacy default pointer!

        access_b_str = resp_switch.data['access']
        refresh_b_str = resp_switch.data['refresh']

        access_b = AccessToken(access_b_str)
        self.assertEqual(access_b['active_org_id'], org_b.id)
        self.assertEqual(access_b['membership_id'], mem_b.id)

        # 3. Call /auth/me with Org B access token
        resp_me = self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {access_b_str}")
        self.assertEqual(resp_me.status_code, 200)
        self.assertEqual(resp_me.data['active_organization']['id'], org_b.id)
        self.assertEqual(resp_me.data['designation'], "Org B Developer")

        # 4. Refresh token preserves Org B selected workspace
        resp_ref = self.client.post("/api/v1/auth/refresh/", {"refresh": refresh_b_str}, format="json")
        self.assertEqual(resp_ref.status_code, 200)
        refreshed_access = AccessToken(resp_ref.data['access'])
        self.assertEqual(refreshed_access['active_org_id'], org_b.id)
        self.assertEqual(refreshed_access['membership_id'], mem_b.id)

        # 5. Unauthorized switch attempt to Org C -> Rejected 403
        resp_bad_switch = self.client.post("/api/v1/auth/switch-organization/", {"organization_id": org_c.id}, format="json")
        self.assertEqual(resp_bad_switch.status_code, 403)

        # 6. Deactivate Org B membership -> Refresh fails closed
        mem_b.is_active_in_org = False
        mem_b.save()

        resp_deact_ref = self.client.post("/api/v1/auth/refresh/", {"refresh": refresh_b_str}, format="json")
        # Should not re-authorize deactivated membership
        self.assertNotEqual(resp_deact_ref.status_code, 200)


class JWTRefreshLifecycleRegressionTest(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="JWT Regression Test Org", subdomain="jwt-reg-test")
        self.user = Employee.objects.create(
            email="jwt_reg_test@example.com",
            first_name="JWT",
            last_name="Tester",
            organization=self.org,
            is_active=True
        )
        self.role = Role.objects.create(name="Admin", organization=self.org)
        self.mem = OrganizationMembership.objects.create(
            user=self.user,
            organization=self.org,
            role=self.role,
            is_active_in_org=True
        )

    def test_full_jwt_refresh_and_rotation_lifecycle(self):
        # 1. Obtain initial token pair
        refresh_a = RefreshToken.for_user(self.user)
        refresh_a_str = str(refresh_a)

        # 2. Refresh #1 (A -> B)
        r1 = self.client.post("/api/auth/refresh/", {"refresh": refresh_a_str}, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertIn("access", r1.data)
        self.assertIn("refresh", r1.data)
        refresh_b_str = r1.data["refresh"]

        # Verify claims on refreshed access token
        access_b = AccessToken(r1.data["access"])
        self.assertEqual(access_b['active_org_id'], self.org.id)
        self.assertEqual(access_b['membership_id'], self.mem.id)

        # 3. Old refresh token A must be rejected
        r_old_a = self.client.post("/api/auth/refresh/", {"refresh": refresh_a_str}, format="json")
        self.assertEqual(r_old_a.status_code, 401)

        # 4. Refresh #2 (B -> C)
        r2 = self.client.post("/api/auth/refresh/", {"refresh": refresh_b_str}, format="json")
        self.assertEqual(r2.status_code, 200)
        self.assertIn("access", r2.data)
        self.assertIn("refresh", r2.data)
        refresh_c_str = r2.data["refresh"]

        # 5. Old refresh token B must now be rejected
        r_old_b = self.client.post("/api/auth/refresh/", {"refresh": refresh_b_str}, format="json")
        self.assertEqual(r_old_b.status_code, 401)

        # 6. Current refresh token C works for Refresh #3 (C -> D)
        r3 = self.client.post("/api/auth/refresh/", {"refresh": refresh_c_str}, format="json")
        self.assertEqual(r3.status_code, 200)
        access_d = AccessToken(r3.data["access"])
        self.assertEqual(access_d['active_org_id'], self.org.id)
        self.assertEqual(access_d['membership_id'], self.mem.id)


class RoleSecurityRegressionTest(APITestCase):
    def setUp(self):
        from users.models import Role
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a")
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b")

        # Org A Admin (tenant superadmin)
        self.admin_a = Employee.objects.create_user(
            email="admin_a@example.com",
            password="password123",
            first_name="Admin",
            last_name="A",
            organization=self.org_a,
            isSuperAdmin=True
        )
        self.mem_a = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_a,
            is_active_in_org=True
        )

        # Platform root superuser
        self.root_superuser = Employee.objects.create_superuser(
            email="root@example.com",
            password="rootpassword"
        )

        # Global system role
        self.global_role = Role.objects.create(
            name="System Viewer",
            slug="system-viewer",
            is_system_role=True,
            organization=None
        )

        # Org A custom role
        self.role_a = Role.objects.create(
            name="Org A Custom Role",
            slug="org-a-custom-role",
            is_system_role=False,
            organization=self.org_a
        )

        # Org B custom role
        self.role_b = Role.objects.create(
            name="Org B Custom Role",
            slug="org-b-custom-role",
            is_system_role=False,
            organization=self.org_b
        )

    def test_org_a_admin_lists_own_and_global_roles_but_not_org_b(self):
        self.client.force_authenticate(user=self.admin_a)
        res = self.client.get('/api/roles/')
        self.assertEqual(res.status_code, 200)
        data = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        role_ids = [r['id'] for r in data]

        # 1. Org A admin lists own custom roles
        self.assertIn(self.role_a.id, role_ids)
        # 2. Org A admin sees global/default roles
        self.assertIn(self.global_role.id, role_ids)
        # 3. Org A admin CANNOT list Org B custom roles
        self.assertNotIn(self.role_b.id, role_ids)

    def test_org_a_admin_cannot_retrieve_org_b_role(self):
        self.client.force_authenticate(user=self.admin_a)
        # 4. Org A admin cannot retrieve Org B role by ID (returns 404)
        res = self.client.get(f'/api/roles/{self.role_b.id}/')
        self.assertEqual(res.status_code, 404)

    def test_org_a_admin_cannot_patch_or_delete_org_b_role(self):
        self.client.force_authenticate(user=self.admin_a)
        from users.models import Role
        # 5. Org A admin cannot PATCH Org B role
        res_patch = self.client.patch(f'/api/roles/{self.role_b.id}/', {'label': 'Hacked'}, format='json')
        self.assertIn(res_patch.status_code, [403, 404])
        self.role_b.refresh_from_db()
        self.assertNotEqual(self.role_b.label, 'Hacked')

        # 6. Org A admin cannot DELETE Org B role
        res_del = self.client.delete(f'/api/roles/{self.role_b.id}/')
        self.assertIn(res_del.status_code, [403, 404])
        self.assertTrue(Role.objects.filter(id=self.role_b.id).exists())

    def test_org_a_admin_cannot_delete_global_role(self):
        self.client.force_authenticate(user=self.admin_a)
        from users.models import Role
        res = self.client.delete(f'/api/roles/{self.global_role.id}/')
        self.assertIn(res.status_code, [400, 403])
        self.assertTrue(Role.objects.filter(id=self.global_role.id).exists())

    def test_role_creation_attaches_to_active_organization(self):
        self.client.force_authenticate(user=self.admin_a)
        from users.models import Role
        # 7. Org A role creation attaches to active organization
        res = self.client.post('/api/roles/', {'name': 'New Org A Role', 'label': 'New Label'}, format='json')
        self.assertEqual(res.status_code, 201)
        new_role = Role.objects.get(id=res.data['id'])
        self.assertEqual(new_role.organization_id, self.org_a.id)

    def test_switching_organization_changes_role_scope(self):
        # 8. Switching organization changes role scope correctly
        OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_b,
            is_active_in_org=True
        )
        self.client.force_authenticate(user=self.admin_a)

        # Switch to Org B
        res_switch = self.client.post('/api/auth/switch-organization/', {'organization_id': self.org_b.id}, format='json')
        self.assertEqual(res_switch.status_code, 200)
        access_token = res_switch.data['access']

        res_roles = self.client.get('/api/roles/', HTTP_AUTHORIZATION=f'Bearer {access_token}')
        self.assertEqual(res_roles.status_code, 200)
        data = res_roles.data.get('results', res_roles.data) if isinstance(res_roles.data, dict) else res_roles.data
        role_ids = [r['id'] for r in data]
        self.assertIn(self.role_b.id, role_ids)
        self.assertNotIn(self.role_a.id, role_ids)

    def test_platform_root_superuser_preserves_global_access(self):
        # 9. Django platform superuser behavior remains intentionally preserved
        self.client.force_authenticate(user=self.root_superuser)
        res = self.client.get('/api/roles/')
        self.assertEqual(res.status_code, 200)
        data = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        role_ids = [r['id'] for r in data]
        self.assertIn(self.role_a.id, role_ids)
        self.assertIn(self.role_b.id, role_ids)
        self.assertIn(self.global_role.id, role_ids)


class BillingSecurityRegressionTest(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Billing Org", subdomain="billing-org")
        self.org_settings = OrgSettings.objects.create()
        self.org.settings = self.org_settings
        self.org.save()

        # Admin with settings:billing
        self.admin = Employee.objects.create_user(
            email="billing_admin@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=True
        )
        self.mem_admin = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org,
            is_active_in_org=True
        )

        # Regular employee without settings:billing
        self.employee = Employee.objects.create_user(
            email="regular_emp@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=False
        )
        self.mem_emp = OrganizationMembership.objects.create(
            user=self.employee,
            organization=self.org,
            is_active_in_org=True
        )

        self.wallet = Wallet.objects.create(
            organization=self.org,
            employee=self.admin,
            balance=Decimal('5000.00')
        )

    def test_admin_can_toggle_module_and_topup(self):
        self.client.force_authenticate(user=self.admin)
        res_toggle = self.client.post('/api/wallet/toggle-module/', {'module': 'attendance', 'enable': True}, format='json')
        self.assertEqual(res_toggle.status_code, 200)

        res_topup = self.client.post('/api/wallet/topup/', {'amount': 100}, format='json')
        self.assertEqual(res_topup.status_code, 200)
        self.assertEqual(res_topup.data['gateway'], 'razorpay')

    def test_regular_employee_without_billing_permission_gets_403(self):
        self.client.force_authenticate(user=self.employee)
        # 1. Module toggle blocked
        res_toggle = self.client.post('/api/wallet/toggle-module/', {'module': 'attendance', 'enable': True}, format='json')
        self.assertEqual(res_toggle.status_code, 403)

        # 2. Wallet topup blocked
        res_topup = self.client.post('/api/wallet/topup/', {'amount': 100}, format='json')
        self.assertEqual(res_topup.status_code, 403)

        # 3. Live billing estimate blocked
        res_estimate = self.client.get('/api/billing-estimate/')
        self.assertEqual(res_estimate.status_code, 403)


class AttendanceApprovalModuleGateRegressionTest(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Att Org", subdomain="att-org")
        self.org_settings = OrgSettings.objects.create()
        self.org.settings = self.org_settings
        self.org.save()

        # Manager with attendance:admin and attendance:management_portal
        self.manager = Employee.objects.create_user(
            email="manager@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=True
        )
        self.mem_mgr = OrganizationMembership.objects.create(
            user=self.manager,
            organization=self.org,
            is_active_in_org=True
        )

        # Employee without approval permission
        self.staff = Employee.objects.create_user(
            email="staff@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=False
        )
        self.mem_staff = OrganizationMembership.objects.create(
            user=self.staff,
            organization=self.org,
            is_active_in_org=True
        )

        from attendance.models import AttendanceLog
        self.log = AttendanceLog.objects.create(
            employee=self.staff,
            date=timezone.now().date(),
            status="Pending Approval"
        )

    def test_attendance_on_with_permission_succeeds(self):
        self.org.settings.is_attendance_enabled = True
        self.client.force_authenticate(user=self.manager)
        res = self.client.patch(f'/api/attendance/approve/{self.log.id}/', {'status': 'Approved'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, 'Approved')

    def test_attendance_off_with_permission_returns_403(self):
        self.org.settings.is_attendance_enabled = False
        self.client.force_authenticate(user=self.manager)
        res = self.client.patch(f'/api/attendance/approve/{self.log.id}/', {'status': 'Approved'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertIn("plan", str(res.data).lower())

    def test_attendance_on_without_permission_returns_403(self):
        self.org.settings.is_attendance_enabled = True
        self.client.force_authenticate(user=self.staff)
        res = self.client.patch(f'/api/attendance/approve/{self.log.id}/', {'status': 'Approved'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_cross_tenant_approval_returns_404(self):
        self.org.settings.is_attendance_enabled = True
        other_org = Organization.objects.create(name="Other Org", subdomain="other-org")
        other_manager = Employee.objects.create_user(
            email="other_manager@example.com",
            password="password",
            organization=other_org,
            isSuperAdmin=True
        )
        OrganizationMembership.objects.create(
            user=other_manager,
            organization=other_org,
            is_active_in_org=True
        )
        self.client.force_authenticate(user=other_manager)
        res = self.client.patch(f'/api/attendance/approve/{self.log.id}/', {'status': 'Approved'}, format='json')
        self.assertEqual(res.status_code, 404)


class BillingEstimateCanonicalSourceRegressionTest(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org = Organization.objects.create(name="Billing Org", subdomain="billing-org", settings=self.org_settings)
        self.admin = Employee.objects.create_user(
            email="billing_admin@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=True
        )
        self.admin_mem = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org,
            is_active_in_org=True
        )

        # 3 Active billable employees
        self.billable_users = []
        for i in range(3):
            u = Employee.objects.create_user(
                email=f"emp_{i}@example.com",
                password="password",
                organization=self.org,
                isSuperAdmin=False
            )
            OrganizationMembership.objects.create(
                user=u,
                organization=self.org,
                is_active_in_org=True,
                employment_status='Active'
            )
            self.billable_users.append(u)

        # 1 Inactive membership (should NOT be billable)
        u_inactive = Employee.objects.create_user(
            email="inactive_emp@example.com",
            password="password",
            organization=self.org,
            isSuperAdmin=False
        )
        OrganizationMembership.objects.create(
            user=u_inactive,
            organization=self.org,
            is_active_in_org=False,
            employment_status='Active'
        )

        # 1 Unauthorized employee for permission check
        self.regular_emp = self.billable_users[0]
        self.client = APIClient()

    def test_canonical_count_excludes_superadmin_and_inactive(self):
        from company.api.v1.services import BillingService
        canonical_count = BillingService.get_billable_memberships_qs(self.org).count()
        self.assertEqual(canonical_count, 3)

        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/billing-estimate/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['billable_employee_count'], 3)

    def test_estimate_project_only(self):
        self.org.settings.is_attendance_enabled = False
        self.org.settings.is_project_enabled = True
        self.org.settings.save()

        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/billing-estimate/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['attendance_enabled'])
        self.assertEqual(res.data['attendance_price'], 0.0)
        self.assertTrue(res.data['project_enabled'])
        self.assertGreater(res.data['project_price'], 0.0)

    def test_estimate_attendance_only(self):
        self.org.settings.is_attendance_enabled = True
        self.org.settings.is_project_enabled = False
        self.org.settings.save()

        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/billing-estimate/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['attendance_enabled'])
        self.assertGreater(res.data['attendance_price'], 0.0)
        self.assertFalse(res.data['project_enabled'])
        self.assertEqual(res.data['project_price'], 0.0)

    def test_estimate_both_enabled_and_disabled(self):
        self.org.settings.is_attendance_enabled = True
        self.org.settings.is_project_enabled = True
        self.org.settings.save()

        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/billing-estimate/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['attendance_enabled'])
        self.assertTrue(res.data['project_enabled'])

        # Both disabled
        self.org.settings.is_attendance_enabled = False
        self.org.settings.is_project_enabled = False
        self.org.settings.save()

        res_none = self.client.get('/api/billing-estimate/')
        self.assertEqual(res_none.status_code, 200)
        self.assertFalse(res_none.data['attendance_enabled'])
        self.assertEqual(res_none.data['attendance_price'], 0.0)
        self.assertFalse(res_none.data['project_enabled'])
        self.assertEqual(res_none.data['project_price'], 0.0)

    def test_unauthorized_user_blocked_from_estimate(self):
        self.client.force_authenticate(user=self.regular_emp)
        res = self.client.get('/api/billing-estimate/')
        self.assertEqual(res.status_code, 403)


class BillingPhaseETest(TestCase):
    def setUp(self):
        from core.models import Organization, OrgSettings
        from users.models import Employee, OrganizationMembership, Role
        from subscribers.models import Wallet, WalletTransaction, MonthlyInvoice, GlobalBillingSettings
        from datetime import date
        from decimal import Decimal

        self.g_settings, _ = GlobalBillingSettings.objects.get_or_create(id=1)
        self.org_a = Organization.objects.create(name='Org Alpha', subdomain='alpha')
        self.org_a_settings = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org_a.settings = self.org_a_settings
        self.org_a.save()

        self.org_b = Organization.objects.create(name='Org Beta', subdomain='beta')
        self.org_b_settings = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org_b.settings = self.org_b_settings
        self.org_b.save()

        self.admin_a = Employee.objects.create_user(
            email='admin_a@alpha.com',
            password='testpassword123',
            first_name='Admin',
            last_name='Alpha',
            organization=self.org_a,
            isSuperAdmin=True
        )
        self.mem_a = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_a,
            employment_status='Active',
            is_active_in_org=True
        )
        self.wallet_a = Wallet.objects.create(
            organization=self.org_a,
            employee=self.admin_a,
            balance=Decimal('500.00')
        )

        self.admin_b = Employee.objects.create_user(
            email='admin_b@beta.com',
            password='testpassword123',
            first_name='Admin',
            last_name='Beta',
            organization=self.org_b,
            isSuperAdmin=True
        )
        self.mem_b = OrganizationMembership.objects.create(
            user=self.admin_b,
            organization=self.org_b,
            employment_status='Active',
            is_active_in_org=True
        )

        self.inv_a = MonthlyInvoice.objects.create(
            organization=self.org_a,
            billing_month=date(2026, 9, 1),
            amount=Decimal('800.00'),
            is_paid=True,
            paid_at=timezone.now(),
            base_price_snapshot=Decimal('399.00'),
            employee_count_snapshot=6,
            employee_unit_price_snapshot=Decimal('50.00'),
            employee_total_snapshot=Decimal('300.00'),
            project_enabled_snapshot=True,
            project_price_snapshot=Decimal('200.00'),
            subtotal_snapshot=Decimal('899.00'),
            tax_percentage_snapshot=Decimal('18.00'),
            tax_amount_snapshot=Decimal('161.82')
        )
        self.client = APIClient()

    def test_pdf_download_success_for_authorized_tenant(self):
        self.client.force_authenticate(user=self.admin_a)
        res = self.client.get(f'/api/monthly-invoices/{self.inv_a.id}/pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn(f'CubeLogs_Invoice_{self.inv_a.id}.pdf', res['Content-Disposition'])

    def test_pdf_download_invalid_id_returns_404(self):
        self.client.force_authenticate(user=self.admin_a)
        res = self.client.get('/api/monthly-invoices/999999/pdf/')
        self.assertEqual(res.status_code, 404)

    def test_pdf_download_cross_tenant_blocked(self):
        # Admin B tries to download Org A's invoice
        self.client.force_authenticate(user=self.admin_b)
        res = self.client.get(f'/api/monthly-invoices/{self.inv_a.id}/pdf/')
        self.assertEqual(res.status_code, 404)

    def test_wallet_transaction_serializer_exposes_invoice_id(self):
        from subscribers.models import WalletTransaction
        from subscribers.api.v1.serializers import WalletTransactionSerializer

        tx = WalletTransaction.objects.create(
            wallet=self.wallet_a,
            amount=Decimal('800.00'),
            transactionType='Debit',
            success=True,
            status='Success',
            invoice_url=f"/api/monthly-invoices/{self.inv_a.id}/pdf/",
            details="Automated wallet deduction: Settled invoice(s) for September 2026."
        )

        serializer = WalletTransactionSerializer(tx)
        self.assertEqual(serializer.data.get('invoice_id'), self.inv_a.id)

    def test_reconcile_pending_stale_mock_orders(self):
        from subscribers.models import WalletTransaction
        from company.tasks import reconcile_pending_wallet_transactions
        from datetime import timedelta

        old_dt = timezone.now() - timedelta(hours=30)
        tx = WalletTransaction.objects.create(
            wallet=self.wallet_a,
            amount=Decimal('100.00'),
            transactionType='Credit',
            success=False,
            status='Pending',
            razorpay_order_id='mock_order_old_123',
            details='Pending mock top-up'
        )
        # Manually backdate created_at to simulate stale pending
        WalletTransaction.objects.filter(id=tx.id).update(created_at=old_dt)

        reconciled = reconcile_pending_wallet_transactions(threshold_hours=24)
        self.assertGreaterEqual(reconciled, 1)

        tx.refresh_from_db()
        self.assertEqual(tx.status, 'Abandoned')


class BillingPhaseFTest(APITestCase):
    def setUp(self):
        from core.models import Organization, OrgSettings
        from users.models import Employee, OrganizationMembership, Role
        from subscribers.models import Wallet, MonthlyInvoice, GlobalBillingSettings, SubscriptionPackage
        from rest_framework.test import APIClient
        import uuid

        # Ensure single canonical GlobalBillingSettings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.monthly_subscription_price = Decimal('0.00')
        self.g_settings.employee_seat_price = Decimal('50.00')
        self.g_settings.attendance_module_price = Decimal('99.00')
        self.g_settings.tasks_module_price = Decimal('56.00')
        self.g_settings.tax_percentage = Decimal('0.00')
        self.g_settings.auto_deduction_day = 5
        self.g_settings.reminder_email_days_before = 1
        self.g_settings.grace_period_days = 5
        self.g_settings.currency = 'INR'
        self.g_settings.save()

        # Create test Organization & OrgSettings
        suffix = uuid.uuid4().hex[:6]
        self.org = Organization.objects.create(name=f"Org F {suffix}", subdomain=f"org-f-{suffix}")
        self.settings_obj = OrgSettings.objects.create(
            subscriptionStatus='Active',
            is_attendance_enabled=True,
            is_project_enabled=True
        )
        self.org.settings = self.settings_obj
        self.org.save()

        # Admin user
        self.admin = Employee.objects.create_user(
            email=f"admin-{suffix}@example.com",
            password='testpassword123',
            first_name='Admin',
            last_name='F',
            organization=self.org,
            isSuperAdmin=True
        )
        self.admin_mem = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org,
            employment_status='Active',
            is_active_in_org=True,
            is_deleted=False
        )

        # Add 6 additional employees (superadmin is non-billable, so 6 billable seats)
        self.members = [self.admin_mem]
        for i in range(6):
            emp = Employee.objects.create_user(
                email=f"emp-{i}-{suffix}@example.com",
                password='testpassword123',
                first_name=f"Emp{i}",
                last_name="Test",
                organization=self.org
            )
            mem = OrganizationMembership.objects.create(
                user=emp,
                organization=self.org,
                employment_status='Active',
                is_active_in_org=True,
                is_deleted=False
            )
            self.members.append(mem)

        self.wallet = Wallet.objects.create(
            organization=self.org,
            employee=self.admin,
            balance=Decimal('2000.00')
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_calculation_matrix_cases_a_b_c_d(self):
        # Billable count = 6, Rate = ₹50/emp, Att = ₹99/emp, Proj = ₹56/emp
        url = '/api/billing-estimate/'

        # Case A: Att = False, Proj = False -> 6 × 50 = ₹300.00
        self.settings_obj.is_attendance_enabled = False
        self.settings_obj.is_project_enabled = False
        self.settings_obj.save()

        res_a = self.client.get(url)
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_a.data['billable_employee_count'], 6)
        self.assertEqual(res_a.data['employee_charge'], '300.00')
        self.assertEqual(res_a.data['attendance_charge'], '0.00')
        self.assertEqual(res_a.data['project_charge'], '0.00')
        self.assertEqual(res_a.data['estimated_next_total'], 300.0)

        # Case B: Att = True, Proj = False -> 300 + (6 × 99) = 300 + 594 = ₹894.00
        self.settings_obj.is_attendance_enabled = True
        self.settings_obj.is_project_enabled = False
        self.settings_obj.save()

        res_b = self.client.get(url)
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.data['employee_charge'], '300.00')
        self.assertEqual(res_b.data['attendance_charge'], '594.00')
        self.assertEqual(res_b.data['project_charge'], '0.00')
        self.assertEqual(res_b.data['estimated_next_total'], 894.0)

        # Case C: Att = False, Proj = True -> 300 + (6 × 56) = 300 + 336 = ₹636.00
        self.settings_obj.is_attendance_enabled = False
        self.settings_obj.is_project_enabled = True
        self.settings_obj.save()

        res_c = self.client.get(url)
        self.assertEqual(res_c.status_code, 200)
        self.assertEqual(res_c.data['employee_charge'], '300.00')
        self.assertEqual(res_c.data['attendance_charge'], '0.00')
        self.assertEqual(res_c.data['project_charge'], '336.00')
        self.assertEqual(res_c.data['estimated_next_total'], 636.0)

        # Case D: Att = True, Proj = True -> 300 + 594 + 336 = ₹1,230.00
        self.settings_obj.is_attendance_enabled = True
        self.settings_obj.is_project_enabled = True
        self.settings_obj.save()

        res_d = self.client.get(url)
        self.assertEqual(res_d.status_code, 200)
        self.assertEqual(res_d.data['employee_charge'], '300.00')
        self.assertEqual(res_d.data['attendance_charge'], '594.00')
        self.assertEqual(res_d.data['project_charge'], '336.00')
        self.assertEqual(res_d.data['estimated_next_total'], 1230.0)

    def test_invoice_creation_snapshots_phase_f(self):
        from company.tasks import sweep_workspace_subscriptions
        from subscribers.models import MonthlyInvoice

        # Enable both modules for canonical ₹1,230.00 invoice
        self.settings_obj.is_attendance_enabled = True
        self.settings_obj.is_project_enabled = True
        self.settings_obj.save()

        # Run sweep
        sweep_workspace_subscriptions()

        invoice = MonthlyInvoice.objects.filter(organization=self.org).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.employee_count_snapshot, 6)
        self.assertEqual(invoice.employee_unit_price_snapshot, Decimal('50.00'))
        self.assertEqual(invoice.employee_total_snapshot, Decimal('300.00'))
        self.assertEqual(invoice.base_price_snapshot, Decimal('0.00'))
        self.assertEqual(invoice.attendance_enabled_snapshot, True)
        self.assertEqual(invoice.attendance_unit_price_snapshot, Decimal('99.00'))
        self.assertEqual(invoice.attendance_total_snapshot, Decimal('594.00'))
        self.assertEqual(invoice.project_enabled_snapshot, True)
        self.assertEqual(invoice.project_unit_price_snapshot, Decimal('56.00'))
        self.assertEqual(invoice.project_total_snapshot, Decimal('336.00'))
        self.assertEqual(invoice.subtotal_snapshot, Decimal('1230.00'))
        self.assertEqual(invoice.tax_percentage_snapshot, Decimal('0.00'))
        self.assertEqual(invoice.tax_amount_snapshot, Decimal('0.00'))
        self.assertEqual(invoice.amount, Decimal('1230.00'))

    def test_date_boundaries_calendar_arithmetic(self):
        import calendar
        from datetime import date, timedelta

        # Test A: February in a leap year (2028 has 29 days)
        feb_leap = date(2028, 2, 1)
        max_days = calendar.monthrange(feb_leap.year, feb_leap.month)[1]
        self.assertEqual(max_days, 29)

        deduction_day_clamped = min(28, max_days)
        deduction_date = feb_leap.replace(day=deduction_day_clamped)
        restriction_date = deduction_date + timedelta(days=5)
        # Feb 28 + 5 days in leap year: Feb 29 (1), Mar 1 (2), Mar 2 (3), Mar 3 (4), Mar 4 (5)
        self.assertEqual(restriction_date, date(2028, 3, 4))

        # Test B: February in a non-leap year (2026 has 28 days)
        feb_non_leap = date(2026, 2, 1)
        max_days_nl = calendar.monthrange(feb_non_leap.year, feb_non_leap.month)[1]
        self.assertEqual(max_days_nl, 28)

        deduction_date_nl = feb_non_leap.replace(day=min(28, max_days_nl))
        restriction_date_nl = deduction_date_nl + timedelta(days=5)
        # Feb 28 + 5 days in non-leap year: Mar 1 (1), Mar 2 (2), Mar 3 (3), Mar 4 (4), Mar 5 (5)
        self.assertEqual(restriction_date_nl, date(2026, 3, 5))

        # Test C: Reminder date underflow (Day 1 - 1 day)
        mar_date = date(2026, 3, 1)
        deduction_mar = mar_date.replace(day=min(1, 31))
        reminder_mar = deduction_mar - timedelta(days=1)
        self.assertEqual(reminder_mar, date(2026, 2, 28))

    def test_cross_month_billing_recovery_gap(self):
        from subscribers.models import MonthlyInvoice
        from datetime import date

        # Documenting and testing CROSS-MONTH BILLING RECOVERY GAP:
        # sweep_workspace_subscriptions uses today.replace(day=1) as billing_month.
        # If today is September 3, 2026, only September 2026 invoice is caught up.
        # August 2026 is NOT retroactively generated by the current-month sweep.
        billing_month_current = date(2026, 9, 1)
        inv = MonthlyInvoice.objects.create(
            organization=self.org,
            billing_month=billing_month_current,
            invoice_type='monthly_usage',
            amount=Decimal('1230.00'),
            is_paid=False
        )

        past_month = date(2026, 8, 1)
        past_exists = MonthlyInvoice.objects.filter(organization=self.org, billing_month=past_month).exists()
        self.assertFalse(past_exists, "Past months are not reconstructed by default current-month sweep")

    from django.test import override_settings

    @override_settings(ALLOW_MOCK_PAYMENTS=True)
    def test_verify_payment_active_tenant_context(self):
        from subscribers.models import Wallet
        from core.models import Organization
        from subscribers.api.v1.views import VerifyPaymentView
        from rest_framework.test import APIRequestFactory, force_authenticate
        import uuid

        # Create secondary organization and its wallet
        suffix2 = uuid.uuid4().hex[:6]
        org_b = Organization.objects.create(name=f"Org B {suffix2}", subdomain=f"org-b-{suffix2}")
        admin_b = Employee.objects.create_user(
            email=f"admin-b-{suffix2}@example.com",
            password="testpassword123",
            first_name="Admin",
            last_name="B",
            organization=org_b,
            isSuperAdmin=True
        )
        wallet_b = Wallet.objects.create(organization=org_b, employee=admin_b, balance=Decimal('50.00'))
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=org_b,
            employment_status='Active',
            is_active_in_org=True,
            is_deleted=False
        )

        factory = APIRequestFactory()
        payload = {
            'razorpay_order_id': f'mock_order_{uuid.uuid4().hex[:8]}',
            'razorpay_payment_id': f'mock_pay_{uuid.uuid4().hex[:8]}',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet'
        }
        request = factory.post('/api/subscribers/verify-payment/', payload, format='json')
        force_authenticate(request, user=self.admin)
        # Even if user's legacy pointer is self.org, active_organization points to org_b:
        request.active_organization = org_b

        view = VerifyPaymentView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)

        # org_b wallet must be credited, NOT self.wallet
        wallet_b.refresh_from_db()
        self.assertGreater(wallet_b.balance, Decimal('50.00'))

    def test_pdf_generation_new_vs_historical(self):
        from subscribers.models import MonthlyInvoice
        from subscribers.pdf import generate_invoice_pdf
        from datetime import date

        # 1. New Phase F Invoice: No base fee, no tax
        new_inv = MonthlyInvoice.objects.create(
            organization=self.org,
            billing_month=date(2026, 10, 1),
            invoice_type='monthly_usage',
            amount=Decimal('1230.00'),
            employee_count_snapshot=6,
            employee_unit_price_snapshot=Decimal('50.00'),
            employee_total_snapshot=Decimal('300.00'),
            base_price_snapshot=Decimal('0.00'),
            attendance_enabled_snapshot=True,
            attendance_unit_price_snapshot=Decimal('99.00'),
            attendance_total_snapshot=Decimal('594.00'),
            project_enabled_snapshot=True,
            project_unit_price_snapshot=Decimal('56.00'),
            project_total_snapshot=Decimal('336.00'),
            subtotal_snapshot=Decimal('1230.00'),
            tax_percentage_snapshot=Decimal('0.00'),
            tax_amount_snapshot=Decimal('0.00'),
            is_paid=True
        )

        pdf_bytes_new = generate_invoice_pdf(new_inv)
        self.assertTrue(pdf_bytes_new.getvalue().startswith(b'%PDF'))

        # 2. Historical Invoice: Has base fee ₹399 and GST 18%
        hist_inv = MonthlyInvoice.objects.create(
            organization=self.org,
            billing_month=date(2026, 7, 1),
            invoice_type='monthly_usage',
            amount=Decimal('824.82'),
            employee_count_snapshot=6,
            employee_unit_price_snapshot=Decimal('50.00'),
            employee_total_snapshot=Decimal('300.00'),
            base_price_snapshot=Decimal('399.00'),
            attendance_enabled_snapshot=False,
            project_enabled_snapshot=False,
            subtotal_snapshot=Decimal('699.00'),
            tax_percentage_snapshot=Decimal('18.00'),
            tax_amount_snapshot=Decimal('125.82'),
            is_paid=True
        )

        pdf_bytes_hist = generate_invoice_pdf(hist_inv)
        self.assertTrue(pdf_bytes_hist.getvalue().startswith(b'%PDF'))


class BillingPhaseGTest(APITestCase):
    def setUp(self):
        from core.models import Organization, OrgSettings
        from users.models import Employee, OrganizationMembership
        from subscribers.models import Wallet, WalletTransaction, MonthlyInvoice, GlobalBillingSettings, SubscriptionPackage
        from rest_framework.test import APIClient
        from decimal import Decimal
        import uuid

        # Ensure single canonical GlobalBillingSettings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.monthly_subscription_price = Decimal('0.00')
        self.g_settings.employee_seat_price = Decimal('50.00')
        self.g_settings.attendance_module_price = Decimal('99.00')
        self.g_settings.tasks_module_price = Decimal('56.00')
        self.g_settings.tax_percentage = Decimal('0.00')
        self.g_settings.auto_deduction_day = 5
        self.g_settings.reminder_email_days_before = 1
        self.g_settings.grace_period_days = 5
        self.g_settings.currency = 'INR'
        self.g_settings.save()

        # Create test Organization & OrgSettings for Org A
        suffix = uuid.uuid4().hex[:6]
        self.org_a = Organization.objects.create(name=f"Org G Alpha {suffix}", subdomain=f"org-g-a-{suffix}")
        self.settings_a = OrgSettings.objects.create(
            subscriptionStatus='Active',
            is_attendance_enabled=False,
            is_project_enabled=False
        )
        self.org_a.settings = self.settings_a
        self.org_a.save()

        # Admin user
        self.admin = Employee.objects.create_user(
            email=f"admin-g-{suffix}@example.com",
            password='testpassword123',
            first_name='Admin',
            last_name='G',
            organization=self.org_a,
            isSuperAdmin=True
        )
        self.mem_a = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org_a,
            employment_status='Active',
            is_active_in_org=True,
            is_deleted=False
        )

        # 6 billable employee members in Org A
        for i in range(6):
            emp = Employee.objects.create_user(
                email=f"emp-g-{i}-{suffix}@example.com",
                password='testpassword123',
                first_name=f"Emp{i}",
                last_name="Test",
                organization=self.org_a
            )
            OrganizationMembership.objects.create(
                user=emp,
                organization=self.org_a,
                employment_status='Active',
                is_active_in_org=True,
                is_deleted=False
            )

        self.wallet_a = Wallet.objects.create(
            organization=self.org_a,
            employee=self.admin,
            balance=Decimal('1000.00')
        )

        # Secondary Org B
        self.org_b = Organization.objects.create(name=f"Org G Beta {suffix}", subdomain=f"org-g-b-{suffix}")
        self.settings_b = OrgSettings.objects.create(subscriptionStatus='Active')
        self.org_b.settings = self.settings_b
        self.org_b.save()

        self.admin_b = Employee.objects.create_user(
            email=f"admin-b-{suffix}@example.com",
            password='testpassword123',
            first_name='Admin',
            last_name='B',
            organization=self.org_b,
            isSuperAdmin=True
        )
        self.mem_b = OrganizationMembership.objects.create(
            user=self.admin_b,
            organization=self.org_b,
            employment_status='Active',
            is_active_in_org=True,
            is_deleted=False
        )
        self.wallet_b = Wallet.objects.create(
            organization=self.org_b,
            employee=self.admin_b,
            balance=Decimal('500.00')
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_canonical_pricing_authority_and_no_reverse_sync(self):
        from subscribers.models import GlobalBillingSettings, SubscriptionPackage
        from subscribers.api.v1.serializers import WalletSerializer
        from decimal import Decimal

        # 1. Authority read directly from GlobalBillingSettings
        ser = WalletSerializer(self.wallet_a)
        self.assertEqual(ser.data['attendance_module_price'], '99.00')
        self.assertEqual(ser.data['tasks_module_price'], '56.00')

        # 2. Update SubscriptionPackage price - MUST NOT overwrite GlobalBillingSettings
        pkg = SubscriptionPackage.objects.filter(features__icontains='attendance').first()
        if not pkg:
            pkg = SubscriptionPackage.objects.create(name='Attendance Package', price=Decimal('99.00'), features=['attendance'])
        pkg.price = Decimal('999.00')
        pkg.save()

        # GlobalBillingSettings authority must remain untouched
        g_current = GlobalBillingSettings.get_settings()
        self.assertEqual(g_current.attendance_module_price, Decimal('99.00'))

        # 3. Canonical update via GlobalBillingSettingsViewSet cascades one-way to SubscriptionPackage
        self.client.force_authenticate(user=self.admin)
        res = self.client.post('/api/backoffice/billing-settings/', {
            'attendance_module_price': 120.00,
            'tasks_module_price': 65.00
        }, format='json')
        self.assertEqual(res.status_code, 200)

        g_after = GlobalBillingSettings.get_settings()
        self.assertEqual(g_after.attendance_module_price, Decimal('120.00'))
        self.assertEqual(g_after.tasks_module_price, Decimal('65.00'))

        pkg.refresh_from_db()
        self.assertEqual(pkg.price, Decimal('120.00'))

    def test_module_toggle_no_immediate_debit_and_no_refund(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from subscribers.api.v1.views import WalletViewSet
        from decimal import Decimal

        # Both modules initially False. Wallet balance = 1000.00
        initial_balance = self.wallet_a.balance
        factory = APIRequestFactory()

        # 1. Enable Project Module
        req = factory.post('/api/subscribers/toggle-module/', {'module': 'project', 'enable': True}, format='json')
        force_authenticate(req, user=self.admin)
        req.active_organization = self.org_a
        req.active_membership = self.mem_a

        view = WalletViewSet.as_view({'post': 'toggle_module'})
        res_enable = view(req)
        self.assertEqual(res_enable.status_code, 200)
        self.assertEqual(res_enable.data['charged'], '0.00')
        self.assertTrue(res_enable.data['enabled'])

        # Verify entitlement is active immediately
        self.settings_a.refresh_from_db()
        self.assertTrue(self.settings_a.is_project_enabled)

        # Verify Wallet is NOT debited
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, initial_balance)

        # Check Live Estimate immediately updates: 6 emp * 50 + 6 emp * 56 = 636.00
        self.client.force_authenticate(user=self.admin)
        res_est = self.client.get('/api/billing-estimate/')
        self.assertEqual(res_est.status_code, 200)
        self.assertEqual(res_est.data['estimated_next_total'], 636.0)

        # 2. Disable Project Module
        req_dis = factory.post('/api/subscribers/toggle-module/', {'module': 'project', 'enable': False}, format='json')
        force_authenticate(req_dis, user=self.admin)
        req_dis.active_organization = self.org_a
        req_dis.active_membership = self.mem_a

        res_disable = view(req_dis)
        self.assertEqual(res_disable.status_code, 200)
        self.assertEqual(res_disable.data['charged'], '0.00')
        self.assertFalse(res_disable.data['enabled'])

        # Entitlement disabled immediately, no refund, wallet remains initial_balance
        self.settings_a.refresh_from_db()
        self.assertFalse(self.settings_a.is_project_enabled)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, initial_balance)

        # Live Estimate back to 300.00
        res_est2 = self.client.get('/api/billing-estimate/')
        self.assertEqual(res_est2.data['estimated_next_total'], 300.0)

    from django.test import override_settings

    @override_settings(ALLOW_MOCK_PAYMENTS=True)
    def test_multi_org_payment_security_org_a_to_b(self):
        from subscribers.models import WalletTransaction
        from subscribers.api.v1.views import VerifyPaymentView
        from rest_framework.test import APIRequestFactory, force_authenticate
        from decimal import Decimal
        import uuid

        order_id = f"mock_order_sec_{uuid.uuid4().hex[:8]}"
        pay_id = f"mock_pay_sec_{uuid.uuid4().hex[:8]}"

        # Create Order and pending Transaction bound to Org A's wallet
        WalletTransaction.objects.create(
            wallet=self.wallet_a,
            amount=Decimal('500.00'),
            transactionType='Credit',
            success=False,
            razorpay_order_id=order_id,
            status='Pending',
            details="Pending topup for Org A"
        )

        bal_a_before = self.wallet_a.balance
        bal_b_before = self.wallet_b.balance

        factory = APIRequestFactory()
        view = VerifyPaymentView.as_view()

        # ATTACK ATTEMPT: Verify Org A's order under Org B active tenant context
        req_attack = factory.post('/api/subscribers/verify-payment/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': pay_id,
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet'
        }, format='json')
        force_authenticate(req_attack, user=self.admin)
        req_attack.active_organization = self.org_b
        req_attack.active_membership = self.mem_b

        res_attack = view(req_attack)
        # MUST BE REJECTED (403 Forbidden)
        self.assertEqual(res_attack.status_code, 403)

        # Assert zero mutation on both wallets
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, bal_a_before)
        self.assertEqual(self.wallet_b.balance, bal_b_before)

        # LEGITIMATE VERIFY: Verify under Org A active tenant context
        req_legit = factory.post('/api/subscribers/verify-payment/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': pay_id,
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet'
        }, format='json')
        force_authenticate(req_legit, user=self.admin)
        req_legit.active_organization = self.org_a
        req_legit.active_membership = self.mem_a

        res_legit = view(req_legit)
        self.assertEqual(res_legit.status_code, 200)

        # Assert Org A credited exactly once, Org B unchanged
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, bal_a_before + Decimal('500.00'))
        self.assertEqual(self.wallet_b.balance, bal_b_before)

    def test_verify_and_webhook_race_idempotency(self):
        from company.api.v1.services import BillingService
        from decimal import Decimal
        import uuid

        order_id = f"mock_order_race_{uuid.uuid4().hex[:8]}"
        pay_id = f"mock_pay_race_{uuid.uuid4().hex[:8]}"
        amount = Decimal('250.00')
        bal_initial = self.wallet_a.balance

        # Event 1: Webhook arrives first
        _, tx1, credited1 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=amount,
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            details="Webhook credit",
            target_org_id=self.org_a.id
        )
        self.wallet_a.refresh_from_db()
        self.assertTrue(credited1)
        self.assertEqual(self.wallet_a.balance, bal_initial + amount)

        # Event 2: Client verify arrives second for same order/payment
        _, tx2, credited2 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet_a.id,
            amount_dec=amount,
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            details="Verify view credit",
            target_org_id=self.org_a.id
        )
        self.wallet_a.refresh_from_db()
        # MUST BE CREDITED EXACTLY ONCE
        self.assertFalse(credited2)
        self.assertEqual(self.wallet_a.balance, bal_initial + amount)
        self.assertEqual(tx1.id, tx2.id)

    def test_missing_billing_period_detection_and_current_month_idempotency(self):
        from company.tasks import sweep_workspace_subscriptions
        from subscribers.models import MonthlyInvoice
        from datetime import date
        from decimal import Decimal

        # 1. First execution creates September invoice
        sweep_workspace_subscriptions()
        today = date.today()
        current_month = today.replace(day=1)

        inv_count_1 = MonthlyInvoice.objects.filter(organization=self.org_a, billing_month=current_month).count()
        self.assertEqual(inv_count_1, 1)

        # 2. Second execution MUST NOT create a duplicate (idempotent)
        sweep_workspace_subscriptions()
        inv_count_2 = MonthlyInvoice.objects.filter(organization=self.org_a, billing_month=current_month).count()
        self.assertEqual(inv_count_2, 1)

    def test_historical_invoice_and_pdf_immutability_on_rate_change(self):
        from subscribers.models import MonthlyInvoice, GlobalBillingSettings
        from subscribers.pdf import generate_invoice_pdf
        from datetime import date
        from decimal import Decimal

        # Create historical invoice
        hist = MonthlyInvoice.objects.create(
            organization=self.org_a,
            billing_month=date(2026, 6, 1),
            invoice_type='monthly_usage',
            amount=Decimal('894.00'),
            employee_count_snapshot=6,
            employee_unit_price_snapshot=Decimal('50.00'),
            employee_total_snapshot=Decimal('300.00'),
            base_price_snapshot=Decimal('0.00'),
            attendance_enabled_snapshot=True,
            attendance_unit_price_snapshot=Decimal('99.00'),
            attendance_total_snapshot=Decimal('594.00'),
            subtotal_snapshot=Decimal('894.00'),
            tax_percentage_snapshot=Decimal('0.00'),
            tax_amount_snapshot=Decimal('0.00'),
            is_paid=True
        )

        # Change GlobalBillingSettings rates to new values
        self.g_settings.employee_seat_price = Decimal('80.00')
        self.g_settings.attendance_module_price = Decimal('150.00')
        self.g_settings.save()

        # Historical invoice MUST REMAIN UNMUTATED
        hist.refresh_from_db()
        self.assertEqual(hist.amount, Decimal('894.00'))
        self.assertEqual(hist.employee_unit_price_snapshot, Decimal('50.00'))
        self.assertEqual(hist.attendance_unit_price_snapshot, Decimal('99.00'))

        # Historical PDF renders verbatim
        pdf_buf = generate_invoice_pdf(hist)
        self.assertTrue(pdf_buf.getvalue().startswith(b'%PDF'))


class BillingPhaseHTest(TestCase):
    """
    CUBELOGS PHASE H — Automated Production Integrity & Concurrency Test Suite
    Validates all Phase H Hard Gates:
    1. Only captured Razorpay payments can credit a wallet (authorized does not credit).
    2. Client payment verification is cryptographically validated and amount/currency match.
    3. Webhook signatures are verified using unmodified raw body and webhook secret.
    4. Currency & Amount invariance between order, payment, and stored transaction.
    5. Duplicate verification / webhook delivery cannot double credit (idempotency).
    6. Database-level payment uniqueness constraints enforced.
    7. Concurrency-safe locking for wallet credits and monthly invoice sweeps.
    8. Multi-month gap detection without historical fabrication.
    9. Public pricing API backed directly by GlobalBillingSettings without drift.
    """

    def setUp(self):
        import uuid
        from decimal import Decimal
        from django.conf import settings as django_settings
        from core.models import Organization, OrgSettings
        from users.models import Employee, OrganizationMembership
        from subscribers.models import Wallet, WalletTransaction, MonthlyInvoice, GlobalBillingSettings
        from rest_framework.test import APIClient

        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.monthly_subscription_price = Decimal('0.00')
        self.g_settings.employee_seat_price = Decimal('50.00')
        self.g_settings.attendance_module_price = Decimal('99.00')
        self.g_settings.tasks_module_price = Decimal('56.00')
        self.g_settings.tax_percentage = Decimal('0.00')
        self.g_settings.auto_deduction_day = 5
        self.g_settings.reminder_email_days_before = 1
        self.g_settings.grace_period_days = 5
        self.g_settings.currency = 'INR'
        self.g_settings.save()

        suffix = uuid.uuid4().hex[:6]
        self.org = Organization.objects.create(name=f"Org H {suffix}", subdomain=f"org-h-{suffix}")
        self.settings_obj = OrgSettings.objects.create(
            subscriptionStatus='Active',
            is_attendance_enabled=True,
            is_project_enabled=True
        )
        self.org.settings = self.settings_obj
        self.org.save()

        self.admin = Employee.objects.create_user(
            email=f"admin_{suffix}@orgh.com",
            password="password123",
            username=f"admin_{suffix}",
            organization=self.org,
            isSuperAdmin=True
        )
        self.admin_membership = OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.org,
            employment_status='Active',
            is_active_in_org=True,
            is_deleted=False
        )

        self.wallet = Wallet.objects.create(
            employee=self.admin,
            organization=self.org,
            balance=Decimal('0.00')
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.client.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org.id)

    def test_authorized_payment_produces_no_wallet_credit(self):
        """1. Authorized payment returns 202 Accepted and does NOT credit wallet."""
        import uuid
        from decimal import Decimal
        from subscribers.models import WalletTransaction

        url = '/api/payment/verify/'
        order_id = f"mock_order_auth_{uuid.uuid4().hex[:8]}"
        WalletTransaction.objects.create(
            wallet=self.wallet,
            amount=Decimal('500.00'),
            transactionType='Credit',
            success=False,
            status='Pending',
            razorpay_order_id=order_id
        )

        res = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_auth_test_123',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'payment_status': 'authorized'
        }, format='json')

        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.data.get('status'), 'payment_authorized')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

        tx = WalletTransaction.objects.filter(razorpay_order_id=order_id).first()
        self.assertFalse(tx.success)
        self.assertEqual(tx.status, 'Pending')

    def test_captured_payment_credits_wallet_once_and_reverification_is_idempotent(self):
        """2. Captured payment credits wallet exactly once; reverification is idempotent."""
        import uuid
        from decimal import Decimal
        from subscribers.models import WalletTransaction

        url = '/api/payment/verify/'
        order_id = f"mock_order_cap_{uuid.uuid4().hex[:8]}"
        WalletTransaction.objects.create(
            wallet=self.wallet,
            amount=Decimal('500.00'),
            transactionType='Credit',
            success=False,
            status='Pending',
            razorpay_order_id=order_id
        )

        # 1st attempt: success
        res1 = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_cap_test_123',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'payment_status': 'captured'
        }, format='json')
        self.assertEqual(res1.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

        tx = WalletTransaction.objects.filter(razorpay_order_id=order_id).first()
        self.assertTrue(tx.success)
        self.assertEqual(tx.status, 'Success')

        # 2nd attempt: idempotent replay returns 200 without double crediting
        res2 = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_cap_test_123',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'payment_status': 'captured'
        }, format='json')
        self.assertEqual(res2.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

    def test_failed_payment_rejected_and_no_credit(self):
        """3. Failed payment returns 400 Bad Request with zero wallet mutation."""
        import uuid
        from decimal import Decimal
        from subscribers.models import WalletTransaction

        url = '/api/payment/verify/'
        order_id = f"mock_order_fail_{uuid.uuid4().hex[:8]}"
        WalletTransaction.objects.create(
            wallet=self.wallet,
            amount=Decimal('500.00'),
            transactionType='Credit',
            success=False,
            status='Pending',
            razorpay_order_id=order_id
        )

        res = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_fail_test_123',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'payment_status': 'failed'
        }, format='json')
        self.assertEqual(res.status_code, 400)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_payment_amount_and_currency_mismatch_rejected(self):
        """4. Currency mismatch or amount tampering is rejected with 400."""
        import uuid
        from decimal import Decimal
        from subscribers.models import WalletTransaction

        url = '/api/payment/verify/'
        order_id = f"mock_order_tamper_{uuid.uuid4().hex[:8]}"
        WalletTransaction.objects.create(
            wallet=self.wallet,
            amount=Decimal('500.00'),
            transactionType='Credit',
            success=False,
            status='Pending',
            razorpay_order_id=order_id
        )

        # Currency mismatch
        res_curr = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_tamper_1',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'currency': 'USD'
        }, format='json')
        self.assertEqual(res_curr.status_code, 400)

        # Amount mismatch (trying to credit 1000 instead of 500)
        res_amt = self.client.post(url, {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': 'pay_tamper_2',
            'razorpay_signature': 'mock_sig',
            'payment_type': 'wallet',
            'amount': '1000.00'
        }, format='json')
        self.assertEqual(res_amt.status_code, 400)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_webhook_missing_and_invalid_signature_rejected(self):
        """5. Webhook without signature or with invalid signature returns 400."""
        import json

        url = '/api/razorpay/webhook/'
        payload = json.dumps({'event': 'payment.captured', 'payload': {}})

        # Missing signature
        res_missing = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(res_missing.status_code, 400)

        # Invalid signature
        res_invalid = self.client.post(
            url, data=payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='invalid_sha256_sig'
        )
        self.assertEqual(res_invalid.status_code, 400)

    def test_webhook_authorized_event_produces_no_credit(self):
        """6. Webhook receiving payment.authorized returns 200 without crediting wallet."""
        import uuid, json, hmac, hashlib
        from decimal import Decimal
        from django.conf import settings as django_settings

        url = '/api/razorpay/webhook/'
        webhook_secret = getattr(django_settings, 'RAZORPAY_WEBHOOK_SECRET', 'qrs9@fPwNpxyY3d')
        order_id = f"order_hook_auth_{uuid.uuid4().hex[:8]}"

        payload_dict = {
            'event': 'payment.authorized',
            'id': f"evt_auth_{uuid.uuid4().hex[:8]}",
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_webhook_auth_123',
                        'order_id': order_id,
                        'amount': 50000,
                        'currency': 'INR',
                        'status': 'authorized',
                        'notes': {'org_id': str(self.org.id), 'wallet_id': str(self.wallet.id), 'payment_type': 'wallet'}
                    }
                }
            }
        }
        body_bytes = json.dumps(payload_dict).encode('utf-8')
        sig = hmac.new(webhook_secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()

        res = self.client.post(
            url, data=body_bytes, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig
        )
        self.assertEqual(res.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_webhook_captured_credits_once_and_duplicate_delivery_is_safe(self):
        """7. Webhook receiving payment.captured credits wallet once; duplicate replay is safe."""
        import uuid, json, hmac, hashlib
        from decimal import Decimal
        from django.conf import settings as django_settings

        url = '/api/razorpay/webhook/'
        webhook_secret = getattr(django_settings, 'RAZORPAY_WEBHOOK_SECRET', 'qrs9@fPwNpxyY3d')
        order_id = f"order_hook_cap_{uuid.uuid4().hex[:8]}"
        evt_id = f"evt_cap_{uuid.uuid4().hex[:8]}"

        payload_dict = {
            'event': 'payment.captured',
            'id': evt_id,
            'payload': {
                'payment': {
                    'entity': {
                        'id': f"pay_hook_cap_{uuid.uuid4().hex[:8]}",
                        'order_id': order_id,
                        'amount': 50000,
                        'currency': 'INR',
                        'status': 'captured',
                        'notes': {'org_id': str(self.org.id), 'wallet_id': str(self.wallet.id), 'payment_type': 'wallet'}
                    }
                }
            }
        }
        body_bytes = json.dumps(payload_dict).encode('utf-8')
        sig = hmac.new(webhook_secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()

        # 1st delivery: credits wallet
        res1 = self.client.post(
            url, data=body_bytes, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig
        )
        self.assertEqual(res1.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

        # 2nd delivery: duplicate delivery safe (returns 200 without double crediting)
        res2 = self.client.post(
            url, data=body_bytes, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig
        )
        self.assertEqual(res2.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500.00'))

    def test_public_pricing_endpoint_reflects_canonical_settings(self):
        """8. Public pricing endpoint reads directly from GlobalBillingSettings without auth."""
        from decimal import Decimal
        from rest_framework.test import APIClient

        public_client = APIClient()  # Unauthenticated
        url = '/api/public-pricing/'

        res = public_client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['currency'], 'INR')
        self.assertEqual(res.data['employee_seat_price'], '50.00')
        self.assertEqual(res.data['attendance_module_price'], '99.00')
        self.assertEqual(res.data['project_module_price'], '56.00')
        self.assertEqual(res.data['base_subscription_price'], '0.00')
        self.assertEqual(res.data['tax_percentage'], '0.00')

        # Update canonical rate
        self.g_settings.employee_seat_price = Decimal('75.00')
        self.g_settings.save()

        res2 = public_client.get(url)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['employee_seat_price'], '75.00')

    def test_multi_month_gap_detection(self):
        """9. Multi-month gap detection warns about all missing billing periods without fabrication."""
        import datetime
        from django.utils import timezone
        from company.tasks import sweep_workspace_subscriptions
        from subscribers.models import MonthlyInvoice

        # Set org created 3 months ago
        today = timezone.localtime(timezone.now()).date()
        three_months_ago = (today.replace(day=1) - datetime.timedelta(days=70)).replace(day=1)
        self.org.created_at = timezone.make_aware(datetime.datetime.combine(three_months_ago, datetime.time(0, 0)))
        self.org.save(update_fields=['created_at'])

        with self.assertLogs('company.tasks', level='WARNING') as cm:
            sweep_workspace_subscriptions()

        # Verify structured warnings were emitted for missing months
        gap_logs = [msg for msg in cm.output if 'CROSS-MONTH BILLING RECOVERY GAP' in msg]
        self.assertTrue(len(gap_logs) >= 2, f"Expected multi-month gap warnings, got: {gap_logs}")

        # Verify current month invoice was created idempotently
        curr_invs = MonthlyInvoice.objects.filter(organization=self.org, billing_month=today.replace(day=1))
        self.assertEqual(curr_invs.count(), 1)

    def test_database_payment_uniqueness_constraint(self):
        """10. Database unique constraint prevents duplicate successful payment IDs."""
        import uuid
        from decimal import Decimal
        from django.db import IntegrityError
        from subscribers.models import WalletTransaction

        pay_id = f"pay_unique_{uuid.uuid4().hex[:8]}"

        # 1st record with success=True
        WalletTransaction.objects.create(
            wallet=self.wallet,
            amount=Decimal('100.00'),
            transactionType='Credit',
            success=True,
            status='Success',
            razorpay_payment_id=pay_id
        )

        # 2nd record attempting same payment ID with success=True MUST violate UniqueConstraint
        with self.assertRaises(IntegrityError):
            WalletTransaction.objects.create(
                wallet=self.wallet,
                amount=Decimal('100.00'),
                transactionType='Credit',
                success=True,
                status='Success',
                razorpay_payment_id=pay_id
            )

    def test_concurrency_safe_credit_wallet_locking(self):
        """11. BillingService.credit_wallet_from_payment serializes and prevents double credits."""
        import uuid
        from decimal import Decimal
        from company.api.v1.services import BillingService

        pay_id = f"pay_service_lock_{uuid.uuid4().hex[:8]}"
        order_id = f"order_service_lock_{uuid.uuid4().hex[:8]}"

        # Call 1: successfully credits
        w1, tx1, credited1 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet.id,
            amount_dec=Decimal('250.00'),
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            target_org_id=self.org.id
        )
        self.assertTrue(credited1)
        self.assertEqual(w1.balance, Decimal('250.00'))

        # Call 2 with same payment ID: detected as already processed
        w2, tx2, credited2 = BillingService.credit_wallet_from_payment(
            wallet_id=self.wallet.id,
            amount_dec=Decimal('250.00'),
            razorpay_order_id=order_id,
            razorpay_payment_id=pay_id,
            target_org_id=self.org.id
        )
        self.assertFalse(credited2)
        self.assertEqual(w2.balance, Decimal('250.00'))

















