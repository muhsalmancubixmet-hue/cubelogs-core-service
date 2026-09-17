from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from core.models import Organization, OrgSettings, OrganizationModule
from storage_billing.api.v1.views import CompanyStorageSummaryView, CompanyStorageHistoryView
from storage_billing.models import StorageFile, StorageDailyUsage
from subscribers.models import GlobalBillingSettings
from users.models import Employee, Role, OrganizationMembership
from users.roles import sync_default_roles


class StorageBillingAPITestCase(TestCase):
    def setUp(self):
        sync_default_roles()

        # Org A
        self.settings_a = OrgSettings.objects.create()
        self.org_a = Organization.objects.create(name="Org A", subdomain="org-a", settings=self.settings_a)
        OrganizationModule.objects.create(organization=self.org_a, module_id='project_management', enabled=True)

        # Org B
        self.settings_b = OrgSettings.objects.create()
        self.org_b = Organization.objects.create(name="Org B", subdomain="org-b", settings=self.settings_b)
        OrganizationModule.objects.create(organization=self.org_b, module_id='project_management', enabled=True)

        # Global billing settings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_billing_enabled = False
        self.g_settings.storage_credit_size_bytes = 1_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal('20.00')
        self.g_settings.currency = 'INR'
        self.g_settings.save()

        # Roles: company-admin has 'settings:billing'
        self.role_admin = Role.objects.get(slug='company-admin', organization__isnull=True)
        # Regular employee lacks 'settings:billing'
        self.role_emp = Role.objects.get(slug='employee', organization__isnull=True)

        # Users
        self.admin_a = Employee.objects.create_user(
            email="admin@org-a.com",
            password="password123",
            organization=self.org_a,
            role=self.role_admin,
            is_active=True
        )
        self.mem_admin_a = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_a,
            role=self.role_admin,
            is_active_in_org=True
        )

        self.emp_no_billing = Employee.objects.create_user(
            email="emp@org-a.com",
            password="password123",
            organization=self.org_a,
            role=self.role_emp,
            is_active=True
        )
        self.mem_emp = OrganizationMembership.objects.create(
            user=self.emp_no_billing,
            organization=self.org_a,
            role=self.role_emp,
            is_active_in_org=True
        )

        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.admin_a)

        self.factory = APIRequestFactory()

    def _create_storage_file(self, org, size_bytes=1000, status='ACTIVE', module='projects', model='ProjectAttachment', obj_id='1'):
        return StorageFile.objects.create(
            organization=org,
            source_module=module,
            source_model=model,
            source_object_id=str(obj_id),
            original_filename=f"file_{obj_id}.pdf",
            file_path=f"private_media/file_{obj_id}.pdf",
            storage_backend="private_filesystem",
            size_bytes=size_bytes,
            status=status,
        )

    def test_01_summary_success_for_active_organization(self):
        """1. Summary endpoint returns HTTP 200 with all required fields."""
        self._create_storage_file(self.org_a, size_bytes=5_000_000, obj_id='1')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['organization_id'], self.org_a.id)
        self.assertEqual(resp.data['active_bytes'], 5_000_000)
        self.assertEqual(resp.data['current_credits'], 1)
        self.assertEqual(resp.data['currency'], 'INR')
        self.assertEqual(resp.data['billing_enabled'], False)

    def test_02_correct_active_bytes(self):
        """2. Correct active bytes summed from all ACTIVE files."""
        self._create_storage_file(self.org_a, size_bytes=10_000_000, obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=25_000_000, obj_id='2')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['active_bytes'], 35_000_000)

    def test_03_deleted_files_excluded_from_active_bytes(self):
        """3. DELETED files are excluded from active_bytes."""
        self._create_storage_file(self.org_a, size_bytes=10_000_000, status='ACTIVE', obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=50_000_000, status='DELETED', obj_id='2')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['active_bytes'], 10_000_000)

    def test_04_active_files_correct(self):
        """4. active_files reflects exact count of ACTIVE status files."""
        self._create_storage_file(self.org_a, size_bytes=100, status='ACTIVE', obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=200, status='ACTIVE', obj_id='2')
        self._create_storage_file(self.org_a, size_bytes=300, status='DELETED', obj_id='3')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['active_files'], 2)

    def test_05_deleted_files_correct(self):
        """5. deleted_files reflects exact count of DELETED status files."""
        self._create_storage_file(self.org_a, size_bytes=100, status='ACTIVE', obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=200, status='DELETED', obj_id='2')
        self._create_storage_file(self.org_a, size_bytes=300, status='DELETED', obj_id='3')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['deleted_files'], 2)

    def test_06_organization_wide_ceil(self):
        """
        6. Organization-wide CEIL: total bytes aggregated first, then CEIL once.
        Two files of 400MB (400,000,000 bytes) each.
        Per-file ceil would be: ceil(0.4) + ceil(0.4) = 1 + 1 = 2 credits.
        Organization-wide ceil is: ceil(800,000,000 / 1,000,000,000) = ceil(0.8) = 1 credit.
        """
        self._create_storage_file(self.org_a, size_bytes=400_000_000, obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=400_000_000, obj_id='2')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['active_bytes'], 800_000_000)
        self.assertEqual(resp.data['current_credits'], 1)  # Proves org-wide ceil

    def test_07_cross_tenant_isolation(self):
        """7. Cross-tenant isolation: Org A cannot see Org B's files."""
        self._create_storage_file(self.org_a, size_bytes=10_000_000, obj_id='1')
        self._create_storage_file(self.org_b, size_bytes=90_000_000, obj_id='2')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['organization_id'], self.org_a.id)
        self.assertEqual(resp.data['active_bytes'], 10_000_000)
        self.assertEqual(resp.data['active_files'], 1)

    def test_08_no_request_user_organization_fallback(self):
        """
        8. No fallback to request.user.organization.
        If request.active_organization is None, the view fails closed with 400.
        """
        req = self.factory.get('/api/v1/storage/summary/')
        req.user = self.admin_a  # user.organization is Org A
        req.active_organization = None
        req.active_membership = None
        force_authenticate(req, user=self.admin_a)

        view = CompanyStorageSummaryView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Active organization context is required', str(resp.data))

    def test_09_missing_active_organization_fails_closed(self):
        """9. Missing active organization fails closed."""
        req = self.factory.get('/api/v1/storage/summary/')
        req.user = self.admin_a
        req.active_organization = None
        force_authenticate(req, user=self.admin_a)

        view = CompanyStorageSummaryView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_10_settings_billing_permission_required(self):
        """10. User lacking 'settings:billing' is denied (HTTP 403)."""
        client_emp = APIClient()
        client_emp.force_authenticate(user=self.emp_no_billing)

        resp = client_emp.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_11_billing_disabled_still_returns_usage(self):
        """11. When storage_billing_enabled=False, metrics are still returned with billing_enabled=false."""
        self._create_storage_file(self.org_a, size_bytes=7_000_000, obj_id='1')

        self.g_settings.storage_billing_enabled = False
        self.g_settings.save()

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['billing_enabled'], False)
        self.assertEqual(resp.data['active_bytes'], 7_000_000)
        self.assertEqual(resp.data['current_credits'], 1)

    def test_12_current_price_credit_size_returned_correctly(self):
        """12. Current price and credit size match GlobalBillingSettings."""
        self.g_settings.storage_credit_size_bytes = 2_000_000_000
        self.g_settings.storage_credit_monthly_price = Decimal('35.50')
        self.g_settings.save()

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['credit_size_bytes'], 2_000_000_000)
        self.assertEqual(resp.data['credit_monthly_price'], "35.50")

    def test_13_source_breakdown_correct(self):
        """13. Source breakdown aggregates by source_module and source_model."""
        self._create_storage_file(self.org_a, size_bytes=1000, module='projects', model='ProjectAttachment', obj_id='1')
        self._create_storage_file(self.org_a, size_bytes=2000, module='projects', model='ProjectAttachment', obj_id='2')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        breakdown = resp.data['source_breakdown']
        self.assertEqual(len(breakdown), 1)
        self.assertEqual(breakdown[0]['source_module'], 'projects')
        self.assertEqual(breakdown[0]['source_model'], 'ProjectAttachment')
        self.assertEqual(breakdown[0]['bytes'], 3000)
        self.assertEqual(breakdown[0]['active_files'], 2)

    def test_14_response_does_not_leak_private_paths(self):
        """14. Response JSON contains no file_path, storage_backend, source_object_id, or metadata."""
        self._create_storage_file(self.org_a, size_bytes=1000, obj_id='1')

        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        raw_content = str(resp.content)
        self.assertNotIn('file_path', raw_content)
        self.assertNotIn('storage_backend', raw_content)
        self.assertNotIn('source_object_id', raw_content)
        self.assertNotIn('private_media', raw_content)
        self.assertNotIn('is_backfill', raw_content)

    def test_15_summary_get_creates_zero_storage_daily_usage(self):
        """15. GET /summary/ is a pure read and creates zero StorageDailyUsage rows."""
        self._create_storage_file(self.org_a, size_bytes=1000, obj_id='1')

        before_count = StorageDailyUsage.objects.count()
        resp = self.client_a.get('/api/v1/storage/summary/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(StorageDailyUsage.objects.count(), before_count)

    def test_16_history_empty_returns_200_empty_list(self):
        """16. GET /history/ returns HTTP 200 with [] when no rows exist."""
        resp = self.client_a.get('/api/v1/storage/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_17_history_only_returns_active_org_rows(self):
        """17. GET /history/ returns only rows belonging to active organization."""
        # Create a usage row for Org A and Org B
        StorageDailyUsage.objects.create(
            organization=self.org_a,
            usage_date=date(2026, 9, 7),
            billable_bytes=5_000_000,
            billable_gb=Decimal('0.0050'),
            storage_credits=1,
            storage_charge=Decimal('0.666666666667'),
            is_finalized=True
        )
        StorageDailyUsage.objects.create(
            organization=self.org_b,
            usage_date=date(2026, 9, 7),
            billable_bytes=90_000_000,
            billable_gb=Decimal('0.0900'),
            storage_credits=1,
            storage_charge=Decimal('0.666666666667'),
            is_finalized=True
        )

        resp = self.client_a.get('/api/v1/storage/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['billable_bytes'], 5_000_000)
        self.assertEqual(resp.data[0]['billable_gb'], '0.0050')
        self.assertEqual(resp.data[0]['date'], '2026-09-07')
        self.assertEqual(resp.data[0]['is_finalized'], True)

    def test_18_history_get_creates_zero_storage_daily_usage(self):
        """18. GET /history/ has zero side-effects and writes nothing."""
        before_count = StorageDailyUsage.objects.count()
        resp = self.client_a.get('/api/v1/storage/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(StorageDailyUsage.objects.count(), before_count)

    def test_19_history_response_does_not_leak_unrelated_internal_fields(self):
        """19. History serializer only returns specified safe fields."""
        StorageDailyUsage.objects.create(
            organization=self.org_a,
            usage_date=date(2026, 9, 8),
            billable_bytes=1000,
            billable_gb=Decimal('0.0001'),
            storage_credits=1,
            storage_credit_size_bytes_snapshot=1000000000,
            monthly_credit_price_snapshot=Decimal('20.00'),
            daily_credit_rate_snapshot=Decimal('0.666666666667'),
            storage_charge=Decimal('0.666666666667'),
            calculation_version=1,
            is_finalized=False
        )

        resp = self.client_a.get('/api/v1/storage/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        row = resp.data[0]
        self.assertEqual(set(row.keys()), {'date', 'billable_bytes', 'billable_gb', 'storage_credits', 'storage_charge', 'is_finalized'})
        self.assertNotIn('calculation_version', row)
        self.assertNotIn('storage_credit_size_bytes_snapshot', row)

    def test_20_legacy_employee_org_a_but_active_org_b(self):
        """
        20. Legacy user.organization = Org A, but request.active_organization = Org B.
        Only Org B storage metrics appear in response.
        """
        # User belongs to Org A by legacy pointer, but has active membership in Org B
        mem_b = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_b,
            role=self.role_admin,
            is_active_in_org=True
        )

        self._create_storage_file(self.org_a, size_bytes=10_000_000, obj_id='1')
        self._create_storage_file(self.org_b, size_bytes=70_000_000, obj_id='2')

        req = self.factory.get('/api/v1/storage/summary/')
        req.user = self.admin_a
        req.active_organization = self.org_b
        req.active_membership = mem_b
        force_authenticate(req, user=self.admin_a)

        view = CompanyStorageSummaryView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['organization_id'], self.org_b.id)
        self.assertEqual(resp.data['active_bytes'], 70_000_000)
        self.assertEqual(resp.data['active_files'], 1)
