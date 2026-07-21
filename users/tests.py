from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status
from django.core.signing import TimestampSigner

from core.models import Organization, OrgSettings
from users.models import Employee


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'rest_framework.authentication.SessionAuthentication',
        ],
        'DEFAULT_PERMISSION_CLASSES': [
            'rest_framework.permissions.IsAuthenticated',
        ],
        'DEFAULT_THROTTLE_CLASSES': [],
        'DEFAULT_THROTTLE_RATES': {},
    }
)
class SessionAuthLifecycleTestCase(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=False,
        )
        self.org = Organization.objects.create(
            name="Session Auth Test Org",
            subdomain="session_test",
            settings=self.org_settings,
        )
        self.user = Employee.objects.create_user(  # type: ignore[call-arg]
            email="session_user@example.com",
            password="securepassword123",
            first_name="Session",
            last_name="User",
            organization=self.org,
            permissions=["tasks:create", "tasks:view", "dashboard"]
        )

    def test_password_login_creates_session(self):
        client = APIClient()
        response = client.post("/api/auth/login/", {
            "email": "session_user@example.com",
            "password": "securepassword123"
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "session_user@example.com")
        self.assertIn("sessionid", response.cookies)

        # Confirm session is valid for subsequent requests
        me_res = client.get("/api/auth/me/")
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)
        self.assertEqual(me_res.json()["email"], "session_user@example.com")

    def test_magic_login_creates_session(self):
        signer = TimestampSigner(salt="auto-login")
        token = signer.sign(str(self.user.id))

        client = APIClient()
        response = client.post("/api/auth/magic-login/", {"token": token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "session_user@example.com")
        self.assertIn("sessionid", response.cookies)

        me_res = client.get("/api/auth/me/")
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)

    def test_magic_login_invalid_token_returns_400(self):
        client = APIClient()
        response = client.post("/api/auth/magic-login/", {"token": "invalid.token.value"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_logout_flushes_session(self):
        client = APIClient()
        client.post("/api/auth/login/", {
            "email": "session_user@example.com",
            "password": "securepassword123"
        }, format="json")

        # Verify authenticated
        me_before = client.get("/api/auth/me/")
        self.assertEqual(me_before.status_code, status.HTTP_200_OK)

        # Call logout
        logout_res = client.post("/api/auth/logout/", format="json")
        self.assertEqual(logout_res.status_code, status.HTTP_200_OK)

        # Verify session is invalidated
        me_after = client.get("/api/auth/me/")
        self.assertIn(me_after.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_me_endpoint_includes_enrichment_data(self):
        client = APIClient()
        client.force_login(self.user)

        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("is_attendance_enabled", data)
        self.assertIn("is_project_enabled", data)
        self.assertTrue(data["is_attendance_enabled"])
        self.assertFalse(data["is_project_enabled"])
        self.assertIn("subscription", data)

