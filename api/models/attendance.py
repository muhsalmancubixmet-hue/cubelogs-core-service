from django.db import models
from api.models.employee import Employee


class AttendanceLog(models.Model):
    ATTENDANCE_STATUS_CHOICES = [
        ('Pending Approval', 'Pending Approval'),
        ('Approved', 'Approved'),
        ('Late', 'Late'),
        ('Half Day', 'Half Day'),
        ('Absent', 'Absent'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance_logs')
    employeeName = models.CharField(max_length=255)
    date = models.DateField()
    clockIn = models.DateTimeField(null=True, blank=True)
    clockOut = models.DateTimeField(null=True, blank=True)
    totalDuration = models.CharField(max_length=50, blank=True, null=True)  # "HH:MM"
    verificationPhoto = models.TextField(blank=True, null=True)  # base64
    verificationLocation = models.JSONField(default=dict, blank=True)  # {lat, lon}
    status = models.CharField(
        max_length=50,
        choices=ATTENDANCE_STATUS_CHOICES,
        default='Pending Approval',
    )  # Pending Approval | Approved | Late | Half Day | Absent

    def __str__(self):
        return f"{self.employeeName} - {self.date} ({self.status})"


class Schedule(models.Model):
    designation = models.CharField(max_length=100, unique=True)
    shiftStart = models.CharField(max_length=5, default="09:00")  # "HH:MM"
    shiftEnd = models.CharField(max_length=5, default="17:00")    # "HH:MM"
    createdAt = models.DateTimeField(auto_now_add=True)
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.designation} ({self.shiftStart} - {self.shiftEnd})"
