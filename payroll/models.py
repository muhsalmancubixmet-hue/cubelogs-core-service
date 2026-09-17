# --------------------------------------------------------------------------------
#       Payroll Models - Salary Components, Versioned Structures, Periods & Snapshots
# --------------------------------------------------------------------------------

from decimal import Decimal
from django.db import models
from django.utils import timezone
from core.models import BaseModel, Organization


class SalaryComponent(BaseModel):
    COMPONENT_TYPE_CHOICES = [
        ('Earning', 'Earning'),
        ('Deduction', 'Deduction'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='salary_components'
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50)
    component_type = models.CharField(max_length=20, choices=COMPONENT_TYPE_CHOICES, default='Earning')
    is_taxable = models.BooleanField(default=False)
    is_proratable = models.BooleanField(default=True, help_text="Subject to attendance / unpaid leave proration")
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'api_salarycomponent'
        ordering = ['component_type', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'],
                name='unique_org_salary_component_code'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.code}) - {self.component_type}"


class EmployeeSalaryStructure(BaseModel):
    COMPENSATION_TYPE_CHOICES = [
        ('MONTHLY', 'Monthly Salary'),
        ('DAILY', 'Daily Wage'),
        ('HOURLY', 'Hourly Wage'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='employee_salary_structures'
    )
    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='salary_structures'
    )
    employee_profile = models.ForeignKey(
        'users.EmployeeProfile',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='salary_structures'
    )
    effective_from = models.DateField(db_index=True)
    currency = models.CharField(max_length=10, default='INR')

    compensation_type = models.CharField(
        max_length=20,
        choices=COMPENSATION_TYPE_CHOICES,
        default='MONTHLY',
        db_index=True,
    )
    daily_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Contractual rate per payable day (for DAILY compensation type)"
    )
    hourly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Contractual rate per payable work hour (for HOURLY compensation type)"
    )

    gross_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    base_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    base_net_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_salary_structures'
    )

    class Meta:
        db_table = 'api_employeesalarystructure'
        ordering = ['-effective_from', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'effective_from'],
                name='unique_emp_salary_effective_date'
            )
        ]

    def save(self, *args, **kwargs):
        if self.employee_id and not self.employee_profile_id:
            try:
                from users.models import EmployeeProfile
                prof = EmployeeProfile.objects.filter(user_id=self.employee_id).first()
                if prof:
                    self.employee_profile_id = prof.id
            except Exception:
                pass
        elif self.employee_profile_id and not self.employee_id:
            try:
                self.employee_id = self.employee_profile.user_id
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} Salary (From {self.effective_from}): Net {self.base_net_salary} {self.currency}"


class EmployeeSalaryComponent(BaseModel):
    salary_structure = models.ForeignKey(
        EmployeeSalaryStructure,
        on_delete=models.CASCADE,
        related_name='components'
    )
    salary_component = models.ForeignKey(
        SalaryComponent,
        on_delete=models.PROTECT,
        related_name='employee_assignments'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'api_employeesalarycomponent'
        constraints = [
            models.UniqueConstraint(
                fields=['salary_structure', 'salary_component'],
                name='unique_structure_component'
            )
        ]

    def __str__(self):
        return f"{self.salary_component.name}: {self.amount}"


class PayrollPeriod(BaseModel):
    STATUS_CHOICES = [
        ('Draft', 'Draft'),
        ('Calculated', 'Calculated'),
        ('Finalized', 'Finalized'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='payroll_periods'
    )
    year = models.PositiveSmallIntegerField(db_index=True)
    month = models.PositiveSmallIntegerField(db_index=True)  # 1 - 12
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Draft', db_index=True)
    current_revision = models.PositiveIntegerField(default=0)

    attendance_period = models.ForeignKey(
        'attendance.AttendancePeriod',
        on_delete=models.PROTECT,
        related_name='payroll_periods'
    )
    attendance_revision = models.PositiveIntegerField(default=0, help_text="Attendance snapshot revision consumed.")

    currency = models.CharField(max_length=10, default='INR')
    proration_basis = models.CharField(max_length=20, default='WORKING_DAYS')

    total_employees = models.IntegerField(default=0)
    total_base_gross = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_attendance_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_earned_gross = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_base_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_adjustments_net = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_net_payable = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    calculated_at = models.DateTimeField(null=True, blank=True)
    calculated_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='calculated_payrolls'
    )

    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='finalized_payrolls'
    )

    reopen_reason = models.TextField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reopened_payrolls'
    )

    class Meta:
        db_table = 'api_payrollperiod'
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'year', 'month'],
                name='unique_org_year_month_payroll_period'
            )
        ]

    def __str__(self):
        return f"{self.organization} Payroll {self.year}/{self.month:02d} ({self.status} Rev {self.current_revision})"


class PayrollAdjustment(BaseModel):
    ADJUSTMENT_TYPE_CHOICES = [
        ('Earning', 'Earning'),
        ('Deduction', 'Deduction'),
    ]
    CATEGORY_CHOICES = [
        ('Bonus', 'Bonus'),
        ('Commission', 'Commission'),
        ('Reimbursement', 'Reimbursement'),
        ('Fine', 'Fine / Penalty'),
        ('AdvanceRecovery', 'Salary Advance Recovery'),
        ('Other', 'Other Adjustment'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='payroll_adjustments'
    )
    payroll_period = models.ForeignKey(
        PayrollPeriod,
        on_delete=models.CASCADE,
        related_name='adjustments'
    )
    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='payroll_adjustments'
    )

    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPE_CHOICES)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Bonus')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    description = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_payroll_adjustments'
    )

    class Meta:
        db_table = 'api_payrolladjustment'
        ordering = ['employee', 'adjustment_type', 'created_at']

    def __str__(self):
        return f"{self.employee} {self.adjustment_type} ({self.category}): {self.amount}"


class PayrollEmployeeSnapshot(BaseModel):
    STATUS_CHOICES = [
        ('OK', 'OK'),
        ('NegativeNet', 'Negative Net Pay'),
        ('MissingSalaryStructure', 'Missing Salary Structure'),
        ('NotEligible', 'Not Eligible'),
        ('NeedsReview', 'Needs Review'),
    ]

    payroll_period = models.ForeignKey(
        PayrollPeriod,
        on_delete=models.CASCADE,
        related_name='employee_snapshots'
    )
    revision = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)

    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='payroll_snapshots'
    )
    employee_name = models.CharField(max_length=255)
    designation = models.CharField(max_length=100, blank=True, default='')

    salary_structure = models.ForeignKey(
        EmployeeSalaryStructure,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='payroll_snapshots'
    )
    salary_effective_from = models.DateField(null=True, blank=True)
    compensation_type = models.CharField(max_length=20, default='MONTHLY')
    hourly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Contractual hourly rate for HOURLY compensation type"
    )
    currency = models.CharField(max_length=10, default='INR')

    # Attendance metrics from AttendancePeriodEmployeeSnapshot
    working_days = models.IntegerField(default=0)
    payable_attendance_units = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    payable_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Finalized payable work hours for HOURLY calculation"
    )
    paid_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    unpaid_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    absent_days = models.IntegerField(default=0)

    # Base Contractual Amounts
    base_gross_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    base_fixed_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    base_net_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Proration & Attendance Deductions
    proratable_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    non_proratable_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    daily_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    attendance_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    earned_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Variable Adjustments
    additional_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    additional_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Totals
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    net_payable = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Frozen JSON Data for Full Historical Auditability
    salary_components_snapshot = models.JSONField(default=list)
    attendance_summary_snapshot = models.JSONField(default=dict)
    calculation_breakdown = models.JSONField(default=dict)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='OK')
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'api_payrollemployeesnapshot'
        ordering = ['employee_name']
        constraints = [
            models.UniqueConstraint(
                fields=['payroll_period', 'employee', 'revision'],
                name='unique_payroll_period_employee_revision'
            )
        ]

    def __str__(self):
        return f"{self.employee_name} Payroll Rev {self.revision}: Net {self.net_payable} {self.currency} ({self.status})"


class Payslip(BaseModel):
    STATUS_CHOICES = [
        ('Issued', 'Issued'),
        ('Superseded', 'Superseded'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='payslips'
    )
    payroll_period = models.ForeignKey(
        PayrollPeriod,
        on_delete=models.PROTECT,
        related_name='payslips'
    )
    payroll_snapshot = models.ForeignKey(
        PayrollEmployeeSnapshot,
        on_delete=models.PROTECT,
        related_name='issued_payslips'
    )
    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.PROTECT,
        related_name='payslips'
    )
    employee_profile = models.ForeignKey(
        'users.EmployeeProfile',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='payslips'
    )
    revision = models.PositiveIntegerField(default=1)
    payslip_number = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Issued', db_index=True)

    company_name_snapshot = models.CharField(max_length=255)
    company_details_snapshot = models.JSONField(default=dict)
    employee_details_snapshot = models.JSONField(default=dict)

    issued_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_payslips'
    )

    class Meta:
        db_table = 'api_payslip'
        ordering = ['-payroll_period__year', '-payroll_period__month', 'employee']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'payroll_period', 'employee', 'revision'],
                name='unique_org_period_employee_revision_payslip'
            ),
            models.UniqueConstraint(
                fields=['organization', 'payslip_number'],
                name='unique_org_payslip_number'
            )
        ]

    def save(self, *args, **kwargs):
        if self.employee_id and not self.employee_profile_id:
            try:
                from users.models import EmployeeProfile
                prof = EmployeeProfile.objects.filter(user_id=self.employee_id).first()
                if prof:
                    self.employee_profile_id = prof.id
            except Exception:
                pass
        elif self.employee_profile_id and not self.employee_id:
            try:
                self.employee_id = self.employee_profile.user_id
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.payslip_number} - {self.employee} ({self.status})"


class SalaryPayment(BaseModel):
    PAYMENT_METHOD_CHOICES = [
        ('BankTransfer', 'Bank Transfer'),
        ('Cash', 'Cash'),
        ('Cheque', 'Cheque'),
        ('Other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('Paid', 'Paid'),
        ('Voided', 'Voided'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='salary_payments'
    )
    payroll_period = models.ForeignKey(
        PayrollPeriod,
        on_delete=models.CASCADE,
        related_name='salary_payments'
    )
    payroll_snapshot = models.ForeignKey(
        PayrollEmployeeSnapshot,
        on_delete=models.CASCADE,
        related_name='payment_records'
    )
    employee = models.ForeignKey(
        'users.Employee',
        on_delete=models.CASCADE,
        related_name='salary_payments'
    )

    paid_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateField(default=timezone.now)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default='BankTransfer')
    transaction_reference = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Paid', db_index=True)
    recorded_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='recorded_salary_payments'
    )

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        'users.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='voided_salary_payments'
    )
    void_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'api_salarypayment'
        ordering = ['-paid_at', '-created_at']
        indexes = [
            models.Index(fields=['organization', 'payroll_period', 'status']),
            models.Index(fields=['employee', 'paid_at']),
            models.Index(fields=['payroll_snapshot', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['payroll_snapshot'],
                condition=models.Q(status='Paid'),
                name='unique_active_paid_salary_payment'
            )
        ]

    def __str__(self):
        return f"{self.employee} - {self.payroll_period} ({self.status}: {self.paid_amount} {self.payment_method})"


