# api/serializers/cms.py
from rest_framework import serializers
from api.models import CMSContent, LMSModule, Coupon, PromoVideoSection, Testimonial
from api.serializers.utils import extract_youtube_id

class CMSContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CMSContent
        fields = '__all__'

class LMSModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LMSModule
        fields = '__all__'

class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
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
