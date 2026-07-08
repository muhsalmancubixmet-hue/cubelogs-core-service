from django.db import models


class EmailQueue(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
        ('RETRYING', 'Retrying'),
    ]

    recipient = models.EmailField()
    from_email = models.EmailField(blank=True, null=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    html_body = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    task_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient} - {self.subject} ({self.status})"


class EmailLog(models.Model):
    TEMPLATE_CHOICES = [
        ('WELCOME', 'User Registration Welcome Email'),
        ('LOW_BALANCE', 'Low Wallet Alert'),
        ('DEBIT_INVOICE', 'Standard Transaction Invoice'),
        ('SUBSCRIPTION_EXPIRED', 'Subscription Expired Notice'),
        ('DATA_KEEPING_FEE', 'Monthly Data Maintenance Invoice'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
    ]

    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    template_type = models.CharField(max_length=20, choices=TEMPLATE_CHOICES)
    html_content = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    password = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient} - {self.template_type} ({self.status})"
