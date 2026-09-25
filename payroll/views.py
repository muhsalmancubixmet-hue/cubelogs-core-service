# --------------------------------------------------------------------------------
#       Payroll Views - Tenant Scoped APIs for Salary & Monthly Payroll Processing
# --------------------------------------------------------------------------------

from datetime import datetime, date as datetime_date
from decimal import Decimal
import io
import zipfile
import re
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from django.http import HttpResponse
from django.utils import timezone

from django.db.models import Q, Sum, Count
from core.decorators import has_fine_grained_permission
from core.pagination import StandardResultsSetPagination
from core.permissions import DRFPlanPermissionRequired
from users.models import Employee
from attendance.models import AttendancePeriod
from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    PayrollPeriod,
    PayrollAdjustment,
    PayrollEmployeeSnapshot,
    Payslip,
    SalaryPayment,
)
from payroll.serializers import (
    SalaryComponentSerializer,
    EmployeeSalaryStructureSerializer,
    SalaryStructureCreateSerializer,
    PayrollPeriodSerializer,
    PayrollEmployeeSnapshotSerializer,
    PayrollAdjustmentSerializer,
    PayslipSerializer,
    PayslipDetailSerializer,
    MyPayslipListSerializer,
    SalaryPaymentSerializer,
)
from payroll.pdf import generate_payslip_pdf
from payroll.exporters import BankPaymentExporter
from payroll.services import (
    get_employee_salary_structure,
    get_bulk_employee_salary_structures,
    assign_or_revise_salary_structure,
    calculate_payroll_period,
    finalize_payroll_period,
    reopen_payroll_period,
    detect_stale_payroll_sources,
    record_salary_payment,
    bulk_record_salary_payments,
    void_salary_payment,
)


class BasePayrollAPIView(APIView):
    """
    Base APIView for Payroll endpoints.
    Enforces that the parent Attendance addon (is_attendance_enabled) is active
    on the user's organization before granting access, in addition to standard authentication.
    """
    permission_classes = [permissions.IsAuthenticated, DRFPlanPermissionRequired]
    required_plan_feature = 'is_attendance_enabled'

    def get_organization(self, request):
        """
        Resolves active organization and validates active membership.
        Phase 2B multi-tenant canonical resolution.
        """
        active_org = getattr(request, 'active_organization', None)
        if not active_org:
            raise PermissionDenied("No active organization associated with your account.")
        user = request.user
        if not (user.is_superuser or getattr(user, 'isSuperAdmin', False)):
            from users.models import OrganizationMembership
            has_membership = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).exists()
            user_has_any_mems = OrganizationMembership.objects.filter(user=user).exists()
            if user_has_any_mems:
                if not has_membership:
                    raise PermissionDenied("You do not have an active membership in this organization.")
            else:
                if getattr(user, 'organization', None) != active_org:
                    raise PermissionDenied("You do not have an active membership in this organization.")
        return active_org


class SalaryComponentViewSet(viewsets.ModelViewSet):
    """
    Tenant-scoped ViewSet for managing organization salary component catalog.
    """
    serializer_class = SalaryComponentSerializer
    permission_classes = [permissions.IsAuthenticated, DRFPlanPermissionRequired]
    required_plan_feature = 'is_attendance_enabled'

    def get_organization(self):
        active_org = getattr(self.request, 'active_organization', None)
        if not active_org:
            return None
        user = self.request.user
        if not (user.is_superuser or getattr(user, 'isSuperAdmin', False)):
            from users.models import OrganizationMembership
            has_membership = OrganizationMembership.objects.filter(
                user=user,
                organization=active_org,
                is_active_in_org=True,
                is_deleted=False
            ).exists()
            user_has_any_mems = OrganizationMembership.objects.filter(user=user).exists()
            if user_has_any_mems:
                if not has_membership:
                    raise PermissionDenied("You do not have an active membership in this organization.")
            else:
                if getattr(user, 'organization', None) != active_org:
                    raise PermissionDenied("You do not have an active membership in this organization.")
        return active_org

    def get_queryset(self):
        active_org = self.get_organization()
        if not active_org:
            return SalaryComponent.objects.none()
        return SalaryComponent.objects.filter(
            organization=active_org,
            is_deleted=False
        ).order_by('component_type', 'name')

    def check_permissions_custom(self, required_perms):
        if not has_fine_grained_permission(self.request.user, required_perms):
            raise PermissionDenied("You do not have permission to perform this salary action.")

    def list(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:view', 'salary:manage'])
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:view', 'salary:manage'])
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:manage'])
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        active_org = self.get_organization()
        if not active_org:
            raise PermissionDenied("No active organization associated with your account.")
        serializer.save(organization=active_org)

    def update(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:manage'])
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:manage'])
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.check_permissions_custom(['salary:manage'])
        instance = self.get_object()
        instance.is_deleted = True
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeSalaryDetailView(BasePayrollAPIView):
    """
    API endpoint for viewing and assigning/revising employee salary structures.
    Enforces immutable append-only revision pattern.
    """

    def get_employee(self, employee_id):
        active_org = self.get_organization(self.request)

        from django.db.models import Q
        employee = Employee.objects.filter(
            Q(id=employee_id) & (
                Q(organization=active_org) |
                Q(memberships__organization=active_org, memberships__is_active_in_org=True, memberships__is_deleted=False)
            )
        ).distinct().first()

        if not employee:
            raise NotFound(f"Employee with ID {employee_id} was not found in your organization.")
        return employee

    def get(self, request, employee_id):
        active_org = self.get_organization(request)
        employee = self.get_employee(employee_id)
        
        is_self = (request.user.id == employee.id)
        if not is_self and not has_fine_grained_permission(request.user, ['salary:view', 'salary:manage']):
            raise PermissionDenied("You do not have permission to view salary structures.")

        today = timezone.now().date()
        active_structure = get_employee_salary_structure(employee, active_org, today)

        history = EmployeeSalaryStructure.objects.filter(
            employee=employee,
            organization=active_org,
            is_active=True
        ).order_by('-effective_from', '-created_at')

        return Response({
            "employee_id": employee.id,
            "employee_name": f"{employee.first_name} {employee.last_name}".strip() or employee.email,
            "active_structure": EmployeeSalaryStructureSerializer(active_structure).data if active_structure else None,
            "history": EmployeeSalaryStructureSerializer(history, many=True).data,
            "has_structure": active_structure is not None
        })

    def post(self, request, employee_id):
        if not has_fine_grained_permission(request.user, ['salary:manage']):
            raise PermissionDenied("You do not have permission to assign or revise salary structures.")

        active_org = self.get_organization(request)
        employee = self.get_employee(employee_id)

        serializer = SalaryStructureCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        structure = assign_or_revise_salary_structure(
            employee=employee,
            organization=active_org,
            effective_from=data['effective_from'],
            components_data=data.get('components', []),
            notes=data.get('notes'),
            created_by=request.user,
            compensation_type=data.get('compensation_type', 'MONTHLY'),
            daily_rate=data.get('daily_rate'),
            hourly_rate=data.get('hourly_rate'),
        )

        return Response(
            EmployeeSalaryStructureSerializer(structure).data,
            status=status.HTTP_201_CREATED
        )


EmployeeSalaryStructureView = EmployeeSalaryDetailView


class EmployeeSalaryResolveView(BasePayrollAPIView):
    """
    Resolves the effective salary structure for an employee on a target date.
    """

    def get(self, request, employee_id):
        active_org = self.get_organization(request)

        from django.db.models import Q
        employee = Employee.objects.filter(
            Q(id=employee_id) & (
                Q(organization=active_org) |
                Q(memberships__organization=active_org, memberships__is_active_in_org=True, memberships__is_deleted=False)
            )
        ).distinct().first()

        if not employee:
            raise NotFound(f"Employee with ID {employee_id} was not found in your organization.")

        is_self = (request.user.id == employee.id)
        if not is_self and not has_fine_grained_permission(request.user, ['salary:view', 'salary:manage']):
            raise PermissionDenied("You do not have permission to resolve salary structures.")

        date_str = request.query_params.get('date')
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError({"date": "Invalid date format. Use YYYY-MM-DD."})
        else:
            target_date = timezone.now().date()

        structure = get_employee_salary_structure(employee, active_org, target_date)
        return Response({
            "target_date": target_date.isoformat(),
            "structure": EmployeeSalaryStructureSerializer(structure).data if structure else None
        })


class EmployeeSalaryListView(BasePayrollAPIView):
    """
    Read-only list of all active employees in the organization with their
    current resolved salary structure. Used by the admin Salary Structures directory.
    Supports server-side pagination (?page=, ?page_size=) and search (?search=).
    Does not recalculate any values — purely reads stored structures in 1 query.
    """

    def get(self, request):
        if not has_fine_grained_permission(request.user, ['salary:view', 'salary:manage']):
            raise PermissionDenied("You do not have permission to view salary structures.")

        org = self.get_organization(request)
        if not org:
            return Response([])

        search_q = request.query_params.get('search', '').strip()
        today = timezone.now().date()

        from django.db.models import Q
        queryset = Employee.objects.filter(
            Q(organization=org) |
            Q(memberships__organization=org, memberships__is_active_in_org=True, memberships__is_deleted=False),
            is_active=True,
        ).distinct().order_by('first_name', 'last_name')

        if search_q:
            queryset = queryset.filter(
                Q(first_name__icontains=search_q) |
                Q(last_name__icontains=search_q) |
                Q(email__icontains=search_q) |
                Q(employee_code__icontains=search_q) |
                Q(department__icontains=search_q) |
                Q(designation__icontains=search_q)
            )

        is_all = (request.query_params.get('all') == 'true')
        paginator = None
        if not is_all:
            paginator = StandardResultsSetPagination()
            page_qs = paginator.paginate_queryset(queryset, request)
            employees_list = list(page_qs) if page_qs is not None else list(queryset)
        else:
            employees_list = list(queryset)

        # Bulk pre-fetch active structures for organization in 1 query
        structures_by_emp = get_bulk_employee_salary_structures(org, today)

        result = []
        for emp in employees_list:
            structure = structures_by_emp.get(emp.id)
            result.append({
                "employee_id": emp.id,
                "employee_name": f"{emp.first_name} {emp.last_name}".strip() or emp.email,
                "employee_code": getattr(emp, 'employee_code', None) or f"EMP-{emp.id:04d}",
                "designation": getattr(emp, 'designation', None) or '',
                "department": getattr(emp, 'department', None) or '',
                "email": emp.email,
                "has_structure": structure is not None,
                "structure": EmployeeSalaryStructureSerializer(structure).data if structure else None,
            })

        if paginator and not is_all:
            return paginator.get_paginated_response(result)

        return Response(result)


class PayrollPeriodListView(BasePayrollAPIView):
    """
    List all payroll periods for the organization.
    """

    def get(self, request):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view payroll periods.")

        org = self.get_organization(request)
        if not org:
            return Response([])

        periods = PayrollPeriod.objects.filter(organization=org, is_deleted=False).order_by('-year', '-month')
        return Response(PayrollPeriodSerializer(periods, many=True).data)


class PayrollPeriodDetailView(BasePayrollAPIView):
    """
    Get summary status and attendance prerequisite status for a specific month.
    """

    def get(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view payroll.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        att_period = AttendancePeriod.objects.filter(organization=org, year=year, month=month, is_deleted=False).first()
        payroll_period = PayrollPeriod.objects.filter(organization=org, year=year, month=month, is_deleted=False).first()

        is_stale, stale_reasons = detect_stale_payroll_sources(payroll_period)

        payment_summary = {
            "total_paid_amount": "0.00",
            "total_unpaid_amount": "0.00",
            "paid_employee_count": 0,
            "unpaid_employee_count": 0
        }
        if payroll_period:
            p_stats = SalaryPayment.objects.filter(
                payroll_period=payroll_period,
                status='Paid',
                is_deleted=False
            ).aggregate(
                total_paid=Sum('paid_amount'),
                paid_count=Count('id')
            )
            total_paid = p_stats['total_paid'] or Decimal('0.00')
            paid_count = p_stats['paid_count'] or 0
            total_emp = payroll_period.total_employees or 0
            unpaid_count = max(0, total_emp - paid_count)
            net_payable_total = payroll_period.total_net_payable or Decimal('0.00')
            total_unpaid = max(Decimal('0.00'), net_payable_total - total_paid)
            payment_summary = {
                "total_paid_amount": str(total_paid),
                "total_unpaid_amount": str(total_unpaid),
                "paid_employee_count": paid_count,
                "unpaid_employee_count": unpaid_count
            }

        return Response({
            "year": year,
            "month": month,
            "attendance_finalized": att_period.status == 'Finalized' if att_period else False,
            "attendance_revision": att_period.current_revision if att_period else 0,
            "payroll_period": PayrollPeriodSerializer(payroll_period).data if payroll_period else None,
            "is_stale": is_stale,
            "stale_reasons": stale_reasons,
            "payment_summary": payment_summary,
        })


class RecordSalaryPaymentView(BasePayrollAPIView):
    """
    Records salary payment for a single employee snapshot in a finalized payroll period.
    """

    def post(self, request):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to record salary payments.")

        org = self.get_organization(request)

        snapshot_id = request.data.get('snapshot_id')
        paid_at = request.data.get('paid_at')
        payment_method = request.data.get('payment_method', 'BankTransfer')
        transaction_reference = request.data.get('transaction_reference', '')
        notes = request.data.get('notes', '')

        payment = record_salary_payment(
            organization=org,
            snapshot_id=snapshot_id,
            paid_at=paid_at,
            payment_method=payment_method,
            transaction_reference=transaction_reference,
            notes=notes,
            user=request.user
        )
        return Response(SalaryPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class BulkRecordSalaryPaymentView(BasePayrollAPIView):
    """
    Bulk records salary payments for all or selected employees in a finalized payroll period.
    """

    def post(self, request):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to record salary payments.")

        org = self.get_organization(request)

        year = request.data.get('year')
        month = request.data.get('month')
        snapshot_ids = request.data.get('snapshot_ids', [])
        paid_at = request.data.get('paid_at')
        payment_method = request.data.get('payment_method', 'BankTransfer')
        transaction_reference = request.data.get('transaction_reference', '')
        notes = request.data.get('notes', '')

        if not year or not month:
            raise ValidationError({"detail": "Year and month are required."})

        count = bulk_record_salary_payments(
            organization=org,
            year=int(year),
            month=int(month),
            snapshot_ids=snapshot_ids,
            paid_at=paid_at,
            payment_method=payment_method,
            transaction_reference=transaction_reference,
            notes=notes,
            user=request.user
        )
        return Response({"message": f"Successfully marked {count} employee(s) as Paid.", "paid_count": count}, status=status.HTTP_201_CREATED)


class VoidSalaryPaymentView(BasePayrollAPIView):
    """
    Voids an existing active Paid salary payment with a mandatory reason.
    """

    def post(self, request, payment_id):
        if not has_fine_grained_permission(request.user, ['payroll:manage']):
            raise PermissionDenied("You do not have permission to void salary payments.")

        org = self.get_organization(request)

        void_reason = request.data.get('void_reason', '')
        payment = void_salary_payment(
            organization=org,
            payment_id=payment_id,
            void_reason=void_reason,
            user=request.user
        )
        return Response(SalaryPaymentSerializer(payment).data, status=status.HTTP_200_OK)


class PayrollPeriodCalculateView(BasePayrollAPIView):
    """
    Calculates / recalculates monthly payroll for an organization.
    Supports asynchronous background execution via ?async=true or payload {"async": true}.
    """

    def post(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to calculate payroll.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        is_async = (
            request.query_params.get('async', '').lower() in ['true', '1'] or
            request.data.get('async') is True or
            str(request.data.get('async', '')).lower() in ['true', '1']
        )

        if is_async:
            from payroll.tasks import calculate_payroll_period_task, dispatch_task_safely
            task = dispatch_task_safely(
                calculate_payroll_period_task,
                organization_id=org.id,
                year=year,
                month=month,
                user_id=request.user.id
            )
            return Response(
                {
                    "task_id": str(task.id),
                    "status": getattr(task, 'status', 'PENDING'),
                    "message": f"Payroll calculation queued for {year}-{month:02d}."
                },
                status=status.HTTP_202_ACCEPTED
            )

        period = calculate_payroll_period(organization=org, year=year, month=month, user=request.user)
        return Response(PayrollPeriodSerializer(period).data, status=status.HTTP_200_OK)


class PayrollPeriodFinalizeView(BasePayrollAPIView):
    """
    Finalizes and locks a calculated payroll period.
    """

    def post(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:manage']):
            raise PermissionDenied("You do not have permission to finalize payroll.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        period = finalize_payroll_period(organization=org, year=year, month=month, user=request.user)
        return Response(PayrollPeriodSerializer(period).data, status=status.HTTP_200_OK)


class PayrollPeriodReopenView(BasePayrollAPIView):
    """
    Reopens a finalized payroll period with a mandatory reason.
    """

    def post(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:manage']):
            raise PermissionDenied("You do not have permission to reopen payroll.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        reason = request.data.get('reason', '')
        period = reopen_payroll_period(organization=org, year=year, month=month, user=request.user, reason=reason)
        return Response(PayrollPeriodSerializer(period).data, status=status.HTTP_200_OK)


class PayrollEmployeeSnapshotsView(BasePayrollAPIView):
    """
    Returns calculated employee payroll snapshots for a month.
    Supports server-side pagination (?page=, ?page_size=) and search (?search=).
    """

    def get(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view employee payroll records.")

        org = self.get_organization(request)
        if not org:
            return Response([])

        year, month = int(year), int(month)
        period = PayrollPeriod.objects.filter(organization=org, year=year, month=month, is_deleted=False).first()
        if not period:
            return Response([])

        snapshots = PayrollEmployeeSnapshot.objects.filter(
            payroll_period=period,
            revision=period.current_revision,
            is_current=True
        ).order_by('employee_name')

        search_q = request.query_params.get('search', '').strip()
        if search_q:
            snapshots = snapshots.filter(
                Q(employee_name__icontains=search_q) |
                Q(designation__icontains=search_q) |
                Q(employee__employee_code__icontains=search_q)
            )

        is_all = (request.query_params.get('all') == 'true')
        if not is_all:
            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(snapshots, request)
            if page is not None:
                serializer = PayrollEmployeeSnapshotSerializer(page, many=True)
                return paginator.get_paginated_response(serializer.data)

        return Response(PayrollEmployeeSnapshotSerializer(snapshots, many=True).data)


class PayrollEmployeeDetailSnapshotView(BasePayrollAPIView):
    """
    Returns single employee snapshot breakdown and their monthly adjustments.
    """

    def get(self, request, year, month, employee_id):
        is_self = (request.user.id == int(employee_id))
        if not is_self and not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view this payroll record.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        period = PayrollPeriod.objects.filter(organization=org, year=year, month=month, is_deleted=False).first()
        if not period:
            raise NotFound("Payroll period not found.")

        snap = PayrollEmployeeSnapshot.objects.filter(
            payroll_period=period,
            employee_id=employee_id,
            revision=period.current_revision,
            is_current=True
        ).first()

        if not snap:
            raise NotFound("Employee payroll record not found for this period.")

        adjustments = PayrollAdjustment.objects.filter(
            payroll_period=period,
            employee_id=employee_id,
            is_deleted=False
        )

        return Response({
            "snapshot": PayrollEmployeeSnapshotSerializer(snap).data,
            "adjustments": PayrollAdjustmentSerializer(adjustments, many=True).data
        })


class PayrollAdjustmentCreateView(BasePayrollAPIView):
    """
    Adds a manual earning or deduction adjustment to a draft/calculated payroll period.
    """

    def post(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to add adjustments.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        period = PayrollPeriod.objects.filter(organization=org, year=year, month=month, is_deleted=False).first()
        if not period or period.status == 'Finalized':
            raise ValidationError({"detail": "Adjustments can only be added to non-finalized payroll periods."})

        emp_id = request.data.get('employee') or request.data.get('employee_id')
        from django.db.models import Q
        employee = Employee.objects.filter(
            Q(id=emp_id) & (
                Q(organization=org) |
                Q(memberships__organization=org, memberships__is_active_in_org=True, memberships__is_deleted=False)
            )
        ).distinct().first()
        if not employee:
            raise ValidationError({"employee": "Employee not found in your organization."})

        serializer = PayrollAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        adj = PayrollAdjustment.objects.create(
            organization=org,
            payroll_period=period,
            employee=employee,
            adjustment_type=serializer.validated_data['adjustment_type'],
            category=serializer.validated_data.get('category', 'Bonus'),
            amount=serializer.validated_data['amount'],
            description=serializer.validated_data['description'],
            created_by=request.user,
        )

        # AuditLog
        user_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email
        from core.models import AuditLog
        AuditLog.objects.create(
            organization=org,
            employee=request.user,
            employeeName=user_name,
            action="Payroll Adjustment Added",
            details=f"Added {adj.adjustment_type} ({adj.category}) of {adj.amount} for {employee}."
        )

        return Response(PayrollAdjustmentSerializer(adj).data, status=status.HTTP_201_CREATED)


class PayrollAdjustmentDeleteView(BasePayrollAPIView):
    """
    Deletes a manual adjustment from a non-finalized payroll period.
    """

    def delete(self, request, adjustment_id):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to delete adjustments.")

        org = self.get_organization(request)

        adj = PayrollAdjustment.objects.filter(id=adjustment_id, organization=org, is_deleted=False).first()
        if not adj:
            raise NotFound("Adjustment not found.")

        if adj.payroll_period.status == 'Finalized':
            raise ValidationError({"detail": "Cannot delete adjustments from a Finalized payroll period."})

        adj.is_deleted = True
        adj.save()

        # AuditLog
        user_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email
        from core.models import AuditLog
        AuditLog.objects.create(
            organization=org,
            employee=request.user,
            employeeName=user_name,
            action="Payroll Adjustment Removed",
            details=f"Removed {adj.adjustment_type} adjustment from payroll period {adj.payroll_period.year}-{adj.payroll_period.month:02d}."
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, adjustment_id):
        if not has_fine_grained_permission(request.user, ['payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to update adjustments.")

        org = self.get_organization(request)

        adj = PayrollAdjustment.objects.filter(id=adjustment_id, organization=org, is_deleted=False).first()
        if not adj:
            raise NotFound("Adjustment not found.")

        if adj.payroll_period.status == 'Finalized':
            raise ValidationError({"detail": "Cannot update adjustments for a Finalized payroll period."})

        serializer = PayrollAdjustmentSerializer(adj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # AuditLog
        user_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email
        from core.models import AuditLog
        AuditLog.objects.create(
            organization=org,
            employee=request.user,
            employeeName=user_name,
            action="Payroll Adjustment Updated",
            details=f"Updated {adj.adjustment_type} ({adj.category}) adjustment to {adj.amount} for employee {adj.employee_id}."
        )

        return Response(serializer.data, status=status.HTTP_200_OK)


class PayslipDetailView(BasePayrollAPIView):
    """
    Retrieves the complete frozen detail of an issued or superseded Payslip.
    Enforces tenant scoping and payroll:view permissions.
    """

    def get(self, request, payslip_id):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view payslips.")

        org = self.get_organization(request)

        payslip = Payslip.objects.filter(
            id=payslip_id,
            organization=org,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee', 'issued_by').first()

        if not payslip:
            raise NotFound("Payslip not found.")

        serializer = PayslipDetailSerializer(payslip)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PayslipPDFView(BasePayrollAPIView):
    """
    Renders and streams the immutable A4 PDF document for an issued/superseded Payslip.
    Enforces tenant scoping and payroll:view permissions.
    """

    def get(self, request, payslip_id):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to download payslips.")

        org = self.get_organization(request)

        payslip = Payslip.objects.filter(
            id=payslip_id,
            organization=org,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee').first()

        if not payslip:
            payslip = Payslip.objects.filter(
                payroll_snapshot_id=payslip_id,
                organization=org,
                is_deleted=False
            ).select_related('payroll_period', 'payroll_snapshot', 'employee').first()

        if not payslip:
            raise NotFound("Payslip not found.")

        pdf_buffer = generate_payslip_pdf(payslip)
        filename = f"Payslip_{payslip.payslip_number}.pdf"
        disposition = request.query_params.get('disposition', 'attachment')
        if disposition not in ['inline', 'attachment']:
            disposition = 'attachment'

        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PeriodPayslipListView(BasePayrollAPIView):
    """
    Lists all issued or superseded payslips for a given finalized payroll period.
    Enforces tenant scoping and payroll:view permissions.
    """

    def get(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to view payroll payslips.")

        org = self.get_organization(request)

        payslips = Payslip.objects.filter(
            organization=org,
            payroll_period__year=year,
            payroll_period__month=month,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee', 'issued_by').order_by('employee__first_name', 'employee__last_name')

        serializer = PayslipSerializer(payslips, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PeriodPayslipBulkZipExportView(BasePayrollAPIView):
    """
    Exports all currently Issued payslips for a finalized payroll period as a single ZIP file.
    Only accessible by users with payroll:view or payroll:manage permissions.
    Excludes Superseded payslips. Does not query raw attendance or recalculate payroll.
    """

    def get(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to export payroll payslips.")

        org = self.get_organization(request)

        year, month = int(year), int(month)
        period = PayrollPeriod.objects.filter(
            organization=org,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period:
            raise NotFound("Payroll period not found.")

        if period.status != 'Finalized':
            return Response(
                {"detail": "Bulk ZIP export is only available for Finalized payroll periods."},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_async = (
            request.query_params.get('async', '').lower() in ['true', '1'] or
            request.query_params.get('background', '').lower() in ['true', '1']
        )

        if is_async:
            from payroll.tasks import generate_bulk_payslip_zip_task, dispatch_task_safely
            task = dispatch_task_safely(
                generate_bulk_payslip_zip_task,
                organization_id=org.id,
                year=year,
                month=month,
                user_id=request.user.id
            )
            return Response(
                {
                    "task_id": str(task.id),
                    "status": getattr(task, 'status', 'PENDING'),
                    "message": f"Bulk payslip ZIP generation queued for {year}-{month:02d}."
                },
                status=status.HTTP_202_ACCEPTED
            )

        issued_payslips = Payslip.objects.filter(
            organization=org,
            payroll_period=period,
            status='Issued',
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee').order_by('employee__id')

        if not issued_payslips.exists():
            return Response(
                {"detail": "No issued payslips found for this period."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if issued_payslips.count() > 200:
            return Response(
                {"detail": "Bulk payslip export is limited to 200 payslips per request. Please narrow the selection or export in batches."},
                status=status.HTTP_400_BAD_REQUEST
            )

        zip_buffer = io.BytesIO()
        used_filenames = set()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for ps in issued_payslips:
                emp_code = None
                if ps.employee_details_snapshot and isinstance(ps.employee_details_snapshot, dict):
                    emp_code = ps.employee_details_snapshot.get('employee_code')
                if not emp_code and ps.employee:
                    emp_code = ps.employee.employee_code
                if not emp_code:
                    emp_code = f"EMP-{ps.employee_id:04d}" if ps.employee_id else f"EMP-{ps.id:04d}"

                # Sanitize employee code to prevent directory traversal or invalid zip paths
                safe_code = re.sub(r'[^a-zA-Z0-9_-]', '_', str(emp_code).strip())
                if not safe_code:
                    safe_code = f"EMP-{ps.employee_id:04d}" if ps.employee_id else f"EMP-{ps.id:04d}"

                base_filename = f"Payslip_{safe_code}_{year}_{month:02d}"
                pdf_filename = f"{base_filename}.pdf"

                # Collision handling
                counter = 1
                while pdf_filename in used_filenames:
                    pdf_filename = f"{base_filename}_{counter}.pdf"
                    counter += 1
                used_filenames.add(pdf_filename)

                pdf_buffer = generate_payslip_pdf(ps)
                zip_file.writestr(pdf_filename, pdf_buffer.getvalue())

        zip_buffer.seek(0)
        zip_filename = f"Payslips_{year}_{month:02d}.zip"

        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class MyPayslipListView(BasePayrollAPIView):
    """
    Employee Self-Service: Lists active Issued payslips for the authenticated employee.
    Does NOT require payroll:view admin permissions.
    Superseded payslips are excluded from this standard employee view.
    """

    def get(self, request):
        user = request.user
        org = self.get_organization(request)

        payslips = Payslip.objects.filter(
            organization=org,
            employee=user,
            status='Issued',
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot').order_by('-payroll_period__year', '-payroll_period__month')

        serializer = MyPayslipListSerializer(payslips, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MyPayslipDetailView(BasePayrollAPIView):
    """
    Employee Self-Service: Retrieves full itemized breakdown for the employee's own payslip.
    Enforces strict ownership: payslip.employee == request.user.
    """

    def get(self, request, payslip_id):
        user = request.user
        org = self.get_organization(request)

        payslip = Payslip.objects.filter(
            id=payslip_id,
            organization=org,
            employee=user,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee', 'issued_by').first()

        if not payslip:
            raise NotFound("Payslip not found.")

        serializer = PayslipDetailSerializer(payslip)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MyPayslipPDFView(BasePayrollAPIView):
    """
    Employee Self-Service: Downloads the official immutable A4 PDF payslip.
    Enforces strict ownership: payslip.employee == request.user.
    """

    def get(self, request, payslip_id):
        user = request.user
        org = self.get_organization(request)

        payslip = Payslip.objects.filter(
            id=payslip_id,
            organization=org,
            employee=user,
            is_deleted=False
        ).select_related('payroll_period', 'payroll_snapshot', 'employee').first()

        if not payslip:
            raise NotFound("Payslip not found.")

        pdf_buffer = generate_payslip_pdf(payslip)
        filename = f"Payslip_{payslip.payslip_number}.pdf"
        disposition = request.query_params.get('disposition', 'attachment')
        if disposition not in ['inline', 'attachment']:
            disposition = 'attachment'

        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PayrollTaskStatusView(BasePayrollAPIView):
    """
    Retrieves the status and result of an asynchronous payroll Celery task.
    """

    def get(self, request, task_id):
        if not has_fine_grained_permission(request.user, ['payroll:view', 'payroll:process', 'payroll:manage']):
            raise PermissionDenied("You do not have permission to inspect payroll tasks.")

        from celery.result import AsyncResult
        res = AsyncResult(task_id)

        response_data = {
            "task_id": task_id,
            "status": res.status,
            "ready": res.ready(),
            "successful": res.successful() if res.ready() else False,
            "result": res.result if res.ready() and not isinstance(res.result, Exception) else None,
        }
        if res.failed():
            response_data["error"] = str(res.result)

        return Response(response_data, status=status.HTTP_200_OK)


class PayrollBankExportView(BasePayrollAPIView):
    """
    Generates bank-compatible payment disbursement files (Excel / CSV)
    from a finalized payroll period.
    Supports HDFC, ICICI, SBI, and GENERIC_NEFT formats.
    GET: Pre-check summary (payable count, total amount, unpayable employees count).
    POST: Generate and download file (or JSON precheck if requested).
    """

    def get(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:manage', 'payroll:process']):
            raise PermissionDenied("You do not have permission to export bank payment files.")

        org = self.get_organization(request)
        year, month = int(year), int(month)

        period = PayrollPeriod.objects.filter(
            organization=org,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period:
            return Response(
                {"error": f"Payroll period {year}-{month:02d} not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if period.status != 'Finalized':
            return Response(
                {"error": "Cannot generate bank payment file for non-finalized payroll period."},
                status=status.HTTP_400_BAD_REQUEST
            )

        template = request.query_params.get('template', 'GENERIC_NEFT')
        debit_account = request.query_params.get('debit_account')
        remarks = request.query_params.get('remarks')

        exporter = BankPaymentExporter(
            payroll_period=period,
            template=template,
            debit_account=debit_account,
            remarks=remarks
        )

        return Response(exporter.get_summary(), status=status.HTTP_200_OK)

    def post(self, request, year, month):
        if not has_fine_grained_permission(request.user, ['payroll:manage', 'payroll:process']):
            raise PermissionDenied("You do not have permission to export bank payment files.")

        org = self.get_organization(request)
        year, month = int(year), int(month)

        period = PayrollPeriod.objects.filter(
            organization=org,
            year=year,
            month=month,
            is_deleted=False
        ).first()

        if not period:
            return Response(
                {"error": f"Payroll period {year}-{month:02d} not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if period.status != 'Finalized':
            return Response(
                {"error": "Cannot generate bank payment file for non-finalized payroll period."},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = request.data if isinstance(request.data, dict) else {}
        template = data.get('template') or request.query_params.get('template', 'GENERIC_NEFT')
        debit_account = data.get('debit_account') or request.query_params.get('debit_account')
        remarks = data.get('remarks') or request.query_params.get('remarks')
        file_format = data.get('format') or request.query_params.get('format', 'XLSX')
        is_precheck = data.get('precheck') or (request.query_params.get('precheck') == 'true')

        exporter = BankPaymentExporter(
            payroll_period=period,
            template=template,
            debit_account=debit_account,
            remarks=remarks
        )

        if is_precheck:
            return Response(exporter.get_summary(), status=status.HTTP_200_OK)

        file_buffer, filename, content_type = exporter.generate_file(file_format=file_format)

        response = HttpResponse(file_buffer.getvalue(), content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        return response




