class APICSRFExemptMiddleware:
    """
    Middleware that marks /api/ routes as CSRF-exempt.
    API endpoints use JWTCookieAuthentication with SameSite=Lax HttpOnly cookies,
    which inherently prevents cross-site request forgery without requiring HTML CSRF tokens.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            setattr(request, '_dont_enforce_csrf_checks', True)
        return self.get_response(request)
