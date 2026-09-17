from django.db import migrations


def backfill_payroll_profiles(apps, schema_editor):
    EmployeeSalaryStructure = apps.get_model('payroll', 'EmployeeSalaryStructure')
    Payslip = apps.get_model('payroll', 'Payslip')
    EmployeeProfile = apps.get_model('users', 'EmployeeProfile')

    user_to_profile = dict(
        EmployeeProfile.objects.values_list('user_id', 'id')
    )

    # 1. Backfill EmployeeSalaryStructure
    structures_to_update = []
    for s in EmployeeSalaryStructure.objects.filter(employee_id__isnull=False, employee_profile_id__isnull=True).iterator(chunk_size=500):
        prof_id = user_to_profile.get(s.employee_id)
        if prof_id:
            s.employee_profile_id = prof_id
            structures_to_update.append(s)
            if len(structures_to_update) >= 500:
                EmployeeSalaryStructure.objects.bulk_update(structures_to_update, ['employee_profile'])
                structures_to_update = []
    if structures_to_update:
        EmployeeSalaryStructure.objects.bulk_update(structures_to_update, ['employee_profile'])

    # 2. Backfill Payslip
    payslips_to_update = []
    for p in Payslip.objects.filter(employee_id__isnull=False, employee_profile_id__isnull=True).iterator(chunk_size=500):
        prof_id = user_to_profile.get(p.employee_id)
        if prof_id:
            p.employee_profile_id = prof_id
            payslips_to_update.append(p)
            if len(payslips_to_update) >= 500:
                Payslip.objects.bulk_update(payslips_to_update, ['employee_profile'])
                payslips_to_update = []
    if payslips_to_update:
        Payslip.objects.bulk_update(payslips_to_update, ['employee_profile'])


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0007_employeesalarystructure_employee_profile_and_more'),
        ('users', '0012_backfill_employee_profiles'),
    ]

    operations = [
        migrations.RunPython(
            backfill_payroll_profiles,
            reverse_code=migrations.RunPython.noop
        ),
    ]
