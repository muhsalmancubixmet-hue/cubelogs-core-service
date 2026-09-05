from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('users', '0008_employee_department_employee_employee_code'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('employee_code', models.CharField(blank=True, db_index=True, help_text='HR / Payroll Employee ID within this organization', max_length=50, null=True)),
                ('department', models.CharField(blank=True, default='', help_text='Department or team within this organization', max_length=100)),
                ('designation', models.CharField(blank=True, help_text='HR Job Title within this organization', max_length=100, null=True)),
                ('employment_status', models.CharField(choices=[('Active', 'Active'), ('Deactivated', 'Deactivated'), ('Terminated', 'Terminated'), ('Resigned', 'Resigned')], default='Active', max_length=20)),
                ('joining_date', models.DateField(blank=True, null=True)),
                ('last_working_date', models.DateField(blank=True, null=True)),
                ('is_active_in_org', models.BooleanField(default=True, help_text='Active employment access flag within this specific organization')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='core.organization')),
                ('role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='org_memberships', to='users.role')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'api_organization_membership',
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'organization'), name='unique_user_organization_membership'),
                    models.UniqueConstraint(condition=models.Q(('employee_code__isnull', False), models.Q(('employee_code', ''), _negated=True)), fields=('organization', 'employee_code'), name='unique_org_employee_code'),
                ],
            },
        ),
    ]
