from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from api.models import Employee, AttendanceLog
from django.conf import settings

class AttendanceTests(APITestCase):
    def setUp(self):
        # Create an employee
        self.employee = Employee.objects.create_user(
            email='test@example.com',
            password='password123',
            first_name='Test',
            last_name='User'
        )
        self.client.force_authenticate(user=self.employee)

    def test_clock_in_and_clock_out(self):
        # Clock in
        url = '/api/attendance/clock-in/'
        response = self.client.post(url, {'employeeId': self.employee.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AttendanceLog.objects.count(), 1)
        
        # Clock out
        url_out = '/api/attendance/clock-out/'
        response_out = self.client.post(url_out, {'employeeId': self.employee.id}, format='json')
        self.assertEqual(response_out.status_code, status.HTTP_200_OK)
        
        # Check duration
        log = AttendanceLog.objects.first()
        self.assertIsNotNone(log.clockOut)
        self.assertIsNotNone(log.totalDuration)


class PasswordRecoveryTests(APITestCase):
    def setUp(self):
        from django.core import mail
        self.employee = Employee.objects.create_user(
            email='reset-test@example.com',
            password='old-password123',
            first_name='Reset',
            last_name='User'
        )
        mail.outbox = []

    def test_password_reset_request_success(self):
        from django.core import mail
        from api.models import AuditLog
        
        url = '/api/auth/password-reset/request/'
        response = self.client.post(url, {'email': 'reset-test@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify email is sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Reset Your Password', mail.outbox[0].subject)
        self.assertIn('reset-test@example.com', mail.outbox[0].to)
        
        # Check Audit Log creation
        log = AuditLog.objects.filter(employee=self.employee, action="Password Reset Requested").first()
        self.assertIsNotNone(log)

    def test_password_reset_request_non_existent_email(self):
        url = '/api/auth/password-reset/request/'
        response = self.client.post(url, {'email': 'non-existent@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_password_reset_validate_success(self):
        from django.core.signing import TimestampSigner
        signer = TimestampSigner(salt='password-reset')
        token = signer.sign(str(self.employee.id))
        
        url = '/api/auth/password-reset/validate/'
        response = self.client.post(url, {'token': token}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Token is valid.')

    def test_password_reset_validate_expired(self):
        import time
        from unittest.mock import patch
        from django.core.signing import TimestampSigner
        
        signer = TimestampSigner(salt='password-reset')
        # Sign 150 seconds in the past to exceed the 120-second max_age
        with patch('time.time', return_value=time.time() - 150):
            token = signer.sign(str(self.employee.id))
            
        url = '/api/auth/password-reset/validate/'
        response = self.client.post(url, {'token': token}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expired', response.data['error'])

    def test_password_reset_validate_invalid_signature(self):
        url = '/api/auth/password-reset/validate/'
        response = self.client.post(url, {'token': 'invalid-token-value'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid', response.data['error'])

    def test_password_reset_confirm_success(self):
        from django.core.signing import TimestampSigner
        from api.models import AuditLog
        
        signer = TimestampSigner(salt='password-reset')
        token = signer.sign(str(self.employee.id))
        
        url = '/api/auth/password-reset/confirm/'
        response = self.client.post(url, {
            'token': token,
            'password': 'new-password123',
            'passwordConfirm': 'new-password123'
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Password has been successfully updated.')
        
        # Verify password is changed
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password('new-password123'))
        
        # Check Audit Log creation
        log = AuditLog.objects.filter(employee=self.employee, action="Password Reset Confirmed").first()
        self.assertIsNotNone(log)

    def test_password_reset_confirm_mismatched_passwords(self):
        from django.core.signing import TimestampSigner
        signer = TimestampSigner(salt='password-reset')
        token = signer.sign(str(self.employee.id))
        
        url = '/api/auth/password-reset/confirm/'
        response = self.client.post(url, {
            'token': token,
            'password': 'new-password123',
            'passwordConfirm': 'different-pass'
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('do not match', response.data['error'])

    def test_password_reset_confirm_expired_token(self):
        import time
        from unittest.mock import patch
        from django.core.signing import TimestampSigner
        
        signer = TimestampSigner(salt='password-reset')
        with patch('time.time', return_value=time.time() - 150):
            token = signer.sign(str(self.employee.id))
            
        url = '/api/auth/password-reset/confirm/'
        response = self.client.post(url, {
            'token': token,
            'password': 'new-password123',
            'passwordConfirm': 'new-password123'
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expired', response.data['error'])


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.employee = Employee.objects.create_user(
            email='change-test@example.com',
            password='old-password123',
            first_name='Change',
            last_name='User'
        )
        self.client.force_authenticate(user=self.employee)

    def test_change_password_success(self):
        from api.models import AuditLog
        url = '/api/auth/change-password/'
        response = self.client.post(url, {
            'currentPassword': 'old-password123',
            'newPassword': 'new-password123',
            'confirmPassword': 'new-password123'
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

        # Verify password changed
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password('new-password123'))

        # Verify audit log
        log = AuditLog.objects.filter(employee=self.employee, action="Password Changed").first()
        self.assertIsNotNone(log)

    def test_change_password_incorrect_current(self):
        url = '/api/auth/change-password/'
        response = self.client.post(url, {
            'currentPassword': 'wrong-current-password',
            'newPassword': 'new-password123',
            'confirmPassword': 'new-password123'
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Incorrect current password', response.data['error'])

    def test_change_password_mismatched_new(self):
        url = '/api/auth/change-password/'
        response = self.client.post(url, {
            'currentPassword': 'old-password123',
            'newPassword': 'new-password123',
            'confirmPassword': 'mismatched-password'
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('do not match', response.data['error'])

    def test_change_password_weak_password(self):
        url = '/api/auth/change-password/'
        response = self.client.post(url, {
            'currentPassword': 'old-password123',
            'newPassword': '123',
            'confirmPassword': '123'
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(len(response.data['error']) > 0)


class LeadTests(APITestCase):
    def setUp(self):
        from api.models import Employee
        self.employee = Employee.objects.create_user(
            email='lead-admin@example.com',
            password='password123',
            first_name='Lead',
            last_name='Admin'
        )

    def test_anonymous_lead_creation(self):
        from api.models import Lead
        url = '/api/leads/'
        payload = {
            'name': 'Test Lead',
            'email': 'lead@example.com',
            'phone': '1234567890',
            'companyName': 'Lead Corp',
            'message': 'Hello, I want to contact you!'
        }
        
        # Test anonymous post
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Lead.objects.count(), 1)
        
        lead = Lead.objects.first()
        self.assertEqual(lead.name, 'Test Lead')
        self.assertEqual(lead.email, 'lead@example.com')
        self.assertEqual(lead.phone, '1234567890')
        self.assertEqual(lead.companyName, 'Lead Corp')
        self.assertEqual(lead.message, 'Hello, I want to contact you!')

    def test_anonymous_lead_listing_denied(self):
        url = '/api/leads/'
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_lead_listing_allowed(self):
        from api.models import Lead
        # Create a lead
        Lead.objects.create(
            name='Another Lead',
            email='another@example.com'
        )
        
        url = '/api/leads/'
        # Authenticate
        self.client.force_authenticate(user=self.employee)
        response = self.client.get(url, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Another Lead')


class LeadWorkflowTests(APITestCase):
    def setUp(self):
        from api.models import Employee
        self.staff_member = Employee.objects.create_user(
            email='staff@example.com',
            password='password123',
            first_name='Staff',
            last_name='User',
            isSuperAdmin=True
        )

    def test_public_lead_ingestion_and_audit(self):
        from api.models import Lead, LeadHistory
        url = '/api/leads/public/'
        payload = {
            'name': 'Public Inquirer',
            'email': 'public@example.com',
            'phone': '555-0199',
            'message': 'Looking for corporate packages.'
        }
        # Unauthenticated request
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Lead.objects.count(), 1)
        
        # Verify initial LeadHistory log
        lead = Lead.objects.first()
        self.assertEqual(lead.status, 'New')
        self.assertFalse(lead.is_read)
        self.assertEqual(LeadHistory.objects.filter(lead=lead).count(), 1)
        self.assertEqual(LeadHistory.objects.first().action, "Lead generated from public website enquiry.")

    def test_backoffice_lead_listing_and_detail_read_flow(self):
        from api.models import Lead, LeadHistory
        lead = Lead.objects.create(
            name='Jane Doe',
            email='jane@example.com',
            phone='555-0122'
        )
        
        list_url = '/api/leads/backoffice/'
        detail_url = f'/api/leads/backoffice/{lead.id}/'
        
        # Unauthorized check
        response = self.client.get(list_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Authenticate
        self.client.force_authenticate(user=self.staff_member)
        
        # List leads
        list_resp = self.client.get(list_url, format='json')
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_resp.data), 1)
        
        # Get lead detail (triggers read logic)
        detail_resp = self.client.get(detail_url, format='json')
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        
        # Refresh from DB
        lead.refresh_from_db()
        self.assertTrue(lead.is_read)
        self.assertEqual(lead.read_by, self.staff_member)
        
        # Verify read audit history log
        histories = LeadHistory.objects.filter(lead=lead)
        self.assertEqual(histories.count(), 1)
        self.assertTrue("read by" in histories.first().action)

    def test_backoffice_lead_patch_status_and_assign_flow(self):
        from api.models import Lead, LeadHistory
        lead = Lead.objects.create(
            name='Bob Builder',
            email='bob@example.com',
            status='New'
        )
        
        detail_url = f'/api/leads/backoffice/{lead.id}/'
        self.client.force_authenticate(user=self.staff_member)
        
        # 1. Read first
        self.client.get(detail_url, format='json')
        
        # 2. Patch status and assignee
        payload = {
            'status': 'In Progress',
            'assigned_staff': self.staff_member.id
        }
        patch_resp = self.client.patch(detail_url, payload, format='json')
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        
        # Refresh from DB
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'In Progress')
        self.assertEqual(lead.assigned_staff, self.staff_member)
        
        # Verify LeadHistory entries:
        # - 1 for read, 1 for status change, 1 for assignment change = 3 total!
        histories = LeadHistory.objects.filter(lead=lead).order_by('timestamp')
        self.assertEqual(histories.count(), 3)
        self.assertTrue("Status updated" in histories[1].action)
        self.assertTrue("assigned to" in histories[2].action)


class BackofficeTests(APITestCase):
    def setUp(self):
        from api.models import Employee
        # Create a regular employee
        self.employee = Employee.objects.create_user(
            email='operator@example.com',
            password='password123',
            first_name='Regular',
            last_name='Operator',
            isSuperAdmin=False
        )
        # Create a superadmin
        self.superadmin = Employee.objects.create_user(
            email='superadmin@example.com',
            password='password123',
            first_name='Super',
            last_name='Admin',
            isSuperAdmin=True
        )

    def test_backoffice_html_view_anonymous_redirect(self):
        url = '/'
        response = self.client.get(url)
        # Should redirect to admin login
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/backoffice/login/', response.url)

    def test_backoffice_html_view_regular_employee_redirect(self):
        url = '/'
        self.client.force_login(self.employee)
        response = self.client.get(url)
        # Should redirect to login since they aren't superadmin
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/backoffice/login/', response.url)

    def test_backoffice_html_view_superadmin_allowed(self):
        url = '/'
        self.client.force_login(self.superadmin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_backoffice_apis_denied_to_regular_user(self):
        url = '/api/subscribers/'
        self.client.force_authenticate(user=self.employee)
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_package_crud_as_superadmin(self):
        from api.models import SubscriptionPackage
        url = '/api/packages/'
        self.client.force_authenticate(user=self.superadmin)
        
        # Create
        payload = {
            'name': 'Gold Plan',
            'price': '99.99',
            'employeeLimit': 100,
            'features': ['Unlimited Logs', 'GeoTracking']
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SubscriptionPackage.objects.count(), 1)
        
        # List
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_subscriber_crud_as_superadmin(self):
        from api.models import SubscriberAccount
        url = '/api/subscribers/'
        self.client.force_authenticate(user=self.superadmin)
        
        payload = {
            'email': 'customer@tenant.com',
            'packageName': 'Professional',
            'isActive': True
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SubscriberAccount.objects.count(), 1)

    def test_coupon_crud_as_superadmin(self):
        from api.models import Coupon
        url = '/api/coupons/'
        self.client.force_authenticate(user=self.superadmin)
        
        payload = {
            'code': 'OFF50',
            'discountType': 'Percentage',
            'discountValue': 50,
            'usageLimit': 10
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Coupon.objects.count(), 1)

    def test_backoffice_payment_list_flow(self):
        from api.models import Wallet, WalletTransaction
        wallet = Wallet.objects.create(
            employee=self.superadmin,
            balance=100.00
        )
        WalletTransaction.objects.create(
            wallet=wallet,
            amount=50.00,
            transactionType='Credit',
            success=True,
            status='Success',
            stripe_session_id='sess_123',
            stripeEventId='evt_123',
            details='Test deposit payment'
        )

        url = '/api/payments/backoffice/'
        
        # 1. Unauthenticated gets 401/403
        response = self.client.get(url, format='json')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        # 2. Regular staff member (not superadmin) gets 403
        self.client.force_authenticate(user=self.employee)
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Superadmin gets 200 and data
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['stripe_session_id'], 'sess_123')
        self.assertEqual(response.data[0]['employee_email'], self.superadmin.email)


class UnifiedConnectionFrameworkTests(APITestCase):
    def setUp(self):
        from api.models import Employee, SubscriptionPackage, SubscriberAccount, CMSContent, LMSModule, Organization, OrgSettings
        
        # Seed Settings and Org
        self.org_settings = OrgSettings.objects.create(max_employees_allowed=3)
        self.org = Organization.objects.create(name="Test Org", subdomain="test", settings=self.org_settings)

        # Superadmin for admin operations
        self.superadmin = Employee.objects.create_user(
            email='superadmin@example.com',
            password='password123',
            first_name='Super',
            last_name='Admin',
            isSuperAdmin=True,
            organization=self.org
        )
        
        # Regular employee
        self.employee = Employee.objects.create_user(
            email='employee@example.com',
            password='password123',
            first_name='Regular',
            last_name='Employee',
            isSuperAdmin=False,
            organization=self.org
        )
        
        # Seed Subscription Packages
        self.free_package = SubscriptionPackage.objects.create(
            name="Free Package",
            price="0.00",
            employeeLimit=3,
            features=[]
        )
        self.pro_package = SubscriptionPackage.objects.create(
            name="Professional",
            price="49.99",
            employeeLimit=10,
            features=["geofence", "biometric"]
        )
        
        # Seed SubscriberAccount for superadmin
        self.subscriber_account = SubscriberAccount.objects.create(
            email=self.superadmin.email,
            packageName="Free Package",
            isActive=True
        )
        
        # Seed CMS Content
        self.cms_item = CMSContent.objects.create(
            key="hero_title",
            value="Welcome to CubeLogs"
        )
        
        # Seed LMS Module
        self.lms_item = LMSModule.objects.create(
            title="Intro to Compliance",
            description="LMS Description",
            content="Module Content"
        )

    def test_cms_lms_public_endpoints_allowed_for_anonymous(self):
        # CMS
        cms_url = '/api/cms/'
        response = self.client.get(cms_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['key'], 'hero_title')
        
        # LMS
        lms_url = '/api/lms/'
        response = self.client.get(lms_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], 'Intro to Compliance')

    def test_cms_lms_write_endpoints_denied_for_anonymous_and_regular(self):
        # CMS Create
        cms_url = '/api/cms/'
        cms_payload = {'key': 'new_key', 'value': 'New Content'}
        response = self.client.post(cms_url, cms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # LMS Create
        lms_url = '/api/lms/'
        lms_payload = {'title': 'New Module', 'description': 'desc', 'content': 'Content'}
        response = self.client.post(lms_url, lms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Regular employee edit
        self.client.force_authenticate(user=self.employee)
        response = self.client.post(cms_url, cms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        response = self.client.post(lms_url, lms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cms_lms_write_endpoints_allowed_for_superadmin(self):
        self.client.force_authenticate(user=self.superadmin)
        
        # CMS Create
        cms_url = '/api/cms/'
        cms_payload = {'key': 'new_key', 'value': 'New Content'}
        response = self.client.post(cms_url, cms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # LMS Create
        lms_url = '/api/lms/'
        lms_payload = {'title': 'New Module', 'description': 'desc', 'content': 'Content'}
        response = self.client.post(lms_url, lms_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_employee_limit_enforced(self):
        from api.models import Employee
        # Currently we have 2 employees: superadmin, employee
        # Free Package limit is 3. We can add 1 more, but the 4th will be blocked.
        self.client.force_authenticate(user=self.superadmin)
        
        # Onboard 3rd employee - should succeed
        url = '/api/employees/'
        payload = {
            'email': 'third@example.com',
            'first_name': 'Third',
            'last_name': 'Employee'
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Total count is now 3. Try to onboard 4th employee - should fail limit validation
        payload_fourth = {
            'email': 'fourth@example.com',
            'first_name': 'Fourth',
            'last_name': 'Employee'
        }
        response = self.client.post(url, payload_fourth, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('non_field_errors', response.data)
        self.assertIn('capped at 3 active employees', response.data['non_field_errors'][0])

        # Upgrade settings employee limit to 10
        self.org_settings.max_employees_allowed = 10
        self.org_settings.save()
        
        # Try onboarding 4th employee again - should succeed now
        response = self.client.post(url, payload_fourth, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_package_feature_normalization(self):
        self.client.force_authenticate(user=self.superadmin)
        url = '/api/packages/'
        payload = {
            'name': 'Custom High Plan',
            'price': '49.99',
            'employeeLimit': 50,
            'features': ['Geofencing', 'Biometric Photos', 'Priority Support']
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify normalization in database
        from api.models import SubscriptionPackage
        pkg = SubscriptionPackage.objects.get(name='Custom High Plan')
        self.assertIn('geofence', pkg.features)
        self.assertIn('biometric', pkg.features)
        self.assertIn('priority support', pkg.features)


class WorkspaceProvisioningPipelineTests(APITestCase):
    def setUp(self):
        from django.core import mail
        mail.outbox = []

    def test_automated_provisioning_on_lead_creation(self):
        from api.models import Lead, SubscriptionPackage, SubscriberAccount, Employee
        from django.core import mail

        # 1. Create a lead with customization message
        url = '/api/leads/'
        payload = {
            'name': 'Fadi Manager',
            'email': 'fadi@cubelogs-tenant.com',
            'phone': '+91 9999999999',
            'companyName': 'Fadi Logistics',
            'message': 'Employees: 35 | Modules: Geofence, Biometric'
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 2. Verify custom package creation
        pkg_name = 'Build-Your-Own Plan - fadi@cubelogs-tenant.com'
        self.assertTrue(SubscriptionPackage.objects.filter(name=pkg_name).exists())
        pkg = SubscriptionPackage.objects.get(name=pkg_name)
        self.assertEqual(pkg.employeeLimit, 35)
        self.assertEqual(pkg.price, 3500.00) # 35 * 100
        self.assertIn('geofence', pkg.features)
        self.assertIn('biometric', pkg.features)

        # 3. Verify superuser employee creation
        self.assertTrue(Employee.objects.filter(email='fadi@cubelogs-tenant.com').exists())
        emp = Employee.objects.get(email='fadi@cubelogs-tenant.com')
        self.assertTrue(emp.isSuperAdmin)
        self.assertFalse(emp.is_superuser)
        self.assertFalse(emp.is_staff)
        self.assertEqual(emp.first_name, 'Fadi')
        self.assertEqual(emp.last_name, 'Manager')

        # 4. Verify subscriber account creation
        self.assertTrue(SubscriberAccount.objects.filter(email='fadi@cubelogs-tenant.com').exists())
        sub = SubscriberAccount.objects.get(email='fadi@cubelogs-tenant.com')
        self.assertEqual(sub.packageName, pkg_name)
        self.assertTrue(sub.isActive)

        # 5. Verify email dispatch
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('fadi@cubelogs-tenant.com', email.to)
        self.assertIn('Welcome to CubeLogs - Your Login Credentials', email.subject)
        
        # Verify alternative HTML body contains credentials and revoke links
        html_body = email.alternatives[0][0]
        self.assertIn('<strong>Email:</strong> fadi@cubelogs-tenant.com', html_body)
        self.assertIn('/revoke?token=', html_body)

        # 6. Verify EmailLog record was created
        from api.models import EmailLog
        log = EmailLog.objects.filter(recipient='fadi@cubelogs-tenant.com', template_type='WELCOME').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, 'SENT')  # celery runs synchronously in eager test mode
        self.assertIsNotNone(log.password)


from unittest.mock import patch

class StripeWebhookTests(APITestCase):
    def setUp(self):
        from api.models import Employee
        self.employee = Employee.objects.create_user(
            email='customer@example.com',
            password='password123',
            first_name='Stripe',
            last_name='Customer'
        )

    @patch('stripe.Webhook.construct_event')
    def test_payment_intent_succeeded_direct_topup(self, mock_construct):
        from api.models import Wallet, WalletTransaction
        
        # Mock Stripe event
        mock_construct.return_value = {
            'id': 'evt_123',
            'type': 'payment_intent.succeeded',
            'data': {
                'object': {
                    'id': 'pi_123',
                    'receipt_email': 'customer@example.com',
                    'amount_received': 5000,  # $50.00
                    'charges': {
                        'data': [
                            {
                                'receipt_url': 'https://stripe.com/receipt/123'
                            }
                        ]
                    }
                }
            }
        }
        
        url = '/backoffice/stripe-webhook/'
        headers = {'HTTP_STRIPE_SIGNATURE': 'valid_sig'}
        response = self.client.post(url, data='{}', content_type='application/json', **headers)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify wallet creation and balance
        wallet = Wallet.objects.get(employee=self.employee)
        self.assertEqual(wallet.balance, 50.00)
        
        # Verify transaction
        tx = WalletTransaction.objects.get(wallet=wallet)
        self.assertEqual(tx.amount, 50.00)
        self.assertEqual(tx.transactionType, 'Credit')
        self.assertTrue(tx.success)
        self.assertEqual(tx.stripeEventId, 'evt_123')
        self.assertEqual(tx.receipt_url, 'https://stripe.com/receipt/123')

    @patch('stripe.Webhook.construct_event')
    def test_invoice_paid_subscription_renewal(self, mock_construct):
        from api.models import Wallet, WalletTransaction
        
        # Pre-seed wallet with some balance
        wallet = Wallet.objects.create(employee=self.employee, balance=100.00)
        
        mock_construct.return_value = {
            'id': 'evt_124',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'id': 'in_123',
                    'customer_email': 'customer@example.com',
                    'amount_paid': 2900,  # $29.00
                    'hosted_invoice_url': 'https://stripe.com/invoice/124'
                }
            }
        }
        
        url = '/backoffice/stripe-webhook/'
        headers = {'HTTP_STRIPE_SIGNATURE': 'valid_sig'}
        response = self.client.post(url, data='{}', content_type='application/json', **headers)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify wallet balance decremented
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 71.00)
        
        # Verify transaction
        tx = WalletTransaction.objects.get(wallet=wallet, stripeEventId='evt_124')
        self.assertEqual(tx.amount, 29.00)
        self.assertEqual(tx.transactionType, 'Debit')
        self.assertTrue(tx.success)
        self.assertEqual(tx.receipt_url, 'https://stripe.com/invoice/124')

    @patch('stripe.Webhook.construct_event')
    def test_invoice_payment_failed(self, mock_construct):
        from api.models import Wallet, WalletTransaction
        
        # Pre-seed wallet with some balance
        wallet = Wallet.objects.create(employee=self.employee, balance=100.00)
        
        mock_construct.return_value = {
            'id': 'evt_125',
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'id': 'in_124',
                    'customer_email': 'customer@example.com',
                    'amount_due': 2900,  # $29.00
                }
            }
        }
        
        url = '/backoffice/stripe-webhook/'
        headers = {'HTTP_STRIPE_SIGNATURE': 'valid_sig'}
        response = self.client.post(url, data='{}', content_type='application/json', **headers)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify wallet balance remains unchanged
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 100.00)
        
        # Verify failed transaction created
        tx = WalletTransaction.objects.get(wallet=wallet, stripeEventId='evt_125')
        self.assertEqual(tx.amount, 29.00)
        self.assertEqual(tx.transactionType, 'Debit')
        self.assertFalse(tx.success)


class DynamicSubscriptionTests(APITestCase):
    def setUp(self):
        from api.models import Organization, OrgSettings, Employee
        self.org_settings = OrgSettings.objects.create(max_employees_allowed=10)
        self.org = Organization.objects.create(name="Dynamic Org", subdomain="dynamic", settings=self.org_settings)
        self.superadmin = Employee.objects.create_user(
            email='superadmin@dynamic.com',
            password='password123',
            first_name='Super',
            last_name='Admin',
            isSuperAdmin=True,
            organization=self.org
        )
        self.client.force_authenticate(user=self.superadmin)

    def test_dynamic_checkout_zero_cost(self):
        url = '/api/subscription/dynamic-checkout/'
        payload = {
            'employee_count': 15,
            'addons': []
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('checkoutUrl', response.data)
        self.assertIn('status=success', response.data['checkoutUrl'])

        self.org_settings.refresh_from_db()
        self.assertEqual(self.org_settings.max_employees_allowed, 15)
        self.assertFalse(self.org_settings.is_attendance_enabled)
        self.assertFalse(self.org_settings.is_project_enabled)

    @patch('stripe.checkout.Session.create')
    def test_dynamic_checkout_with_addons(self, mock_session_create):
        class MockSession:
            id = 'sess_123'
            url = 'https://stripe.checkout/sess_123'
        mock_session_create.return_value = MockSession()

        url = '/api/subscription/dynamic-checkout/'
        payload = {
            'employee_count': 10,
            'addons': ['attendance', 'project']
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['checkoutUrl'], 'https://stripe.checkout/sess_123')

    @patch('stripe.Webhook.construct_event')
    def test_dynamic_subscription_webhook(self, mock_construct):
        mock_construct.return_value = {
            'id': 'evt_999',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'sess_123',
                    'mode': 'payment',
                    'customer_email': 'superadmin@dynamic.com',
                    'metadata': {
                        'type': 'dynamic_subscription',
                        'employee_count': '25',
                        'addons': 'attendance',
                        'org_id': str(self.org.id),
                        'total_cost': '2500'
                    }
                }
            }
        }

        url = '/backoffice/stripe-webhook/'
        headers = {'HTTP_STRIPE_SIGNATURE': 'valid_sig'}
        response = self.client.post(url, data='{}', content_type='application/json', **headers)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.org_settings.refresh_from_db()
        self.assertEqual(self.org_settings.max_employees_allowed, 25)
        self.assertTrue(self.org_settings.is_attendance_enabled)
        self.assertFalse(self.org_settings.is_project_enabled)

    @patch('stripe.checkout.Session.retrieve')
    def test_confirm_subscription_success(self, mock_retrieve):
        from api.models import Wallet, WalletTransaction
        class MockSession:
            payment_status = 'paid'
            metadata = {
                'type': 'dynamic_subscription',
                'employee_count': '25',
                'addons': 'attendance',
                'org_id': str(self.org.id),
                'total_cost': '2500'
            }
        mock_retrieve.return_value = MockSession()

        # Create a pending wallet/transaction to confirm
        wallet, _ = Wallet.objects.get_or_create(employee=self.superadmin, defaults={'organization': self.org})
        tx = WalletTransaction.objects.create(
            wallet=wallet,
            amount=2500,
            transactionType='Debit',
            stripe_session_id='sess_confirm_123',
            status='Pending',
            details='Pending dynamic subscription'
        )

        url = '/api/subscription/confirm/'
        response = self.client.post(url, {'session_id': 'sess_confirm_123'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'subscription_success')

        self.org_settings.refresh_from_db()
        self.assertEqual(self.org_settings.max_employees_allowed, 25)
        self.assertTrue(self.org_settings.is_attendance_enabled)
        self.assertFalse(self.org_settings.is_project_enabled)
        self.assertEqual(self.org_settings.subscriptionDays, 30)

        tx.refresh_from_db()
        self.assertEqual(tx.status, 'Success')
        self.assertTrue(tx.success)

    @patch('stripe.checkout.Session.retrieve')
    def test_confirm_wallet_topup_success(self, mock_retrieve):
        from api.models import Wallet, WalletTransaction
        from decimal import Decimal
        
        wallet, _ = Wallet.objects.get_or_create(employee=self.superadmin, defaults={'organization': self.org, 'balance': Decimal('100.00')})
        class MockSession:
            payment_status = 'paid'
            metadata = {
                'type': 'topup',
                'wallet_id': str(wallet.id),
                'amount': '1500'
            }
        mock_retrieve.return_value = MockSession()

        tx = WalletTransaction.objects.create(
            wallet=wallet,
            amount=1500,
            transactionType='Credit',
            stripe_session_id='sess_topup_123',
            status='Pending',
            details='Pending top-up'
        )

        url = '/api/subscription/confirm/'
        response = self.client.post(url, {'session_id': 'sess_topup_123'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'wallet_success')

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('1600.00'))

        tx.refresh_from_db()
        self.assertEqual(tx.status, 'Success')
        self.assertTrue(tx.success)

    def test_sweep_workspace_subscriptions_auto_renew_success(self):
        from api.tasks import sweep_workspace_subscriptions
        from api.models import Wallet, WalletTransaction
        from decimal import Decimal
        from django.utils import timezone
        
        # Set subscriptionExpiresAt to None or past (due for renewal)
        self.org_settings.subscriptionExpiresAt = None
        self.org_settings.is_attendance_enabled = True
        self.org_settings.max_employees_allowed = 10  # 10 * 100 = 1000 cost
        self.org_settings.save()
        
        # Give wallet sufficient balance
        wallet, _ = Wallet.objects.get_or_create(employee=self.superadmin, defaults={'organization': self.org})
        wallet.balance = Decimal('1500.00')
        wallet.save()
        
        # Run sweep task
        sweep_workspace_subscriptions()  # type: ignore
        
        # Verify wallet balance deducted
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('500.00'))
        
        # Verify settings updated
        self.org_settings.refresh_from_db()
        self.assertEqual(self.org_settings.subscriptionDays, 30)
        self.assertEqual(self.org_settings.subscriptionStatus, 'Active')
        self.assertIsNotNone(self.org_settings.subscriptionExpiresAt)
        self.assertTrue(self.org_settings.subscriptionExpiresAt > timezone.now())
        
        # Verify Ledger entry created
        tx = WalletTransaction.objects.filter(wallet=wallet, transactionType='Debit').first()
        self.assertIsNotNone(tx)
        self.assertTrue(tx.success)
        self.assertEqual(tx.status, 'Success')
        self.assertEqual(tx.amount, Decimal('1000.00'))

    def test_sweep_workspace_subscriptions_insufficient_funds(self):
        from api.tasks import sweep_workspace_subscriptions
        from api.models import Wallet, WalletTransaction
        from decimal import Decimal
        
        # Set subscriptionExpiresAt to None (due for renewal)
        self.org_settings.subscriptionExpiresAt = None
        self.org_settings.is_attendance_enabled = True
        self.org_settings.max_employees_allowed = 10  # 1000 cost
        self.org_settings.save()
        
        # Give wallet insufficient balance
        wallet, _ = Wallet.objects.get_or_create(employee=self.superadmin, defaults={'organization': self.org})
        wallet.balance = Decimal('200.00')
        wallet.save()
        
        # Run sweep task
        sweep_workspace_subscriptions()  # type: ignore
        
        # Verify wallet balance not deducted
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('200.00'))
        
        # Verify settings updated to Pending Payment
        self.org_settings.refresh_from_db()
        self.assertEqual(self.org_settings.subscriptionStatus, 'Pending Payment')
        
        # Verify failed Ledger entry created
        tx = WalletTransaction.objects.filter(wallet=wallet, transactionType='Debit').first()
        self.assertIsNotNone(tx)
        self.assertFalse(tx.success)
        self.assertEqual(tx.status, 'Failed')


class BillingAcceleratedTests(APITestCase):
    def setUp(self):
        from api.models import Organization, OrgSettings, Employee, Wallet
        from django.core import mail
        from django.core.cache import cache
        from decimal import Decimal
        mail.outbox = []
        cache.clear()
        
        self.org_settings = OrgSettings.objects.create(
            max_employees_allowed=10,
            is_attendance_enabled=True,
            subscriptionStatus='Active'
        )
        self.org = Organization.objects.create(name="Accelerated Org", subdomain="accel", settings=self.org_settings)
        self.superadmin = Employee.objects.filter(isSuperAdmin=True).first()
        if not self.superadmin:
            self.superadmin = Employee.objects.create_user(
                email='superadmin@accel.com',
                password='password123',
                first_name='Super',
                last_name='Admin',
                isSuperAdmin=True,
                organization=self.org
            )
        else:
            self.superadmin.organization = self.org
            self.superadmin.save()
            
        self.wallet, _ = Wallet.objects.get_or_create(
            employee=self.superadmin,
            defaults={'organization': self.org, 'balance': Decimal('0.00')}
        )
        self.wallet.balance = Decimal('0.00')
        self.wallet.save()
        mail.outbox = []

    def test_accelerated_pipeline_flow(self):
        from api.tasks import sweep_workspace_subscriptions
        from django.utils import timezone
        from django.core import mail
        from datetime import timedelta
        from django.test import override_settings
        from decimal import Decimal
        
        with override_settings(TEST_MODE=True):
            # 1. Minute 0: Usage invoice generated (0 to 119 seconds)
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=10)
            self.org_settings.subscriptionStatus = 'Active'
            self.org_settings.save()
            
            sweep_workspace_subscriptions()
            
            # Verify Email 1 sent
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('Invoice generated', mail.outbox[0].subject)
            
            # 2. Minute 2: Unpaid check & Grace period reminder (120 to 239 seconds)
            mail.outbox = []
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=130)
            self.org_settings.save()
            
            sweep_workspace_subscriptions()
            
            # Verify settings transitioned to Pending Payment
            self.org_settings.refresh_from_db()
            self.assertEqual(self.org_settings.subscriptionStatus, 'Pending Payment')
            # Verify Email 2 sent
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('Grace Period Reminder', mail.outbox[0].subject)
            
            # 3. Minute 4: Final Warning (240 to 359 seconds)
            mail.outbox = []
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=250)
            self.org_settings.save()
            
            sweep_workspace_subscriptions()
            
            # Verify Email 3 sent
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('FINAL WARNING', mail.outbox[0].subject)
            
            # 4. Test Scenario A: Top up wallet at Minute 5:30 (330 seconds)
            # Make sure balance is sufficient
            self.wallet.balance = Decimal('2000.00')
            self.wallet.save()
            
            mail.outbox = []
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=330)
            self.org_settings.save()
            
            sweep_workspace_subscriptions()
            
            # Verify wallet balance deducted (10 employees * 100 cost = 1000 INR)
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, Decimal('1000.00'))
            # Verify workspace status reset to Active
            self.org_settings.refresh_from_db()
            self.assertEqual(self.org_settings.subscriptionStatus, 'Active')
            # Check cycle reset (renewed time updated to near now)
            self.assertTrue((timezone.now() - self.org_settings.subscriptionRenewedAt).total_seconds() < 5)
            # Verify activation email sent
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('Subscription Paid & Activated', mail.outbox[0].subject)
            
            # 5. Test Scenario B: Leave wallet empty at Minute 6:00 (360 seconds)
            # Set wallet balance back to 0
            self.wallet.balance = Decimal('0.00')
            self.wallet.save()
            # Start a new cycle and wait for 6 minutes (370 seconds)
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=370)
            self.org_settings.subscriptionStatus = 'Pending Payment'
            self.org_settings.save()
            
            mail.outbox = []
            sweep_workspace_subscriptions()
            
            # Verify workspace transitions immediately to Suspended
            self.org_settings.refresh_from_db()
            self.assertEqual(self.org_settings.subscriptionStatus, 'Suspended')
            # Verify suspension email dispatched
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('Workspace Suspended', mail.outbox[0].subject)
            
            # 6. Minute 8: Data maintenance rent simulation (480 seconds)
            mail.outbox = []
            self.org_settings.subscriptionRenewedAt = timezone.now() - timedelta(seconds=490)
            self.org_settings.save()
            
            sweep_workspace_subscriptions()
            
            # Verify Monthly Data Maintenance Rent Invoice email dispatched
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn('Data Maintenance Rent Invoice', mail.outbox[0].subject)


class EmployeeBulkUploadTests(APITestCase):
    def setUp(self):
        from api.models import Organization, OrgSettings, Employee, Template, Schedule
        from django.core import mail
        mail.outbox = []

        self.org_settings = OrgSettings.objects.create(
            max_employees_allowed=10,
            is_attendance_enabled=True,
            subscriptionStatus='Active'
        )
        self.org = Organization.objects.create(name="Bulk Org", subdomain="bulk", settings=self.org_settings)
        self.admin = Employee.objects.filter(isSuperAdmin=True).first()
        if not self.admin:
            self.admin = Employee.objects.create_user(
                email='admin@bulk.com',
                password='password123',
                first_name='Admin',
                last_name='User',
                isSuperAdmin=True,
                organization=self.org
            )
        else:
            self.admin.organization = self.org
            self.admin.save()
            
        self.client.force_authenticate(user=self.admin)

        # Create valid template/roles
        Template.objects.get_or_create(name='Developer', defaults={'permissions': []})
        Schedule.objects.get_or_create(designation='Admin', defaults={'shiftStart': '09:00', 'shiftEnd': '17:00'})

    def test_bulk_upload_partial_success_and_smtp_failure(self):
        import pandas as pd
        import io
        import django.core.mail
        from unittest.mock import patch
        from api.models import Employee
        from django.core.files.uploadedfile import SimpleUploadedFile
        from decimal import Decimal

        # 1. Prepare sample sheet contents in memory (DataFrame to Excel)
        df_data = {
            'Full Name': ['Alice Green', 'Bob Smith', 'Charlie Brown', 'Invalid Designation', 'Duplicate Email', 'Invalid Phone'],
            'Email Address': ['alice@bulk.com', 'bob@bulk.com', 'charlie@bulk.com', 'valid@bulk.com', 'admin@bulk.com', 'invalidphone@bulk.com'],
            'Phone Number': ['1234567890', '9876543210', '5556667777', '1112223333', '9998887777', 'abcd123'],
            'Designation Role(s)': ['Developer', 'Admin', 'Developer', 'NonExistentRole', 'Admin', 'Admin']
        }
        df = pd.DataFrame(df_data)
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False)
        excel_buffer.seek(0)

        uploaded_file = SimpleUploadedFile(
            "employees.xlsx",
            excel_buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Mock send_mail: we want Bob Smith's email to throw an SMTP delivery exception
        # to test the SMTP credential-delivery failure rule!
        def mock_send_mail(subject, message, from_email, recipient_list, *args, **kwargs):
            if 'bob@bulk.com' in recipient_list:
                raise Exception("SMTP Connection Timeout")
            # For others, use normal mocked behavior
            from django.core.mail import outbox
            from django.core.mail import EmailMessage
            msg = EmailMessage(subject, message, from_email, recipient_list)
            outbox.append(msg)
            return 1

        url = '/api/employees/bulk-upload/'
        
        with patch('django.core.mail.send_mail', side_effect=mock_send_mail):
            response = self.client.post(url, {'file': uploaded_file}, format='multipart')
            
        self.assertEqual(response.status_code, 200)
        res_data = response.data
        
        # Verify summaries
        self.assertTrue(res_data['success'])
        self.assertEqual(res_data['total_processed'], 6)
        
        # Successfully inserted: Alice Green and Charlie Brown
        self.assertEqual(res_data['inserted_count'], 2)
        self.assertEqual(res_data['failed_count'], 4)
        
        # Check specific failure reasons:
        failures = res_data['failures']
        
        self.assertEqual(failures[0]['row'], 3)
        self.assertEqual(failures[0]['email'], 'bob@bulk.com')
        self.assertEqual(failures[0]['reason'], 'SMTP Mail Delivery Failed')
        
        self.assertEqual(failures[1]['row'], 5)
        self.assertEqual(failures[1]['email'], 'valid@bulk.com')
        self.assertEqual(failures[1]['reason'], "Designation Role 'NonExistentRole' does not exist")
        
        self.assertEqual(failures[2]['row'], 6)
        self.assertEqual(failures[2]['email'], 'admin@bulk.com')
        self.assertEqual(failures[2]['reason'], 'Email Already Exists')
        
        self.assertEqual(failures[3]['row'], 7)
        self.assertEqual(failures[3]['email'], 'invalidphone@bulk.com')
        self.assertEqual(failures[3]['reason'], 'Invalid Phone Number')
        
        # Assert database check
        # Alice Green and Charlie Brown are in DB
        self.assertTrue(Employee.objects.filter(email='alice@bulk.com').exists())
        self.assertTrue(Employee.objects.filter(email='charlie@bulk.com').exists())
        
        # Bob Smith must have been deleted from DB due to SMTP mail error
        self.assertFalse(Employee.objects.filter(email='bob@bulk.com').exists())

    def test_bulk_upload_records_password_in_email_log(self):
        import pandas as pd
        import io
        from api.models import Employee, EmailLog
        from django.core.files.uploadedfile import SimpleUploadedFile

        df_data = {
            'Full Name': ['Alice Green'],
            'Email Address': ['alice@bulk.com'],
            'Phone Number': ['1234567890'],
            'Designation Role(s)': ['Developer']
        }
        df = pd.DataFrame(df_data)
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False)
        excel_buffer.seek(0)

        uploaded_file = SimpleUploadedFile(
            "employees.xlsx",
            excel_buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        url = '/api/employees/bulk-upload/'
        response = self.client.post(url, {'file': uploaded_file}, format='multipart')
        self.assertEqual(response.status_code, 200)

        # Check that EmailLog exists for alice@bulk.com with WELCOME type
        log = EmailLog.objects.filter(recipient='alice@bulk.com', template_type='WELCOME').first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.password)
        self.assertTrue(len(log.password) > 0)
        self.assertIn(log.password, log.html_content)

    def test_employee_raw_password_sync(self):
        from api.models import Employee
        
        # Test creation sets raw_password
        emp = Employee.objects.create_user(
            email='test_sync@example.com',
            password='InitialPassword123'
        )
        self.assertEqual(emp.raw_password, 'InitialPassword123')
        
        # Test change password updates raw_password
        emp.set_password('NewPassword456')
        emp.save()
        
        # Reload from DB and verify
        emp.refresh_from_db()
        self.assertEqual(emp.raw_password, 'NewPassword456')


class CompanyRegistrationTests(APITestCase):
    def setUp(self):
        from django.core import mail
        from api.models import Employee
        self.operator = Employee.objects.create_user(
            email='operator@cubelogs.com',
            password='Password123',
            isSuperAdmin=True
        )
        mail.outbox = []
        self.client.force_login(self.operator)

    def test_backoffice_register_company_sends_email_and_logs(self):
        from django.core import mail
        from api.models import EmailLog

        url = '/api/register-company/'
        payload = {
            'companyName': 'Acme Corp',
            'adminFullName': 'Acme Owner',
            'adminEmail': 'owner@acme.com',
            'adminPhone': '+15555555',
            'packageName': 'Starter, Attendance'
        }
        
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify exactly 1 email sent in outbox
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['owner@acme.com'])
        self.assertIn('Welcome to CubeLogs - Your Workspace is Ready!', email.subject)
        
        # Verify exactly 1 EmailLog record was created
        logs = EmailLog.objects.filter(recipient='owner@acme.com', template_type='WELCOME')
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.status, 'SENT')
        self.assertEqual(log.password, 'Welcome@123')









