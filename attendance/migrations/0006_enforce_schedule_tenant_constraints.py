# Generated manually for safe multi-step tenant migration
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('attendance', '0005_backfill_schedules_and_attendance_policies'),
    ]

    operations = [
        migrations.AlterField(
            model_name='schedule',
            name='organization',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='schedules',
                to='core.organization'
            ),
        ),
        migrations.AddConstraint(
            model_name='schedule',
            constraint=models.UniqueConstraint(
                fields=('organization', 'designation'),
                name='unique_org_designation_schedule'
            ),
        ),
    ]
