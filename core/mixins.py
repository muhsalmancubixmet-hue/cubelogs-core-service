# STANDARD LIBRARY
import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------
# FilterMixinNew: Mixin to store, restore, and clear page filters using the session.
# --------------------------------------------------------------------------------
class FilterMixinNew:
    """
    Mixin that automatically saves the query filter parameters of list views
    to the user's session and restores them if no filter parameters are supplied in the request.
    Supports both standard Django Class-Based Views (CBVs) and DRF views.
    """

    def _get_session_key(self, request):
        """Generates a unique session key for this view and user."""
        view_name = self.__class__.__name__
        user = getattr(request, 'user', None)
        user_id = getattr(user, 'id', None) if (user and getattr(user, 'is_authenticated', False)) else "anonymous"
        path = getattr(request, 'path', '')
        return f"filters_{user_id}_{view_name}_{path.replace('/', '_')}"

    def _get_session_data(self, request):
        """Retrieves saved filters from the session."""
        session = getattr(request, 'session', None)
        if session is None:
            return {}
        key = self._get_session_key(request)
        return session.get(key, {})

    def _save_session_data(self, request, data):
        """Saves current filters to the session."""
        session = getattr(request, 'session', None)
        if session is None:
            return
        key = self._get_session_key(request)
        session[key] = data
        session.modified = True

    def get_saved_data(self, request):
        """Public method to fetch currently saved filters for the view."""
        return self._get_session_data(request)

    def clear_saved_data(self, request):
        """Clears saved filter state for this view."""
        session = getattr(request, 'session', None)
        if session is None:
            return
        key = self._get_session_key(request)
        if key in session:
            del session[key]
            session.modified = True

    def handle_filter_session(self, request):
        """
        Main hook to handle filter sessions:
        - If 'clear_filters' is provided in request.GET, clears saved filters.
        - If other filter parameters are provided, updates/saves them in the session.
        - If no filter parameters are provided, restores the previously saved filters from the session.
        """
        # Determine the underlying django request (handling DRF Request wrapper)
        django_req = request._request if hasattr(request, '_request') else request
        
        # Create a mutable copy of GET parameters
        query_params = django_req.GET.copy()

        # Handle filter clearance
        if 'clear_filters' in query_params:
            self.clear_saved_data(django_req)
            query_params.pop('clear_filters', None)
            django_req.GET = query_params
            
            # Reset DRF's cached query_params
            if hasattr(request, 'query_params'):
                try:
                    delattr(request, '_query_params')
                except AttributeError:
                    pass
            return

        # Parameters to ignore (pagination, format, cache-busting, etc.)
        ignore_params = {'page', 'page_size', 'limit', 'offset', '_', 'format'}
        active_filters = {k: v for k, v in query_params.items() if k not in ignore_params and v}

        if active_filters:
            # New active filters provided, save them to the session
            self._save_session_data(django_req, active_filters)
        else:
            # No new filters provided, restore saved filters if any exist
            saved_filters = self._get_session_data(django_req)
            if saved_filters:
                for k, v in saved_filters.items():
                    query_params[k] = v
                django_req.GET = query_params
                
                # Reset DRF's cached query_params
                if hasattr(request, 'query_params'):
                    try:
                        delattr(request, '_query_params')
                    except AttributeError:
                        pass

    def dispatch(self, request, *args, **kwargs):
        """
        Intercepts dispatch to process filter saving and restoration.
        """
        self.handle_filter_session(request)
        return super().dispatch(request, *args, **kwargs)


# --------------------------------------------------------------------------------
# TenantScopedViewSetMixin: Mixin to scope views and creations to organization.
# --------------------------------------------------------------------------------
class TenantScopedViewSetMixin:
    """
    Mixin that automatically filters querysets by the logged-in user's organization
    and auto-assigns the organization during model creation.
    """
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, 'organization', None):
            model = self.queryset.model
            fields = [f.name for f in model._meta.get_fields()]
            if 'organization' in fields:
                qs = qs.filter(organization=user.organization)
            elif 'employee' in fields:
                qs = qs.filter(employee__organization=user.organization)
            elif 'assignedTo' in fields:
                qs = qs.filter(assignedTo__organization=user.organization)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_authenticated and getattr(user, 'organization', None):
            model = self.queryset.model
            fields = [f.name for f in model._meta.get_fields()]
            if 'organization' in fields:
                serializer.save(organization=user.organization)
                return
        serializer.save()

