"""skill-projects — the Projects tool family over the gateway ``/projects`` API.

Spec: ``project-docs/specs/projects_ai_chat.md``.

``__all__`` is the tool surface. The declarative builder
(``orchestrator/declarative.py``) reads it, and
``tests/unit/test_projects_chat_coverage.py`` holds it against
``manifest.py``: every exported name is a manifest tool, and every manifest
tool that is not exported is in ``manifest.PLANNED`` with its slice.
"""

from skill_projects.reads import (
    analytics_finished,
    analytics_load,
    analytics_outlook,
    analytics_stuck,
    analytics_throughput,
    find_tasks,
    list_tasks,
    my_work,
    people_for,
    project_summary,
    projects_tree,
    report_list,
    report_render,
    task_detail,
    vocabulary,
)

__all__ = [
    "analytics_finished",
    "analytics_load",
    "analytics_outlook",
    "analytics_stuck",
    "analytics_throughput",
    "find_tasks",
    "list_tasks",
    "my_work",
    "people_for",
    "project_summary",
    "projects_tree",
    "report_list",
    "report_render",
    "task_detail",
    "vocabulary",
]
