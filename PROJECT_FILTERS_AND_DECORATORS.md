# Project Filters and Decorators Documentation

This document outlines the organization, purpose, and usage of Custom Decorators, Filters, and Mixins implemented within the Django project.

---

# Decorators

## permission_required
- **File**: `core/decorators.py`
- **Purpose**: Restricts access to view actions based on custom user permission strings stored in the `Employee.permissions` JSONField.
- **Working**: Retrieves `request.user.permissions` (list of strings). If the user is a superuser or is designated as `isSuperAdmin`, access is granted. Otherwise, validates if at least one of the required permission arguments is present in the user's permission list. Returns a `401 Unauthorized` JSON response if validation fails.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import permission_required

    @permission_required('tasks:create', 'tasks:view')
    def my_view(request):
        ...
    ```
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import permission_required

    @method_decorator(permission_required('tasks:create'), name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## role_required
- **File**: `core/decorators.py`
- **Purpose**: Restricts access using Django's default permissions system (user_permissions).
- **Working**: Verifies whether the authenticated user has any of the requested Django codenames via `request.user.user_permissions.filter(codename__in=permissions).exists()`. Bypasses checks for superusers and superadmins. Returns a `401 Unauthorized` JSON response if validation fails.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import role_required

    @role_required('add_employee', 'change_employee')
    def my_view(request):
        ...
    ```
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import role_required

    @method_decorator(role_required('add_employee'), name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## group_required
- **File**: `core/decorators.py`
- **Purpose**: Restricts view access to users belonging to standard Django User Groups.
- **Working**: Uses Django's built-in `user_passes_test` decorator to check if the user is authenticated and belongs to at least one of the specified group names. Redirects unauthorized users to the login page.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import group_required

    @group_required('HR Managers', 'Administrators')
    def my_view(request):
        ...
    ```
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import group_required

    @method_decorator(group_required('HR Managers'), name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## user_access_required
- **File**: `core/decorators.py`
- **Purpose**: Restricts access based on user groups but returns a standard JSON response instead of redirecting.
- **Working**: Evaluates group membership via `user.groups.filter(name__in=group_names).exists()`. If the user is not in the specified groups, it returns a standard JSON error response with `StatusCode: 6001` and status code `401 Unauthorized`.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import user_access_required

    @user_access_required('HR Managers')
    def my_view(request):
        ...
    ```
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import user_access_required

    @method_decorator(user_access_required('HR Managers'), name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## plan_permission_required
- **File**: `core/decorators.py`
- **Purpose**: Checks plan-based subscription limits and features.
- **Working**: Resolves the organization's subscription plan by fetching the `SubscriberAccount` associated with the organization's superadmin email. Reads the active plan features from the `SubscriptionPackage.features` JSONField. Grants access if the plan has the required feature. Returns `401 Unauthorized` with a plan restriction message on validation failure.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import plan_permission_required

    @plan_permission_required('attendance:admin')
    def my_view(request):
        ...
    ```
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import plan_permission_required

    @method_decorator(plan_permission_required('attendance:admin'), name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## check_mode
- **File**: `core/decorators.py`
- **Purpose**: Restricts application usage when Down, Read-Only, or Maintenance mode is enabled.
- **Working**: Queries the first row of the `Mode` model.
  - If `down=True`, blocks all requests and returns a `503 Service Unavailable` page or JSON response.
  - If `readonly=True`, blocks all write methods (POST, PUT, PATCH, DELETE) returning a `403 Forbidden` response.
  - If `maintenance=True`, blocks all requests returning a `503 Service Under Maintenance` response.
- **Example Usage**:
  - *Class-Based Views (CBV)*:
    ```python
    from django.utils.decorators import method_decorator
    from core.decorators import check_mode

    @method_decorator(check_mode, name='dispatch')
    class MyViewSet(viewsets.ModelViewSet):
        ...
    ```

## ajax_required
- **File**: `core/decorators.py`
- **Purpose**: Restricts endpoints to AJAX-only requests.
- **Working**: Validates if `HTTP_X_REQUESTED_WITH` header matches `XMLHttpRequest`. Returns a `400 Bad Request` JSON response if validation fails.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import ajax_required

    @ajax_required
    def my_ajax_view(request):
        ...
    ```

## timer
- **File**: `core/decorators.py`
- **Purpose**: Development helper decorator to measure execution speed and query efficiency.
- **Working**: Wraps views, recording start/end execution timestamps and query counts (`len(connection.queries)`). Evaluates duplicate queries by checking SQL strings, printing a detailed debug log statement to the console.
- **Example Usage**:
  - *Function-Based Views (FBV)*:
    ```python
    from core.decorators import timer

    @timer
    def my_slow_view(request):
        ...
    ```

---

# Filters

All filters are built on `django-filter` to enforce exact matching, search across fields (including related model attributes), and support custom sort ordering.

## EmployeeFilter
- **File**: `users/filters.py`
- **Model**: `Employee`
- **Search fields**: `first_name`, `last_name`, `email`, `phone`, `designation`, `organization__name`
- **Usage**:
  ```python
  class EmployeeViewSet(FilterMixinNew, viewsets.ModelViewSet):
      filter_backends = [DjangoFilterBackend]
      filterset_class = EmployeeFilter
  ```

## TemplateFilter
- **File**: `users/filters.py`
- **Model**: `Template`
- **Search fields**: `name`
- **Usage**: Used inside `TemplateViewSet` to filter security templates by name.

## TaskFilter
- **File**: `tasks/filters.py`
- **Model**: `Task`
- **Search fields**: `title`, `description`, `assignedName`, `assignedTo__first_name`, `assignedTo__last_name`, `assignedTo__email`
- **Usage**: Used inside `TaskViewSet` to search and order tasks.

## AttendanceLogFilter
- **File**: `attendance/filters.py`
- **Model**: `AttendanceLog`
- **Search fields**: `employeeName`, `employee__first_name`, `employee__last_name`, `employee__email`
- **Usage**: Applied on `AttendanceLogViewSet` to inspect daily clock logs.

## ScheduleFilter
- **File**: `attendance/filters.py`
- **Model**: `Schedule`
- **Search fields**: `designation`
- **Usage**: Applied on `ScheduleViewSet` to find shifts.

## LeaveTypeFilter
- **File**: `attendance/filters.py`
- **Model**: `LeaveType`
- **Search fields**: `name`, `description`
- **Usage**: Applied on `LeaveTypeViewSet` to search leave configurations.

## LeaveFilter
- **File**: `attendance/filters.py`
- **Model**: `Leave`
- **Search fields**: `employeeName`, `leaveTypeName`, `reason`, `employee__first_name`, `employee__last_name`, `employee__email`
- **Usage**: Applied on `LeaveViewSet` to search leave applications.

## HolidayFilter
- **File**: `attendance/filters.py`
- **Model**: `Holiday`
- **Search fields**: `name`, `description`
- **Usage**: Applied on `HolidayViewSet` to search public calendars.

## OfficeLocationFilter
- **File**: `attendance/filters.py`
- **Model**: `OfficeLocation`
- **Search fields**: `name`
- **Usage**: Applied on `OfficeLocationViewSet` to search office sites.

## SubscriptionPackageFilter
- **File**: `company/filters.py`
- **Model**: `SubscriptionPackage`
- **Search fields**: `name`, `features`
- **Usage**: Used inside `SubscriptionPackageViewSet` to filter plans.

## SubscriberAccountFilter
- **File**: `company/filters.py`
- **Model**: `SubscriberAccount`
- **Search fields**: `email`, `packageName`
- **Usage**: Used inside `SubscriberAccountViewSet` to list subscriber organization plans.

## LeadFilter
- **File**: `company/filters.py`
- **Model**: `Lead`
- **Search fields**: `name`, `email`, `phone`, `companyName`, `message`, `assigned_staff__first_name`, `assigned_staff__last_name`, `assigned_staff__email`
- **Usage**: Used inside `LeadViewSet` to search and order CRM leads.

## CouponFilter
- **File**: `company/filters.py`
- **Model**: `Coupon`
- **Search fields**: `code`
- **Usage**: Used inside `CouponViewSet`.

## BackofficeCouponFilter
- **File**: `company/filters.py`
- **Model**: `BackofficeCoupon`
- **Search fields**: `code`, `value_type`
- **Usage**: Used inside `BackofficeCouponViewSet`.

## CMSContentFilter
- **File**: `company/filters.py`
- **Model**: `CMSContent`
- **Search fields**: `key`, `value`
- **Usage**: Used inside `CMSContentViewSet`.

## LMSModuleFilter
- **File**: `company/filters.py`
- **Model**: `LMSModule`
- **Search fields**: `title`, `description`, `category`
- **Usage**: Used inside `LMSModuleViewSet`.

## TestimonialFilter
- **File**: `company/filters.py`
- **Model**: `Testimonial`
- **Search fields**: `author_name`, `author_title`, `text`
- **Usage**: Used inside `TestimonialViewSet`.

## PromoVideoSectionFilter
- **File**: `company/filters.py`
- **Model**: `PromoVideoSection`
- **Search fields**: `title`, `description`
- **Usage**: Used inside `PromoVideoSectionViewSet`.

---

# Mixins

## FilterMixinNew
- **File**: `core/mixins.py`
- **Purpose**: Persists filter values into the user's session and restores them automatically upon subsequent visits when no filter params are present.
- **Available methods**:
  - `_get_session_key(request)`: Generates a unique key based on the user ID, view class name, and request path.
  - `_get_session_data(request)`: Fetches filter data from request session.
  - `_save_session_data(request, data)`: Stores active parameters to the session and flags it modified.
  - `get_saved_data(request)`: Public accessor to session-stored filters.
  - `clear_saved_data(request)`: Deletes stored filters for this page.
  - `handle_filter_session(request)`: Performs primary orchestration: saves filter inputs if present, restores saved filters if absent, or clears them if `clear_filters` is requested.
