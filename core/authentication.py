from rest_framework_simplejwt.authentication import JWTAuthentication

class JWTCookieAuthentication(JWTAuthentication):
    """
    Custom authentication class that reads the access token from cookies
    instead of (or in addition to) the Authorization header.
    """
    def authenticate(self, request):
        # First try to get token from cookies
        raw_token = request.COOKIES.get('cubelogs_access_token')
        if raw_token is not None:
            validated_token = self.get_validated_token(raw_token)
            return self.get_user(validated_token), validated_token

        # Fallback to default SimpleJWT header authentication
        return super().authenticate(request)
