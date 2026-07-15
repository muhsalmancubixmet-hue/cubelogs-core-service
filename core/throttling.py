from rest_framework.throttling import SimpleRateThrottle

class AuthRateThrottle(SimpleRateThrottle):
    """
    Custom IP-based rate throttle for authentication endpoints.
    Limits attempts (failed or successful) to 10 requests per minute per IP address.
    Configured via 'auth' rate in REST_FRAMEWORK settings.
    """
    scope = 'auth'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }
