from django.db import models


class CMSContent(models.Model):
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True, null=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key


class LMSModule(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=100, default='Coaching')
    content = models.TextField(blank=True, null=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class PromoVideoSection(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    youtube_url = models.URLField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Testimonial(models.Model):
    stars = models.IntegerField(default=5)
    text = models.TextField()
    author_initials = models.CharField(max_length=10, blank=True, null=True)
    author_name = models.CharField(max_length=255)
    author_title = models.CharField(max_length=255)
    bg_color = models.CharField(max_length=50, default='var(--primary)')
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.author_name} ({self.stars} stars)"
