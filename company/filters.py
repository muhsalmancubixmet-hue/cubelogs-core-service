# THIRD PARTY
from django.db import models
import django_filters as filters

# APPLICATION SPECIFIC
from company.models import (
    Lead, CMSContent, LMSModule, Testimonial, PromoVideoSection
)


class LeadFilter(filters.FilterSet):

    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('name', 'name'),
            ('status', 'status'),
            ('created_at', 'created_at'),
        )
    )

    class Meta:
        model = Lead
        fields = [
            'id',
            'email',
            'phone',
            'status',
            'assigned_staff',
            'is_read',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value) |
            models.Q(email__icontains=value) |
            models.Q(phone__icontains=value) |
            models.Q(companyName__icontains=value) |
            models.Q(message__icontains=value) |
            models.Q(assigned_staff__first_name__icontains=value) |
            models.Q(assigned_staff__last_name__icontains=value) |
            models.Q(assigned_staff__email__icontains=value)
        )


class CMSContentFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('key', 'key'),
        )
    )

    class Meta:
        model = CMSContent
        fields = [
            'id',
            'key',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(key__icontains=value) |
            models.Q(value__icontains=value)
        )


class LMSModuleFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('title', 'title'),
            ('category', 'category'),
        )
    )

    class Meta:
        model = LMSModule
        fields = [
            'id',
            'title',
            'category',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(title__icontains=value) |
            models.Q(description__icontains=value) |
            models.Q(category__icontains=value)
        )


class TestimonialFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('author_name', 'author_name'),
            ('stars', 'stars'),
        )
    )

    class Meta:
        model = Testimonial
        fields = [
            'id',
            'author_name',
            'is_approved',
            'stars',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(author_name__icontains=value) |
            models.Q(author_title__icontains=value) |
            models.Q(text__icontains=value)
        )



class PromoVideoSectionFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    ordering = filters.OrderingFilter(
        fields=(
            ('id', 'id'),
            ('title', 'title'),
        )
    )

    class Meta:
        model = PromoVideoSection
        fields = [
            'id',
            'title',
            'is_active',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            models.Q(title__icontains=value) |
            models.Q(description__icontains=value)
        )



