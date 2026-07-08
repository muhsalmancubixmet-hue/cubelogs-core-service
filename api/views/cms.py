"""
api/views/cms.py — CMS, LMS, promo video, testimonials, and coupon views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from api.models import (
    CMSContent, LMSModule, Coupon, BackofficeCoupon,
    PromoVideoSection, Testimonial
)
from api.serializers import (
    CMSContentSerializer, LMSModuleSerializer, CouponSerializer,
    BackofficeCouponSerializer, PromoVideoSectionSerializer, TestimonialSerializer
)
from api.views.crm import IsSuperAdminUser


class CMSContentViewSet(viewsets.ModelViewSet):
    queryset = CMSContent.objects.all().order_by('key')
    serializer_class = CMSContentSerializer

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

        import os
        ext = os.path.splitext(video_file.name)[1].lower()
        if ext not in ['.mp4', '.webm', '.ogg', '.mov']:
            return Response({'error': 'Unsupported file format. Please upload MP4, WebM, OGG or MOV.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.conf import settings
        from django.core.files.storage import FileSystemStorage

        os.makedirs(os.path.join(str(settings.MEDIA_ROOT), 'cms'), exist_ok=True)
        fs = FileSystemStorage(
            location=os.path.join(str(settings.MEDIA_ROOT), 'cms'),
            base_url=str(settings.MEDIA_URL) + 'cms/'
        )
        filename = fs.save(video_file.name, video_file)
        file_url = request.build_absolute_uri(fs.url(filename))

        obj, created = CMSContent.objects.update_or_create(
            key=video_type,
            defaults={'value': file_url}
        )

        return Response({
            'message': 'Video uploaded successfully.',
            'url': file_url,
            'cms_content': CMSContentSerializer(obj).data
        }, status=status.HTTP_200_OK)


class LMSModuleViewSet(viewsets.ModelViewSet):
    queryset = LMSModule.objects.all().order_by('-createdAt')
    serializer_class = LMSModuleSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsSuperAdminUser()]


class CouponViewSet(viewsets.ModelViewSet):
    queryset = Coupon.objects.all().order_by('-createdAt')
    serializer_class = CouponSerializer
    permission_classes = [IsSuperAdminUser]


class BackofficeCouponViewSet(viewsets.ModelViewSet):
    queryset = BackofficeCoupon.objects.all().order_by('-created_at')
    serializer_class = BackofficeCouponSerializer
    permission_classes = [IsSuperAdminUser]

    def perform_create(self, serializer):
        code = self.request.data.get('code')
        if not code or not code.strip():
            from api.models import default_coupon_code
            code = default_coupon_code()
        serializer.save(code=code.upper())


class PromoVideoSectionViewSet(viewsets.ModelViewSet):
    permission_classes = []

    def get_queryset(self):
        return PromoVideoSection.objects.all().order_by('-created_at')

    def get_serializer_class(self):
        return PromoVideoSectionSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]


class TestimonialViewSet(viewsets.ModelViewSet):
    permission_classes = []

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
