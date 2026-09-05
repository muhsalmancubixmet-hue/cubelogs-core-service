import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
django.setup()

from projects.models import Project, ProjectEpic, ProjectSprint, ProjectStory, ProjectTask

def classify_text(val):
    if not val or not val.strip():
        return "Empty / None"
    has_html_tags = bool('<' in val and '>' in val)
    has_markdown = bool(any(m in val for m in ['**', '##', '# ', '- [ ]', '- [x]', '- [X]', '> ', '```', '](http', '1. ']))
    has_escaped_html = bool('&lt;' in val or '&gt;' in val or '&amp;' in val)

    if has_escaped_html and has_html_tags:
        return "Escaped HTML + Raw HTML"
    if has_escaped_html:
        return "Escaped HTML"
    if has_html_tags and has_markdown:
        return "Mixed Markdown + HTML"
    if has_html_tags:
        return "Valid Editor HTML"
    if has_markdown:
        return "Markdown"
    return "Plain Text"

print("=== PROJECTS AUDIT ===")
for p in Project.objects.all():
    print(f"Project ID {p.id}: {p.name}")
    print(f"  Classification: {classify_text(p.description)}")
    print(f"  Raw Content: {repr(p.description)}\n")

print("=== EPICS AUDIT ===")
for e in ProjectEpic.objects.all():
    print(f"Epic ID {e.id}: {e.title}")
    print(f"  Classification: {classify_text(e.description)}")
    print(f"  Raw Content: {repr(e.description)}\n")

print("=== STORIES AUDIT ===")
for s in ProjectStory.objects.all():
    print(f"Story ID {s.id}: {s.title}")
    print(f"  Description Classification: {classify_text(s.description)}")
    print(f"  Desc Raw: {repr(s.description)}")
    print(f"  Acceptance Criteria Classification: {classify_text(s.acceptance_criteria)}")
    print(f"  AC Raw: {repr(s.acceptance_criteria)}\n")

print("=== TASKS AUDIT ===")
for t in ProjectTask.objects.all():
    print(f"Task ID {t.id}: {t.title}")
    print(f"  Classification: {classify_text(t.description)}")
    print(f"  Raw Content: {repr(t.description)}\n")

print("=== SPRINTS AUDIT ===")
for sp in ProjectSprint.objects.all():
    print(f"Sprint ID {sp.id}: {sp.name}")
    print(f"  Classification: {classify_text(sp.goal)}")
    print(f"  Raw Content: {repr(sp.goal)}\n")
