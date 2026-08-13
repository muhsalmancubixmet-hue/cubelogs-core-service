"""
CubeLogs Seed Script
Creates demo organization, default roles, permissions, and test users/employees.
"""

import os
import sys
import django

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
django.setup()

from core.models import Organization, OrgSettings
from users.models import Employee, Role
from users.roles import sync_default_roles

# Set default password to Password123 (matching what user entered in UI)
DEFAULT_PASSWORD = "Password123"

SEED_USERS = [
    {
        "email": "admin@cubelogs.com",
        "username": "admin",
        "first_name": "System",
        "last_name": "Admin",
        "designation": "Super Administrator",
        "role_name": "Super Admin",
        "role_slug": "super-admin",
        "is_superuser": True,
        "is_staff": True,
        "isSuperAdmin": True,
    },
    {
        "email": "companyadmin@cubelogs.com",
        "username": "companyadmin",
        "first_name": "Sarah",
        "last_name": "Connor",
        "designation": "Managing Director",
        "role_name": "Company Admin",
        "role_slug": "company-admin",
        "is_superuser": False,
        "is_staff": True,
        "isSuperAdmin": False,
    },
    {
        "email": "pm@cubelogs.com",
        "username": "pm_john",
        "first_name": "John",
        "last_name": "Doe",
        "designation": "Senior Project Manager",
        "role_name": "Project Manager",
        "role_slug": "project-manager",
        "is_superuser": False,
        "is_staff": False,
        "isSuperAdmin": False,
    },
    {
        "email": "developer@cubelogs.com",
        "username": "dev_alex",
        "first_name": "Alex",
        "last_name": "Morgan",
        "designation": "Full Stack Developer",
        "role_name": "Employee",
        "role_slug": "employee",
        "is_superuser": False,
        "is_staff": False,
        "isSuperAdmin": False,
    },
    {
        "email": "qa@cubelogs.com",
        "username": "qa_emily",
        "first_name": "Emily",
        "last_name": "Watson",
        "designation": "QA Lead",
        "role_name": "Employee",
        "role_slug": "employee",
        "is_superuser": False,
        "is_staff": False,
        "isSuperAdmin": False,
    },
    {
        "email": "hr@cubelogs.com",
        "username": "hr_lisa",
        "first_name": "Lisa",
        "last_name": "Ray",
        "designation": "HR Manager",
        "role_name": "HR Manager",
        "role_slug": "hr-manager",
        "is_superuser": False,
        "is_staff": False,
        "isSuperAdmin": False,
    },
    {
        "email": "viewer@cubelogs.com",
        "username": "viewer_sam",
        "first_name": "Sam",
        "last_name": "Altman",
        "designation": "Product Observer",
        "role_name": "Viewer",
        "role_slug": "viewer",
        "is_superuser": False,
        "is_staff": False,
        "isSuperAdmin": False,
    },
]


def run_seed():
    print("=" * 65)
    print("STARTING CUBELOGS DATABASE SEEDING...")
    print("=" * 65)

    # 1. Determine active Organization
    # Prefer organization of existing active admin/users (e.g. salmankcsiju@gmail.com or org with most users)
    org = None
    existing_user = Employee.objects.filter(email='salmankcsiju@gmail.com').first()
    if existing_user and existing_user.organization:
        org = existing_user.organization
    else:
        # Fallback to org with most users or first org
        org = Organization.objects.first()

    if not org:
        settings = OrgSettings.objects.create(
            is_attendance_enabled=True,
            is_project_enabled=True,
        )
        org = Organization.objects.create(
            name="CubeLogs Inc",
            subdomain="cubelogs",
            settings=settings,
        )
        print(f"[+] Created default Organization: '{org.name}' ({org.subdomain})")
    else:
        print(f"[*] Target Organization for Seed Users: '{org.name}' (Subdomain: {org.subdomain}, ID: {org.id})")

    # 2. Sync Default Roles & Permissions for target organization and global
    print("[*] Syncing permissions and system roles...")
    sync_default_roles(organization=org)
    sync_default_roles(organization=None)
    print("[+] System roles & permissions synchronized successfully.")

    # 3. Seed Users
    created_users_info = []

    for user_data in SEED_USERS:
        email = user_data["email"]
        role_slug = user_data["role_slug"]
        
        # Get role object for this org or global system role
        role_obj = Role.objects.filter(slug=role_slug, organization=org).first() or Role.objects.filter(slug=role_slug).first()

        user, created = Employee.objects.get_or_create(
            email=email,
            defaults={
                "username": user_data["username"],
                "first_name": user_data["first_name"],
                "last_name": user_data["last_name"],
                "designation": user_data["designation"],
                "role": role_obj,
                "role_name": user_data["role_name"],
                "organization": org,
                "is_superuser": user_data["is_superuser"],
                "is_staff": user_data["is_staff"],
                "isSuperAdmin": user_data["isSuperAdmin"],
                "employment_status": "Active",
            }
        )

        # Set password to Password123
        user.set_password(DEFAULT_PASSWORD)
        user.username = user_data["username"]
        user.first_name = user_data["first_name"]
        user.last_name = user_data["last_name"]
        user.designation = user_data["designation"]
        user.role = role_obj
        user.role_name = user_data["role_name"]
        user.organization = org
        user.is_superuser = user_data["is_superuser"]
        user.is_staff = user_data["is_staff"]
        user.isSuperAdmin = user_data["isSuperAdmin"]
        user.employment_status = "Active"
        user.save()

        action_str = "Created" if created else "Updated"
        print(f"  [{action_str}] User: {user.get_full_name()} <{user.email}> ({user_data['role_name']}) in Org '{org.name}'")

        created_users_info.append({
            "name": user.get_full_name(),
            "email": user.email,
            "username": user.username,
            "password": DEFAULT_PASSWORD,
            "role": user_data["role_name"],
            "designation": user_data["designation"],
            "org": org.name,
        })

    print("\n" + "=" * 65)
    print("SEEDING COMPLETED SUCCESSFULLY!")
    print("=" * 65)
    print("\nSEEDED TEST ACCOUNTS SUMMARY:\n")
    print(f"{'NAME':<18} | {'EMAIL / USERNAME':<25} | {'PASSWORD':<12} | {'ROLE':<16} | {'ORGANIZATION'}")
    print("-" * 95)
    for u in created_users_info:
        print(f"{u['name']:<18} | {u['email']:<25} | {u['password']:<12} | {u['role']:<16} | {u['org']}")
    print("-" * 95)


if __name__ == "__main__":
    run_seed()
