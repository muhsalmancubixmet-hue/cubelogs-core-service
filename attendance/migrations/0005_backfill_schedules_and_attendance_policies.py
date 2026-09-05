from django.db import migrations
from django.utils import timezone


def backfill_schedules_and_policies(apps, schema_editor):
    Organization = apps.get_model('core', 'Organization')
    OrgSettings = apps.get_model('core', 'OrgSettings')
    Schedule = apps.get_model('attendance', 'Schedule')
    AttendancePolicy = apps.get_model('attendance', 'AttendancePolicy')
    AttendanceLog = apps.get_model('attendance', 'AttendanceLog')

    global_schedules = list(Schedule.objects.filter(organization__isnull=True))

    for org in Organization.objects.all():
        # 1. Clone unassigned global schedules into each organization
        for gs in global_schedules:
            Schedule.objects.get_or_create(
                organization=org,
                designation=gs.designation.strip(),
                defaults={
                    'shiftStart': gs.shiftStart,
                    'shiftEnd': gs.shiftEnd,
                }
            )

        # 2. Determine initial policy effective_from
        earliest_log_date = AttendanceLog.objects.filter(
            employee__organization=org
        ).order_by('date').values_list('date', flat=True).first()

        if earliest_log_date:
            eff_date = earliest_log_date
        elif org.created_at:
            eff_date = org.created_at.date()
        else:
            eff_date = timezone.now().date()

        # 3. Read OrgSettings or safe defaults
        settings = None
        if hasattr(org, 'settings') and org.settings:
            settings = org.settings
        elif hasattr(org, 'settings_id') and org.settings_id:
            settings = OrgSettings.objects.filter(id=org.settings_id).first()

        grace = getattr(settings, 'grace_period_minutes', 15) if settings else 15
        if grace is None:
            grace = 15

        half_day = getattr(settings, 'half_day_threshold_minutes', 240) if settings else 240
        if half_day is None:
            half_day = 240

        weekly_offs = getattr(settings, 'default_weekly_holidays', None) if settings else None
        if weekly_offs is None or not isinstance(weekly_offs, list):
            weekly_offs = ["Saturday", "Sunday"]

        auto_approve = getattr(settings, 'auto_approve_attendance', False) if settings else False
        if auto_approve is None:
            auto_approve = False

        AttendancePolicy.objects.get_or_create(
            organization=org,
            effective_from=eff_date,
            defaults={
                'grace_period_minutes': grace,
                'full_day_minimum_minutes': 480,
                'half_day_minimum_minutes': half_day,
                'break_duration_minutes': 60,
                'break_type': 'Unpaid',
                'default_weekly_holidays': weekly_offs,
                'auto_approve_attendance': auto_approve,
            }
        )

    # 4. Remove unassigned global schedules only after cloning succeeds
    if Organization.objects.exists():
        Schedule.objects.filter(organization__isnull=True).delete()


def reverse_backfill(apps, schema_editor):
    Schedule = apps.get_model('attendance', 'Schedule')
    AttendancePolicy = apps.get_model('attendance', 'AttendancePolicy')

    global_count = Schedule.objects.filter(organization__isnull=True).count()
    if global_count == 0:
        distinct_desigs = Schedule.objects.values('designation', 'shiftStart', 'shiftEnd').distinct()
        for d in distinct_desigs:
            Schedule.objects.get_or_create(
                organization=None,
                designation=d['designation'],
                defaults={'shiftStart': d['shiftStart'], 'shiftEnd': d['shiftEnd']}
            )

    Schedule.objects.filter(organization__isnull=False).delete()
    AttendancePolicy.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0004_attendance_policy_and_schedule_tenant'),
    ]

    operations = [
        migrations.RunPython(backfill_schedules_and_policies, reverse_backfill),
    ]
