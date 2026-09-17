# --------------------------------------------------------------------------------
#       Subscribers Celery Tasks - Async Billing Cascades & Wallet Adjustments
# --------------------------------------------------------------------------------

import logging
from decimal import Decimal
from celery import shared_task
from django.db import transaction

from core.models import Organization, OrgSettings, AuditLog
from users.models import Employee
from subscribers.models import SubscriptionPackage, SubscriberAccount, Wallet, WalletTransaction

logger = logging.getLogger(__name__)


def dispatch_task_safely(task_func, *args, **kwargs):
    """
    Attempts to dispatch a task via Celery delay().
    If broker connection fails or Celery is unavailable in local dev,
    gracefully executes the task synchronously.
    """
    try:
        return task_func.delay(*args, **kwargs)
    except Exception as exc:
        logger.warning(
            f"Celery broker unavailable for {task_func.__name__} ({exc}). "
            f"Executing eagerly as fallback."
        )
        return task_func.apply(args=args, kwargs=kwargs)


@shared_task(bind=True, name='subscribers.cascade_package_update_task')
def cascade_package_update_task(self, package_id, updated_fields=None):
    """
    Asynchronous Celery task that cascades SubscriptionPackage updates to
    SubscriberAccounts and tenant OrgSettings.
    Prevents blocking HTTP request cycles when multiple accounts are linked.
    """
    logger.info(f"[Task {self.request.id}] Cascading package update for package_id={package_id}")
    try:
        package = SubscriptionPackage.objects.get(id=package_id)
    except SubscriptionPackage.DoesNotExist:
        logger.error(f"[Task {self.request.id}] SubscriptionPackage {package_id} not found.")
        return {"status": "FAILURE", "error": f"Package {package_id} not found"}

    updated_accounts_count = 0
    with transaction.atomic():
        # Update matching subscriber accounts if package isActive changed
        subscribers = SubscriberAccount.objects.filter(packageName=package.name)
        for sub in subscribers:
            if not package.isActive and sub.isActive:
                sub.isActive = False
                sub.save(update_fields=['isActive'])
                updated_accounts_count += 1

            # Synchronize employee limits on organizations linked to this subscriber
            emp = Employee.objects.filter(email=sub.email, organization__isnull=False).select_related('organization', 'organization__settings').first()
            if emp and emp.organization and emp.organization.settings:
                if hasattr(package, 'employeeLimit') and package.employeeLimit:
                    emp.organization.settings.max_employees_allowed = package.employeeLimit
                    emp.organization.settings.save(update_fields=['max_employees_allowed'])

    logger.info(
        f"[Task {self.request.id}] Package cascade completed: Package={package.name}, "
        f"Affected Accounts={updated_accounts_count}"
    )

    return {
        "status": "SUCCESS",
        "task_id": self.request.id,
        "package_id": package.id,
        "package_name": package.name,
        "updated_accounts_count": updated_accounts_count,
    }


@shared_task(bind=True, name='subscribers.process_bulk_wallet_adjustments_task')
def process_bulk_wallet_adjustments_task(self, adjustments, initiated_by_id=None):
    """
    Asynchronously applies bulk wallet credits or debits across multiple accounts.
    adjustments: list of dicts with keys:
      - organization_id (int) or employee_id (int)
      - amount (str/float/Decimal)
      - type: 'Credit' | 'Debit'
      - details: str (optional)
    """
    logger.info(f"[Task {self.request.id}] Processing {len(adjustments)} wallet adjustments")
    initiator = None
    if initiated_by_id:
        initiator = Employee.objects.filter(id=initiated_by_id).first()

    processed = 0
    errors = []

    with transaction.atomic():
        for item in adjustments:
            org_id = item.get('organization_id')
            emp_id = item.get('employee_id')
            amount_val = item.get('amount')
            tx_type = item.get('type', 'Credit')
            details = item.get('details', 'Bulk administrative adjustment')

            try:
                amount_dec = Decimal(str(amount_val))
                if amount_dec <= 0:
                    continue

                wallet = None
                if org_id:
                    wallet = Wallet.objects.filter(organization_id=org_id).first()
                elif emp_id:
                    wallet = Wallet.objects.filter(employee_id=emp_id).first()

                if not wallet:
                    errors.append(f"Wallet not found for org={org_id}, emp={emp_id}")
                    continue

                if tx_type == 'Credit':
                    wallet.balance += amount_dec
                elif tx_type == 'Debit':
                    wallet.balance = max(Decimal('0.00'), wallet.balance - amount_dec)
                wallet.save(update_fields=['balance'])

                WalletTransaction.objects.create(
                    wallet=wallet,
                    amount=amount_dec,
                    transactionType=tx_type,
                    success=True,
                    status='Success',
                    details=details
                )
                processed += 1
            except Exception as ex:
                errors.append(f"Error processing item {item}: {str(ex)}")

    logger.info(f"[Task {self.request.id}] Completed {processed} wallet adjustments. Errors={len(errors)}")

    return {
        "status": "SUCCESS",
        "task_id": self.request.id,
        "processed_count": processed,
        "errors": errors,
    }
