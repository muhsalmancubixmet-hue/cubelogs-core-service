# --------------------------------------------------------------------------------
#       Payroll URLs
# --------------------------------------------------------------------------------

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from payroll.views import (
    SalaryComponentViewSet,
    EmployeeSalaryDetailView,
    EmployeeSalaryResolveView,
    EmployeeSalaryListView,
    PayrollPeriodListView,
    PayrollPeriodDetailView,
    PayrollPeriodCalculateView,
    PayrollPeriodFinalizeView,
    PayrollPeriodReopenView,
    PayrollEmployeeSnapshotsView,
    PayrollEmployeeDetailSnapshotView,
    PayrollAdjustmentCreateView,
    PayrollAdjustmentDeleteView,
    PayslipDetailView,
    PayslipPDFView,
    PeriodPayslipListView,
    PeriodPayslipBulkZipExportView,
    MyPayslipListView,
    MyPayslipDetailView,
    MyPayslipPDFView,
    RecordSalaryPaymentView,
    BulkRecordSalaryPaymentView,
    VoidSalaryPaymentView,
    PayrollTaskStatusView,
)

router = DefaultRouter()
router.register(r'payroll/components', SalaryComponentViewSet, basename='salary-components')

urlpatterns = [
    path('', include(router.urls)),
    path('payroll/tasks/<str:task_id>/', PayrollTaskStatusView.as_view(), name='payroll-task-status'),
    path('payroll/employees/salaries/', EmployeeSalaryListView.as_view(), name='employee-salary-list'),
    path('payroll/employees/<int:employee_id>/salary/', EmployeeSalaryDetailView.as_view(), name='employee-salary-detail'),
    path('payroll/employees/<int:employee_id>/salary/resolve/', EmployeeSalaryResolveView.as_view(), name='employee-salary-resolve'),
    path('payroll/periods/', PayrollPeriodListView.as_view(), name='payroll-period-list'),
    path('payroll/periods/<int:year>/<int:month>/', PayrollPeriodDetailView.as_view(), name='payroll-period-detail'),
    path('payroll/periods/<int:year>/<int:month>/calculate/', PayrollPeriodCalculateView.as_view(), name='payroll-period-calculate'),
    path('payroll/periods/<int:year>/<int:month>/finalize/', PayrollPeriodFinalizeView.as_view(), name='payroll-period-finalize'),
    path('payroll/periods/<int:year>/<int:month>/reopen/', PayrollPeriodReopenView.as_view(), name='payroll-period-reopen'),
    path('payroll/periods/<int:year>/<int:month>/employees/', PayrollEmployeeSnapshotsView.as_view(), name='payroll-period-employees'),
    path('payroll/periods/<int:year>/<int:month>/employees/<int:employee_id>/', PayrollEmployeeDetailSnapshotView.as_view(), name='payroll-employee-detail-snapshot'),
    path('payroll/periods/<int:year>/<int:month>/adjustments/', PayrollAdjustmentCreateView.as_view(), name='payroll-adjustment-create'),
    path('payroll/adjustments/<int:adjustment_id>/', PayrollAdjustmentDeleteView.as_view(), name='payroll-adjustment-delete'),
    path('payroll/payments/pay-employee/', RecordSalaryPaymentView.as_view(), name='payroll-pay-employee'),
    path('payroll/payments/bulk-pay/', BulkRecordSalaryPaymentView.as_view(), name='payroll-bulk-pay'),
    path('payroll/payments/<int:payment_id>/void/', VoidSalaryPaymentView.as_view(), name='payroll-void-payment'),
    path('payroll/periods/<int:year>/<int:month>/payslips/export-zip/', PeriodPayslipBulkZipExportView.as_view(), name='payroll-period-payslips-export-zip'),
    path('payroll/periods/<int:year>/<int:month>/payslips/', PeriodPayslipListView.as_view(), name='payroll-period-payslips'),
    path('payroll/payslips/<int:payslip_id>/', PayslipDetailView.as_view(), name='payroll-payslip-detail'),
    path('payroll/payslips/<int:payslip_id>/pdf/', PayslipPDFView.as_view(), name='payroll-payslip-pdf'),
    path('payroll/payslips/<int:payslip_id>/pdf', PayslipPDFView.as_view(), name='payroll-payslip-pdf-noslash'),
    path('payroll/my-payslips/', MyPayslipListView.as_view(), name='payroll-my-payslips'),
    path('payroll/my-payslips/<int:payslip_id>/', MyPayslipDetailView.as_view(), name='payroll-my-payslip-detail'),
    path('payroll/my-payslips/<int:payslip_id>/pdf/', MyPayslipPDFView.as_view(), name='payroll-my-payslip-pdf'),
    path('payroll/my-payslips/<int:payslip_id>/pdf', MyPayslipPDFView.as_view(), name='payroll-my-payslip-pdf-noslash'),
]

