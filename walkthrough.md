# Walkthrough: Django REST Framework Backend Build

We have successfully created a complete Django REST Framework backend for the CubeLogs Workforce Platform under the `backend/` directory.

## What Was Accomplished

1. **Local Setup & Virtual Environment**:
   - Created a python virtual environment `.venv`.
   - Installed all required backend packages (`django`, `djangorestframework`, `djangorestframework-simplejwt`, `django-cors-headers`, `python-decouple`).
   - Standardized config values into `.env` and `.env.example` configurations.

2. **Core `api` Application**:
   - Designed 10 cohesive database models to fully match the frontend models:
     - `Employee`: Custom User model extending `AbstractUser`, using `email` as standard identifier.
     - `AttendanceLog`: Supports check-in coords and base64 verification photos.
     - `Task`: Assignable staff task logs.
     - `LeaveType`: Custom category limits and settings.
     - `Leave`: Standard staff leave request applications.
     - `Holiday`: Standard system-wide holiday listings.
     - `Template`: Designation permission presets.
     - `OfficeLocation`: Multi-location geofencing presets.
     - `Schedule`: Designation shift hour ranges.
     - `OrgSettings`: Branding and subscription metadata singleton.
   - Built corresponding `serializers.py` with custom password hashing.
   - Built `views.py` using DRF `ModelViewSet` along with:
     - `@action` clock-in and clock-out routines under `/api/attendance/`.
     - `@action` status modification under `/api/leaves/`.
     - Singular settings wrapper under `/api/settings/current/`.
     - Custom JWT authentication flow showing user details in the login response payload.

3. **Custom Seeding System**:
   - Implemented package-based management command `python manage.py seed`.
   - Seeded standard default leave types (Casual Leave CL, Sick Leave SL, Earned Leave EL) with matching settings.
   - Seeded default office location coordinates, schedules, settings, and automatically created the default Super Admin user (`admin@cubelogs.com` / `admin123`).

---

## Verification & Validation

### 1. Database Migrations
Created and successfully ran migration scripts:
```bash
python manage.py makemigrations
python manage.py migrate
```

### 2. Seeding Run
Seeded database data successfully using the command:
```bash
python manage.py seed
```
Output:
```
Seeding database...
Created Leave Type: Casual Leave (CL)
Created Leave Type: Sick Leave (SL)
Created Leave Type: Earned Leave (EL)
Created primary Office Location: Head Office
Created Schedule: Admin (09:00 - 17:00)
Created OrgSettings (12 subscription days)
No superuser found. Creating default Super Admin...
Successfully created default Super Admin user: admin@cubelogs.com / admin123
Database seeding completed!
```

### 3. Application Soundness
Executed system check to verify setup integrity:
```bash
python manage.py check
```
Result:
```
System check identified no issues (0 silenced).
```
