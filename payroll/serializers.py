# --------------------------------------------------------------------------------
#       Payroll Serializers
# --------------------------------------------------------------------------------

from rest_framework import serializers
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    PayrollPeriod,
    PayrollAdjustment,
    PayrollEmployeeSnapshot,
    Payslip,
    SalaryPayment,
)


class SalaryComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryComponent
        fields = [
            'id', 'name', 'code', 'component_type',
            'is_taxable', 'is_proratable', 'is_active', 'description',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def create(self, validated_data):
        if 'organization' not in validated_data:
            request = self.context.get('request')
            if request:
                validated_data['organization'] = getattr(request, 'active_organization', None) or getattr(request.user, 'organization', None)
            else:
                user = self.context.get('user')
                if user:
                    validated_data['organization'] = getattr(user, 'organization', None)
        return super().create(validated_data)


class EmployeeSalaryComponentSerializer(serializers.ModelSerializer):
    salary_component_id = serializers.PrimaryKeyRelatedField(
        source='salary_component',
        read_only=True
    )
    name = serializers.CharField(source='salary_component.name', read_only=True)
    code = serializers.CharField(source='salary_component.code', read_only=True)
    component_type = serializers.CharField(source='salary_component.component_type', read_only=True)
    is_proratable = serializers.BooleanField(source='salary_component.is_proratable', read_only=True)

    class Meta:
        model = EmployeeSalaryComponent
        fields = [
            'id', 'salary_component_id', 'name', 'code',
            'component_type', 'is_proratable', 'amount'
        ]


class EmployeeSalaryStructureSerializer(serializers.ModelSerializer):
    components = EmployeeSalaryComponentSerializer(many=True, read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeSalaryStructure
        fields = [
            'id', 'employee', 'effective_from', 'currency',
            'compensation_type', 'daily_rate', 'hourly_rate',
            'gross_salary', 'base_deductions', 'base_net_salary',
            'is_active', 'notes', 'created_by', 'created_by_name',
            'components', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'employee', 'currency', 'gross_salary',
            'base_deductions', 'base_net_salary', 'created_by',
            'created_at', 'updated_at'
        ]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip() or obj.created_by.email
        return None


class SalaryStructureCreateSerializer(serializers.Serializer):
    effective_from = serializers.DateField(required=True)
    compensation_type = serializers.ChoiceField(choices=['MONTHLY', 'DAILY', 'HOURLY'], default='MONTHLY', required=False)
    daily_rate = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    hourly_rate = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    components = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list
    )
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, attrs):
        comp_type = attrs.get('compensation_type', 'MONTHLY')
        daily_rate = attrs.get('daily_rate')
        hourly_rate = attrs.get('hourly_rate')
        components = attrs.get('components', [])

        if comp_type == 'HOURLY':
            if hourly_rate is None or hourly_rate <= 0:
                raise serializers.ValidationError({"hourly_rate": "Hourly rate is required and must be greater than zero for Hourly Wage."})
        elif comp_type == 'DAILY':
            if daily_rate is None or daily_rate <= 0:
                raise serializers.ValidationError({"daily_rate": "Daily rate is required and must be greater than zero for Daily Wage."})
        elif comp_type == 'MONTHLY':
            if not components:
                raise serializers.ValidationError({"components": "At least one salary component is required for Monthly Salary."})
        return attrs


class PayrollAdjustmentSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PayrollAdjustment
        fields = [
            'id', 'payroll_period', 'employee', 'adjustment_type',
            'category', 'amount', 'description', 'created_by',
            'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'payroll_period', 'created_by', 'created_at', 'updated_at']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip() or obj.created_by.email
        return None

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Adjustment amount must be greater than zero.")
        return value


class SalaryPaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    voided_by_name = serializers.SerializerMethodField()
    employee_name = serializers.CharField(source='employee.get_full_name', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    class Meta:
        model = SalaryPayment
        fields = [
            'id', 'organization', 'payroll_period', 'payroll_snapshot', 'employee', 'employee_name',
            'paid_amount', 'paid_at', 'payment_method', 'payment_method_display',
            'transaction_reference', 'notes', 'status', 'recorded_by', 'recorded_by_name',
            'voided_at', 'voided_by', 'voided_by_name', 'void_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = fields

    def get_recorded_by_name(self, obj):
        if obj.recorded_by:
            return f"{obj.recorded_by.first_name} {obj.recorded_by.last_name}".strip() or obj.recorded_by.email
        return None

    def get_voided_by_name(self, obj):
        if obj.voided_by:
            return f"{obj.voided_by.first_name} {obj.voided_by.last_name}".strip() or obj.voided_by.email
        return None


class PayrollEmployeeSnapshotSerializer(serializers.ModelSerializer):
    payslip_id = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_details = serializers.SerializerMethodField()

    class Meta:
        model = PayrollEmployeeSnapshot
        fields = [
            'id', 'payroll_period', 'revision', 'is_current',
            'employee', 'employee_name', 'designation',
            'salary_structure', 'salary_effective_from', 'compensation_type', 'daily_rate', 'hourly_rate', 'currency',
            'working_days', 'payable_attendance_units', 'payable_hours',
            'paid_leave_days', 'unpaid_leave_days', 'absent_days',
            'base_gross_salary', 'base_fixed_deductions', 'base_net_salary',
            'proratable_gross', 'non_proratable_gross',
            'attendance_deduction', 'earned_gross',
            'additional_earnings', 'additional_deductions',
            'total_deductions', 'net_payable',
            'salary_components_snapshot', 'attendance_summary_snapshot',
            'calculation_breakdown', 'status', 'notes',
            'payslip_id', 'payment_status', 'payment_details', 'created_at'
        ]

    def get_payslip_id(self, obj):
        first_ps = obj.issued_payslips.filter(status='Issued').first()
        return first_ps.id if first_ps else None

    def get_payment_status(self, obj):
        active_payment = obj.payment_records.filter(status='Paid', is_deleted=False).first()
        return 'Paid' if active_payment else 'Unpaid'

    def get_payment_details(self, obj):
        active_payment = obj.payment_records.filter(status='Paid', is_deleted=False).first()
        if active_payment:
            return SalaryPaymentSerializer(active_payment).data
        return None


class PayrollPeriodSerializer(serializers.ModelSerializer):
    calculated_by_name = serializers.SerializerMethodField()
    finalized_by_name = serializers.SerializerMethodField()
    reopened_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PayrollPeriod
        fields = [
            'id', 'organization', 'year', 'month', 'status',
            'current_revision', 'attendance_period', 'attendance_revision',
            'currency', 'proration_basis', 'total_employees',
            'total_base_gross', 'total_attendance_deductions',
            'total_earned_gross', 'total_base_deductions',
            'total_adjustments_net', 'total_net_payable',
            'calculated_at', 'calculated_by', 'calculated_by_name',
            'finalized_at', 'finalized_by', 'finalized_by_name',
            'reopen_reason', 'reopened_at', 'reopened_by', 'reopened_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = fields

    def get_calculated_by_name(self, obj):
        if obj.calculated_by:
            return f"{obj.calculated_by.first_name} {obj.calculated_by.last_name}".strip() or obj.calculated_by.email
        return None

    def get_finalized_by_name(self, obj):
        if obj.finalized_by:
            return f"{obj.finalized_by.first_name} {obj.finalized_by.last_name}".strip() or obj.finalized_by.email
        return None

    def get_reopened_by_name(self, obj):
        if obj.reopened_by:
            return f"{obj.reopened_by.first_name} {obj.reopened_by.last_name}".strip() or obj.reopened_by.email
        return None


class PayslipSerializer(serializers.ModelSerializer):
    issued_by_name = serializers.SerializerMethodField()
    year = serializers.IntegerField(source='payroll_period.year', read_only=True)
    month = serializers.IntegerField(source='payroll_period.month', read_only=True)
    net_payable = serializers.DecimalField(source='payroll_snapshot.net_payable', max_digits=12, decimal_places=2, read_only=True)
    compensation_type = serializers.CharField(source='payroll_snapshot.compensation_type', read_only=True)
    daily_rate = serializers.DecimalField(source='payroll_snapshot.daily_rate', max_digits=12, decimal_places=2, read_only=True)
    hourly_rate = serializers.DecimalField(source='payroll_snapshot.hourly_rate', max_digits=12, decimal_places=2, read_only=True)
    payable_hours = serializers.DecimalField(source='payroll_snapshot.payable_hours', max_digits=8, decimal_places=2, read_only=True)
    currency = serializers.CharField(source='payroll_snapshot.currency', read_only=True)
    employee_name = serializers.CharField(source='payroll_snapshot.employee_name', read_only=True)
    designation = serializers.CharField(source='payroll_snapshot.designation', read_only=True)

    payment_status = serializers.SerializerMethodField()
    paid_at = serializers.SerializerMethodField()
    payment_method = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            'id', 'payroll_period', 'payroll_snapshot', 'employee',
            'year', 'month', 'revision', 'payslip_number', 'status',
            'compensation_type', 'daily_rate', 'hourly_rate', 'payable_hours',
            'employee_name', 'designation', 'net_payable', 'currency',
            'company_name_snapshot', 'company_details_snapshot', 'employee_details_snapshot',
            'issued_at', 'issued_by', 'issued_by_name', 'payment_status', 'paid_at', 'payment_method', 'created_at'
        ]
        read_only_fields = fields

    def get_issued_by_name(self, obj):
        if obj.issued_by:
            return f"{obj.issued_by.first_name} {obj.issued_by.last_name}".strip() or obj.issued_by.email
        return None

    def get_payment_status(self, obj):
        if not obj.payroll_snapshot:
            return 'Unpaid'
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return 'Paid' if active_payment else 'Unpaid'

    def get_paid_at(self, obj):
        if not obj.payroll_snapshot:
            return None
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return active_payment.paid_at if active_payment else None

    def get_payment_method(self, obj):
        if not obj.payroll_snapshot:
            return None
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return active_payment.get_payment_method_display() if active_payment else None


class PayslipDetailSerializer(serializers.ModelSerializer):
    payroll_snapshot = PayrollEmployeeSnapshotSerializer(read_only=True)
    issued_by_name = serializers.SerializerMethodField()
    year = serializers.IntegerField(source='payroll_period.year', read_only=True)
    month = serializers.IntegerField(source='payroll_period.month', read_only=True)
    net_payable = serializers.DecimalField(source='payroll_snapshot.net_payable', max_digits=12, decimal_places=2, read_only=True)
    compensation_type = serializers.CharField(source='payroll_snapshot.compensation_type', read_only=True)
    daily_rate = serializers.DecimalField(source='payroll_snapshot.daily_rate', max_digits=12, decimal_places=2, read_only=True)
    hourly_rate = serializers.DecimalField(source='payroll_snapshot.hourly_rate', max_digits=12, decimal_places=2, read_only=True)
    payable_hours = serializers.DecimalField(source='payroll_snapshot.payable_hours', max_digits=8, decimal_places=2, read_only=True)
    currency = serializers.CharField(source='payroll_snapshot.currency', read_only=True)
    employee_name = serializers.CharField(source='payroll_snapshot.employee_name', read_only=True)
    designation = serializers.CharField(source='payroll_snapshot.designation', read_only=True)

    payment_status = serializers.SerializerMethodField()
    paid_at = serializers.SerializerMethodField()
    payment_method = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            'id', 'payroll_period', 'payroll_snapshot', 'employee',
            'year', 'month', 'revision', 'payslip_number', 'status',
            'compensation_type', 'daily_rate', 'hourly_rate', 'payable_hours',
            'employee_name', 'designation', 'net_payable', 'currency',
            'company_name_snapshot', 'company_details_snapshot', 'employee_details_snapshot',
            'issued_at', 'issued_by', 'issued_by_name', 'payment_status', 'paid_at', 'payment_method', 'created_at'
        ]
        read_only_fields = fields

    def get_issued_by_name(self, obj):
        if obj.issued_by:
            return f"{obj.issued_by.first_name} {obj.issued_by.last_name}".strip() or obj.issued_by.email
        return None

    def get_payment_status(self, obj):
        if not obj.payroll_snapshot:
            return 'Unpaid'
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return 'Paid' if active_payment else 'Unpaid'

    def get_paid_at(self, obj):
        if not obj.payroll_snapshot:
            return None
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return active_payment.paid_at if active_payment else None

    def get_payment_method(self, obj):
        if not obj.payroll_snapshot:
            return None
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return active_payment.get_payment_method_display() if active_payment else None


class MyPayslipListSerializer(serializers.ModelSerializer):
    year = serializers.IntegerField(source='payroll_period.year', read_only=True)
    month = serializers.IntegerField(source='payroll_period.month', read_only=True)
    month_name = serializers.SerializerMethodField()
    net_payable = serializers.DecimalField(source='payroll_snapshot.net_payable', max_digits=12, decimal_places=2, read_only=True)
    currency = serializers.CharField(source='payroll_snapshot.currency', read_only=True)

    payment_status = serializers.SerializerMethodField()
    paid_at = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            'id', 'payslip_number', 'year', 'month', 'month_name',
            'revision', 'currency', 'net_payable', 'issued_at',
            'payment_status', 'paid_at'
        ]
        read_only_fields = fields

    def get_month_name(self, obj):
        month_names = [
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'
        ]
        if 1 <= obj.payroll_period.month <= 12:
            return f"{month_names[obj.payroll_period.month - 1]} {obj.payroll_period.year}"
        return f"{obj.payroll_period.year}-{obj.payroll_period.month:02d}"

    def get_payment_status(self, obj):
        if not obj.payroll_snapshot:
            return 'Unpaid'
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return 'Paid' if active_payment else 'Unpaid'

    def get_paid_at(self, obj):
        if not obj.payroll_snapshot:
            return None
        active_payment = obj.payroll_snapshot.payment_records.filter(status='Paid', is_deleted=False).first()
        return active_payment.paid_at if active_payment else None




