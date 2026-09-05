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
                # System superadmin or global fallback
                request._cached_active_organization = getattr(request.user, 'organization', None)
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

        # System superadmin without tenant organization bypasses tenant membership requirements
        if getattr(user, 'isSuperAdmin', False) and getattr(user, 'organization', None) is None:
            return None

        from users.models import OrganizationMembership

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
            logger.warning(
                "Tenant authorization failure: User %s has organization %s but no active OrganizationMembership.",
                getattr(user, 'email', str(user)),
                getattr(current_org, 'id', str(current_org))
            )
            return None

        return membership
