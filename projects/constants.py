# --------------------------------------------------------------------------------
#       Projects Constants & Configuration
# --------------------------------------------------------------------------------

FIBONACCI_STORY_POINTS = [1, 2, 3, 5, 8, 13, 21]

SPRINT_STATUS_PLANNING = 'planning'
SPRINT_STATUS_ACTIVE = 'active'
SPRINT_STATUS_COMPLETED = 'completed'
SPRINT_STATUS_CANCELLED = 'cancelled'

SPRINT_STATUS_CHOICES = [
    (SPRINT_STATUS_PLANNING, 'Planning'),
    (SPRINT_STATUS_ACTIVE, 'Active'),
    (SPRINT_STATUS_COMPLETED, 'Completed'),
    (SPRINT_STATUS_CANCELLED, 'Cancelled'),
]

WORK_TYPE_CHOICES = [
    ('Frontend', 'Frontend'),
    ('Backend', 'Backend'),
    ('Testing', 'Testing'),
    ('Design', 'Design'),
    ('DevOps', 'DevOps'),
    ('General', 'General'),
]

PRIORITY_CHOICES = [
    ('Low', 'Low'),
    ('Medium', 'Medium'),
    ('High', 'High'),
    ('Urgent', 'Urgent'),
]

# --------------------------------------------------------------------------------
# Project-Specific Roles & Canonical Permission Bundles
# --------------------------------------------------------------------------------
PROJECT_ROLE_PROJECT_MANAGER = 'Project Manager'
PROJECT_ROLE_TEAM_LEAD = 'Team Lead'
PROJECT_ROLE_CONTRIBUTOR = 'Contributor'
PROJECT_ROLE_DEVELOPER = 'Developer'
PROJECT_ROLE_QA_ENGINEER = 'QA Engineer'
PROJECT_ROLE_DESIGNER = 'Designer'
PROJECT_ROLE_PRODUCT_OWNER = 'Product Owner'
PROJECT_ROLE_VIEWER = 'Viewer'

PROJECT_ROLE_CHOICES = [
    (PROJECT_ROLE_PROJECT_MANAGER, 'Project Manager'),
    (PROJECT_ROLE_TEAM_LEAD, 'Team Lead'),
    (PROJECT_ROLE_CONTRIBUTOR, 'Contributor'),
    (PROJECT_ROLE_DEVELOPER, 'Developer'),
    (PROJECT_ROLE_QA_ENGINEER, 'QA Engineer'),
    (PROJECT_ROLE_DESIGNER, 'Designer'),
    (PROJECT_ROLE_PRODUCT_OWNER, 'Product Owner'),
    (PROJECT_ROLE_VIEWER, 'Viewer'),
]

# Canonical Permission Bundles per Project-Specific Role
PROJECT_ROLE_PERMISSIONS = {
    PROJECT_ROLE_PROJECT_MANAGER: [
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
    ],
    PROJECT_ROLE_TEAM_LEAD: [
        'dashboard', 'attendance:staff', 'leaves:apply', 'holidays:view',
        'projects.overview.view', 'projects:view',
        'projects.backlog.view', 'projects.backlog.create', 'projects.backlog.edit', 'projects.backlog.move', 'projects.backlog.assign',
        'projects.epic.view', 'projects.epic.create', 'projects.epic.edit', 'projects.epic.assign',
        'projects.story.view', 'projects.story.create', 'projects.story.edit', 'projects.story.assign', 'projects.story.status', 'projects.story.move', 'project_stories:view', 'project_stories:create', 'project_stories:update',
        'projects.task.view', 'projects.task.create', 'projects.task.edit', 'projects.task.assign', 'projects.task.update_status', 'projects.task.log_time', 'project_tasks:view_all', 'project_tasks:create', 'project_tasks:update_all',
        'projects.my_tasks.view', 'projects.my_tasks.update', 'projects.my_tasks.update_status', 'projects.my_tasks.log_time', 'projects.my_tasks.subtasks', 'project_tasks:view_own', 'project_tasks:update_own',
        'projects.subtask.create', 'projects.subtask.edit', 'projects.subtask.complete',
        'projects.sprint.view', 'projects.sprint.create', 'projects.sprint.edit', 'projects.sprint.start', 'projects.sprint.complete', 'projects.sprint.move_stories',
        'projects.board.view', 'projects.board.move_cards', 'projects.board.update_status', 'projects.board.manage',
        'projects.members.view', 'projects.members.assign',
        'projects.comment.view', 'projects.comment.create', 'projects.comment.edit', 'projects.comment.delete',
        'projects.attachment.view', 'projects.attachment.upload', 'projects.attachment.delete', 'projects.attachment.download',
        'projects.reports.view', 'projects.settings.view', 'project_statuses:view',
        'projects.retrospective.view', 'projects.retrospective.create', 'projects.retrospective.edit', 'projects.retrospective.close'
    ],
    PROJECT_ROLE_CONTRIBUTOR: [
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
    ],
    PROJECT_ROLE_DEVELOPER: [
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
    ],
    PROJECT_ROLE_QA_ENGINEER: [
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
    ],
    PROJECT_ROLE_DESIGNER: [
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
    ],
    PROJECT_ROLE_PRODUCT_OWNER: [
        'dashboard', 'attendance:staff', 'leaves:apply', 'holidays:view',
        'projects.overview.view', 'projects:view',
        'projects.backlog.view', 'projects.backlog.create', 'projects.backlog.edit', 'projects.backlog.move', 'projects.backlog.assign',
        'projects.epic.view', 'projects.epic.create', 'projects.epic.edit', 'projects.epic.assign',
        'projects.story.view', 'projects.story.create', 'projects.story.edit', 'projects.story.assign', 'projects.story.status', 'projects.story.move', 'project_stories:view', 'project_stories:create', 'project_stories:update',
        'projects.task.view', 'projects.sprint.view', 'projects.board.view', 'projects.members.view',
        'projects.comment.view', 'projects.comment.create', 'projects.attachment.view', 'projects.attachment.upload',
        'projects.retrospective.view'
    ],
    PROJECT_ROLE_VIEWER: [
        'dashboard', 'attendance:staff', 'holidays:view',
        'projects.overview.view', 'projects:view',
        'projects.backlog.view', 'projects.epic.view', 'projects.story.view', 'projects.task.view',
        'projects.my_tasks.view', 'projects.sprint.view', 'projects.board.view',
        'projects.members.view', 'projects.comment.view',
        'projects.attachment.view', 'projects.attachment.download',
        'project_statuses:view'
    ]
}
