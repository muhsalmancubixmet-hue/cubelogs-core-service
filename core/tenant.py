# --------------------------------------------------------------------------------
#       Tenant Context Abstraction
# --------------------------------------------------------------------------------

import logging

logger = logging.getLogger(__name__)


class TenantContext:
    """
    Central abstraction for active tenant context resolution in Phase 2B.
    Resolves request.active_organization and request.active_membership while
    maintaining 100% backward compatibility with legacy single-organization runtime.
    """

    @staticmethod
    def get_active_membership(request):
        """
        Resolves the active OrganizationMembership for the request.
        Caches the result on the request object to ensure at most 1 DB query per request context.
        """
        if not hasattr(request, '_cached_active_membership'):
            request._cached_active_membership = TenantContext._resolve_active_membership(request)
        elif request._cached_active_membership is not None:
            if not getattr(request._cached_active_membership, 'is_active_in_org', True) or getattr(request._cached_active_membership, 'is_deleted', False):
                request._cached_active_membership = None
        return request._cached_active_membership

    @staticmethod
    def _extract_header_org_id(request):
        if not request:
            return None
        header_val = None
        if hasattr(request, 'headers'):
            header_val = request.headers.get('X-Organization-ID') or request.headers.get('x-organization-id')
        if not header_val and hasattr(request, 'META'):
            header_val = request.META.get('HTTP_X_ORGANIZATION_ID')
        if not header_val and hasattr(request, 'query_params'):
            header_val = request.query_params.get('organization') or request.query_params.get('organization_id')
        elif not header_val and hasattr(request, 'GET'):
            header_val = request.GET.get('organization') or request.GET.get('organization_id')

        if header_val is not None:
            try:
                return int(header_val)
            except (ValueError, TypeError):
                return str(header_val).strip() or None
        return None

    @staticmethod
    def get_active_organization(request):
        """
        Resolves the active Organization for the request.
        Caches the result on the request object to ensure at most 1 DB query per request context.
        """
        if not hasattr(request, '_cached_active_organization'):
            membership = TenantContext.get_active_membership(request)
            if membership:
                request._cached_active_organization = membership.organization
            elif getattr(request, 'user', None) and request.user.is_authenticated:
                user = request.user
                header_org_id = TenantContext._extract_header_org_id(request)
                if header_org_id and getattr(user, 'isSuperAdmin', False) and getattr(user, 'organization', None) is None:
                    from core.models import Organization
                    request._cached_active_organization = Organization.objects.filter(id=header_org_id).first()
                else:
                    request._cached_active_organization = getattr(user, 'organization', None)
            else:
                request._cached_active_organization = None
        return request._cached_active_organization

    @staticmethod
    def get_active_role(request):
        """
        Compatibility helper for active membership role resolution.
        Returns the Role associated with the active organization membership or legacy user role.
        """
        membership = TenantContext.get_active_membership(request)
        if membership and membership.role:
            return membership.role
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            return getattr(user, 'role', None)
        return None

    @staticmethod
    def _resolve_active_membership(request):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None

        from users.models import OrganizationMembership

        header_org_id = TenantContext._extract_header_org_id(request)

        # Handle explicit organization request header
        if header_org_id:
            # Check if user has an active membership in the requested organization
            mem = OrganizationMembership.objects.filter(
                organization_id=header_org_id,
                user=user,
                is_active_in_org=True,
                is_deleted=False
            ).select_related('organization', 'role').first()
            if mem:
                return mem

            # Platform superadmin without tenant organization bypasses tenant membership requirements
            if getattr(user, 'isSuperAdmin', False) and getattr(user, 'organization', None) is None:
                return None

            # Fallback self-healing if user's legacy organization matches header
            if getattr(user, 'organization_id', None) == header_org_id:
                from users.api.v1.services import UserService
                return UserService.sync_employee_shadow_membership(user)

            logger.warning(
                "Tenant authorization failure: User %s passed X-Organization-ID %s without active membership.",
                getattr(user, 'email', str(user)),
                header_org_id
            )
            return None

        # System superadmin without tenant organization bypasses tenant membership requirements
        if getattr(user, 'isSuperAdmin', False) and getattr(user, 'organization', None) is None:
            return None

        # JWT Claim Resolution (if token auth was used)
        token_mem_id = None
        token_org_id = None
        auth_header = getattr(request, 'META', {}).get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            try:
                from rest_framework_simplejwt.tokens import AccessToken
                token_str = auth_header.split(' ')[1]
                token = AccessToken(token_str)
                token_mem_id = token.get('membership_id')
                token_org_id = token.get('active_org_id')
            except Exception:
                pass

        if token_mem_id is not None:
            membership = OrganizationMembership.objects.filter(
                id=token_mem_id,
                user=user,
                is_active_in_org=True
            ).select_related('organization', 'role').first()
            if token_org_id is not None and membership and membership.organization_id != token_org_id:
                logger.warning(
                    "JWT claim tampering / mismatch detected: token active_org_id=%s vs DB active organization_id=%s for user %s",
                    token_org_id, membership.organization_id, getattr(user, 'email', str(user))
                )
                return None
            return membership
        elif token_org_id is not None:
            membership = OrganizationMembership.objects.filter(
                organization_id=token_org_id,
                user=user,
                is_active_in_org=True
            ).select_related('organization', 'role').first()
            return membership

        current_org = getattr(user, 'organization', None)
        if not current_org:
            return None

        membership = OrganizationMembership.objects.filter(
            user=user,
            organization=current_org,
            is_active_in_org=True
        ).select_related('organization', 'role').first()

        if not membership:
            # Self-healing fallback for legacy users
            from users.api.v1.services import UserService
            membership = UserService.sync_employee_shadow_membership(user)

        if not membership:
            logger.warning(
                "Tenant authorization failure: User %s has organization %s but no active OrganizationMembership.",
                getattr(user, 'email', str(user)),
                getattr(current_org, 'id', str(current_org))
            )
            return None

        return membership
