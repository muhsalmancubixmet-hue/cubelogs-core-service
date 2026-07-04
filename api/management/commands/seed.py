from django.core.management.base import BaseCommand
from api.models import (
    Employee, LeaveType, OfficeLocation, Schedule, OrgSettings, PERMISSION_FLAGS,
    SubscriptionPackage, SubscriberAccount, CMSContent
)

class Command(BaseCommand):
    help = 'Seeds default data: default Leave Types, default Location, Schedule, Settings and Super Admin'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Seeding database...'))

        # 1. Seed default Leave Types
        leave_types_data = [
            {
                'name': 'Casual Leave (CL)',
                'description': 'Casual Leave must be applied for short personal matters.',
                'limitPeriod': 'Yearly',
                'maxLimit': 10,
                'restrictedDates': [],
                'carryForward': False,
                'maxCarryForward': 0,
                'minAdvanceDays': 0,
                'status': 'Active',
            },
            {
                'name': 'Sick Leave (SL)',
                'description': 'Medical certificate required for more than 3 days.',
                'limitPeriod': 'Yearly',
                'maxLimit': 8,
                'restrictedDates': [],
                'carryForward': False,
                'maxCarryForward': 0,
                'minAdvanceDays': 0,
                'status': 'Active',
            },
            {
                'name': 'Earned Leave (EL)',
                'description': 'Vacation leave earned by work.',
                'limitPeriod': 'Yearly',
                'maxLimit': 12,
                'restrictedDates': [],
                'carryForward': True,
                'maxCarryForward': 5,
                'minAdvanceDays': 0,
                'status': 'Active',
            }
        ]

        for lt in leave_types_data:
            existing = LeaveType.objects.filter(name=lt['name'])
            if existing.exists():
                obj = existing.first()
                created = False
            else:
                obj = LeaveType.objects.create(**lt)
                created = True

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Leave Type: {lt['name']}"))
            else:
                # Update existing global leaves in case they need minAdvanceDays set to default
                if getattr(obj, 'minAdvanceDays', None) is None:
                    obj.minAdvanceDays = 0
                    obj.save()
                self.stdout.write(f"Leave Type already exists: {lt['name']}")

        # 2. Seed default Office Location
        loc, created = OfficeLocation.objects.get_or_create(
            name="Head Office",
            defaults={
                'lat': 11.1143,
                'lon': 76.2274,
                'radius': 100.0,
                'isPrimary': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created primary Office Location: Head Office"))
        else:
            self.stdout.write("Office Location already exists: Head Office")

        # 3. Seed default Schedule
        sched, created = Schedule.objects.get_or_create(
            designation="Admin",
            defaults={
                'shiftStart': '09:00',
                'shiftEnd': '17:00'
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created Schedule: Admin (09:00 - 17:00)"))
        else:
            self.stdout.write("Schedule already exists: Admin")

        # 4. Seed Org Settings
        settings, created = OrgSettings.objects.get_or_create(
            id=1,
            defaults={
                'subscriptionDays': 12
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created OrgSettings (12 subscription days)"))
        else:
            self.stdout.write("OrgSettings already exists")

        # 4.1 Seed Subscription Packages
        packages_data = [
            {
                'name': 'Free Package',
                'price': 0.00,
                'employeeLimit': 5,
                'features': ['dashboard', 'attendance:staff', 'leaves:apply']
            },
            {
                'name': 'Starter',
                'price': 29.00,
                'employeeLimit': 15,
                'features': ['dashboard', 'attendance:staff', 'leaves:apply', 'leaves:approve', 'tasks:view']
            },
            {
                'name': 'Professional',
                'price': 79.00,
                'employeeLimit': 100,
                'features': ['dashboard', 'attendance:staff', 'leaves:apply', 'leaves:approve', 'tasks:view', 'tasks:create', 'admin:templates', 'attendance:admin', 'leaves:manage', 'holidays:view', 'geofence', 'biometric', 'scheduling']
            },
            {
                'name': 'Business',
                'price': 149.00,
                'employeeLimit': 99999,
                'features': ['dashboard', 'attendance:staff', 'leaves:apply', 'leaves:approve', 'tasks:view', 'tasks:create', 'admin:templates', 'attendance:admin', 'leaves:manage', 'holidays:view', 'holidays:manage', 'locations:manage', 'settings:branding', 'settings:billing', 'geofence', 'biometric', 'scheduling', 'auditLogs', 'multiLocation']
            }
        ]

        for pkg in packages_data:
            obj, created = SubscriptionPackage.objects.get_or_create(
                name=pkg['name'],
                defaults=pkg
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Subscription Package: {pkg['name']}"))

        # 5. Seed default Super Admin if no superuser exists
        super_users = Employee.objects.filter(is_superuser=True)
        admin_email = 'admin@cubelogs.com'
        if not super_users.exists():
            self.stdout.write("No superuser found. Creating default Super Admin...")
            admin_pass = 'admin123'
            
            # Give all permissions
            permissions_list = [p['id'] for p in PERMISSION_FLAGS]
            
            admin = Employee.objects.create_superuser(
                email=admin_email,
                password=admin_pass,
                first_name='Super',
                last_name='Admin',
                isSuperAdmin=True,
                useDefaultPermissions=True,
                permissions=permissions_list,
                designation='Admin'
            )
            self.stdout.write(self.style.SUCCESS(f"Successfully created default Super Admin user: {admin_email} / {admin_pass}"))
        else:
            self.stdout.write("Superuser already exists in database.")

        # 5.1 Seed SubscriberAccount for Super Admin (Professional package tier by default, isActive=True, expires in 12 days to match settings)
        from django.utils import timezone
        sub, created = SubscriberAccount.objects.get_or_create(
            email=admin_email,
            defaults={
                'packageName': 'Professional',
                'isActive': True,
                'expiresAt': timezone.now() + timezone.timedelta(days=12)
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created default active SubscriberAccount override for {admin_email} (Professional package)"))

        # 6. Seed CMS Content
        cms_data = [
            {
                'key': 'hero_title',
                'value': 'The Modular Workspace Suite for Modern Workforces'
            },
            {
                'key': 'hero_subtitle',
                'value': 'Secure, scalable, and fully customizable. Streamline attendance, tasks, auditing, billing, and all your critical business operations in one unified ecosystem.'
            },
            {
                'key': 'features_title',
                'value': 'Designed for Accountability and Compliance'
            },
            {
                'key': 'features_subtitle',
                'value': 'Everything you need to automate workforce check-ins, record actions securely, and keep operations running cleanly.'
            },
            {
                'key': 'hero_video_url',
                'value': 'https://www.w3schools.com/html/mov_bbb.mp4'
            }
        ]

        for item in cms_data:
            obj, created = CMSContent.objects.get_or_create(
                key=item['key'],
                defaults={'value': item['value']}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created CMS Content: {item['key']}"))
            else:
                obj.value = item['value']
                obj.save()
                self.stdout.write(f"Updated CMS Content: {item['key']}")

        # 7. Seed Testimonials
        from api.models import Testimonial
        testimonials_data = [
            {
                'stars': 5,
                'text': 'Implementing CubeLogs cut down our timesheet dispute rates by 94%. Geofencing ensures that technicians are on-site before clocking in, and the audit logs keep everything compliance-friendly.',
                'author_initials': 'RS',
                'author_name': 'Robert Shaw',
                'author_title': 'Operations Director, Vortex Logistics',
                'bg_color': 'var(--primary)',
                'is_approved': True
            },
            {
                'stars': 5,
                'text': 'The biometric photo log has eliminated buddy punching entirely in our warehouses. I can instantly verify attendance histories from the backoffice dashboard with full geolocation accuracy.',
                'author_initials': 'MH',
                'author_name': 'Maria Halen',
                'author_title': 'HR Manager, Aether Manufacturing',
                'bg_color': 'var(--secondary)',
                'is_approved': True
            },
            {
                'stars': 5,
                'text': 'We love the module flexibility. Being able to choose only Attendance and Tasks allowed us to stay within budget while scaling our remote operations across 12 branch locations.',
                'author_initials': 'DK',
                'author_name': 'David Kovic',
                'author_title': 'Chief Operating Officer, Apex Tech',
                'bg_color': '#818cf8',
                'is_approved': True
            }
        ]

        for item in testimonials_data:
            obj, created = Testimonial.objects.get_or_create(
                author_name=item['author_name'],
                defaults=item
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Testimonial: {item['author_name']}"))

        self.stdout.write(self.style.SUCCESS('Database seeding completed!'))
