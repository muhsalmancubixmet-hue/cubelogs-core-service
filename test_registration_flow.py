import os
import sys
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
django.setup()

from api.models import Lead, Employee, SubscriberAccount, Organization
from rest_framework.test import APIClient

email = "endtoend@test.com"

# Clean up
Employee.objects.filter(email=email).delete()
Lead.objects.filter(email=email).delete()
SubscriberAccount.objects.filter(email=email).delete()
Organization.objects.filter(subdomain="endtoend-test-com").delete()

print("Submitting Lead...")
client = APIClient()
response = client.post('/api/leads/', {
    'name': 'End to End User',
    'email': email,
    'phone': '1234567890',
    'companyName': 'E2E Corp',
    'message': 'Employees: 25 | Modules: Attendance Management, Project & Tasks Management | Total: 2500/mo'
}, format='json')

print("Lead Response:", response.status_code, response.data)

emp = Employee.objects.filter(email=email).first()
if not emp:
    print("Employee was not created!")
    sys.exit(1)

print("Employee details:", emp.username, emp.isSuperAdmin, emp.permissions)

# Force password for login
emp.set_password("password123")
emp.save()

print("Logging in...")
login_response = client.post('/api/auth/login/', {
    'email': email,
    'password': 'password123'
}, format='json')

print("Login status:", login_response.status_code)
access_token = login_response.data.get('access')

client.credentials(HTTP_AUTHORIZATION='Bearer ' + access_token)
me_response = client.get('/api/auth/me/')

print("ME Response:", me_response.status_code)
data = me_response.data
sub = data.get('subscription', {})
print("is_attendance_enabled:", sub.get('is_attendance_enabled'))
print("is_project_enabled:", sub.get('is_project_enabled'))
print("permissions:", data.get('permissions'))
