# --------------------------------------------------------------------------------
#       Company API v1 Routing
# --------------------------------------------------------------------------------

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from company.api.v1.views import (
    CMSContentViewSet, LMSModuleViewSet,
    PromoVideoSectionViewSet, TestimonialViewSet,
    LeadViewSet, PublicLeadCreateView, BackofficeLeadListView, BackofficeLeadDetailView,
)

router = DefaultRouter()
router.register('cms', CMSContentViewSet, basename='cms')
router.register('lms', LMSModuleViewSet, basename='lms')
router.register('promo-video', PromoVideoSectionViewSet, basename='promo-video')
router.register('testimonials', TestimonialViewSet, basename='testimonial')
router.register('leads', LeadViewSet, basename='lead')

urlpatterns = [
    path('leads/public/', PublicLeadCreateView.as_view(), name='public-lead-create'),
    path('leads/backoffice/', BackofficeLeadListView.as_view(), name='backoffice-lead-list'),
    path('leads/backoffice/<int:pk>/', BackofficeLeadDetailView.as_view(), name='backoffice-lead-detail'),
    path('', include(router.urls)),
]

