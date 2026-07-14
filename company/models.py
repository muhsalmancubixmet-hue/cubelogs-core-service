# --------------------------------------------------------------------------------
#       Company Models (CMS and CRM)
# --------------------------------------------------------------------------------

from django.db import models
from core.models import BaseModel
import secrets


def default_coupon_code():
    return secrets.token_hex(4).upper()



# ==============================================================================
# 1. CMS Models
# ==============================================================================

# CMSContent Model: Stores static key-value content items displayed on frontend
class CMSContent(BaseModel):
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'api_cmscontent'

    def __str__(self):
        return self.key


# LMSModule Model: Represents learning modules, titles, categories, and content
class LMSModule(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=100, default='Coaching')
    content = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'api_lmsmodule'

    def __str__(self):
        return self.title


# PromoVideoSection Model: Configures promotional YouTube videos with titles
class PromoVideoSection(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField()
    youtube_url = models.URLField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'api_promovideosection'

    def __str__(self):
        return self.title


# Testimonial Model: Stores client reviews, rating stars, approvals, and colors
class Testimonial(BaseModel):
    stars = models.IntegerField(default=5)
    text = models.TextField()
    author_initials = models.CharField(max_length=10, blank=True, null=True)
    author_name = models.CharField(max_length=255)
    author_title = models.CharField(max_length=255)
    bg_color = models.CharField(max_length=50, default='var(--primary)')
    is_approved = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_testimonial'

    def __str__(self):
        return f"{self.author_name} ({self.stars} stars)"


# ==============================================================================
# 2. CRM Models
# ==============================================================================


# Lead Model: Tracks customer enquiries, status tracking, and operator assignment
class Lead(BaseModel):
    STATUS_CHOICES = [
        ('New', 'New'),
        ('In Progress', 'In Progress'),
        ('Closed', 'Closed'),
    ]
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, null=True)
    companyName = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='New')
    assigned_staff = models.ForeignKey(
        'users.Employee', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_leads',
    )
    is_read = models.BooleanField(default=False)
    read_by = models.ForeignKey(
        'users.Employee', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='read_leads',
    )

    class Meta:
        db_table = 'api_lead'

    def __str__(self):
        return f"{self.name} ({self.email})"


# LeadHistory Model: Maintains logs of modification events (e.g. status changes)
class LeadHistory(BaseModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='histories')
    modified_by = models.ForeignKey(
        'users.Employee', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='lead_histories',
    )
    action = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_leadhistory'

    def __str__(self):
        return f"Lead {self.lead_id} history - {self.action}"
