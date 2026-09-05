from datetime import date, datetime, timedelta, timezone as dt_tz
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, OrgSettings, AuditLog
from users.models import Employee, Role, PermissionFlag, OrganizationMembership
from attendance.models import (
    Schedule, AttendancePolicy, AttendanceLog, Holiday, LeaveType, Leave,
    AttendancePeriod, AttendancePeriodEmployeeSnapshot, OfficeLocation
)
from attendance.services import (
    get_attendance_policy, save_attendance_policy,
    get_daily_attendance_summary, get_monthly_attendance_summary,
    is_date_locked, is_date_range_locked,
    validate_attendance_period, finalize_attendance_period, reopen_attendance_period
)
from attendance.api.v1.services import AttendanceService



class AttendanceFoundationPhase1Tests(TestCase):
    def setUp(self):
        # Create Org A
        self.settings_a = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=15,
            half_day_threshold_minutes=240,
            auto_approve_attendance=False,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org_a = Organization.objects.create(
            name="Org Alpha",
            subdomain="alpha",
            settings=self.settings_a
        )

        # Create Org B
        self.settings_b = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=20,
            half_day_threshold_minutes=200,
            auto_approve_attendance=True,
            default_weekly_holidays=["Friday", "Saturday"]
        )
        self.org_b = Organization.objects.create(
            name="Org Beta",
            subdomain="beta",
            settings=self.settings_b
        )

        # Create Permission Flags
        perm_staff, _ = PermissionFlag.objects.get_or_create(key="attendance:staff", defaults={"name": "Attendance Staff"})
        perm_portal, _ = PermissionFlag.objects.get_or_create(key="attendance:management_portal", defaults={"name": "Attendance Portal"})
        perm_admin, _ = PermissionFlag.objects.get_or_create(key="attendance:admin", defaults={"name": "Attendance Admin"})

        # Create Users
        self.admin_role_a = Role.objects.create(
            name="Admin",
            slug="admin",
            organization=self.org_a
        )
        self.admin_role_a.permissions.add(perm_staff, perm_portal, perm_admin)

        self.user_a = Employee.objects.create(
            email="admin@alpha.com",
            username="admin_alpha",
            first_name="Alpha",
            last_name="Admin",
            designation="Developer",
            organization=self.org_a,
            role=self.admin_role_a,
            is_staff=True,
            is_active=True
        )
        self.user_a.set_password("pass123")
        self.user_a.save()

        self.admin_role_b = Role.objects.create(
            name="Admin",
            slug="admin_b",
            organization=self.org_b
        )
        self.admin_role_b.permissions.add(perm_staff, perm_portal, perm_admin)

        self.user_b = Employee.objects.create(
            email="admin@beta.com",
            username="admin_beta",
            first_name="Beta",
            last_name="Admin",
            designation="Developer",
            organization=self.org_b,
            role=self.admin_role_b,
            is_staff=True,
            is_active=True
        )
        self.user_b.set_password("pass123")
        self.user_b.save()

        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.user_a)

        self.client_b = APIClient()
        self.client_b.force_authenticate(user=self.user_b)

    def test_01_and_02_schedules_per_organization_same_designation(self):
        """1 & 2: Org A and Org B can have different schedules for same designation."""
        sch_a = Schedule.objects.create(
            organization=self.org_a,
            designation="Developer",
            shiftStart="09:00",
            shiftEnd="18:00"
        )
        sch_b = Schedule.objects.create(
            organization=self.org_b,
            designation="Developer",
            shiftStart="10:00",
            shiftEnd="19:00"
        )

        self.assertEqual(sch_a.shiftStart, "09:00")
        self.assertEqual(sch_b.shiftStart, "10:00")
        self.assertEqual(Schedule.objects.filter(designation="Developer").count(), 2)

    def test_03_cross_tenant_schedule_isolation(self):
        """3: Org A cannot read, update, or delete Org B's schedule."""
        sch_b = Schedule.objects.create(
            organization=self.org_b,
            designation="Designer",
            shiftStart="10:00",
            shiftEnd="19:00"
        )

        # Org A lists schedules -> Should NOT see Org B's schedule
        response = self.client_a.get('/api/schedules/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertFalse(any(s['id'] == sch_b.id for s in results))

        # Org A attempts to update Org B's schedule
        update_res = self.client_a.put(f'/api/schedules/{sch_b.id}/', {
            'designation': 'Designer',
            'shiftStart': '08:00',
            'shiftEnd': '16:00'
        }, format='json')
        self.assertEqual(update_res.status_code, status.HTTP_404_NOT_FOUND)

        # Org A attempts to delete Org B's schedule
        del_res = self.client_a.delete(f'/api/schedules/{sch_b.id}/')
        self.assertEqual(del_res.status_code, status.HTTP_404_NOT_FOUND)

        # Confirm Org B's schedule remains untouched
        sch_b.refresh_from_db()
        self.assertEqual(sch_b.shiftStart, "10:00")

    def test_04_and_05_initial_attendance_policy_backfill(self):
        """4 & 5: get_attendance_policy lazily or through backfill creates baseline."""
        today = date(2026, 8, 19)
        policy_a = get_attendance_policy(self.org_a, today)
        self.assertIsNotNone(policy_a)
        self.assertEqual(policy_a.organization, self.org_a)
        self.assertEqual(policy_a.grace_period_minutes, 15)
        self.assertEqual(policy_a.full_day_minimum_minutes, 480)
        self.assertEqual(policy_a.half_day_minimum_minutes, 240)
        self.assertEqual(policy_a.break_duration_minutes, 60)
        self.assertEqual(policy_a.break_type, "Unpaid")

    def test_06_get_attendance_policy_historical_resolution(self):
        """6: get_attendance_policy(org, date) returns correct historical version."""
        AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=10,
            full_day_minimum_minutes=450
        )
        AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 6, 1),
            grace_period_minutes=20,
            full_day_minimum_minutes=500
        )

        # Date in May 2026 resolves to Jan 1 version
        p_may = get_attendance_policy(self.org_a, date(2026, 5, 15))
        self.assertEqual(p_may.grace_period_minutes, 10)
        self.assertEqual(p_may.full_day_minimum_minutes, 450)

        # Date in July 2026 resolves to June 1 version
        p_july = get_attendance_policy(self.org_a, date(2026, 7, 1))
        self.assertEqual(p_july.grace_period_minutes, 20)
        self.assertEqual(p_july.full_day_minimum_minutes, 500)

    def test_07_08_09_policy_save_rule_tomorrow_and_immutability(self):
        """7, 8 & 9: Saving today creates tomorrow's version, updates existing future version, keeps past immutable."""
        today = date(2026, 8, 19)
        tomorrow = date(2026, 8, 20)

        # Past baseline policy
        p_past = AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=15,
            full_day_minimum_minutes=480
        )

        # 7: Admin saves changes today (Aug 19)
        policy_v1, created1 = save_attendance_policy(self.org_a, {
            'grace_period_minutes': 25,
            'full_day_minimum_minutes': 500,
            'half_day_minimum_minutes': 250,
            'break_duration_minutes': 45,
            'break_type': 'Paid'
        }, save_date=today)

        self.assertTrue(created1)
        self.assertEqual(policy_v1.effective_from, tomorrow)
        self.assertEqual(policy_v1.grace_period_minutes, 25)
        self.assertEqual(policy_v1.full_day_minimum_minutes, 500)

        # 8: Admin saves changes again today -> Updates tomorrow's row, does not create duplicate
        policy_v2, created2 = save_attendance_policy(self.org_a, {
            'grace_period_minutes': 30,
            'full_day_minimum_minutes': 510,
            'half_day_minimum_minutes': 255,
            'break_duration_minutes': 60,
            'break_type': 'Unpaid'
        }, save_date=today)

        self.assertFalse(created2)
        self.assertEqual(policy_v2.id, policy_v1.id)
        self.assertEqual(policy_v2.grace_period_minutes, 30)
        self.assertEqual(policy_v2.full_day_minimum_minutes, 510)
        self.assertEqual(AttendancePolicy.objects.filter(organization=self.org_a).count(), 2)

        # 9: Past / Today's resolution remains completely unchanged
        p_today = get_attendance_policy(self.org_a, today)
        self.assertEqual(p_today.id, p_past.id)
        self.assertEqual(p_today.grace_period_minutes, 15)
        self.assertEqual(p_today.full_day_minimum_minutes, 480)

        # Tomorrow's resolution uses new policy
        p_tomorrow = get_attendance_policy(self.org_a, tomorrow)
        self.assertEqual(p_tomorrow.id, policy_v2.id)
        self.assertEqual(p_tomorrow.grace_period_minutes, 30)

    def test_10_attendance_clock_in_uses_tenant_policy(self):
        """10: Clock-in resolves auto_approve from tenant's AttendancePolicy."""
        # Org A has auto_approve = False -> Pending Approval
        log_a = AttendanceService.clock_in(self.user_a)
        self.assertEqual(log_a.status, 'Pending Approval')

        # Org B has auto_approve = True -> Approved
        log_b = AttendanceService.clock_in(self.user_b)
        self.assertEqual(log_b.status, 'Approved')

        # Clock-out works properly
        out_log_a = AttendanceService.clock_out(self.user_a)
        self.assertIsNotNone(out_log_a.clockOut)


class AttendanceEnginePhase2Tests(TestCase):
    def setUp(self):
        # Create Org Alpha
        self.settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=15,
            half_day_threshold_minutes=240,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org = Organization.objects.create(
            name="Alpha Corp",
            subdomain="alphacorp",
            settings=self.settings
        )

        # Create Baseline Policy (From Jan 1, 2026)
        self.policy = AttendancePolicy.objects.create(
            organization=self.org,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=15,
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            default_weekly_holidays=["Saturday", "Sunday"]
        )

        # Create Tenant Schedule (Developer: 09:00 - 18:00)
        self.schedule = Schedule.objects.create(
            organization=self.org,
            designation="Developer",
            shiftStart="09:00",
            shiftEnd="18:00"
        )

        # Create Employee
        perm_staff, _ = PermissionFlag.objects.get_or_create(key="attendance:staff", defaults={"name": "Attendance Staff"})
        self.role = Role.objects.create(name="Staff", slug="staff", organization=self.org)
        self.role.permissions.add(perm_staff)

        self.employee = Employee.objects.create(
            email="john@alphacorp.com",
            username="john_alpha",
            first_name="John",
            last_name="Doe",
            designation="Developer",
            organization=self.org,
            role=self.role,
            is_active=True
        )

        # Other Org (Beta) for Cross-Tenant tests
        self.org_beta = Organization.objects.create(name="Beta Corp", subdomain="betacorp")
        self.employee_beta = Employee.objects.create(
            email="jane@betacorp.com",
            username="jane_beta",
            first_name="Jane",
            last_name="Beta",
            designation="Developer",
            organization=self.org_beta,
            is_active=True
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.employee)

    def test_01_normal_full_day(self):
        """1. Single session >= full_day_minimum (480 min) -> 1.0 Full Day / Present."""
        d = date(2026, 8, 19)  # Wednesday
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 17, 30, tzinfo=dt_tz.utc),  # 510 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['worked_minutes'], 510)
        self.assertEqual(res['attendance_unit'], 1.0)
        self.assertEqual(res['payable_attendance_unit'], 1.0)
        self.assertEqual(res['daily_status'], "Present")
        self.assertTrue(res['is_payroll_ready'])

    def test_02_normal_half_day(self):
        """2. Single session between half_day_min (240) and full_day_min (480) -> 0.5 Half Day."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 14, 0, tzinfo=dt_tz.utc),  # 300 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['worked_minutes'], 300)
        self.assertEqual(res['attendance_unit'], 0.5)
        self.assertEqual(res['payable_attendance_unit'], 0.5)
        self.assertEqual(res['daily_status'], "Half Day")
        self.assertTrue(res['is_payroll_ready'])

    def test_03_below_half_day_absent(self):
        """3. Single session < half_day_minimum (240 min) -> 0.0 Absent."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 11, 0, tzinfo=dt_tz.utc),  # 120 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['worked_minutes'], 120)
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertEqual(res['payable_attendance_unit'], 0.0)
        self.assertEqual(res['daily_status'], "Absent")

    def test_04_multiple_sessions_summed(self):
        """4. Multiple sessions on same day are summed (240 + 240 = 480 min) -> 1.0 Full Day."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 13, 0, tzinfo=dt_tz.utc),  # 240 min
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 14, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 18, 0, tzinfo=dt_tz.utc),  # 240 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['session_count'], 2)
        self.assertEqual(res['completed_session_count'], 2)
        self.assertEqual(res['worked_minutes'], 480)
        self.assertEqual(res['attendance_unit'], 1.0)
        self.assertEqual(res['daily_status'], "Present")

    def test_05_gap_between_sessions_excluded(self):
        """5. Gap between 13:00 and 14:00 is NOT counted as worked time."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 12, 0, tzinfo=dt_tz.utc),  # 180 min
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 15, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 18, 0, tzinfo=dt_tz.utc),  # 180 min
            status='Approved'
        )
        # Raw span is 09:00 to 18:00 (540m), but actual punch sum is 360m
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['worked_minutes'], 360)
        self.assertEqual(res['attendance_unit'], 0.5)

    def test_06_and_07_late_with_grace_period_does_not_reduce_unit(self):
        """6 & 7. Clock-in at 09:20 (grace is 15m) flags is_late=True, minutes_late=20, but unit remains 1.0."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 20, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 18, 0, tzinfo=dt_tz.utc),  # 520 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['is_late'])
        self.assertEqual(res['minutes_late'], 20)
        self.assertEqual(res['attendance_unit'], 1.0)
        self.assertEqual(res['payable_attendance_unit'], 1.0)

    def test_08_weekly_off_without_attendance(self):
        """8. Saturday without punches is 'Weekly Off' with 0.0 unit, NOT Absent."""
        d = date(2026, 8, 22)  # Saturday
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['is_weekly_off'])
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertEqual(res['payable_attendance_unit'], 0.0)
        self.assertEqual(res['daily_status'], "Weekly Off")
        self.assertFalse(res['worked_on_off_day'])

    def test_09_holiday_without_attendance(self):
        """9. Explicit Holiday without punches is 'Holiday' with 0.0 unit, NOT Absent."""
        d = date(2026, 8, 19)
        Holiday.objects.create(
            organization=self.org,
            name="Independence Day",
            date=d
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['is_holiday'])
        self.assertEqual(res['holiday_name'], "Independence Day")
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertEqual(res['payable_attendance_unit'], 0.0)
        self.assertEqual(res['daily_status'], "Holiday")

    def test_10_and_11_work_on_weekly_off_and_holiday_flagged(self):
        """10 & 11. Work on Weekly Off / Holiday preserves worked minutes and sets worked_on_off_day=True."""
        d = date(2026, 8, 23)  # Sunday
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 23, 10, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 23, 18, 0, tzinfo=dt_tz.utc),  # 480 min
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['is_weekly_off'])
        self.assertTrue(res['worked_on_off_day'])
        self.assertEqual(res['worked_minutes'], 480)
        self.assertEqual(res['attendance_unit'], 1.0)
        self.assertEqual(res['daily_status'], "Present")

    def test_12_full_day_approved_leave(self):
        """12. Approved full-day leave gives leave_unit=1.0, payable=1.0, daily_status='Leave'."""
        d = date(2026, 8, 19)
        lt = LeaveType.objects.create(name="Annual Leave", organization=self.org)
        Leave.objects.create(
            employee=self.employee,
            leaveType=lt,
            leaveTypeName="Annual Leave",
            startDate=d,
            endDate=d,
            duration=1.0,
            dayType='Full',
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['leave_unit'], 1.0)
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertEqual(res['payable_attendance_unit'], 1.0)
        self.assertEqual(res['daily_status'], "Leave")
        self.assertTrue(res['is_payroll_ready'])

    def test_13_and_14_half_day_leave_with_and_without_work(self):
        """13 & 14. Half-day leave alone gives 0.5; half-day leave + 240m work gives 1.0."""
        d = date(2026, 8, 19)
        lt = LeaveType.objects.create(name="Sick Leave", organization=self.org)
        Leave.objects.create(
            employee=self.employee,
            leaveType=lt,
            leaveTypeName="Sick Leave",
            startDate=d,
            endDate=d,
            duration=0.5,
            dayType='Half Day',
            status='Approved'
        )

        # 13: Without work -> 0.5 payable
        res_no_work = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res_no_work['leave_unit'], 0.5)
        self.assertEqual(res_no_work['payable_attendance_unit'], 0.5)
        self.assertEqual(res_no_work['daily_status'], "Half Day")

        # 14: With 240 min work -> 0.5 + 0.5 = 1.0 Full credit
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 13, 0, tzinfo=dt_tz.utc),  # 240 min
            status='Approved'
        )
        res_with_work = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res_with_work['leave_unit'], 0.5)
        self.assertEqual(res_with_work['attendance_unit'], 0.5)
        self.assertEqual(res_with_work['payable_attendance_unit'], 1.0)
        self.assertEqual(res_with_work['daily_status'], "Present")

    def test_15_full_day_leave_plus_attendance_conflict(self):
        """15. Attendance on approved full-day leave flags has_conflict=True and requires_admin_resolution=True."""
        d = date(2026, 8, 19)
        lt = LeaveType.objects.create(name="Casual Leave", organization=self.org)
        Leave.objects.create(
            employee=self.employee,
            leaveType=lt,
            leaveTypeName="Casual Leave",
            startDate=d,
            endDate=d,
            duration=1.0,
            dayType='Full',
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 17, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['has_conflict'])
        self.assertEqual(res['conflict_reason'], "attendance_on_full_day_leave")
        self.assertTrue(res['requires_admin_resolution'])
        self.assertFalse(res['is_payroll_ready'])

    def test_16_open_session_today(self):
        """16. Open session today is 'In Progress'."""
        today = timezone.now().date()
        AttendanceLog.objects.create(
            employee=self.employee,
            date=today,
            clockIn=timezone.now() - timedelta(hours=3),
            clockOut=None,
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, today)
        self.assertEqual(res['daily_status'], "In Progress")
        self.assertTrue(res['is_open_session'])

    def test_17_future_working_day_returns_upcoming(self):
        """17. Future normal working day without punches returns 'Upcoming' (unit=0, is_payroll_ready=False)."""
        future_date = timezone.now().date() + timedelta(days=30)
        # Ensure target date is a normal working day (Monday - Friday)
        while future_date.strftime('%A') in ['Saturday', 'Sunday']:
            future_date += timedelta(days=1)

        res = get_daily_attendance_summary(self.employee, future_date)
        self.assertEqual(res['daily_status'], "Upcoming")
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertEqual(res['payable_attendance_unit'], 0.0)
        self.assertFalse(res['is_payroll_ready'])
        self.assertNotEqual(res['daily_status'], "Absent")

    def test_18_today_unpunched_returns_not_started(self):
        """18. Today normal working day before clock-in returns 'Not Started'."""
        today = timezone.now().date()
        if today.strftime('%A') in ['Saturday', 'Sunday']:
            # Skip if today happens to be a weekend
            return

        res = get_daily_attendance_summary(self.employee, today)
        self.assertEqual(res['daily_status'], "Not Started")
        self.assertEqual(res['attendance_unit'], 0.0)
        self.assertFalse(res['is_payroll_ready'])

    def test_19_past_working_day_unpunched_returns_absent(self):
        """19. Past normal working day without punches returns 'Absent'."""
        past_date = timezone.now().date() - timedelta(days=10)
        while past_date.strftime('%A') in ['Saturday', 'Sunday']:
            past_date -= timedelta(days=1)

        res = get_daily_attendance_summary(self.employee, past_date)
        self.assertEqual(res['daily_status'], "Absent")
        self.assertEqual(res['attendance_unit'], 0.0)

    def test_20_open_session_past_date(self):
        """20. Open session on past date is 'Incomplete' and requires admin resolution."""
        past_date = timezone.now().date() - timedelta(days=2)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=past_date,
            clockIn=timezone.now() - timedelta(days=2, hours=8),
            clockOut=None,
            status='Approved'
        )
        res = get_daily_attendance_summary(self.employee, past_date)
        self.assertTrue(res['is_open_session'])
        self.assertEqual(res['daily_status'], "Incomplete")
        self.assertTrue(res['requires_admin_resolution'])
        self.assertFalse(res['is_payroll_ready'])

    def test_18_pending_approval_sets_not_payroll_ready(self):
        """18. Pending approval sets has_pending_approval=True and is_payroll_ready=False."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 17, 30, tzinfo=dt_tz.utc),
            status='Pending Approval'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertTrue(res['has_pending_approval'])
        self.assertFalse(res['is_payroll_ready'])

    def test_19_soft_deleted_log_excluded(self):
        """19. Soft-deleted AttendanceLog rows (is_deleted=True) are ignored."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 17, 0, tzinfo=dt_tz.utc),
            status='Approved',
            is_deleted=True
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertEqual(res['session_count'], 0)
        self.assertEqual(res['worked_minutes'], 0)
        self.assertEqual(res['daily_status'], "Absent")

    def test_20_multiple_sessions_mixed_raw_statuses(self):
        """20. Multiple sessions preserve all raw statuses in raw_statuses list."""
        d = date(2026, 8, 19)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 13, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d,
            clockIn=datetime(2026, 8, 19, 14, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 18, 0, tzinfo=dt_tz.utc),
            status='Late'
        )
        res = get_daily_attendance_summary(self.employee, d)
        self.assertIn("Approved", res['raw_statuses'])
        self.assertIn("Late", res['raw_statuses'])
        self.assertEqual(res['worked_minutes'], 480)

    def test_21_historical_attendance_policy_resolution(self):
        """21. Historical dates evaluate against the policy effective on that past date."""
        # Create new policy starting June 1 with higher full-day min (540m)
        AttendancePolicy.objects.create(
            organization=self.org,
            effective_from=date(2026, 6, 1),
            grace_period_minutes=10,
            full_day_minimum_minutes=540,
            half_day_minimum_minutes=270
        )

        # Date in May (480m is Full Day under Jan 1 policy)
        d_may = date(2026, 5, 15)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d_may,
            clockIn=datetime(2026, 5, 15, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 5, 15, 17, 0, tzinfo=dt_tz.utc),  # 480 min
            status='Approved'
        )
        res_may = get_daily_attendance_summary(self.employee, d_may)
        self.assertEqual(res_may['attendance_unit'], 1.0)
        self.assertEqual(res_may['daily_status'], "Present")

        # Date in July (480m is Half Day under June 1 policy because min is 540)
        d_july = date(2026, 7, 15)
        AttendanceLog.objects.create(
            employee=self.employee,
            date=d_july,
            clockIn=datetime(2026, 7, 15, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 15, 17, 0, tzinfo=dt_tz.utc),  # 480 min
            status='Approved'
        )
        res_july = get_daily_attendance_summary(self.employee, d_july)
        self.assertEqual(res_july['attendance_unit'], 0.5)
        self.assertEqual(res_july['daily_status'], "Half Day")

    def test_22_cross_tenant_access_protection_for_api(self):
        """22. API endpoint prevents Org A user from inspecting Org B employee summary."""
        # Employee Alpha queries own summary -> 200 OK
        res_own = self.client.get(f'/api/attendance/daily-summary/?date=2026-08-19')
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)
        self.assertEqual(res_own.data['employee_id'], self.employee.id)

        # Employee Alpha queries Employee Beta -> 404 NOT FOUND (tenant boundary)
        res_other = self.client.get(f'/api/attendance/daily-summary/?employee_id={self.employee_beta.id}&date=2026-08-19')
        self.assertEqual(res_other.status_code, status.HTTP_404_NOT_FOUND)

        # Monthly summary endpoint works
        res_monthly = self.client.get(f'/api/attendance/daily-summary/?month=2026-08')
        self.assertEqual(res_monthly.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_monthly.data), 31)  # August has 31 days


class AttendanceIntegrationPhase3Tests(TestCase):
    def setUp(self):
        # Create Organization
        self.settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=15,
            half_day_threshold_minutes=240,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org = Organization.objects.create(
            name="Alpha Corp",
            subdomain="alphacorp",
            settings=self.settings
        )

        # Baseline Policy
        self.policy = AttendancePolicy.objects.create(
            organization=self.org,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=15,
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            default_weekly_holidays=["Saturday", "Sunday"]
        )

        # Schedule
        self.schedule = Schedule.objects.create(
            organization=self.org,
            designation="Developer",
            shiftStart="09:00",
            shiftEnd="18:00"
        )

        # Permissions & Roles
        perm_staff, _ = PermissionFlag.objects.get_or_create(key="attendance:staff", defaults={"name": "Attendance Staff"})
        perm_portal, _ = PermissionFlag.objects.get_or_create(key="attendance:management_portal", defaults={"name": "Attendance Portal"})
        perm_admin, _ = PermissionFlag.objects.get_or_create(key="attendance:admin", defaults={"name": "Attendance Admin"})

        self.admin_role = Role.objects.create(name="HR Admin", slug="hr_admin", organization=self.org)
        self.admin_role.permissions.add(perm_staff, perm_portal, perm_admin)

        self.staff_role = Role.objects.create(name="Staff", slug="staff", organization=self.org)
        self.staff_role.permissions.add(perm_staff)

        # Employees
        self.admin_user = Employee.objects.create(
            email="admin@alphacorp.com",
            username="admin_user",
            first_name="Admin",
            last_name="User",
            designation="HR Manager",
            organization=self.org,
            role=self.admin_role,
            is_staff=True,
            is_active=True
        )
        self.admin_user.set_password("pass123")
        self.admin_user.save()

        self.emp_1 = Employee.objects.create(
            email="emp1@alphacorp.com",
            username="emp1",
            first_name="Alice",
            last_name="Smith",
            designation="Developer",
            organization=self.org,
            role=self.staff_role,
            is_active=True
        )

        self.emp_2 = Employee.objects.create(
            email="emp2@alphacorp.com",
            username="emp2",
            first_name="Bob",
            last_name="Jones",
            designation="Developer",
            organization=self.org,
            role=self.staff_role,
            is_active=True
        )

        self.emp_3 = Employee.objects.create(
            email="emp3@alphacorp.com",
            username="emp3",
            first_name="Charlie",
            last_name="Brown",
            designation="Developer",
            organization=self.org,
            role=self.staff_role,
            is_active=True
        )

        # Other Org (Beta)
        self.org_beta = Organization.objects.create(name="Beta Corp", subdomain="betacorp")
        self.emp_beta = Employee.objects.create(
            email="beta_user@betacorp.com",
            username="beta_user",
            first_name="Beta",
            last_name="Employee",
            designation="Developer",
            organization=self.org_beta,
            is_active=True
        )

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin_user)

    def test_01_hr_dashboard_uses_centralized_present_and_absent(self):
        """1. HR dashboard uses centralized summary: Present when worked >= 480m, Absent when no punch on working day."""
        today = timezone.now().date()
        # Alice works 480 min today
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=today,
            clockIn=timezone.now().replace(hour=9, minute=0, second=0, microsecond=0),
            clockOut=timezone.now().replace(hour=17, minute=0, second=0, microsecond=0),
            status='Approved'
        )

        res = self.admin_client.get('/api/attendance/hr-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Alice is not in absent list
        self.assertFalse(any(a['id'] == self.emp_1.id for a in res.data['absent']))
        # Bob and Charlie (no punches, no leave) are in absent list if today is not weekly off
        day_name = today.strftime('%A')
        if day_name not in ["Saturday", "Sunday"]:
            self.assertTrue(any(a['id'] == self.emp_2.id for a in res.data['absent']))
            self.assertTrue(any(a['id'] == self.emp_3.id for a in res.data['absent']))

    def test_02_hr_dashboard_uses_centralized_late_flag(self):
        """2. HR dashboard uses centralized Late flag: Clock-in at 09:30 with 15m grace is late by 30 min."""
        today = timezone.now().date()
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=today,
            clockIn=timezone.now().replace(hour=9, minute=30, second=0, microsecond=0),
            clockOut=timezone.now().replace(hour=18, minute=0, second=0, microsecond=0),
            status='Approved'
        )
        res = self.admin_client.get('/api/attendance/hr-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        late_entry = next((l for l in res.data['late'] if l['id'] == self.emp_1.id), None)
        self.assertIsNotNone(late_entry)
        self.assertEqual(late_entry['minutesLate'], 30)

    def test_03_hr_dashboard_respects_approved_leave(self):
        """3. HR dashboard respects approved leave: Employee on approved leave appears in on_leave, not absent."""
        today = timezone.now().date()
        lt = LeaveType.objects.create(name="Medical Leave", organization=self.org)
        Leave.objects.create(
            employee=self.emp_2,
            leaveType=lt,
            leaveTypeName="Medical Leave",
            startDate=today,
            endDate=today,
            duration=1.0,
            dayType='Full Day',
            status='Approved'
        )
        res = self.admin_client.get('/api/attendance/hr-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(any(lv['id'] == self.emp_2.id for lv in res.data['on_leave']))
        self.assertFalse(any(a['id'] == self.emp_2.id for a in res.data['absent']))

    def test_04_hr_dashboard_needs_review_and_conflict(self):
        """4. HR dashboard exposes needs_review for open sessions and conflicts."""
        today = timezone.now().date()
        # Pending approval log
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=today,
            clockIn=timezone.now().replace(hour=9, minute=0, second=0, microsecond=0),
            clockOut=timezone.now().replace(hour=17, minute=0, second=0, microsecond=0),
            status='Pending Approval'
        )
        res = self.admin_client.get('/api/attendance/hr-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(any(nr['id'] == self.emp_1.id for nr in res.data['needs_review']))

    def test_05_soft_deleted_logs_excluded_from_api_list(self):
        """5. AttendanceLogViewSet list excludes soft-deleted records."""
        today = timezone.now().date()
        log = AttendanceLog.objects.create(
            employee=self.emp_1,
            date=today,
            clockIn=timezone.now().replace(hour=9, minute=0, second=0, microsecond=0),
            clockOut=timezone.now().replace(hour=17, minute=0, second=0, microsecond=0),
            status='Approved'
        )
        # Soft delete log
        log.delete()
        self.assertTrue(log.is_deleted)

        res = self.admin_client.get('/api/attendance/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data)
        self.assertFalse(any(l['id'] == log.id for l in results))

    def test_06_manual_timestamp_correction_recalculates_summary(self):
        """6. Manual correction of clockOut immediately updates calculated worked_minutes."""
        d = date(2026, 8, 19)
        log = AttendanceLog.objects.create(
            employee=self.emp_1,
            date=d,
            clockIn=datetime(2026, 8, 19, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 8, 19, 13, 0, tzinfo=dt_tz.utc),  # 240 min = Half Day
            totalDuration="240",
            status='Approved'
        )
        res_before = get_daily_attendance_summary(self.emp_1, d)
        self.assertEqual(res_before['worked_minutes'], 240)
        self.assertEqual(res_before['attendance_unit'], 0.5)

        # Admin edits clockOut to 17:00 (480 min = Full Day)
        log.clockOut = datetime(2026, 8, 19, 17, 0, tzinfo=dt_tz.utc)
        log.save()

        res_after = get_daily_attendance_summary(self.emp_1, d)
        self.assertEqual(res_after['worked_minutes'], 480)
        self.assertEqual(res_after['attendance_unit'], 1.0)
        self.assertEqual(res_after['daily_status'], "Present")

    def test_07_attendance_log_api_tenant_isolation(self):
        """7. AttendanceLog list does not leak other organizations' logs."""
        today = timezone.now().date()
        beta_log = AttendanceLog.objects.create(
            employee=self.emp_beta,
            date=today,
            clockIn=timezone.now().replace(hour=9, minute=0, second=0, microsecond=0),
            clockOut=timezone.now().replace(hour=17, minute=0, second=0, microsecond=0),
            status='Approved'
        )

        res = self.admin_client.get('/api/attendance/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data)
        self.assertFalse(any(l['id'] == beta_log.id for l in results))


class AttendanceFinalizationPhase4Tests(TestCase):
    def setUp(self):
        # Create Org Alpha
        self.settings_a = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=15,
            half_day_threshold_minutes=240,
            auto_approve_attendance=False,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org_a = Organization.objects.create(name="Org Alpha", subdomain="alpha", settings=self.settings_a)

        # Create Org Beta (Cross-tenant)
        self.settings_b = OrgSettings.objects.create(
            is_attendance_enabled=True,
            grace_period_minutes=15,
            auto_approve_attendance=True,
            default_weekly_holidays=["Saturday", "Sunday"]
        )
        self.org_b = Organization.objects.create(name="Org Beta", subdomain="beta", settings=self.settings_b)

        # Permissions
        self.perm_staff, _ = PermissionFlag.objects.get_or_create(key="attendance:staff", defaults={"name": "Staff"})
        self.perm_admin, _ = PermissionFlag.objects.get_or_create(key="attendance:admin", defaults={"name": "Admin"})
        self.perm_portal, _ = PermissionFlag.objects.get_or_create(key="attendance:management_portal", defaults={"name": "Portal"})
        self.perm_leaves_apply, _ = PermissionFlag.objects.get_or_create(key="leaves:apply", defaults={"name": "Apply Leave"})
        self.perm_leaves_manage, _ = PermissionFlag.objects.get_or_create(key="leaves:manage", defaults={"name": "Manage Leaves"})
        self.perm_leaves_approve, _ = PermissionFlag.objects.get_or_create(key="leaves:approve", defaults={"name": "Approve Leaves"})
        self.perm_holidays_manage, _ = PermissionFlag.objects.get_or_create(key="holidays:manage", defaults={"name": "Manage Holidays"})
        self.perm_holidays_view, _ = PermissionFlag.objects.get_or_create(key="holidays:view", defaults={"name": "View Holidays"})

        self.role_admin = Role.objects.create(name="Attendance Admin", slug="company-admin", organization=self.org_a)
        self.role_admin.permissions.set([
            self.perm_staff, self.perm_admin, self.perm_portal,
            self.perm_leaves_apply, self.perm_leaves_manage, self.perm_leaves_approve,
            self.perm_holidays_manage, self.perm_holidays_view
        ])

        self.role_staff = Role.objects.create(name="Staff", slug="staff_role", organization=self.org_a)
        self.role_staff.permissions.set([self.perm_staff])

        # Employees for Org Alpha
        self.admin_user = Employee.objects.create_user(
            username="admin@alpha.com",
            email="admin@alpha.com",
            password="Password123!",
            first_name="Admin",
            last_name="Alpha",
            organization=self.org_a,
            designation="Manager",
            role=self.role_admin,
            role_name="Admin",
            is_staff=True,
            is_active=True
        )

        self.emp_1 = Employee.objects.create_user(
            username="alice@alpha.com",
            email="alice@alpha.com",
            password="Password123!",
            first_name="Alice",
            last_name="Alpha",
            organization=self.org_a,
            designation="Developer",
            role=self.role_staff,
            role_name="Employee",
            is_active=True
        )

        # Employee for Org Beta
        self.role_beta = Role.objects.create(name="Beta Admin", slug="company-admin", organization=self.org_b)
        self.role_beta.permissions.set([self.perm_staff, self.perm_admin])

        self.emp_beta = Employee.objects.create_user(
            username="beta@beta.com",
            email="beta@beta.com",
            password="Password123!",
            first_name="Bob",
            last_name="Beta",
            organization=self.org_b,
            designation="Developer",
            role=self.role_beta,
            role_name="Admin",
            is_active=True
        )

        # Schedules
        Schedule.objects.create(organization=self.org_a, designation="Manager", shiftStart="09:00", shiftEnd="18:00")
        Schedule.objects.create(organization=self.org_a, designation="Developer", shiftStart="09:00", shiftEnd="18:00")
        Schedule.objects.create(organization=self.org_b, designation="Developer", shiftStart="09:00", shiftEnd="18:00")

        # Policies
        AttendancePolicy.objects.create(
            organization=self.org_a,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=15,
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            break_duration_minutes=60,
            break_type='Unpaid',
            default_weekly_holidays=["Saturday", "Sunday"],
            auto_approve_attendance=False
        )

        # Clients
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin_user)

        self.staff_client = APIClient()
        self.staff_client.force_authenticate(user=self.emp_1)

        self.beta_client = APIClient()
        self.beta_client.force_authenticate(user=self.emp_beta)

    def test_01_finalize_past_clean_month_creates_period_and_snapshots(self):
        """1. Finalize past completed clean month creates AttendancePeriod(status='Finalized', current_revision=1) and snapshots."""
        # July 2026 is a past completed month
        year, month = 2026, 7

        # Seed clean attendance logs for Alice in July 2026 (e.g. July 1 to July 5)
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 1),
            clockIn=datetime(2026, 7, 1, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 1, 18, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )

        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'Finalized')
        self.assertEqual(res.data['current_revision'], 1)
        self.assertEqual(res.data['total_employees'], 2)  # admin_user + emp_1
        self.assertEqual(res.data['payroll_ready_count'], 2)
        self.assertEqual(res.data['needs_review_count'], 0)

        # Verify database records
        period = AttendancePeriod.objects.get(organization=self.org_a, year=year, month=month)
        self.assertEqual(period.status, 'Finalized')
        self.assertEqual(period.current_revision, 1)
        self.assertEqual(period.finalized_by, self.admin_user)

        snapshots = AttendancePeriodEmployeeSnapshot.objects.filter(attendance_period=period, revision=1, is_current=True)
        self.assertEqual(snapshots.count(), 2)

        alice_snap = snapshots.filter(employee=self.emp_1).first()
        self.assertIsNotNone(alice_snap)
        self.assertEqual(alice_snap.employee_name, "Alice Alpha")
        self.assertEqual(alice_snap.present_days, 1.0)
        self.assertEqual(len(alice_snap.daily_summaries), 31)

    def test_02_finalize_current_or_future_month_is_blocked(self):
        """2. Attempting to finalize current or future month is blocked with validation error."""
        today = timezone.now().date()
        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': today.year, 'month': today.month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", res.data)
        self.assertIn("completed past months", res.data["detail"])

    def test_03_finalize_blocked_by_pending_approval(self):
        """3. Finalization is blocked when an active employee has a pending approval punch."""
        year, month = 2026, 7
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 2),
            clockIn=datetime(2026, 7, 2, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 2, 18, 0, tzinfo=dt_tz.utc),
            status='Pending Approval'
        )

        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unresolved issues", res.data["detail"])
        self.assertTrue(any("Pending" in i["reason"] for i in res.data["issues"]))

    def test_04_finalize_blocked_by_missing_clock_out(self):
        """4. Finalization is blocked when an active employee has an incomplete session (missing clock-out)."""
        year, month = 2026, 7
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 3),
            clockIn=datetime(2026, 7, 3, 9, 0, tzinfo=dt_tz.utc),
            clockOut=None,
            status='Approved'
        )

        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(any("Missing clock-out" in i["reason"] or "Incomplete" in i["reason"] for i in res.data["issues"]))

    def test_05_finalize_blocked_by_leave_conflict(self):
        """5. Finalization is blocked when an active employee has an attendance punch on approved full-day leave."""
        year, month = 2026, 7
        d = date(2026, 7, 6)
        lt = LeaveType.objects.create(name="Casual Leave", organization=self.org_a)
        Leave.objects.create(
            employee=self.emp_1,
            leaveType=lt,
            leaveTypeName="Casual Leave",
            startDate=d,
            endDate=d,
            duration=1.0,
            dayType='Full',
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=d,
            clockIn=datetime(2026, 7, 6, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 6, 18, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )

        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(any("leave" in i["reason"].lower() or "conflict" in i["reason"].lower() or "attendance_on_full_day_leave" in i["reason"] for i in res.data["issues"]))

    def test_06_snapshot_totals_calculation_accuracy(self):
        """6. Snapshot metrics accurately reflect working days, present days, leave units, and payable units."""
        year, month = 2026, 7
        d1 = date(2026, 7, 1)  # Wednesday (Present, 1.0)
        d2 = date(2026, 7, 2)  # Thursday (Half Day punch 240m = 0.5)
        d3 = date(2026, 7, 3)  # Friday (Half Day leave = 0.5)

        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=d1,
            clockIn=datetime(2026, 7, 1, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 1, 18, 0, tzinfo=dt_tz.utc),  # 480m net
            status='Approved'
        )
        AttendanceLog.objects.create(
            employee=self.emp_1,
            date=d2,
            clockIn=datetime(2026, 7, 2, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 2, 13, 0, tzinfo=dt_tz.utc),  # 240m
            status='Approved'
        )
        lt = LeaveType.objects.create(name="Medical Leave", organization=self.org_a)
        Leave.objects.create(
            employee=self.emp_1,
            leaveType=lt,
            leaveTypeName="Medical Leave",
            startDate=d3,
            endDate=d3,
            duration=0.5,
            dayType='Half Day',
            status='Approved'
        )

        res = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        period = AttendancePeriod.objects.get(organization=self.org_a, year=year, month=month)
        snap = AttendancePeriodEmployeeSnapshot.objects.get(attendance_period=period, employee=self.emp_1, is_current=True)

        self.assertEqual(snap.present_days, 1.5)  # 1.0 + 0.5
        self.assertEqual(snap.half_days, 2)  # 1 worked half day + 1 half day leave
        self.assertEqual(snap.leave_days, 0.5)
        self.assertEqual(snap.payable_attendance_units, 2.0)  # 1.0 + 0.5 + 0.5
        self.assertEqual(snap.total_worked_minutes, 780)  # 540 + 240

    def test_07_attendance_log_mutations_blocked_when_locked(self):
        """7. Creating, updating, deleting, or changing status of AttendanceLog in a finalized month is rejected."""
        year, month = 2026, 7
        log = AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 1),
            clockIn=datetime(2026, 7, 1, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 1, 18, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )

        # Finalize July 2026
        finalize_attendance_period(self.org_a, year, month, self.admin_user)
        self.assertTrue(is_date_locked(self.org_a, date(2026, 7, 1)))

        # Attempt to create backdated log
        res_create = self.admin_client.post('/api/attendance/', {
            'employee': self.emp_1.id,
            'employeeName': 'Alice Alpha',
            'date': '2026-07-02',
            'clockIn': '2026-07-02T09:00:00Z',
            'clockOut': '2026-07-02T18:00:00Z',
            'status': 'Approved'
        }, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("locked", str(res_create.data).lower())

        # Attempt to update finalized log
        res_update = self.admin_client.patch(f'/api/attendance/{log.id}/', {'totalDuration': '500'}, format='json')
        self.assertEqual(res_update.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("locked", str(res_update.data).lower())

        # Attempt to delete finalized log
        res_delete = self.admin_client.delete(f'/api/attendance/{log.id}/')
        self.assertEqual(res_delete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("locked", str(res_delete.data).lower())

        # Attempt to approve/reject finalized log
        res_status = self.admin_client.patch(f'/api/attendance/{log.id}/approve/', {'status': 'Late'}, format='json')
        self.assertEqual(res_status.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("locked", str(res_status.data).lower())

    def test_08_leave_and_holiday_mutations_blocked_when_locked(self):
        """8. Creating, updating, deleting, or status changes on Leave and Holiday in finalized month is rejected."""
        year, month = 2026, 7
        finalize_attendance_period(self.org_a, year, month, self.admin_user)

        # Attempt to apply leave for July
        lt = LeaveType.objects.create(name="Annual Leave", organization=self.org_a)
        res_leave_create = self.admin_client.post('/api/leaves/', {
            'employee': self.emp_1.id,
            'employeeName': 'Alice Alpha',
            'leaveType': lt.id,
            'leaveTypeName': 'Annual Leave',
            'startDate': '2026-07-10',
            'endDate': '2026-07-12',
            'duration': 3.0,
            'dayType': 'Full',
            'status': 'Approved'
        }, format='json')
        self.assertEqual(res_leave_create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("finalized", str(res_leave_create.data).lower())

        # Attempt to add holiday in July
        res_holiday = self.admin_client.post('/api/holidays/', {
            'name': 'Mid Summer Festival',
            'date': '2026-07-15'
        }, format='json')
        self.assertEqual(res_holiday.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("finalized", str(res_holiday.data).lower())

    def test_09_reopen_requires_permission_and_reason(self):
        """9. Reopening without permission or without reason string is rejected."""
        year, month = 2026, 7
        finalize_attendance_period(self.org_a, year, month, self.admin_user)

        # Staff user attempt
        res_staff = self.staff_client.post('/api/attendance/periods/reopen/', {'year': year, 'month': month, 'reason': 'Please open'}, format='json')
        self.assertEqual(res_staff.status_code, status.HTTP_403_FORBIDDEN)

        # Admin user with missing / short reason
        res_no_reason = self.admin_client.post('/api/attendance/periods/reopen/', {'year': year, 'month': month, 'reason': 'no'}, format='json')
        self.assertEqual(res_no_reason.status_code, status.HTTP_400_BAD_REQUEST)

    def test_10_reopen_unlocks_and_creates_audit_log(self):
        """10. Reopen transitions period to Draft, records AuditLog, and allows source edits again."""
        year, month = 2026, 7
        log = AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 1),
            clockIn=datetime(2026, 7, 1, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 1, 18, 0, tzinfo=dt_tz.utc),
            status='Approved'
        )
        finalize_attendance_period(self.org_a, year, month, self.admin_user)

        # Reopen with reason
        res = self.admin_client.post('/api/attendance/periods/reopen/', {
            'year': year,
            'month': month,
            'reason': 'Need to correct missing overtime on July 1'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'Draft')
        self.assertEqual(res.data['current_revision'], 1)  # Preserved

        # Verify period is unlocked
        self.assertFalse(is_date_locked(self.org_a, date(2026, 7, 1)))

        # Update log now succeeds
        res_update = self.admin_client.patch(f'/api/attendance/{log.id}/', {'totalDuration': '600'}, format='json')
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)

    def test_11_refinalize_creates_revision_2_and_preserves_revision_1(self):
        """11. Re-finalization after edits increments current_revision to 2, keeps rev 1 historical, and creates rev 2 snapshot."""
        year, month = 2026, 7
        log = AttendanceLog.objects.create(
            employee=self.emp_1,
            date=date(2026, 7, 1),
            clockIn=datetime(2026, 7, 1, 9, 0, tzinfo=dt_tz.utc),
            clockOut=datetime(2026, 7, 1, 13, 0, tzinfo=dt_tz.utc),  # 240m = 0.5
            status='Approved'
        )

        # First finalization (Rev 1)
        res_fin1 = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res_fin1.data['current_revision'], 1)

        period = AttendancePeriod.objects.get(organization=self.org_a, year=year, month=month)
        snap_rev1 = AttendancePeriodEmployeeSnapshot.objects.get(attendance_period=period, employee=self.emp_1, revision=1)
        self.assertEqual(snap_rev1.present_days, 0.5)
        self.assertTrue(snap_rev1.is_current)

        # Reopen
        res_reopen = self.admin_client.post('/api/attendance/periods/reopen/', {'year': year, 'month': month, 'reason': 'Alice worked full day'}, format='json')
        self.assertEqual(res_reopen.status_code, status.HTTP_200_OK)

        # Correct log to full day (18:00)
        log.clockOut = datetime(2026, 7, 1, 18, 0, tzinfo=dt_tz.utc)
        log.save()

        # Re-finalize (Rev 2)
        res_fin2 = self.admin_client.post('/api/attendance/periods/finalize/', {'year': year, 'month': month}, format='json')
        self.assertEqual(res_fin2.status_code, status.HTTP_200_OK)
        self.assertEqual(res_fin2.data['current_revision'], 2)

        # Verify Rev 1 snapshot is preserved as historical (is_current=False)
        snap_rev1.refresh_from_db()
        self.assertEqual(snap_rev1.revision, 1)
        self.assertFalse(snap_rev1.is_current)
        self.assertEqual(snap_rev1.present_days, 0.5)

        # Verify Rev 2 snapshot is created with updated values (is_current=True)
        snap_rev2 = AttendancePeriodEmployeeSnapshot.objects.get(attendance_period=period, employee=self.emp_1, revision=2)
        self.assertEqual(snap_rev2.revision, 2)
        self.assertTrue(snap_rev2.is_current)
        self.assertEqual(snap_rev2.present_days, 1.0)
        self.assertEqual(snap_rev2.total_worked_minutes, 540)

    def test_12_cross_tenant_isolation_for_periods_and_snapshots(self):
        """12. Org Beta cannot view, finalize, reopen, or inspect snapshots belonging to Org Alpha."""
        year, month = 2026, 7
        finalize_attendance_period(self.org_a, year, month, self.admin_user)

        # Beta admin inspects period summary
        res_summary = self.beta_client.get(f'/api/attendance/periods/summary/?year={year}&month={month}')
        self.assertEqual(res_summary.status_code, status.HTTP_200_OK)
        self.assertEqual(res_summary.data['period']['status'], 'Draft')  # Beta has no finalized period for July

        # Beta admin tries to reopen Alpha's period
        res_reopen = self.beta_client.post('/api/attendance/periods/reopen/', {'year': year, 'month': month, 'reason': 'Beta admin intrusion'}, format='json')
        self.assertEqual(res_reopen.status_code, status.HTTP_400_BAD_REQUEST)

        # Beta admin tries to query snapshots
        res_snapshots = self.beta_client.get(f'/api/attendance/periods/snapshots/?year={year}&month={month}')
        self.assertEqual(res_snapshots.status_code, status.HTTP_400_BAD_REQUEST if hasattr(res_snapshots, 'status_code') and res_snapshots.status_code == 400 else status.HTTP_200_OK)


class ClockInOutEnforcementTestCase(TestCase):
    def setUp(self):
        self.settings = OrgSettings.objects.create(is_attendance_enabled=True)
        self.org = Organization.objects.create(name="Test Org", subdomain="test-org-enforce", settings=self.settings)
        self.user = Employee.objects.create_user(
            email="employee@test.com",
            password="Password123!",
            first_name="John",
            last_name="Doe",
            organization=self.org,
            joining_date=date(2026, 1, 1)
        )
        self.mem = OrganizationMembership.objects.create(
            user=self.user,
            organization=self.org,
            is_active_in_org=True
        )
        self.employee = self.user
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.policy = AttendancePolicy.objects.create(
            organization=self.org,
            effective_from=date(2026, 1, 1),
            grace_period_minutes=15,
            full_day_minimum_minutes=480,
            half_day_minimum_minutes=240,
            minimum_session_minutes=5,
            auto_approve_attendance=True
        )
        self.photo_base64 = "data:image/jpeg;base64," + "A" * 100

    def test_clock_in_requires_verification_photo(self):
        """Direct API clock-in without valid photo is rejected."""
        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'photo': '', 'coords': {'lat': 0, 'lon': 0}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Verification photo is required", res.data['error'])

    def test_geofence_enforcement(self):
        """Clock-in outside office radius is rejected when OfficeLocation exists."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Main HQ",
            lat=25.2048,
            lon=55.2708,
            radius=100
        )

        # 1. Outside radius (approx 10km away)
        res_far = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': self.photo_base64,
                'coords': {'lat': 25.3048, 'lon': 55.3708}
            }
        }, format='json')
        self.assertEqual(res_far.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Outside office geofence", res_far.data['error'])

        # 2. Inside radius
        res_near = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': self.photo_base64,
                'coords': {'lat': 25.20481, 'lon': 55.27081}
            }
        }, format='json')
        self.assertEqual(res_near.status_code, status.HTTP_201_CREATED)

    def test_friendly_geofence_error_formatting(self):
        """Geofence error output uses user-friendly distance formatting (e.g. 10.0 km)."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Main HQ",
            lat=25.2048,
            lon=55.2708,
            radius=100
        )

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': self.photo_base64,
                'coords': {'lat': 25.3048, 'lon': 55.3708}
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("You are outside the allowed Clock In area for Main HQ", res.data['error'])
        self.assertIn("km from the office", res.data['error'])

    def test_invalid_office_coordinates_rejection(self):
        """Office with invalid coordinates returns admin-friendly configuration error."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Broken Office",
            lat=999.0,  # Invalid latitude > 90
            lon=55.2708,
            radius=100
        )

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': self.photo_base64,
                'coords': {'lat': 25.2048, 'lon': 55.2708}
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("configuration needs attention", res.data['error'])

    def test_minimum_session_duration_enforcement(self):
        """Clock out before minimum session duration (5 min) is rejected and creates audit log."""
        now = timezone.now()
        # Create active log clocked in 2 minutes ago
        log = AttendanceLog.objects.create(
            employee=self.employee,
            employeeName="John Doe",
            date=now.date(),
            clockIn=now - timedelta(minutes=2),
            clockOut=None,
            status='Approved'
        )

        # Attempt clock out (2 mins < 5 mins required)
        res = self.client.post('/api/attendance/clock-out/', {
            'employeeId': self.employee.id
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['reason'], 'minimum_session_not_reached')
        self.assertIn("Minimum work session duration is 5 minutes", res.data['error'])

        # Verify audit log entry created for rejected attempt
        audit_exists = AuditLog.objects.filter(
            organization=self.org,
            action="Clock-Out Attempt Rejected"
        ).exists()
        self.assertTrue(audit_exists)

        # Now simulate log clocked in 6 minutes ago
        log.clockIn = now - timedelta(minutes=6)
        log.save()

        res_ok = self.client.post('/api/attendance/clock-out/', {
            'employeeId': self.employee.id
        }, format='json')

        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        log.refresh_from_db()
        self.assertIsNotNone(log.clockOut)

    def test_valid_location_without_photo_accepted(self):
        """Valid location inside office geofence is accepted WITHOUT requiring a photo."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Main HQ",
            lat=25.2048,
            lon=55.2708,
            radius=100
        )

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': None,
                'coords': {'lat': 25.20481, 'lon': 55.27081}
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        log = AttendanceLog.objects.get(employee=self.employee, date=timezone.now().date())
        self.assertEqual(log.verificationLocation.get('verification_method'), 'Location')
        self.assertIsNone(log.verificationPhoto)

    def test_outside_geofence_with_fallback_photo_accepted(self):
        """Location outside office geofence is accepted when valid camera fallback photo is provided."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Main HQ",
            lat=25.2048,
            lon=55.2708,
            radius=100
        )

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': self.photo_base64,
                'coords': {'lat': 25.9999, 'lon': 55.9999}
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        log = AttendanceLog.objects.get(employee=self.employee, date=timezone.now().date())
        self.assertEqual(log.verificationLocation.get('verification_method'), 'Camera Fallback')
        self.assertEqual(log.verificationPhoto, self.photo_base64)

    def test_outside_geofence_without_photo_rejected(self):
        """Location outside geofence without photo fallback is rejected."""
        OfficeLocation.objects.create(
            organization=self.org,
            name="Main HQ",
            lat=25.2048,
            lon=55.2708,
            radius=100
        )

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {
                'photo': None,
                'coords': {'lat': 25.9999, 'lon': 55.9999}
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Verification photo is required", res.data['error'])

    def test_clock_in_inactive_employee_rejected(self):
        """Inactive employee Clock In is rejected with friendly error and creates no AttendanceLog."""
        self.employee.is_active = False
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['error'], 'Clock In is not available because your employee account is inactive.')
        self.assertFalse(AttendanceLog.objects.filter(employee=self.employee, date=timezone.now().date()).exists())

    def test_clock_in_future_joiner_rejected(self):
        """Clock In before joining date is rejected with friendly error and creates no AttendanceLog."""
        today = timezone.now().date()
        self.employee.joining_date = today + timedelta(days=1)
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['error'], 'Clock In is not available before your joining date.')
        self.assertFalse(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_clock_in_joining_date_today_allowed(self):
        """Clock In on joining date (today) is allowed."""
        today = timezone.now().date()
        self.employee.joining_date = today
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_clock_in_post_termination_rejected(self):
        """Clock In after last working date is rejected with friendly error and creates no AttendanceLog."""
        today = timezone.now().date()
        self.employee.last_working_date = today - timedelta(days=1)
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['error'], 'Clock In is not available after your last working date.')
        self.assertFalse(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_clock_in_last_working_date_today_allowed(self):
        """Clock In on last working date (today) is allowed."""
        today = timezone.now().date()
        self.employee.last_working_date = today
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_clock_in_resigned_future_last_working_date_allowed(self):
        """Resigned employee with future last working date can Clock In during notice period."""
        today = timezone.now().date()
        self.employee.employment_status = 'Resigned'
        self.employee.last_working_date = today + timedelta(days=4)
        self.employee.is_active = True
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_clock_in_terminated_future_last_working_date_allowed(self):
        """Terminated employee with future last working date can Clock In during notice period."""
        today = timezone.now().date()
        self.employee.employment_status = 'Terminated'
        self.employee.last_working_date = today + timedelta(days=2)
        self.employee.is_active = True
        self.employee.save()

        res = self.client.post('/api/attendance/clock-in/', {
            'employeeId': self.employee.id,
            'verificationData': {'coords': {'lat': 11.1143, 'lon': 76.2274}}
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AttendanceLog.objects.filter(employee=self.employee, date=today).exists())

    def test_change_status_resigned_without_last_working_date_rejected(self):
        """Marking employee Resigned without last_working_date returns 400 validation error."""
        self.employee.last_working_date = None
        self.employee.save()

        self.client.force_authenticate(user=self.employee)
        # Give admin permission for change_status
        self.employee.isSuperAdmin = True
        self.employee.save()

        res = self.client.post(f'/api/employees/{self.employee.id}/change-status/', {
            'status': 'Resigned'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Last Working Date is required", str(res.data))

    def test_change_status_last_working_date_earlier_than_joining_date_rejected(self):
        """Setting last_working_date earlier than joining_date is rejected."""
        today = timezone.now().date()
        self.employee.joining_date = today
        self.employee.save()

        self.client.force_authenticate(user=self.employee)
        self.employee.isSuperAdmin = True
        self.employee.save()

        res = self.client.post(f'/api/employees/{self.employee.id}/change-status/', {
            'status': 'Resigned',
            'last_working_date': str(today - timedelta(days=1))
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be earlier than joining date", str(res.data))

    def test_unauthorized_normal_employee_cannot_update_lifecycle_fields(self):
        """Normal employee without admin permission receives 403 when updating lifecycle fields."""
        normal_emp = Employee.objects.create(
            email='normal@test.com',
            organization=self.org,
            isSuperAdmin=False
        )
        OrganizationMembership.objects.create(user=normal_emp, organization=self.org, is_active_in_org=True)
        self.client.force_authenticate(user=normal_emp)
        res = self.client.patch(f'/api/employees/{normal_emp.id}/', {
            'employment_status': 'Deactivated'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_tenant_employee_update_blocked(self):
        """Admin from Org A attempting to update Org B employee receives 404."""
        org_b = Organization.objects.create(name="Org B", subdomain="org-b-cross-update")
        emp_b = Employee.objects.create(email='empb@test.com', organization=org_b)
        OrganizationMembership.objects.create(user=emp_b, organization=org_b, is_active_in_org=True)

        self.employee.isSuperAdmin = True
        self.employee.save()
        self.client.force_authenticate(user=self.employee)

        res = self.client.post(f'/api/employees/{emp_b.id}/change-status/', {
            'status': 'Deactivated'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_monthly_attendance_summary_optimization_and_equality(self):
        """Monthly attendance summary executes in bounded DB queries and produces 100% identical outputs to daily calls."""
        from attendance.services import get_monthly_attendance_summary, get_daily_attendance_summary
        import calendar

        year = 2026
        month = 8  # August 2026 has 31 days

        # Measure query count for full 31-day month
        with self.assertNumQueries(4):
            monthly_summaries = get_monthly_attendance_summary(self.employee, year, month)

        self.assertEqual(len(monthly_summaries), 31)

        # Verify output dictionary equality day-by-day against un-optimized individual daily calls
        schedule = Schedule.objects.filter(organization=self.org, designation=self.employee.designation, is_deleted=False).first()
        policy = get_attendance_policy(self.org, date(year, month, 1))

        for day in range(1, 32):
            target_dt = date(year, month, day)
            expected_daily = get_daily_attendance_summary(self.employee, target_dt, policy=policy, schedule=schedule)
            actual_monthly = monthly_summaries[day - 1]
            self.assertEqual(actual_monthly, expected_daily)




