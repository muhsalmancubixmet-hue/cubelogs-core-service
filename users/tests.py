from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status
from django.core.signing import TimestampSigner

from core.models import Organization, OrgSettings
from users.models import Employee


class JWTAuthLifecycleTestCase(TestCase):
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


