from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0009_attendanceperiodemployeesnapshot_payable_work_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancepolicy',
            name='minimum_session_minutes',
            field=models.IntegerField(default=5),
        ),
    ]
