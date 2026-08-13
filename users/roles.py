# --------------------------------------------------------------------------------
#       Users - Canonical Role System & Default Permissions Registry
# --------------------------------------------------------------------------------

from django.utils.text import slugify

# Complete definitions of permission flags for DB seeding
ALL_PERMISSION_DEFS = [
    { 'id': 'dashboard', 'label': 'My Dashboard Analytics', 'category': 'dashboard', 'category_label': 'General Access' },
    { 'id': 'audit_logs:view', 'label': 'System Audit Logs', 'category': 'audit', 'category_label': 'General Access' },
    { 'id': 'admin:templates', 'label': 'Manage Templates (Admin Panel)', 'category': 'settings', 'category_label': 'System Settings' },
    { 'id': 'admin:employees', 'label': 'Manage Employees (Onboard / Edit)', 'category': 'settings', 'category_label': 'System Settings' },
    { 'id': 'attendance:staff', 'label': 'Clock-In / Clock-Out Dashboard', 'category': 'attendance', 'category_label': 'Attendance Management' },
    { 'id': 'attendance:admin', 'label': 'Real-time Global Attendance Monitor', 'category': 'attendance', 'category_label': 'Attendance Management' },
    { 'id': 'attendance:management_portal', 'label': 'Attendance Management Portal', 'category': 'attendance', 'category_label': 'Attendance Management' },
    { 'id': 'leaves:apply', 'label': 'Apply Leave Form', 'category': 'leaves', 'category_label': 'Attendance Management' },
    { 'id': 'leaves:approve', 'label': 'Leave Approval Portal', 'category': 'leaves', 'category_label': 'Attendance Management' },
    { 'id': 'leaves:manage', 'label': 'Manage Leave Types', 'category': 'leaves', 'category_label': 'Attendance Management' },
    { 'id': 'holidays:manage', 'label': 'Configure System Holidays', 'category': 'holidays', 'category_label': 'Attendance Management' },
    { 'id': 'holidays:view', 'label': 'View Holiday Calendar', 'category': 'holidays', 'category_label': 'Attendance Management' },
    { 'id': 'locations:manage', 'label': 'Manage Locations', 'category': 'locations', 'category_label': 'System Settings' },
    { 'id': 'settings:branding', 'label': 'Manage Branding', 'category': 'settings', 'category_label': 'System Settings' },
    { 'id': 'settings:billing', 'label': 'Manage Billing & Subscriptions', 'category': 'billing', 'category_label': 'System Settings' },

    # Role & Permission Management
    { 'id': 'roles.view', 'label': 'View System & Custom Roles', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'roles.create', 'label': 'Create Custom Roles', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'roles.edit', 'label': 'Edit Roles & Permissions', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'roles.delete', 'label': 'Delete Custom Roles', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'roles.assign', 'label': 'Assign Roles to Employees', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'roles.duplicate', 'label': 'Duplicate Roles', 'category': 'roles', 'category_label': 'Administration' },
    { 'id': 'permissions.view', 'label': 'View Permission Registry', 'category': 'permissions', 'category_label': 'Administration' },
    { 'id': 'permissions.manage', 'label': 'Manage Permission Assignments', 'category': 'permissions', 'category_label': 'Administration' },

    # Project Overview
    { 'id': 'projects.overview.view', 'label': 'View Project Overview', 'category': 'project_overview', 'category_label': 'Project Management' },
    { 'id': 'projects:view', 'label': 'View Projects', 'category': 'project_overview', 'category_label': 'Project Management' },
    { 'id': 'projects:create', 'label': 'Create Projects', 'category': 'project_overview', 'category_label': 'Project Management' },
    { 'id': 'projects:update', 'label': 'Edit Projects', 'category': 'project_overview', 'category_label': 'Project Management' },
    { 'id': 'projects:delete', 'label': 'Delete Projects', 'category': 'project_overview', 'category_label': 'Project Management' },

    # Backlog
    { 'id': 'projects.backlog.view', 'label': 'View Backlog', 'category': 'project_backlog', 'category_label': 'Project Management' },
    { 'id': 'projects.backlog.create', 'label': 'Create Backlog Story', 'category': 'project_backlog', 'category_label': 'Project Management' },
    { 'id': 'projects.backlog.edit', 'label': 'Edit Backlog Story', 'category': 'project_backlog', 'category_label': 'Project Management' },
    { 'id': 'projects.backlog.delete', 'label': 'Delete Backlog Story', 'category': 'project_backlog', 'category_label': 'Project Management' },
    { 'id': 'projects.backlog.move', 'label': 'Move Story to Sprint', 'category': 'project_backlog', 'category_label': 'Project Management' },
    { 'id': 'projects.backlog.assign', 'label': 'Assign Story Members', 'category': 'project_backlog', 'category_label': 'Project Management' },

    # Epics
    { 'id': 'projects.epic.view', 'label': 'View Epics', 'category': 'project_epics', 'category_label': 'Project Management' },
    { 'id': 'projects.epic.create', 'label': 'Create Epic', 'category': 'project_epics', 'category_label': 'Project Management' },
    { 'id': 'projects.epic.edit', 'label': 'Edit Epic', 'category': 'project_epics', 'category_label': 'Project Management' },
    { 'id': 'projects.epic.delete', 'label': 'Delete Epic', 'category': 'project_epics', 'category_label': 'Project Management' },
    { 'id': 'projects.epic.assign', 'label': 'Assign Stories to Epic', 'category': 'project_epics', 'category_label': 'Project Management' },

    # Stories
    { 'id': 'projects.story.view', 'label': 'View Stories', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.create', 'label': 'Create Story', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.edit', 'label': 'Edit Story', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.delete', 'label': 'Delete Story', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.assign', 'label': 'Assign Story', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.status', 'label': 'Change Story Status', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'projects.story.move', 'label': 'Move Story', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'project_stories:view', 'label': 'View Project Sections', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'project_stories:create', 'label': 'Create Project Sections', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'project_stories:update', 'label': 'Edit Project Sections', 'category': 'project_stories', 'category_label': 'Project Management' },
    { 'id': 'project_stories:delete', 'label': 'Delete Project Sections', 'category': 'project_stories', 'category_label': 'Project Management' },

    # Tasks
    { 'id': 'projects.task.view', 'label': 'View Tasks', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.create', 'label': 'Create Task', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.edit', 'label': 'Edit Task', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.delete', 'label': 'Delete Task', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.assign', 'label': 'Assign Task', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.update_status', 'label': 'Change Task Status', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.task.log_time', 'label': 'Log Time', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:view_all', 'label': 'View All Project Tasks', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:create', 'label': 'Create Project Tasks', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:update_all', 'label': 'Edit All Project Tasks', 'category': 'project_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:delete', 'label': 'Delete Project Tasks', 'category': 'project_tasks', 'category_label': 'Project Management' },

    # My Tasks
    { 'id': 'projects.my_tasks.view', 'label': 'View My Tasks', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.my_tasks.update', 'label': 'Update My Tasks', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.my_tasks.update_status', 'label': 'Update My Task Status', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.my_tasks.log_time', 'label': 'Log My Time', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'projects.my_tasks.subtasks', 'label': 'Manage My Subtasks', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:view_own', 'label': 'View Assigned Project Tasks', 'category': 'project_my_tasks', 'category_label': 'Project Management' },
    { 'id': 'project_tasks:update_own', 'label': 'Update Assigned Task Status', 'category': 'project_my_tasks', 'category_label': 'Project Management' },

    # Subtasks
    { 'id': 'projects.subtask.create', 'label': 'Create Subtask', 'category': 'project_subtasks', 'category_label': 'Project Management' },
    { 'id': 'projects.subtask.edit', 'label': 'Edit Subtask', 'category': 'project_subtasks', 'category_label': 'Project Management' },
    { 'id': 'projects.subtask.delete', 'label': 'Delete Subtask', 'category': 'project_subtasks', 'category_label': 'Project Management' },
    { 'id': 'projects.subtask.complete', 'label': 'Complete Subtask', 'category': 'project_subtasks', 'category_label': 'Project Management' },

    # Sprints
    { 'id': 'projects.sprint.view', 'label': 'View Sprints', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.create', 'label': 'Create Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.edit', 'label': 'Edit Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.delete', 'label': 'Delete Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.start', 'label': 'Start Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.complete', 'label': 'Complete Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.cancel', 'label': 'Cancel Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.reopen', 'label': 'Reopen Sprint', 'category': 'project_sprints', 'category_label': 'Project Management' },
    { 'id': 'projects.sprint.move_stories', 'label': 'Move Stories', 'category': 'project_sprints', 'category_label': 'Project Management' },

    # Scrum Board
    { 'id': 'projects.board.view', 'label': 'View Scrum Board', 'category': 'project_scrum_board', 'category_label': 'Project Management' },
    { 'id': 'projects.board.move_cards', 'label': 'Move Cards', 'category': 'project_scrum_board', 'category_label': 'Project Management' },
    { 'id': 'projects.board.update_status', 'label': 'Update Card Status', 'category': 'project_scrum_board', 'category_label': 'Project Management' },
    { 'id': 'projects.board.manage', 'label': 'Manage Board', 'category': 'project_scrum_board', 'category_label': 'Project Management' },

    # Members
    { 'id': 'projects.members.view', 'label': 'View Members', 'category': 'project_members', 'category_label': 'Project Management' },
    { 'id': 'projects.members.manage', 'label': 'Manage Members', 'category': 'project_members', 'category_label': 'Project Management' },
    { 'id': 'projects.members.assign', 'label': 'Assign Members', 'category': 'project_members', 'category_label': 'Project Management' },
    { 'id': 'projects.members.remove', 'label': 'Remove Members', 'category': 'project_members', 'category_label': 'Project Management' },

    # Comments
    { 'id': 'projects.comment.view', 'label': 'View Comments', 'category': 'project_comments', 'category_label': 'Project Management' },
    { 'id': 'projects.comment.create', 'label': 'Add Comment', 'category': 'project_comments', 'category_label': 'Project Management' },
    { 'id': 'projects.comment.edit', 'label': 'Edit Comment', 'category': 'project_comments', 'category_label': 'Project Management' },
    { 'id': 'projects.comment.delete', 'label': 'Delete Comment', 'category': 'project_comments', 'category_label': 'Project Management' },

    # Attachments
    { 'id': 'projects.attachment.view', 'label': 'View Attachments', 'category': 'project_attachments', 'category_label': 'Project Management' },
    { 'id': 'projects.attachment.upload', 'label': 'Upload Attachments', 'category': 'project_attachments', 'category_label': 'Project Management' },
    { 'id': 'projects.attachment.delete', 'label': 'Delete Attachments', 'category': 'project_attachments', 'category_label': 'Project Management' },
    { 'id': 'projects.attachment.download', 'label': 'Download Attachments', 'category': 'project_attachments', 'category_label': 'Project Management' },

    # Reports & Settings
    { 'id': 'projects.reports.view', 'label': 'View Reports', 'category': 'project_reports', 'category_label': 'Project Management' },
    { 'id': 'projects.reports.export', 'label': 'Export Reports', 'category': 'project_reports', 'category_label': 'Project Management' },
    { 'id': 'projects.settings.view', 'label': 'View Settings', 'category': 'project_settings', 'category_label': 'Project Management' },
    { 'id': 'projects.settings.edit', 'label': 'Edit Settings', 'category': 'project_settings', 'category_label': 'Project Management' },

    # Sprint Retrospective
    { 'id': 'projects.retrospective.view', 'label': 'View Retrospective', 'category': 'project_retrospective', 'category_label': 'Project Management' },
    { 'id': 'projects.retrospective.create', 'label': 'Create Retrospective Item', 'category': 'project_retrospective', 'category_label': 'Project Management' },
    { 'id': 'projects.retrospective.edit', 'label': 'Edit Retrospective', 'category': 'project_retrospective', 'category_label': 'Project Management' },
    { 'id': 'projects.retrospective.close', 'label': 'Close Retrospective', 'category': 'project_retrospective', 'category_label': 'Project Management' },

    # Notifications
    { 'id': 'projects.notifications.manage', 'label': 'Manage Notifications', 'category': 'project_integrations', 'category_label': 'Project Management' },
]

ALL_PERMISSION_KEYS = [p['id'] for p in ALL_PERMISSION_DEFS]

DEFAULT_ROLES = {
    'Super Admin': {
        'name': 'Super Admin',
        'slug': 'super-admin',
        'label': 'Super Admin',
        'description': 'Full administrative access across entire organization.',
        'permissions': ALL_PERMISSION_KEYS
    },
    'Company Admin': {
        'name': 'Company Admin',
        'slug': 'company-admin',
        'label': 'Company Admin',
        'description': 'Organization administration, employee management, billing, and settings.',
        'permissions': ALL_PERMISSION_KEYS
    },
    'Project Manager': {
        'name': 'Project Manager',
        'slug': 'project-manager',
        'label': 'Project Manager',
        'description': 'Full Scrum Project Management: Backlog, Epics, Stories, Sprints, Board, Team Members, Reports, Retrospectives, Stand-ups, Integrations, and Settings.',
        'permissions': [
            'dashboard', 'attendance:staff', 'leaves:apply', 'holidays:view',
            'projects.overview.view', 'projects:view', 'projects:create', 'projects:update',
            'projects.backlog.view', 'projects.backlog.create', 'projects.backlog.edit', 'projects.backlog.delete', 'projects.backlog.move', 'projects.backlog.assign',
            'projects.epic.view', 'projects.epic.create', 'projects.epic.edit', 'projects.epic.delete', 'projects.epic.assign',
            'projects.story.view', 'projects.story.create', 'projects.story.edit', 'projects.story.delete', 'projects.story.assign', 'projects.story.status', 'projects.story.move', 'project_stories:view', 'project_stories:create', 'project_stories:update', 'project_stories:delete',
            'projects.task.view', 'projects.task.create', 'projects.task.edit', 'projects.task.delete', 'projects.task.assign', 'projects.task.update_status', 'projects.task.log_time', 'project_tasks:view_all', 'project_tasks:create', 'project_tasks:update_all', 'project_tasks:delete',
            'projects.my_tasks.view', 'projects.my_tasks.update', 'projects.my_tasks.update_status', 'projects.my_tasks.log_time', 'projects.my_tasks.subtasks', 'project_tasks:view_own', 'project_tasks:update_own',
            'projects.subtask.create', 'projects.subtask.edit', 'projects.subtask.delete', 'projects.subtask.complete',
            'projects.sprint.view', 'projects.sprint.create', 'projects.sprint.edit', 'projects.sprint.delete', 'projects.sprint.start', 'projects.sprint.complete', 'projects.sprint.cancel', 'projects.sprint.reopen', 'projects.sprint.move_stories',
            'projects.board.view', 'projects.board.move_cards', 'projects.board.update_status', 'projects.board.manage',
            'projects.members.view', 'projects.members.manage', 'projects.members.assign', 'projects.members.remove', 'projects:members_manage',
            'projects.comment.view', 'projects.comment.create', 'projects.comment.edit', 'projects.comment.delete',
            'projects.attachment.view', 'projects.attachment.upload', 'projects.attachment.delete', 'projects.attachment.download',
            'projects.reports.view', 'projects.reports.export',
            'projects.settings.view', 'projects.settings.edit',
            'project_statuses:view', 'project_statuses:create', 'project_statuses:update', 'project_statuses:delete',
            'projects.retrospective.view', 'projects.retrospective.create', 'projects.retrospective.edit', 'projects.retrospective.close',
            'projects.notifications.manage'
        ]
    },
    'Employee': {
        'name': 'Employee',
        'slug': 'employee',
        'label': 'Employee',
        'description': 'Standard workspace member: View Overview, Backlog, Sprints, Board, My Tasks, Comments, Attachments, Stand-up, Retrospectives.',
        'permissions': [
            'dashboard', 'attendance:staff', 'leaves:apply', 'holidays:view',
            'projects.overview.view', 'projects:view',
            'projects.backlog.view', 'projects.epic.view', 'projects.story.view', 'projects.task.view',
            'projects.my_tasks.view', 'projects.my_tasks.update', 'projects.my_tasks.update_status', 'projects.my_tasks.log_time', 'projects.my_tasks.subtasks',
            'project_tasks:view_own', 'project_tasks:update_own', 'projects.task.log_time',
            'projects.subtask.create', 'projects.subtask.edit', 'projects.subtask.complete',
            'projects.sprint.view', 'projects.board.view', 'projects.board.move_cards', 'projects.board.update_status', 'projects.story.status', 'project_stories:update',
            'projects.members.view', 'projects.comment.view', 'projects.comment.create',
            'projects.attachment.view', 'projects.attachment.download', 'projects.attachment.upload',
            'project_statuses:view',
            'projects.retrospective.view', 'projects.retrospective.create'
        ]
    },
    'HR Manager': {
        'name': 'HR Manager',
        'slug': 'hr-manager',
        'label': 'HR Manager',
        'description': 'Employee onboarding, Leave approvals, Attendance monitoring, and Holidays.',
        'permissions': [
            'dashboard', 'admin:employees', 'attendance:staff', 'attendance:management_portal', 'attendance:admin',
            'leaves:apply', 'leaves:approve', 'leaves:manage', 'holidays:view', 'holidays:manage', 'holidays:rules'
        ]
    },
    'Attendance Manager': {
        'name': 'Attendance Manager',
        'slug': 'attendance-manager',
        'label': 'Attendance Manager',
        'description': 'Attendance monitoring and clocking logs.',
        'permissions': [
            'dashboard', 'attendance:staff', 'attendance:management_portal', 'attendance:admin', 'leaves:apply', 'holidays:view'
        ]
    },
    'Viewer': {
        'name': 'Viewer',
        'slug': 'viewer',
        'label': 'Viewer',
        'description': 'Read-only access to workspace, project boards, backlog, sprints, comments, attachments, and statuses.',
        'permissions': [
            'dashboard', 'attendance:staff', 'holidays:view',
            'projects.overview.view', 'projects:view',
            'projects.backlog.view', 'projects.epic.view', 'projects.story.view', 'projects.task.view',
            'projects.my_tasks.view', 'projects.sprint.view', 'projects.board.view',
            'projects.members.view', 'projects.comment.view',
            'projects.attachment.view', 'projects.attachment.download',
            'project_statuses:view'
        ]
    }
}


def sync_default_roles(organization=None):
    """
    Idempotently synchronizes PermissionFlag and Role records into the relational database.
    """
    from users.models import PermissionFlag, Role

    # 1. Sync PermissionFlag records
    for flag_def in ALL_PERMISSION_DEFS:
        key = flag_def['id']
        name = flag_def.get('label', key)
        category = flag_def.get('category', 'General')
        module = flag_def.get('category_label', 'General')

        PermissionFlag.objects.update_or_create(
            key=key,
            defaults={
                'name': name,
                'category': category,
                'module': module,
                'is_active': True
            }
        )

    # 2. Sync Role records
    for role_name, role_data in DEFAULT_ROLES.items():
        role_slug = role_data.get('slug', slugify(role_name))

        role, created = Role.objects.get_or_create(
            organization=organization,
            slug=role_slug,
            defaults={
                'name': role_name,
                'label': role_data.get('label', role_name),
                'description': role_data.get('description', ''),
                'is_system_role': True,
                'is_active': True,
            }
        )

        perm_keys = role_data.get('permissions', [])
        if perm_keys == ALL_PERMISSION_KEYS:
            perm_qs = PermissionFlag.objects.filter(is_active=True)
        else:
            perm_qs = PermissionFlag.objects.filter(key__in=perm_keys, is_active=True)

        role.permissions.set(perm_qs)
