# --------------------------------------------------------------------------------
#       Company Serializers (CMS and CRM)
# --------------------------------------------------------------------------------

from rest_framework import serializers
from core.utils import extract_youtube_id
from users.models import Employee
from company.models import (
    CMSContent, LMSModule, PromoVideoSection, Testimonial,
    Lead, LeadHistory
)


# ==============================================================================
# 1. CMS Serializers
# ==============================================================================

class CMSContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CMSContent
        fields = '__all__'


class LMSModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LMSModule
        fields = '__all__'


class PromoVideoSectionSerializer(serializers.ModelSerializer):
    embed_url = serializers.SerializerMethodField()

    class Meta:
        model = PromoVideoSection
        fields = ['id', 'title', 'description', 'youtube_url', 'embed_url', 'is_active']

    def get_embed_url(self, obj):
        video_id = extract_youtube_id(obj.youtube_url)
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&loop=1&playlist={video_id}"
        return ""


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        fields = '__all__'


# ==============================================================================
# 2. CRM Serializers
# ==============================================================================


class LeadSerializer(serializers.ModelSerializer):
    assigned_staff_name = serializers.SerializerMethodField()
    read_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = '__all__'

    def get_assigned_staff_name(self, obj):
        if obj.assigned_staff:
            return f"{obj.assigned_staff.first_name} {obj.assigned_staff.last_name}".strip() or obj.assigned_staff.email
        return None

    def get_read_by_name(self, obj):
        if obj.read_by:
            return f"{obj.read_by.first_name} {obj.read_by.last_name}".strip() or obj.read_by.email
        return None

class LeadHistorySerializer(serializers.ModelSerializer):
    modified_by_email = serializers.SerializerMethodField()
    modified_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LeadHistory
        fields = ['id', 'lead', 'modified_by', 'modified_by_email', 'modified_by_name', 'action', 'timestamp']

    def get_modified_by_email(self, obj):
        return obj.modified_by.email if obj.modified_by else None

    def get_modified_by_name(self, obj):
        if obj.modified_by:
            return f"{obj.modified_by.first_name} {obj.modified_by.last_name}".strip() or obj.modified_by.email
        return "System"
