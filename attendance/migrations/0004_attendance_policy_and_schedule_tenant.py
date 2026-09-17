# Generated manually for safe multi-step tenant migration
from django.db import migrations, models
import django.db.models.deletion
import attendance.models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('attendance', '0003_alter_holiday_created_at_alter_holiday_updated_at_and_more'),
    ]

    operations = [
        # 1. Add nullable organization to Schedule
        migrations.AddField(
            model_name='schedule',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='schedules',
                to='core.organization'
            ),
        ),
        # 2. Remove global unique constraint from designation
        migrations.AlterField(
            model_name='schedule',
            name='designation',
            field=models.CharField(max_length=100),
        ),
        # 3. Create AttendancePolicy model
        migrations.CreateModel(
            name='AttendancePolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('effective_from', models.DateField(db_index=True)),
                ('grace_period_minutes', models.IntegerField(default=15)),
                ('full_day_minimum_minutes', models.IntegerField(default=480)),
                ('half_day_minimum_minutes', models.IntegerField(default=240)),
                ('break_duration_minutes', models.IntegerField(default=60)),
                ('break_type', models.CharField(choices=[('Paid', 'Paid'), ('Unpaid', 'Unpaid')], default='Unpaid', max_length=20)),
                ('default_weekly_holidays', models.JSONField(blank=True, default=attendance.models.default_weekly_holidays_policy)),
                ('auto_approve_attendance', models.BooleanField(default=False)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendance_policies', to='core.organization')),
            ],
            options={
                'db_table': 'api_attendancepolicy',
                'ordering': ['-effective_from'],
            },
        ),
        migrations.AddConstraint(
            model_name='attendancepolicy',
            constraint=models.UniqueConstraint(fields=('organization', 'effective_from'), name='unique_org_effective_from_policy'),
        ),
    ]
