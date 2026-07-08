# CubeLogs Backend

This is the backend service for the CubeLogs Workforce Platform, built with Python 3.11+, Django 5, and Django REST Framework (DRF).

## Features

- **JWT Authentication**: Secure login and token refresh using `djangorestframework-simplejwt`.
- **Custom User Model**: Custom `Employee` model extending Django's standard User.
- **SQLite Database**: Self-contained SQLite configuration for ease of development.
- **Dynamic Seeding**: Custom `seed` command to populate default leave types, schedules, locations, and default Super Admin credentials.
- **Global CORS**: Ready to integrate with the Next.js frontend application.

## Prerequisites

- Python 3.10 or higher installed.

## Getting Started

1. **Navigate to the backend directory**:
   ```bash
   cd backend
   ```

2. **Set up virtual environment & install dependencies**:
   ```bash
   # Create a virtual environment
   python -m venv .venv

   # Activate it (Windows)
   .venv\Scripts\activate

   # Install requirements
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to `.env` (it is pre-configured with dev defaults):
   ```bash
   copy .env.example .env
   ```

4. **Run Database Migrations**:
   ```bash
   python manage.py migrate
   ```

5. **Seed Default Data**:
   Populates standard leave types, office locations, default schedules, settings, and creates a default Super Admin user (`admin@cubelogs.com` / `admin123`):
   ```bash
   python manage.py seed
   ```

6. **Create Custom Super Admin manually (Optional)**:
   If you want to create a custom Super Admin, run Django's native command:
   ```bash
   python manage.py createsuperuser
   ```

7. **Start Development Server**:
   ```bash
   python manage.py runserver 8000
   ```

The APIs will be live at `http://127.0.0.1:8000/api/`.

---

## Codebase Modular Refactoring

The backend codebase has been refactored from a monolithic setup into highly scalable, domain-driven package modules under `api/`:

- **Models (`api/models/`)**: Decoupled models package representing database schema modules. Every model module (e.g., `employee`, `organization`, `billing`) is explicitly defined and unified in `api/models/__init__.py`.
- **Views (`api/views/`)**: Deconstructed views package separated by functional domains (e.g. `auth.py`, `employee.py`, `attendance.py`, `billing.py`, `cms.py`). All views are imported and re-exported in `api/views/__init__.py` keeping `api/urls.py` intact.
- **Serializers (`api/serializers/`)**: Extracted serialization mapping layers isolated by feature boundaries.
- **Celery Tasks (`api/tasks/`)**: Decoupled async job functions separated into email queue dispatch tasks and billing/Stripe sync logic.
- **Signals (`api/signals/`)**: System triggers for tenant creation, user setup notifications, and webhook alerts separated into modular signal files.

---

## API Documentation

### Authentication Endpoints
- `POST /api/auth/login/` - Authenticate using email & password. Returns JWT tokens and current user details.
- `POST /api/auth/refresh/` - Refresh access token.
- `GET /api/auth/me/` - Retrieve authenticated user profile.

### Core Endpoints (CRUD)
- `/api/employees/` - Employee Directory management.
- `/api/attendance/` - Attendance logs.
  - `POST /api/attendance/clock-in/` - Record clock-in with verification.
  - `POST /api/attendance/clock-out/` - Record clock-out.
- `/api/tasks/` - Assign/track staff tasks.
- `/api/leave-types/` - Custom leave categories (CL, SL, EL, etc.).
- `/api/leaves/` - Staff leave requests.
  - `PATCH /api/leaves/{id}/status/` - Approve/Reject a request.
- `/api/holidays/` - Public holiday lists.
- `/api/templates/` - Role/designation permission presets.
- `/api/locations/` - Authorized office premises coords and radius.
- `/api/schedules/` - Shift timings per designation.
- `/api/settings/` - Organization branding and subscription.
