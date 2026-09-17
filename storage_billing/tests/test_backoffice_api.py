import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageEvent, StorageDailyUsage
from subscribers.models import GlobalBillingSettings, MonthlyInvoice, Wallet, WalletTransaction
from users.models import Employee, Role
from users.roles import sync_default_roles


class BackofficeStorageAPITestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        # Global billing settings baseline
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_credit_size_bytes = 1_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal('20.00')
        self.g_settings.storage_billing_enabled = False
        self.g_settings.currency = 'INR'
        self.g_settings.save()

        # Organizations
        self.org1 = Organization.objects.create(
            name="Org One",
            subdomain="org-one",
            settings=OrgSettings.objects.create()
        )
        self.org2 = Organization.objects.create(
            name="Org Zero",
            subdomain="org-zero",
            settings=OrgSettings.objects.create()
        )

        # Superadmin user (platform admin)
        self.superadmin = Employee.objects.create_user(
            email="platform_superadmin@cubelogs.com",
            password="testpassword123",
            isSuperAdmin=True,
            is_superuser=True,
            is_staff=True,
            is_active=True
        )

        # Non-superadmin user (regular tenant employee)
        self.regular_user = Employee.objects.create_user(
            email="regular_user@org-one.com",
            password="testpassword123",
            isSuperAdmin=False,
            is_superuser=False,
            is_staff=False,
            is_active=True,
            organization=self.org1
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.superadmin)

        self.oversight_url = '/api/backoffice/storage/organizations/'
        self.billing_settings_url = '/api/backoffice/billing-settings/'

    # --------------------------------------------------------------------------
    # 1. Platform superadmin can access storage oversight
    # --------------------------------------------------------------------------
    def test_01_platform_superadmin_can_access_storage_oversight(self):
        resp = self.client.get(self.oversight_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('results', resp.data)

    # --------------------------------------------------------------------------
    # 2. Unauthorized/non-platform user denied according to canonical IsSuperAdminUser semantics
    # --------------------------------------------------------------------------
    def test_02_unauthorized_user_denied(self):
        unauth_client = APIClient()
        # Unauthenticated request returns 401 Unauthorized
        resp_anon = unauth_client.get(self.oversight_url)
        self.assertEqual(resp_anon.status_code, status.HTTP_401_UNAUTHORIZED)

        # Authenticated as regular non-superadmin employee returns 403 Forbidden
        unauth_client.force_authenticate(user=self.regular_user)
        resp_regular = unauth_client.get(self.oversight_url)
        self.assertEqual(resp_regular.status_code, status.HTTP_403_FORBIDDEN)

    # --------------------------------------------------------------------------
    # 3. storage_credit_size_bytes = 0 rejected
    # --------------------------------------------------------------------------
    def test_03_credit_size_zero_rejected(self):
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': 0
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('storage_credit_size_bytes', resp.data)

    # --------------------------------------------------------------------------
    # 4. negative credit size rejected
    # --------------------------------------------------------------------------
    def test_04_negative_credit_size_rejected(self):
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': -500
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('storage_credit_size_bytes', resp.data)

    # --------------------------------------------------------------------------
    # 5. positive credit size accepted
    # --------------------------------------------------------------------------
    def test_05_positive_credit_size_accepted(self):
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': 2_000_000_000
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.g_settings.refresh_from_db()
        self.assertEqual(self.g_settings.storage_credit_size_bytes, 2_000_000_000)

    # --------------------------------------------------------------------------
    # 6. negative monthly price rejected
    # --------------------------------------------------------------------------
    def test_06_negative_monthly_price_rejected(self):
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_monthly_price': -10.00
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('storage_credit_monthly_price', resp.data)

    # --------------------------------------------------------------------------
    # 7. monthly price = 0 accepted
    # --------------------------------------------------------------------------
    def test_07_monthly_price_zero_accepted(self):
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_monthly_price': 0.00
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.g_settings.refresh_from_db()
        self.assertEqual(self.g_settings.storage_credit_monthly_price, Decimal('0.00'))

    # --------------------------------------------------------------------------
    # 8. active bytes correct
    # --------------------------------------------------------------------------
    def test_08_active_bytes_correct(self):
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="file1.pdf",
            file_path="projects/file1.pdf",
            source_object_id="so_1",
            size_bytes=15_000_000,
            status='ACTIVE'
        )
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="file2.pdf",
            file_path="projects/file2.pdf",
            source_object_id="so_2",
            size_bytes=25_000_000,
            status='ACTIVE'
        )
        resp = self.client.get(self.oversight_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertEqual(item['active_bytes'], 40_000_000)
        self.assertEqual(item['active_gb'], "0.0400")

    # --------------------------------------------------------------------------
    # 9. deleted files excluded from active bytes
    # --------------------------------------------------------------------------
    def test_09_deleted_files_excluded_from_active_bytes(self):
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="active.pdf",
            file_path="projects/active.pdf",
            source_object_id="so_act",
            size_bytes=10_000_000,
            status='ACTIVE'
        )
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="deleted.pdf",
            file_path="projects/deleted.pdf",
            source_object_id="so_del",
            size_bytes=50_000_000,
            status='DELETED',
            deleted_at=timezone.now()
        )
        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertEqual(item['active_bytes'], 10_000_000)
        self.assertEqual(item['active_files'], 1)
        self.assertEqual(item['deleted_files'], 1)

    # --------------------------------------------------------------------------
    # 10. active_files correct
    # --------------------------------------------------------------------------
    def test_10_active_files_correct(self):
        for i in range(3):
            StorageFile.objects.create(
                organization=self.org1,
                original_filename=f"active_{i}.pdf",
                file_path=f"projects/active_{i}.pdf",
                source_object_id=f"so_{i}",
                size_bytes=1_000_000,
                status='ACTIVE'
            )
        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertEqual(item['active_files'], 3)

    # --------------------------------------------------------------------------
    # 11. deleted_files correct
    # --------------------------------------------------------------------------
    def test_11_deleted_files_correct(self):
        for i in range(4):
            StorageFile.objects.create(
                organization=self.org1,
                original_filename=f"del_{i}.pdf",
                file_path=f"projects/del_{i}.pdf",
                source_object_id=f"so_del_{i}",
                size_bytes=2_000_000,
                status='DELETED',
                deleted_at=timezone.now()
            )
        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertEqual(item['deleted_files'], 4)

    # --------------------------------------------------------------------------
    # 12. organization-wide CEIL correct
    # --------------------------------------------------------------------------
    def test_12_organization_wide_ceil_correct(self):
        # 1 GB credit size
        # File 1: 600 MB, File 2: 500 MB -> Total: 1,100 MB -> 2 Credits
        # (Must not round per file, which would also be 1+1=2, but test exact ceil)
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="a.bin",
            file_path="projects/a.bin",
            source_object_id="so_a",
            size_bytes=600_000_000,
            status='ACTIVE'
        )
        StorageFile.objects.create(
            organization=self.org1,
            original_filename="b.bin",
            file_path="projects/b.bin",
            source_object_id="so_b",
            size_bytes=500_000_000,
            status='ACTIVE'
        )
        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertEqual(item['active_bytes'], 1_100_000_000)
        self.assertEqual(item['current_credits'], 2)

    # --------------------------------------------------------------------------
    # 13. zero-storage organization included
    # --------------------------------------------------------------------------
    def test_13_zero_storage_organization_included(self):
        resp = self.client.get(self.oversight_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item_zero = next((o for o in resp.data['results'] if o['organization_id'] == self.org2.id), None)
        self.assertIsNotNone(item_zero)
        self.assertEqual(item_zero['active_bytes'], 0)
        self.assertEqual(item_zero['active_gb'], "0.0000")
        self.assertEqual(item_zero['current_credits'], 0)
        self.assertEqual(item_zero['active_files'], 0)
        self.assertEqual(item_zero['deleted_files'], 0)
        self.assertIsNone(item_zero['last_storage_activity_at'])

    # --------------------------------------------------------------------------
    # 14. last activity comes from latest StorageEvent
    # --------------------------------------------------------------------------
    def test_14_last_activity_comes_from_latest_storage_event(self):
        sf = StorageFile.objects.create(
            organization=self.org1,
            original_filename="audit.pdf",
            file_path="projects/audit.pdf",
            source_object_id="so_audit",
            size_bytes=100_000,
            status='ACTIVE'
        )
        t_early = timezone.now() - timedelta(hours=3)
        t_mid = timezone.now() - timedelta(hours=1)
        t_latest = timezone.now()

        StorageEvent.objects.create(
            storage_file=sf,
            organization=self.org1,
            event_type='UPLOAD',
            size_bytes=sf.size_bytes,
            occurred_at=t_early
        )
        StorageEvent.objects.create(
            storage_file=sf,
            organization=self.org1,
            event_type='UPLOAD',
            size_bytes=sf.size_bytes,
            occurred_at=t_mid
        )
        StorageEvent.objects.create(
            storage_file=sf,
            organization=self.org1,
            event_type='DELETE',
            size_bytes=sf.size_bytes,
            occurred_at=t_latest
        )

        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        self.assertIsNotNone(item['last_storage_activity_at'])
        self.assertEqual(item['last_storage_activity_at'], t_latest.isoformat())

    # --------------------------------------------------------------------------
    # 15. multiple files + multiple events do NOT cause Cartesian multiplication
    # --------------------------------------------------------------------------
    def test_15_no_cartesian_multiplication(self):
        # 3 files with 10MB, 20MB, 30MB => Exactly 60,000,000 bytes
        files = []
        for i, sz in enumerate([10_000_000, 20_000_000, 30_000_000]):
            f = StorageFile.objects.create(
                organization=self.org1,
                original_filename=f"cart_{i}.pdf",
                file_path=f"projects/cart_{i}.pdf",
                source_object_id=f"so_cart_{i}",
                size_bytes=sz,
                status='ACTIVE'
            )
            files.append(f)

        # 10 events for org1
        for idx in range(10):
            target_f = files[idx % len(files)]
            StorageEvent.objects.create(
                storage_file=target_f,
                organization=self.org1,
                event_type='UPLOAD',
                size_bytes=target_f.size_bytes,
                occurred_at=timezone.now() - timedelta(minutes=idx)
            )

        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)
        # Verify active_bytes is exactly 60,000,000 (NOT multiplied by 10 events to 600,000,000)
        self.assertEqual(item['active_bytes'], 60_000_000)
        self.assertEqual(item['active_files'], 3)
        self.assertEqual(item['current_credits'], 1)

    # --------------------------------------------------------------------------
    # 16. response does not expose private file fields
    # --------------------------------------------------------------------------
    def test_16_response_does_not_expose_private_file_fields(self):
        sf = StorageFile.objects.create(
            organization=self.org1,
            original_filename="confidential_contract.pdf",
            file_path="private/s3/cubelogs/confidential_contract.pdf",
            source_object_id="source_record_uuid_999",
            storage_backend="s3_custom_vault",
            size_bytes=5_000_000,
            status='ACTIVE'
        )
        StorageEvent.objects.create(
            storage_file=sf,
            organization=self.org1,
            event_type='UPLOAD',
            size_bytes=sf.size_bytes,
            metadata={'ip_address': '192.168.1.100', 'key': 'secret_vault_key'}
        )

        resp = self.client.get(self.oversight_url)
        item = next(o for o in resp.data['results'] if o['organization_id'] == self.org1.id)

        allowed_keys = {
            'organization_id', 'organization_name', 'subdomain',
            'active_bytes', 'active_gb', 'current_credits',
            'active_files', 'deleted_files', 'last_storage_activity_at'
        }
        self.assertEqual(set(item.keys()), allowed_keys)

        # Check raw JSON serialization contains no leaks
        content_str = str(resp.content)
        self.assertNotIn("confidential_contract", content_str)
        self.assertNotIn("private/s3", content_str)
        self.assertNotIn("source_record_uuid", content_str)
        self.assertNotIn("secret_vault_key", content_str)
        self.assertNotIn(str(sf.id), content_str)

    # --------------------------------------------------------------------------
    # 17. storage_billing_enabled remains False after updating size/price
    # --------------------------------------------------------------------------
    def test_17_storage_billing_enabled_remains_false_after_settings_update(self):
        self.assertFalse(self.g_settings.storage_billing_enabled)
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': 1_500_000_000,
            'storage_credit_monthly_price': 25.00
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.g_settings.refresh_from_db()
        self.assertEqual(self.g_settings.storage_credit_size_bytes, 1_500_000_000)
        self.assertEqual(self.g_settings.storage_credit_monthly_price, Decimal('25.00'))
        self.assertFalse(self.g_settings.storage_billing_enabled)

    # --------------------------------------------------------------------------
    # 18. changing settings does not create StorageDailyUsage
    # --------------------------------------------------------------------------
    def test_18_changing_settings_does_not_create_storage_daily_usage(self):
        initial_usage_count = StorageDailyUsage.objects.count()
        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': 2_000_000_000,
            'storage_credit_monthly_price': 15.00
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(StorageDailyUsage.objects.count(), initial_usage_count)

    # --------------------------------------------------------------------------
    # 19. no invoice/wallet side effects from settings update
    # --------------------------------------------------------------------------
    def test_19_no_invoice_wallet_side_effects_from_settings_update(self):
        initial_invoice_count = MonthlyInvoice.objects.count()
        initial_wallet_count = Wallet.objects.count()
        initial_tx_count = WalletTransaction.objects.count()

        resp = self.client.post(self.billing_settings_url, {
            'storage_credit_size_bytes': 500_000_000,
            'storage_credit_monthly_price': 10.00
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(MonthlyInvoice.objects.count(), initial_invoice_count)
        self.assertEqual(Wallet.objects.count(), initial_wallet_count)
        self.assertEqual(WalletTransaction.objects.count(), initial_tx_count)
