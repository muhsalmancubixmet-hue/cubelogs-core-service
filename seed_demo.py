"""
CubeLogs Demo Data Seeder  —  run:  python seed_demo.py
"""
import os, random
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
import django; django.setup()

from datetime import date, timedelta, datetime, time as dt_time
from django.utils import timezone
from api.models import (
    Employee, Organization, AttendanceLog, Leave,
    Task, Holiday, Schedule, LeaveType, OrgSettings
)

org = Organization.objects.get(id=4)
print(f"Seeding into org: {org.name} (id={org.id})")

# ── 1. OrgSettings ────────────────────────────────────────────────────────────
s = OrgSettings.objects.first()
s.grace_period_minutes = 15
s.half_day_threshold_minutes = 240
s.full_day_absent_threshold_minutes = 60
s.save()
print("OrgSettings updated")

# ── 2. Employees ──────────────────────────────────────────────────────────────
emp_rows = [
    ("Arjun",   "Mehta",   "arjun.mehta@smntech.com",    "Senior Developer"),
    ("Priya",   "Sharma",  "priya.sharma@smntech.com",   "UI/UX Designer"),
    ("Rahul",   "Verma",   "rahul.verma@smntech.com",    "DevOps Engineer"),
    ("Sneha",   "Nair",    "sneha.nair@smntech.com",     "QA Engineer"),
    ("Kiran",   "Patel",   "kiran.patel@smntech.com",    "Product Manager"),
    ("Divya",   "Rao",     "divya.rao@smntech.com",      "HR Manager"),
    ("Aditya",  "Kumar",   "aditya.kumar@smntech.com",   "Backend Developer"),
    ("Meera",   "Iyer",    "meera.iyer@smntech.com",     "Business Analyst"),
    ("Varun",   "Singh",   "varun.singh@smntech.com",    "Frontend Developer"),
    ("Lakshmi", "Pillai",  "lakshmi.pillai@smntech.com", "Scrum Master"),
]

employees = []
for first, last, email, desig in emp_rows:
    emp, created = Employee.objects.get_or_create(
        email=email,
        defaults=dict(
            username=email, first_name=first, last_name=last,
            designation=desig,
            phone="98765" + str(random.randint(40000, 49999)),
            organization=org, isSuperAdmin=False, is_active=True,
            permissions=["attendance:staff", "tasks:view", "leaves:apply", "holidays:view"],
        )
    )
    if created:
        emp.set_password("pass1234"); emp.save()
    employees.append(emp)
    print(f"  {'Created' if created else 'Exists '} {first} {last} ({desig})")

print(f"Employees in org: {len(employees)}")

# ── 3. Schedules ──────────────────────────────────────────────────────────────
for desig, start, end in [
    ("Senior Developer",   "09:00", "18:00"),
    ("UI/UX Designer",     "09:30", "18:30"),
    ("DevOps Engineer",    "08:00", "17:00"),
    ("QA Engineer",        "09:00", "18:00"),
    ("Product Manager",    "09:00", "18:00"),
    ("HR Manager",         "09:00", "17:30"),
    ("Backend Developer",  "09:00", "18:00"),
    ("Business Analyst",   "09:30", "18:30"),
    ("Frontend Developer", "09:00", "18:00"),
    ("Scrum Master",       "09:00", "18:00"),
]:
    Schedule.objects.get_or_create(designation=desig, defaults=dict(shiftStart=start, shiftEnd=end))
print("Schedules seeded")

# ── 4. Attendance Logs ────────────────────────────────────────────────────────
today = date.today()
log_count = 0

for emp in employees:
    for days_ago in range(7, 0, -1):
        log_date = today - timedelta(days=days_ago)
        if log_date.weekday() >= 5: continue
        if AttendanceLog.objects.filter(employee=emp, date=log_date).exists(): continue
        offset = random.randint(0, 45)
        ci = timezone.make_aware(datetime.combine(log_date, dt_time(9, offset)))
        co = timezone.make_aware(datetime.combine(log_date, dt_time(18, random.randint(0, 30))))
        dur = int((co - ci).total_seconds() // 60)
        h, m = divmod(dur, 60)
        status = "Late" if offset > 15 else random.choice(["Approved", "Pending Approval"])
        AttendanceLog.objects.create(
            employee=emp,
            employeeName=f"{emp.first_name} {emp.last_name}",
            date=log_date, clockIn=ci, clockOut=co,
            totalDuration=f"{h:02d}:{m:02d}", status=status,
            verificationLocation={"lat": round(11.1143 + random.uniform(-0.001,0.001),6),
                                   "lon": round(76.2274 + random.uniform(-0.001,0.001),6)},
        )
        log_count += 1

# Today — first 6 clocked in (last 4 absent for HR portal demo)
for emp in employees[:6]:
    if AttendanceLog.objects.filter(employee=emp, date=today).exists(): continue
    offset = random.randint(0, 40)
    ci = timezone.make_aware(datetime.combine(today, dt_time(9, offset)))
    status = "Late" if offset > 15 else "Pending Approval"
    AttendanceLog.objects.create(
        employee=emp, employeeName=f"{emp.first_name} {emp.last_name}",
        date=today, clockIn=ci, clockOut=None, totalDuration="0", status=status,
        verificationLocation={"lat": 11.1143, "lon": 76.2274},
    )
    log_count += 1

print(f"Attendance logs seeded: {log_count}")

# ── 5. Leaves ─────────────────────────────────────────────────────────────────
lv_types = list(LeaveType.objects.all()[:4])
lv_statuses = ["Approved", "Approved", "Pending", "Rejected"]
lv_reasons = [
    "Personal medical appointment", "Family function attendance",
    "Home repair emergency", "Annual family vacation",
    "Sick leave - fever", "Child school event",
]
leave_count = 0

if lv_types:
    for i, emp in enumerate(employees):
        start_d = today - timedelta(days=random.randint(2, 20))
        end_d = start_d + timedelta(days=random.randint(0, 2))
        ltype = lv_types[i % len(lv_types)]
        dur = (end_d - start_d).days + 1
        _, created = Leave.objects.get_or_create(
            employee=emp, startDate=start_d,
            defaults=dict(
                employeeName=f"{emp.first_name} {emp.last_name}",
                endDate=end_d, leaveType=ltype, leaveTypeName=ltype.name,
                reason=random.choice(lv_reasons),
                status=lv_statuses[i % len(lv_statuses)],
                dayType="Full Day", duration=str(dur),
            )
        )
        if created: leave_count += 1

    # 2 employees on approved leave TODAY (visible in HR portal Leave tab)
    for emp in employees[6:8]:
        ltype = lv_types[0]
        _, created = Leave.objects.get_or_create(
            employee=emp, startDate=today,
            defaults=dict(
                employeeName=f"{emp.first_name} {emp.last_name}",
                endDate=today, leaveType=ltype, leaveTypeName=ltype.name,
                reason="Sick leave - fever", status="Approved",
                dayType="Full Day", duration="1",
            )
        )
        if created: leave_count += 1

print(f"Leaves seeded: {leave_count}")

# ── 6. Tasks ─────────────────────────────────────────────────────────────────
task_rows = [
    ("Redesign Dashboard UI",       "In Progress", "Improve dashboard metrics widgets and chart visualizations."),
    ("Fix Login Session Bug",       "Completed",   "Resolve JWT token expiry issue on mobile browsers."),
    ("Write Unit Tests for API",    "Pending",     "Add test coverage for attendance and leave endpoints."),
    ("Setup CI/CD Pipeline",        "In Progress", "Configure GitHub Actions for automated build and deploy."),
    ("Database Query Optimization", "Pending",     "Identify and optimize slow queries in attendance module."),
    ("Employee Onboarding Flow",    "Completed",   "Document and implement the new employee onboarding checklist."),
    ("HR Portal Feature Rollout",   "In Progress", "Launch the HR Attendance Management Portal to production."),
    ("Mobile App Prototype",        "Pending",     "Create React Native prototype for field attendance clock-in."),
    ("Security Audit",              "Pending",     "Conduct a full security review of all API endpoints."),
    ("Quarterly Report Automation", "Completed",   "Automate monthly attendance report generation and email delivery."),
]
task_count = 0
for i, (title, tstatus, desc) in enumerate(task_rows):
    assignee = employees[i % len(employees)]
    _, created = Task.objects.get_or_create(
        title=title,
        defaults=dict(
            description=desc, status=tstatus,
            assignedTo=assignee,
            assignedName=f"{assignee.first_name} {assignee.last_name}",
            dueDate=today + timedelta(days=random.randint(1, 30)),
        )
    )
    if created: task_count += 1

print(f"Tasks seeded: {task_count}")

# ── 7. Holidays ───────────────────────────────────────────────────────────────
year = today.year
holiday_count = 0
for name, hdate, desc in [
    ("Republic Day",     date(year, 1, 26),  "National public holiday"),
    ("Holi",             date(year, 3, 25),  "Festival of Colors"),
    ("Good Friday",      date(year, 4, 18),  "Christian public holiday"),
    ("Eid ul-Fitr",      date(year, 4, 10),  "Islamic public holiday"),
    ("Independence Day", date(year, 8, 15),  "National public holiday"),
    ("Ganesh Chaturthi", date(year, 8, 27),  "Hindu festival"),
    ("Dussehra",         date(year, 10, 2),  "Hindu festival"),
    ("Diwali",           date(year, 10, 20), "Festival of Lights"),
    ("Christmas",        date(year, 12, 25), "Christian public holiday"),
]:
    _, created = Holiday.objects.get_or_create(
        name=name, date=hdate, organization=org,
        defaults=dict(description=desc)
    )
    if created: holiday_count += 1

print(f"Holidays seeded: {holiday_count}")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 44)
print("  SEEDING COMPLETE")
print("=" * 44)
print(f"  Employees in org : {Employee.objects.filter(organization=org).count()}")
print(f"  Attendance Logs  : {AttendanceLog.objects.count()}")
print(f"  Leaves           : {Leave.objects.count()}")
print(f"  Tasks            : {Task.objects.count()}")
print(f"  Holidays         : {Holiday.objects.count()}")
print(f"  Schedules        : {Schedule.objects.count()}")
print()
print("  Admin  : salmankcsiju@gmail.com / admin123")
print("  Staff  : <email> / pass1234")
