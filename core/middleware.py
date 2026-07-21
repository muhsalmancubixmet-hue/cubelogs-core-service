class APICSRFExemptMiddleware:
    """
    Middleware that selectively marks API requests as CSRF-exempt ONLY if they are
    authenticated via an explicit Authorization: Bearer <token> HTTP header.
    Requests relying on HttpOnly session cookies MUST pass standard CSRF checks.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            if auth_header.startswith('Bearer '):
                setattr(request, '_dont_enforce_csrf_checks', True)
        return self.get_response(request)

