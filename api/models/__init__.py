# models/__init__.py
#
# This package replaces the old monolithic models.py.
# All public names are re-exported here so that every existing import of the
# form `from api.models import SomeModel` continues to work without any change.

from api.models.employee import (
    PERMISSION_FLAGS,
    EmployeeManager,
    Employee,
)

from api.models.organization import (
    default_weekly_holidays_default,
    OrgSettings,
    Organization,
)

from api.models.attendance import (
    AttendanceLog,
    Schedule,
)

from api.models.tasks_model import (
    Task,
)

from api.models.leaves import (
    LeaveType,
    Leave,
)

from api.models.misc import (
    Holiday,
    Template,
    OfficeLocation,
    AuditLog,
)

from api.models.crm import (
    Lead,
    LeadHistory,
)

from api.models.billing import (
    SubscriptionPackage,
    SubscriberAccount,
    Wallet,
    WalletTransaction,
    BackofficeCoupon,
    MonthlyInvoice,
    Coupon,
    default_coupon_code,
)

from api.models.communications import (
    EmailQueue,
    EmailLog,
)

from api.models.cms import (
    CMSContent,
    LMSModule,
    PromoVideoSection,
    Testimonial,
)

__all__ = [
    # employee
    'PERMISSION_FLAGS',
    'EmployeeManager',
    'Employee',
    # organization
    'default_weekly_holidays_default',
    'OrgSettings',
    'Organization',
    # attendance
    'AttendanceLog',
    'Schedule',
    # tasks
    'Task',
    # leaves
    'LeaveType',
    'Leave',
    # misc
    'Holiday',
    'Template',
    'OfficeLocation',
    'AuditLog',
    # crm
    'Lead',
    'LeadHistory',
    # billing
    'SubscriptionPackage',
    'SubscriberAccount',
    'Wallet',
    'WalletTransaction',
    'BackofficeCoupon',
    'MonthlyInvoice',
    'Coupon',
    'default_coupon_code',
    # communications
    'EmailQueue',
    'EmailLog',
    # cms
    'CMSContent',
    'LMSModule',
    'PromoVideoSection',
    'Testimonial',
]
