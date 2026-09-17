import logging
from django.core.management.base import BaseCommand
from storage_billing.services import StorageReconciliationService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Reconcile existing ProjectAttachment files with storage_billing StorageFile "
        "and create initial UPLOAD StorageEvents. Defaults to DRY RUN unless --apply is provided."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help="Perform actual database writes. If omitted, operates in dry-run mode without creating records.",
        )
        parser.add_argument(
            '--org-id',
            type=int,
            default=None,
            help="Optional Organization ID to restrict reconciliation to a single tenant.",
        )

    def handle(self, *args, **options):
        apply_mode = options['apply']
        org_id = options['org_id']
        dry_run = not apply_mode

        mode_str = "APPLY (LIVE WRITE)" if apply_mode else "DRY RUN (NO WRITES)"
        self.stdout.write(self.style.NOTICE(f"=== Starting ProjectAttachment Media Reconciliation [{mode_str}] ==="))
        if org_id:
            self.stdout.write(f"Tenant filter: Organization ID = {org_id}")

        try:
            summary = StorageReconciliationService.reconcile_project_attachments(
                organization=org_id,
                dry_run=dry_run,
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Reconciliation aborted due to fatal error: {exc}"))
            logger.exception("Reconciliation aborted due to fatal error")
            return

        self.stdout.write(self.style.NOTICE("=== Reconciliation Results ==="))
        self.stdout.write(f"Mode: {'DRY RUN' if summary['dry_run'] else 'APPLY'}")
        self.stdout.write(f"Total attachments scanned: {summary['scanned']}")
        if summary['dry_run']:
            self.stdout.write(self.style.SUCCESS(f"Attachments that would be created: {summary['created']}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Attachments created (StorageFile & UPLOAD event): {summary['created']}"))
        self.stdout.write(f"Already tracked: {summary['already_tracked']}")
        self.stdout.write(f"Missing physical file: {summary['missing_file']}")
        self.stdout.write(f"Missing company (tenant): {summary['missing_company']}")
        self.stdout.write(f"Parent/company mismatch: {summary['tenant_mismatch']}")
        self.stdout.write(f"Physical/metadata size mismatch: {summary['size_mismatch']}")
        self.stdout.write(f"Item errors: {summary['errors']}")

        if summary['errors'] > 0:
            self.stdout.write(self.style.WARNING(f"Completed with {summary['errors']} item errors. See server logs for sanitized diagnostics."))
        else:
            self.stdout.write(self.style.SUCCESS("Reconciliation scan completed successfully."))
