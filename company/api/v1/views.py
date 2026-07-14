# --------------------------------------------------------------------------------
#       Company Views (Unified CMS, Billing, and CRM)
# --------------------------------------------------------------------------------

import os
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import Employee, PERMISSION_FLAGS
from core.models import AuditLog, Organization, OrgSettings

from company.models import (
    CMSContent, LMSModule, PromoVideoSection, Testimonial,
    Lead, LeadHistory
)

from company.api.v1.serializers import (
    CMSContentSerializer, LMSModuleSerializer,
    PromoVideoSectionSerializer, TestimonialSerializer,
    LeadSerializer, LeadHistorySerializer
)
from django_filters.rest_framework import DjangoFilterBackend
from core.mixins import FilterMixinNew
from company.filters import (
    LeadFilter, CMSContentFilter, LMSModuleFilter,
    TestimonialFilter, PromoVideoSectionFilter
)





class IsSuperAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and getattr(request.user, 'isSuperAdmin', False)):
            return False

        # Client superadmin — belongs to an organisation
        if request.user.organization is not None:
            return True

        # Root admin
        if request.user.email == 'salmankcsiju@gmail.com':
            return True

        # Backoffice operators — enforce page-level permissions
        user_perms = getattr(request.user, 'permissions', [])
        if not isinstance(user_perms, list):
            user_perms = []

        all_backoffice_perms = [
            'packages', 'subscribers', 'leads', 'cms', 'faqs',
            'testimonials', 'coupons', 'staff', 'audit_logs',
        ]
        if not any(p in user_perms for p in all_backoffice_perms):
            user_perms = all_backoffice_perms

        path = request.path
        if 'packages' in path:
            return 'packages' in user_perms
        elif 'subscribers' in path:
            return 'subscribers' in user_perms
        elif 'leads' in path:
            return 'leads' in user_perms
        elif 'cms' in path:
            return 'cms' in user_perms
        elif 'lms' in path:
            return 'lms' in user_perms
        elif 'coupons' in path:
            return 'coupons' in user_perms
        elif 'employees' in path:
            return 'staff' in user_perms
        elif 'audit-logs' in path:
            return 'audit_logs' in user_perms

        return True



class CanViewPackagesOrSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if getattr(request.user, 'isSuperAdmin', False):
            return True
        user_perms = getattr(request.user, 'permissions', [])
        return 'settings:billing' in user_perms


# ==============================================================================
# 4. CMS Views
# ==============================================================================


# CMSContentViewSet: ViewSet managing static website landing page content blocks.
class CMSContentViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = CMSContent.objects.all().order_by('key')
    serializer_class = CMSContentSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = CMSContentFilter


    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsSuperAdminUser()]

    @action(detail=False, methods=['post'], url_path='upload-video')
    def upload_video(self, request):
        if 'video' not in request.FILES:
            return Response({'error': 'No video file provided.'}, status=status.HTTP_400_BAD_REQUEST)

        video_file = request.FILES['video']
        video_type = request.data.get('video_type', 'hero_video_url')
        if video_type not in ['hero_video_url', 'hero_video_url_mobile']:
            video_type = 'hero_video_url'

        ext = os.path.splitext(video_file.name)[1].lower()
        if ext not in ['.mp4', '.webm', '.ogg', '.mov']:
            return Response(
                {'error': 'Unsupported file format. Please upload MP4, WebM, OGG or MOV.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.conf import settings as django_settings
        from django.core.files.storage import FileSystemStorage

        os.makedirs(os.path.join(str(django_settings.MEDIA_ROOT), 'cms'), exist_ok=True)
        fs = FileSystemStorage(
            location=os.path.join(str(django_settings.MEDIA_ROOT), 'cms'),
            base_url=str(django_settings.MEDIA_URL) + 'cms/',
        )
        filename = fs.save(video_file.name, video_file)
        file_url = request.build_absolute_uri(fs.url(filename))

        obj, _ = CMSContent.objects.update_or_create(
            key=video_type,
            defaults={'value': file_url},
        )

        return Response({
            'message': 'Video uploaded successfully.',
            'url': file_url,
            'cms_content': CMSContentSerializer(obj).data,
        }, status=status.HTTP_200_OK)


# LMSModuleViewSet: ViewSet managing learning coaching modules and materials.
class LMSModuleViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = LMSModule.objects.all().order_by('-created_at')
    serializer_class = LMSModuleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LMSModuleFilter


    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsSuperAdminUser()]


# PromoVideoSectionViewSet: ViewSet managing promotional video sections and active statuses.
class PromoVideoSectionViewSet(FilterMixinNew, viewsets.ModelViewSet):
    filter_backends = [DjangoFilterBackend]
    filterset_class = PromoVideoSectionFilter

    def get_queryset(self):
        return PromoVideoSection.objects.all().order_by('-created_at')


    def get_serializer_class(self):
        return PromoVideoSectionSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]


# TestimonialViewSet: ViewSet managing testimonials, stars ratings, and approval actions.
class TestimonialViewSet(FilterMixinNew, viewsets.ModelViewSet):
    filter_backends = [DjangoFilterBackend]
    filterset_class = TestimonialFilter

    def get_queryset(self):
        if self.request.user.is_authenticated:
            return Testimonial.objects.all().order_by('-created_at')

        return Testimonial.objects.filter(is_approved=True).order_by('-created_at')

    def get_serializer_class(self):
        return TestimonialSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'create']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]


# ==============================================================================
# 5. CRM Views
# ==============================================================================

# LeadViewSet: ViewSet managing CRM Lead records and authorization rules.
class LeadViewSet(FilterMixinNew, viewsets.ModelViewSet):
    queryset = Lead.objects.all().order_by('-created_at')
    serializer_class = LeadSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = LeadFilter


    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save()


# PublicLeadCreateView: API endpoint for public users to generate a prospective lead enquiry.
class PublicLeadCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LeadSerializer(data=request.data)
        if serializer.is_valid():
            lead = serializer.save()
            LeadHistory.objects.create(
                lead=lead,
                action="Lead generated from public website enquiry."
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# BackofficeLeadListView: View for super-admins to retrieve a list of all lead enquiries.
class BackofficeLeadListView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request):
        leads = Lead.objects.all().order_by('-created_at')
        serializer = LeadSerializer(leads, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# BackofficeLeadDetailView: View to retrieve, update, and log audit histories for detailed lead records.
class BackofficeLeadDetailView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request, pk):
        try:
            lead = Lead.objects.get(pk=pk)
        except Lead.DoesNotExist:
            return Response({'error': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not lead.is_read:
            lead.is_read = True
            lead.read_by = request.user
            lead.save()

            operator_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email
            LeadHistory.objects.create(
                lead=lead,
                modified_by=request.user,
                action=f"Lead details read by {operator_name}."
            )

        lead_data = LeadSerializer(lead).data
        histories = LeadHistory.objects.filter(lead=lead).order_by('timestamp')
        histories_data = LeadHistorySerializer(histories, many=True).data

        return Response({'lead': lead_data, 'histories': histories_data}, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        try:
            lead = Lead.objects.get(pk=pk)
        except Lead.DoesNotExist:
            return Response({'error': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        old_status = lead.status
        old_staff = lead.assigned_staff

        serializer = LeadSerializer(lead, data=request.data, partial=True)
        if serializer.is_valid():
            updated_lead = serializer.save()
            operator_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email

            if old_status != updated_lead.status:
                LeadHistory.objects.create(
                    lead=updated_lead,
                    modified_by=request.user,
                    action=f"Status updated from {old_status} to {updated_lead.status} by {operator_name}."
                )

            new_staff = updated_lead.assigned_staff
            if old_staff != new_staff:
                if new_staff is None:
                    LeadHistory.objects.create(
                        lead=updated_lead, modified_by=request.user,
                        action=f"Lead unassigned by {operator_name}."
                    )
                else:
                    staff_name = f"{new_staff.first_name} {new_staff.last_name}".strip() or new_staff.email
                    LeadHistory.objects.create(
                        lead=updated_lead, modified_by=request.user,
                        action=f"Lead assigned to {staff_name} by {operator_name}."
                    )

            lead_data = LeadSerializer(updated_lead).data
            histories = LeadHistory.objects.filter(lead=updated_lead).order_by('timestamp')
            return Response({'lead': lead_data, 'histories': LeadHistorySerializer(histories, many=True).data})

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

