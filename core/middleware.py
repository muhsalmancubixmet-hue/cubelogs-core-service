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


from django.http import HttpRequest


def _ensure_tenant_properties():
    if not hasattr(HttpRequest, '_tenant_context_patched'):
        from core.tenant import TenantContext

        def _get_active_org(req):
            return TenantContext.get_active_organization(req)

        def _get_active_mem(req):
            return TenantContext.get_active_membership(req)

        def _set_active_org(req, val):
            req._cached_active_organization = val

        def _set_active_mem(req, val):
            req._cached_active_membership = val

        HttpRequest.active_organization = property(_get_active_org, _set_active_org)
        HttpRequest.active_membership = property(_get_active_mem, _set_active_mem)
        HttpRequest._tenant_context_patched = True


_ensure_tenant_properties()


class TenantContextMiddleware:
    """
    Phase 2B Middleware: Attaches request.active_organization and request.active_membership
    as dynamic properties on every HttpRequest.
    Does NOT alter Django authentication or request.user behavior.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        _ensure_tenant_properties()

    def __call__(self, request):
        _ensure_tenant_properties()
        return self.get_response(request)



