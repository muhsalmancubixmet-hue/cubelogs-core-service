"""
Core pagination classes for the CubeLogs API.
"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    """
    Default bounded pagination for all list endpoints.

    Clients request pages using:
        GET /api/v1/stories/?page=2&page_size=25

    Response shape:
        {
            "count": 320,
            "next": "...?page=3",
            "previous": "...?page=1",
            "results": [...]
        }
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response({
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        })

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'properties': {
                'count': {'type': 'integer'},
                'next': {'type': 'string', 'nullable': True},
                'previous': {'type': 'string', 'nullable': True},
                'results': schema,
            },
        }


class NoPagination(PageNumberPagination):
    """
    Explicitly opt out of pagination for tiny configuration lists
    (e.g. ProjectStatusOption — always < 15 rows).
    """
    page_size = None

    def paginate_queryset(self, queryset, request, view=None):
        return None

    def get_paginated_response(self, data):
        return Response(data)
