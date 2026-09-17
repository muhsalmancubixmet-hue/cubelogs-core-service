# --------------------------------------------------------------------------------
#       Payroll Celery Tasks - Asynchronous Batch Operations & PDF Rendering
# --------------------------------------------------------------------------------

import os
import io
import re
import zipfile
import logging
from celery import shared_task
from django.db import transaction
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from core.models import Organization
from users.models import Employee
from payroll.models import PayrollPeriod, Payslip
from payroll.services import calculate_payroll_period
from payroll.pdf import generate_payslip_pdf

logger = logging.getLogger(__name__)


def dispatch_task_safely(task_func, *args, **kwargs):
    """
    Attempts to dispatch a task via Celery delay().
    If the Celery broker is unavailable or fails, gracefully falls back
    to eager in-process execution so that local development and environments
    without a running broker continue without failure.
    """
    try:
        return task_func.delay(*args, **kwargs)
    except Exception as exc:
        logger.warning(
            f"Celery broker unavailable for {task_func.__name__} ({exc}). "
            f"Executing eagerly as fallback."
        )
        return task_func.apply(args=args, kwargs=kwargs)


@shared_task(bind=True, name='payroll.calculate_payroll_period_task')
def calculate_payroll_period_task(self, organization_id, year, month, user_id=None):
    """
    Asynchronous Celery task for multi-employee monthly payroll calculation.
    Wraps calculations in transaction.atomic() to ensure database integrity.
    """
    logger.info(f"[Task {self.request.id}] Starting payroll calculation: Org={organization_id}, Year={year}, Month={month}")
    try:
        org = Organization.objects.get(id=organization_id)
    except Organization.DoesNotExist:
        logger.error(f"[Task {self.request.id}] Organization {organization_id} not found.")
        return {"status": "FAILURE", "error": f"Organization {organization_id} not found."}

    user = None
    if user_id:
        user = Employee.objects.filter(id=user_id).first()

    with transaction.atomic():
        period = calculate_payroll_period(organization=org, year=year, month=month, user=user)

    logger.info(
        f"[Task {self.request.id}] Payroll calculation completed: Period={period.id}, "
        f"Employees={period.total_employees}, Rev={period.current_revision}"
    )

    return {
        "status": "SUCCESS",
        "task_id": self.request.id,
        "period_id": period.id,
        "year": period.year,
        "month": period.month,
        "total_employees": period.total_employees,
        "current_revision": period.current_revision,
        "period_status": period.status,
    }


@shared_task(bind=True, name='payroll.generate_bulk_payslip_zip_task')
def generate_bulk_payslip_zip_task(self, organization_id, year, month, user_id=None):
    """
    Asynchronously renders PDFs for all issued payslips and bundles them into a ZIP archive.
    """
    logger.info(f"[Task {self.request.id}] Starting bulk payslip ZIP generation: Org={organization_id}, Year={year}, Month={month}")
    try:
        org = Organization.objects.get(id=organization_id)
        period = PayrollPeriod.objects.get(organization=org, year=year, month=month, is_deleted=False)
    except Exception as e:
        logger.error(f"[Task {self.request.id}] Error finding period: {e}")
        return {"status": "FAILURE", "error": str(e)}

    if period.status != 'Finalized':
        return {"status": "FAILURE", "error": "Bulk export only available for Finalized payroll periods."}

    issued_payslips = Payslip.objects.filter(
        organization=org,
        payroll_period=period,
        status='Issued',
        is_deleted=False
    ).select_related('payroll_period', 'payroll_snapshot', 'employee').order_by('employee__id')

    count = issued_payslips.count()
    if count == 0:
        return {"status": "FAILURE", "error": "No issued payslips found for this period."}

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

            safe_code = re.sub(r'[^a-zA-Z0-9_-]', '_', str(emp_code).strip())
            if not safe_code:
                safe_code = f"EMP-{ps.employee_id:04d}" if ps.employee_id else f"EMP-{ps.id:04d}"

            base_filename = f"Payslip_{safe_code}_{year}_{month:02d}"
            pdf_filename = f"{base_filename}.pdf"

            counter = 1
            while pdf_filename in used_filenames:
                pdf_filename = f"{base_filename}_{counter}.pdf"
                counter += 1
            used_filenames.add(pdf_filename)

            pdf_buffer = generate_payslip_pdf(ps)
            zip_file.writestr(pdf_filename, pdf_buffer.getvalue())

    zip_buffer.seek(0)
    task_short_id = (self.request.id or 'manual')[:8]
    zip_filename = f"Payslips_{org.id}_{year}_{month:02d}_{task_short_id}.zip"
    save_path = os.path.join('exports', 'payslips', zip_filename)

    saved_file_path = default_storage.save(save_path, ContentFile(zip_buffer.getvalue()))
    file_url = default_storage.url(saved_file_path)

    logger.info(f"[Task {self.request.id}] Bulk ZIP generation completed: {count} payslips, saved to {saved_file_path}")

    return {
        "status": "SUCCESS",
        "task_id": self.request.id,
        "zip_filename": zip_filename,
        "payslip_count": count,
        "download_url": file_url,
    }
