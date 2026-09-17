from django.db import migrations


def backfill_projecttask_profile(apps, schema_editor):
    ProjectTask = apps.get_model('projects', 'ProjectTask')
    EmployeeProfile = apps.get_model('users', 'EmployeeProfile')

    user_to_profile = dict(
        EmployeeProfile.objects.values_list('user_id', 'id')
    )

    tasks_to_update = []
    for task in ProjectTask.objects.filter(assigned_to_id__isnull=False, assigned_to_profile_id__isnull=True).iterator(chunk_size=500):
        prof_id = user_to_profile.get(task.assigned_to_id)
        if prof_id:
            task.assigned_to_profile_id = prof_id
            tasks_to_update.append(task)
            if len(tasks_to_update) >= 500:
                ProjectTask.objects.bulk_update(tasks_to_update, ['assigned_to_profile'])
                tasks_to_update = []
    if tasks_to_update:
        ProjectTask.objects.bulk_update(tasks_to_update, ['assigned_to_profile'])


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0018_projecttask_assigned_to_profile_and_more'),
        ('users', '0012_backfill_employee_profiles'),
    ]

    operations = [
        migrations.RunPython(
            backfill_projecttask_profile,
            reverse_code=migrations.RunPython.noop
        ),
    ]
