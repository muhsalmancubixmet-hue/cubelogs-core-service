from django.db import migrations


def backfill_employee_profiles(apps, schema_editor):
    Employee = apps.get_model('users', 'Employee')
    EmployeeProfile = apps.get_model('users', 'EmployeeProfile')

    for emp in Employee.objects.all().iterator(chunk_size=500):
        EmployeeProfile.objects.update_or_create(
            user_id=emp.id,
            defaults={
                'organization_id': emp.organization_id,
                'employee_code': emp.employee_code,
                'designation': emp.designation,
                'department': emp.department or '',
                'employment_status': emp.employment_status or 'Active',
                'joining_date': emp.joining_date,
                'last_working_date': emp.last_working_date,
                'phone': emp.phone,
                'profile_photo': emp.profilePhoto,
            }
        )


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_add_employee_profile_model'),
    ]

    operations = [
        migrations.RunPython(
            backfill_employee_profiles,
            reverse_code=migrations.RunPython.noop
        ),
    ]
