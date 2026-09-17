# --------------------------------------------------------------------------------
#       Cross-Tenant Data Boundary & Security Isolation Test Suite
# --------------------------------------------------------------------------------

from datetime import date, datetime, timedelta
from decimal import Decimal
import json

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Organization, OrgSettings
from users.models import Employee, PermissionFlag, OrganizationMembership
from attendance.models import AttendanceLog, AttendancePeriod, AttendancePeriodEmployeeSnapshot, Holiday

from payroll.models import (
    SalaryComponent,
    EmployeeSalaryStructure,
    PayrollPeriod,
    Payslip,
)
from payroll.services import (
    assign_or_revise_salary_structure,
    calculate_payroll_period,
    finalize_payroll_period,
)
from projects.models import (
    Project,
    ProjectEpic,
    ProjectStory,
    ProjectTask,
    ProjectMember,
    ProjectStatusOption,
)
from projects.services.statuses import initialize_default_statuses, get_default_status


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_STORE_EAGER_RESULT=True)
class SecurityIsolationCrossTenantTests(TestCase):
    """
    Automated security tests verifying strict multi-tenancy isolation and
    fail-closed behavior across Payroll, Attendance, Agile Projects, and Header Tampering.
    """

    def setUp(self):
        # 1. Setup Required Permissions
        self._setup_permissions()

        # 2. Tenant Alpha Setup
        self.settings_a = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=True,
            payroll_currency='USD',
            subscriptionStatus='Active',
            max_employees_allowed=100
        )
        self.org_a = Organization.objects.create(
            name="Tenant Alpha",
            subdomain="alpha-security",
            settings=self.settings_a
        )
        self.admin_a = Employee.objects.create_user(
            email="admin@alpha.sec",
            username="admin_alpha_sec",
            password="SecurePassAlpha123!",
            first_name="Admin",
            last_name="Alpha",
            organization=self.org_a,
            isSuperAdmin=True
        )
        self.mem_admin_a = OrganizationMembership.objects.create(
            user=self.admin_a,
            organization=self.org_a,
            is_active_in_org=True
        )
        self.emp_a = Employee.objects.create_user(
            email="employee@alpha.sec",
            username="emp_alpha_sec",
            password="SecurePassAlpha123!",
            first_name="Alice",
            last_name="Alpha",
            employee_code="ALP-001",
            organization=self.org_a
        )
        self.mem_emp_a = OrganizationMembership.objects.create(
            user=self.emp_a,
            organization=self.org_a,
            is_active_in_org=True
        )

        # 3. Tenant Beta Setup
        self.settings_b = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=True,
            payroll_currency='EUR',
            subscriptionStatus='Active',
            max_employees_allowed=100
        )
        self.org_b = Organization.objects.create(
            name="Tenant Beta",
            subdomain="beta-security",
            settings=self.settings_b
        )
        self.admin_b = Employee.objects.create_user(
            email="admin@beta.sec",
            username="admin_beta_sec",
            password="SecurePassBeta123!",
            first_name="Admin",
            last_name="Beta",
            organization=self.org_b,
            isSuperAdmin=True
        )
        self.mem_admin_b = OrganizationMembership.objects.create(
            user=self.admin_b,
            organization=self.org_b,
            is_active_in_org=True
        )
        self.emp_b = Employee.objects.create_user(
            email="employee@beta.sec",
            username="emp_beta_sec",
            password="SecurePassBeta123!",
            first_name="Bob",
            last_name="Beta",
            employee_code="BET-001",
            organization=self.org_b
        )
        self.mem_emp_b = OrganizationMembership.objects.create(
            user=self.emp_b,
            organization=self.org_b,
            is_active_in_org=True
        )

        # 4. Setup Tenant Beta Resources (Payroll, Attendance, Projects)
        self._setup_tenant_beta_resources()

        # 5. Clients
        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.admin_a)

        self.client_emp_a = APIClient()
        self.client_emp_a.force_authenticate(user=self.emp_a)

    def _setup_permissions(self):
        for key, name, module in [
            ('payroll:view', 'View Payroll', 'Payroll'),
            ('payroll:process', 'Process Payroll', 'Payroll'),
            ('payroll:manage', 'Manage Payroll', 'Payroll'),
            ('salary:view', 'View Salary', 'Salary'),
            ('salary:manage', 'Manage Salary', 'Salary'),
            ('attendance:admin', 'Attendance Admin', 'Attendance'),
            ('attendance:management_portal', 'Attendance Portal', 'Attendance'),
            ('projects:view', 'View Projects', 'Projects'),
            ('projects:create', 'Create Projects', 'Projects'),
            ('projects:update', 'Update Projects', 'Projects'),
            ('projects:delete', 'Delete Projects', 'Projects'),
        ]:
            PermissionFlag.objects.get_or_create(key=key, defaults={'name': name, 'module': module})

    def _setup_tenant_beta_resources(self):
        # 1. Tenant Beta Payroll
        self.comp_beta = SalaryComponent.objects.create(
            organization=self.org_b,
            name="Base Salary",
            component_type="Earning",
            is_taxable=True
        )
        assign_or_revise_salary_structure(
            employee=self.emp_b,
            organization=self.org_b,
            effective_from=date(2026, 1, 1),
            components_data=[
                {'salary_component_id': self.comp_beta.id, 'amount': Decimal('6000.00')}
            ],
            created_by=self.admin_b
        )
        att_b = AttendancePeriod.objects.create(
            organization=self.org_b,
            year=2026,
            month=4,
            status='Finalized',
            current_revision=1
        )
        AttendancePeriodEmployeeSnapshot.objects.create(
            attendance_period=att_b,
            employee=self.emp_b,
            employee_name="Bob Beta",
            revision=1,
            is_current=True,
            working_days=20,
            present_days=20.0,
            absent_days=0,
            payable_attendance_units=20.0
        )
        self.period_b = calculate_payroll_period(organization=self.org_b, year=2026, month=4, user=self.admin_b)
        finalize_payroll_period(organization=self.org_b, year=2026, month=4, user=self.admin_b)
        self.payslip_b = Payslip.objects.filter(organization=self.org_b, payroll_period=self.period_b, employee=self.emp_b).first()

        # 2. Tenant Beta Attendance Log
        self.att_log_b = AttendanceLog.objects.create(
            employee=self.emp_b,
            date=date(2026, 4, 15),
            clockIn=timezone.now(),
            status='Present'
        )

        # 3. Tenant Beta Projects, Epics, Stories, Tasks
        initialize_default_statuses(self.org_b)
        default_status_b = get_default_status(self.org_b)

        self.project_b = Project.objects.create(
            company=self.org_b,
            name="Project Beta Secret",
            key="BET-SEC",
            status=default_status_b,
            project_manager=self.admin_b,
            created_by=self.admin_b
        )
        ProjectMember.objects.create(
            project=self.project_b,
            user=self.admin_b,
            project_role="Project Manager"
        )
        self.epic_b = ProjectEpic.objects.create(
            company=self.org_b,
            project=self.project_b,
            title="Epic Beta Confidential",
            status=default_status_b,
            created_by=self.admin_b
        )
        self.story_b = ProjectStory.objects.create(
            project=self.project_b,
            epic=self.epic_b,
            title="Story Beta Restricted",
            status=default_status_b,
            created_by=self.admin_b
        )
        self.task_b = ProjectTask.objects.create(
            story=self.story_b,
            title="Task Beta Internal Only",
            assigned_to=self.emp_b,
            status=default_status_b
        )

    # ============================================================================
    # 1. CROSS-TENANT IDOR ON PAYROLL
    # ============================================================================

    def test_payroll_payslip_detail_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot access Tenant B payslip detail via IDOR."""
        self.assertIsNotNone(self.payslip_b)
        res = self.client_a.get(f'/api/v1/payroll/payslips/{self.payslip_b.id}/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_payroll_payslip_pdf_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot download Tenant B payslip PDF."""
        self.assertIsNotNone(self.payslip_b)
        res = self.client_a.get(f'/api/v1/payroll/payslips/{self.payslip_b.id}/pdf/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_payroll_my_payslip_cross_tenant_idor_blocked(self):
        """Tenant A employee cannot fetch Tenant B employee payslip via self-service."""
        self.assertIsNotNone(self.payslip_b)
        res = self.client_emp_a.get(f'/api/v1/payroll/my-payslips/{self.payslip_b.id}/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_payroll_my_payslip_pdf_cross_tenant_idor_blocked(self):
        """Tenant A employee cannot download Tenant B employee PDF via self-service."""
        self.assertIsNotNone(self.payslip_b)
        res = self.client_emp_a.get(f'/api/v1/payroll/my-payslips/{self.payslip_b.id}/pdf/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_payroll_period_detail_cross_tenant_isolated(self):
        """Tenant A cannot read Tenant B's finalized payroll period."""
        res = self.client_a.get(f'/api/v1/payroll/periods/2026/4/')
        # Tenant A has no 2026/4 period, response must not leak Tenant B's data
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data.get('payroll_period'))

    def test_payroll_calculate_cannot_target_another_tenant(self):
        """Tenant A calculation cannot touch Tenant B's period."""
        # Tenant B has 2026/4 finalized. Calculating 2026/4 for Tenant A fails due to missing attendance
        res = self.client_a.post('/api/v1/payroll/periods/2026/4/calculate/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # Verify Tenant B period was untouched
        self.period_b.refresh_from_db()
        self.assertEqual(self.period_b.status, 'Finalized')

    def test_payroll_bulk_zip_export_cross_tenant_blocked(self):
        """Tenant A cannot export Tenant B's issued payslips as ZIP."""
        res = self.client_a.get('/api/v1/payroll/periods/2026/4/payslips/export-zip/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    # ============================================================================
    # 2. CROSS-TENANT ATTENDANCE ACCESS
    # ============================================================================

    def test_attendance_log_detail_cross_tenant_idor_blocked(self):
        """Tenant A cannot retrieve attendance log belonging to Tenant B employee."""
        res = self.client_a.get(f'/api/v1/attendance/{self.att_log_b.id}/')
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_attendance_log_list_cross_tenant_isolation(self):
        """Tenant A attendance list does not leak Tenant B attendance logs."""
        # Create an attendance log in Tenant Alpha
        att_log_a = AttendanceLog.objects.create(
            employee=self.emp_a,
            date=date(2026, 4, 15),
            clockIn=timezone.now(),
            status='Present'
        )
        res = self.client_a.get('/api/v1/attendance/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        returned_ids = [item['id'] for item in results]
        self.assertIn(att_log_a.id, returned_ids)
        self.assertNotIn(self.att_log_b.id, returned_ids)

    def test_attendance_clock_in_cross_tenant_blocked(self):
        """Tenant A employee cannot clock in targeting Tenant B employee."""
        res = self.client_emp_a.post('/api/v1/attendance/clock-in/', data={'employeeId': self.emp_b.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_attendance_clock_in_admin_cross_tenant_blocked(self):
        """Tenant A admin cannot clock in on behalf of Tenant B employee."""
        res = self.client_a.post('/api/v1/attendance/clock-in/', data={'employeeId': self.emp_b.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_attendance_approval_cross_tenant_blocked(self):
        """Tenant A admin cannot approve attendance log of Tenant B."""
        res = self.client_a.patch(f'/api/v1/attendance/{self.att_log_b.id}/approve/', data={'status': 'Approved'})
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    # ============================================================================
    # 3. CROSS-TENANT AGILE PROJECTS & TASKS
    # ============================================================================

    def test_project_list_cross_tenant_isolation(self):
        """Tenant A project list does NOT contain Tenant B's projects."""
        initialize_default_statuses(self.org_a)
        status_a = get_default_status(self.org_a)
        proj_a = Project.objects.create(
            company=self.org_a,
            name="Project Alpha Public",
            key="ALP-PUB",
            status=status_a,
            project_manager=self.admin_a,
            created_by=self.admin_a
        )
        ProjectMember.objects.create(project=proj_a, user=self.admin_a, project_role="Project Manager")

        res = self.client_a.get('/api/v1/projects/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        proj_ids = [p['id'] for p in results]
        self.assertIn(proj_a.id, proj_ids)
        self.assertNotIn(self.project_b.id, proj_ids)

    def test_project_retrieve_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot retrieve Tenant B's project."""
        res = self.client_a.get(f'/api/v1/projects/{self.project_b.id}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_update_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot update Tenant B's project."""
        res = self.client_a.patch(f'/api/v1/projects/{self.project_b.id}/', data={'name': 'Hacked Name'})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_delete_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot delete Tenant B's project."""
        res = self.client_a.delete(f'/api/v1/projects/{self.project_b.id}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_epic_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot view, edit, or delete Tenant B's epic."""
        res_get = self.client_a.get(f'/api/v1/epics/{self.epic_b.id}/')
        self.assertEqual(res_get.status_code, status.HTTP_404_NOT_FOUND)

        res_patch = self.client_a.patch(f'/api/v1/epics/{self.epic_b.id}/', data={'title': 'Hacked Epic'})
        self.assertEqual(res_patch.status_code, status.HTTP_404_NOT_FOUND)

        res_del = self.client_a.delete(f'/api/v1/epics/{self.epic_b.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_story_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot view, edit, or delete Tenant B's story."""
        res_get = self.client_a.get(f'/api/v1/stories/{self.story_b.id}/')
        self.assertEqual(res_get.status_code, status.HTTP_404_NOT_FOUND)

        res_patch = self.client_a.patch(f'/api/v1/stories/{self.story_b.id}/', data={'title': 'Hacked Story'})
        self.assertEqual(res_patch.status_code, status.HTTP_404_NOT_FOUND)

        res_del = self.client_a.delete(f'/api/v1/stories/{self.story_b.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_task_cross_tenant_idor_blocked(self):
        """Tenant A admin cannot view, edit, or delete Tenant B's task."""
        res_get = self.client_a.get(f'/api/v1/project-tasks/{self.task_b.id}/')
        self.assertEqual(res_get.status_code, status.HTTP_404_NOT_FOUND)

        res_patch = self.client_a.patch(f'/api/v1/project-tasks/{self.task_b.id}/', data={'title': 'Hacked Task'})
        self.assertEqual(res_patch.status_code, status.HTTP_404_NOT_FOUND)

        res_del = self.client_a.delete(f'/api/v1/project-tasks/{self.task_b.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_404_NOT_FOUND)

    def test_project_task_injection_into_foreign_story_blocked(self):
        """Tenant A admin cannot inject a task into Tenant B's story."""
        res = self.client_a.post('/api/v1/project-tasks/', data={
            'story': self.story_b.id,
            'title': 'Malicious Task Injection'
        })
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN])

    # ============================================================================
    # 4. HEADER TAMPERING (X-Organization-ID / TenantContext Spoofing)
    # ============================================================================

    def test_header_tampering_projects_fail_closed(self):
        """
        User A explicitly spoofs HTTP_X_ORGANIZATION_ID header with Tenant B's ID.
        System must not switch context to Tenant B or leak Tenant B's projects.
        """
        client = APIClient()
        client.force_authenticate(user=self.admin_a)
        # Attempt to spoof Tenant B context
        client.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_b.id)
        client.defaults['HTTP_X_ACTIVE_ORGANIZATION'] = str(self.org_b.id)

        res = client.get('/api/v1/projects/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        proj_ids = [p['id'] for p in results]
        # Tenant B project must NOT appear
        self.assertNotIn(self.project_b.id, proj_ids)

    def test_header_tampering_attendance_fail_closed(self):
        """
        User A spoofs header attempting to read Tenant B's attendance logs.
        System must not return Tenant B's attendance.
        """
        client = APIClient()
        client.force_authenticate(user=self.admin_a)
        client.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_b.id)
        client.defaults['HTTP_X_ACTIVE_ORGANIZATION'] = str(self.org_b.id)

        res = client.get('/api/v1/attendance/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        att_ids = [a['id'] for a in results]
        self.assertNotIn(self.att_log_b.id, att_ids)

    def test_header_tampering_payroll_membership_validation(self):
        """
        BasePayrollAPIView strictly validates active membership for resolved organization.
        If header tampering is attempted, access to foreign tenant payroll is rejected.
        """
        client = APIClient()
        client.force_authenticate(user=self.admin_a)
        client.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_b.id)
        client.defaults['HTTP_X_ACTIVE_ORGANIZATION'] = str(self.org_b.id)

        res = client.get('/api/v1/payroll/periods/2026/4/payslips/')
        # Either 403 Forbidden (membership failure on spoofed org) or 200 OK (empty list scoped to Org A)
        # In NO CASE should it return Tenant B's payslip
        if res.status_code == status.HTTP_200_OK:
            returned_payslips = res.data if isinstance(res.data, list) else res.data.get('results', [])
            payslip_ids = [p['id'] for p in returned_payslips]
            self.assertNotIn(self.payslip_b.id, payslip_ids)
        else:
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_jwt_tampered_active_org_claim_rejected(self):
        """
        If a JWT access token has a forged active_org_id claim pointing to Tenant B
        for an employee with no membership in Tenant B, TenantContext rejects the claim.
        """
        refresh = RefreshToken.for_user(self.admin_a)
        refresh['active_org_id'] = self.org_b.id
        access_token = str(refresh.access_token)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        res = client.get('/api/v1/projects/')
        # Since TenantContext detects no membership in org_b, it returns 403 or falls back to empty
        if res.status_code == status.HTTP_200_OK:
            results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
            proj_ids = [p['id'] for p in results]
            self.assertNotIn(self.project_b.id, proj_ids)
        else:
            self.assertIn(res.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED])

    def test_holidays_cross_tenant_isolation(self):
        """
        Verify holidays and recurring rules are strictly isolated between tenants,
        and never leak across organization boundaries.
        """
        # Create holiday in Tenant A
        hol_a = Holiday.objects.create(
            name="Alpha Day Celebration",
            date=date(2026, 9, 20),
            organization=self.org_a
        )
        # Create holiday in Tenant B
        hol_b = Holiday.objects.create(
            name="Beta Day Celebration",
            date=date(2026, 9, 25),
            organization=self.org_b
        )

        client_a = APIClient()
        client_a.force_authenticate(user=self.admin_a)
        client_a.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_a.id)

        res_a = client_a.get('/api/v1/holidays/')
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        names_a = [h['name'] for h in res_a.data]
        self.assertIn("Alpha Day Celebration", names_a)
        self.assertNotIn("Beta Day Celebration", names_a)

        client_b = APIClient()
        client_b.force_authenticate(user=self.admin_b)
        client_b.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_b.id)

        res_b = client_b.get('/api/v1/holidays/')
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        names_b = [h['name'] for h in res_b.data]
        self.assertIn("Beta Day Celebration", names_b)
        self.assertNotIn("Alpha Day Celebration", names_b)

    def test_holiday_settings_cross_tenant_isolation(self):
        """
        Verify recurring holiday settings are configured and fetched per tenant.
        """
        client_a = APIClient()
        client_a.force_authenticate(user=self.admin_a)
        client_a.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_a.id)

        patch_res = client_a.patch('/api/v1/settings/holidays/', {
            'default_weekly_holidays': ['Friday', 'Saturday']
        }, format='json')
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        client_b = APIClient()
        client_b.force_authenticate(user=self.admin_b)
        client_b.defaults['HTTP_X_ORGANIZATION_ID'] = str(self.org_b.id)

        res_b = client_b.get('/api/v1/settings/holidays/')
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        # Tenant B should not have Tenant A's weekly holidays
        self.assertNotEqual(res_b.data['default_weekly_holidays'], ['Friday', 'Saturday'])

