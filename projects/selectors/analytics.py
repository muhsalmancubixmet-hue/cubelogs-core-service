# --------------------------------------------------------------------------------
#       Projects Selectors - Analytics (Burndown & Velocity)
# --------------------------------------------------------------------------------

from django.db.models import Sum, Q
from django.utils import timezone
from datetime import timedelta

from projects.models import ProjectSprint, ProjectSprintEvent, ProjectStory


def calculate_sprint_burndown(sprint):
    """
    Calculates historical burndown data for a sprint using ProjectSprintEvent logs.
    Returns daily timeline points:
      - date: YYYY-MM-DD
      - total_points: committed/scope story points
      - completed_points: accumulated story points completed by date
      - remaining_points: total_points - completed_points
      - ideal_remaining: linear ideal burn line from start to end date
    """
    events = list(sprint.events.order_by('created_at'))
    
    start_date = sprint.start_date or (sprint.started_at.date() if sprint.started_at else timezone.now().date())
    end_date = sprint.end_date or (sprint.completed_at.date() if sprint.completed_at else start_date + timedelta(days=14))

    if end_date < start_date:
        end_date = start_date + timedelta(days=1)

    total_days = (end_date - start_date).days or 1

    # Current scope points in sprint
    current_stories = list(sprint.stories.all())
    initial_total_points = sum(s.story_points for s in current_stories)
    current_completed_points = sum(s.story_points for s in current_stories if s.status and s.status.category == 'completed')

    # Build daily snapshots
    timeline = []
    curr_date = start_date
    today = timezone.now().date()
    max_date = min(end_date, today) if sprint.status == 'active' else end_date

    day_index = 0
    while curr_date <= max_date:
        # Filter events up to end of curr_date
        day_events = [e for e in events if e.created_at.date() <= curr_date]
        
        if day_events:
            last_event = day_events[-1]
            tot_p = last_event.total_points
            comp_p = last_event.completed_points
        else:
            tot_p = initial_total_points
            comp_p = current_completed_points if curr_date >= today else 0

        rem_p = max(0, tot_p - comp_p)
        ideal_rem = max(0.0, round(initial_total_points * (1 - (day_index / total_days)), 1))

        timeline.append({
            'date': str(curr_date),
            'total_points': tot_p,
            'completed_points': comp_p,
            'remaining_points': rem_p,
            'ideal_remaining': ideal_rem,
        })
        curr_date += timedelta(days=1)
        day_index += 1

    return {
        'sprint_id': sprint.id,
        'sprint_name': sprint.name,
        'status': sprint.status,
        'start_date': str(start_date),
        'end_date': str(end_date),
        'initial_points': initial_total_points,
        'timeline': timeline,
    }


def calculate_project_velocity(project, limit=6):
    """
    Calculates story point velocity for completed sprints in a project.
    Returns dictionary matching contract:
      - project_id
      - average_velocity
      - sprints: list of objects (sprint_id, sprint_key, sprint_name, completed_at, capacity, committed_points, completed_points)
    """
    completed_sprints = list(
        ProjectSprint.objects.filter(
            project=project,
            status='completed',
            is_deleted=False
        ).order_by('completed_at', 'id')
    )

    if limit and len(completed_sprints) > limit:
        completed_sprints = completed_sprints[-limit:]

    sprints_data = []

    for sp in completed_sprints:
        comp_event = sp.events.filter(event_type='sprint_completed').first()
        if comp_event:
            comm_points = comp_event.total_points
            comp_points = comp_event.completed_points
        else:
            comm_points = sp.stories.aggregate(s=Sum('story_points'))['s'] or 0
            comp_points = sp.stories.filter(status__category='completed').aggregate(s=Sum('story_points'))['s'] or 0
            # Idempotently persist sprint_completed event for historical immutability
            comp_event = ProjectSprintEvent.objects.create(
                sprint=sp,
                event_type='sprint_completed',
                total_points=comm_points,
                completed_points=comp_points
            )

        all_sprints_ids = list(sp.project.sprints.order_by('id').values_list('id', flat=True))
        try:
            seq = all_sprints_ids.index(sp.id) + 1
        except ValueError:
            seq = sp.id
        sprint_key = f"SPR-{seq:03d}"

        sprints_data.append({
            'sprint_id': sp.id,
            'sprint_key': sprint_key,
            'sprint_name': sp.name,
            'completed_at': sp.completed_at.isoformat() if sp.completed_at else None,
            'capacity': sp.capacity,
            'committed_points': comm_points,
            'completed_points': comp_points,
        })

    completed_points_sum = sum(s['completed_points'] for s in sprints_data)
    average_velocity = round(completed_points_sum / len(sprints_data)) if sprints_data else 0

    return {
        'project_id': project.id,
        'average_velocity': average_velocity,
        'sprints': sprints_data
    }
