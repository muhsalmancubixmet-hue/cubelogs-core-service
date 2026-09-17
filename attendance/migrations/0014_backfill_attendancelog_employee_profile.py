from django.db import migrations


def backfill_attendancelog_profile(apps, schema_editor):
    AttendanceLog = apps.get_model('attendance', 'AttendanceLog')
    EmployeeProfile = apps.get_model('users', 'EmployeeProfile')

    user_to_profile = dict(
        EmployeeProfile.objects.values_list('user_id', 'id')
    )

    logs_to_update = []
    for log in AttendanceLog.objects.filter(employee_id__isnull=False, employee_profile_id__isnull=True).iterator(chunk_size=500):
        prof_id = user_to_profile.get(log.employee_id)
        if prof_id:
            log.employee_profile_id = prof_id
            logs_to_update.append(log)
            if len(logs_to_update) >= 500:
                AttendanceLog.objects.bulk_update(logs_to_update, ['employee_profile'])
                logs_to_update = []

    if logs_to_update:
        AttendanceLog.objects.bulk_update(logs_to_update, ['employee_profile'])


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0013_attendancelog_employee_profile_and_more'),
        ('users', '0012_backfill_employee_profiles'),
    ]

    operations = [
        migrations.RunPython(
            backfill_attendancelog_profile,
            reverse_code=migrations.RunPython.noop
        ),
    ]
