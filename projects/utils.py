# --------------------------------------------------------------------------------
#       Projects Utilities & Key Generators
# --------------------------------------------------------------------------------

import re

def generate_project_prefix(project_name):
    """
    Generates a 3-letter uppercase prefix from a project name.
    Examples:
        'Attendance Management'      -> 'ATT'
        'Human Resource Management'  -> 'HRM'
        'CubeLogs HRMS'             -> 'HRM'
        'CubeLogs Website'          -> 'WEB'
        ''                          -> 'PRJ'
    """
    if not project_name:
        return 'PRJ'

    # Known exact mappings for standard domain terms
    normalized = project_name.strip().upper()
    if 'ATTENDANCE' in normalized:
        return 'ATT'
    if 'HRMS' in normalized or ('HUMAN' in normalized and 'RESOURCE' in normalized):
        return 'HRM'
    if 'WEBSITE' in normalized or 'WEB' in normalized:
        return 'WEB'

    # Clean words
    words = [re.sub(r'[^A-Za-z]', '', w) for w in project_name.split()]
    words = [w for w in words if w]

    # Strip company brand prefix if present
    if len(words) > 1 and words[0].upper() == 'CUBELOGS':
        words = words[1:]

    if not words:
        return 'PRJ'

    if len(words) >= 3:
        prefix = ''.join(w[0] for w in words[:3]).upper()
    elif len(words) == 2:
        if len(words[0]) >= 3:
            prefix = words[0][:3].upper()
        else:
            prefix = (words[0] + words[1])[:3].upper()
    else:
        prefix = words[0][:3].upper()

    return prefix if len(prefix) == 3 else 'PRJ'


def generate_unique_project_key(company, project_name):
    """
    Generates an auto-incremented project key per company.
    Format: PRJ-0001, ATT-0001, HRM-0001
    """
    from projects.models import Project

    prefix = generate_project_prefix(project_name)
    existing_keys = Project.objects.filter(
        company=company,
        key__startswith=f"{prefix}-"
    ).values_list('key', flat=True)

    max_sequence = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for key_string in existing_keys:
        match = pattern.match(key_string)
        if match:
            max_sequence = max(max_sequence, int(match.group(1)))

    next_sequence = max_sequence + 1
    return f"{prefix}-{next_sequence:04d}"


def generate_epic_key(project):
    """
    Generates EPIC-001, EPIC-002 key for an epic in a project.
    """
    from projects.models import ProjectEpic
    count = ProjectEpic.objects.filter(project=project).count() + 1
    return f"EPIC-{count:03d}"


def generate_story_key(project):
    """
    Generates STORY-001, STORY-002 key for a story in a project.
    """
    from projects.models import ProjectStory
    count = ProjectStory.objects.filter(project=project).count() + 1
    return f"STORY-{count:03d}"


def generate_task_key(story):
    """
    Generates TASK-001, TASK-002 key for a task in a story.
    """
    from projects.models import ProjectTask
    count = ProjectTask.objects.filter(story=story).count() + 1
    return f"TASK-{count:03d}"


def generate_subtask_key(task):
    """
    Generates SUB-001, SUB-002 key for a subtask in a task.
    """
    from projects.models import ProjectSubtask
    count = ProjectSubtask.objects.filter(task=task).count() + 1
    return f"SUB-{count:03d}"
