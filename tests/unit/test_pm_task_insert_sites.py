"""D80 fence — ``pm_tasks`` keeps ONE write path.

Spec: ``project-docs/specs/project_import.md`` §2 and §8 · decisions **D52.2**
and **D80** · board WS-41.

D52.2 deleted the ClickUp importers partly because each was "a second write
path into pm_tasks". D80 allowed a file importer on the condition that it adds
a CALLER, not a path: it writes tasks through ``core.insert_row``, the helper
every Projects route uses, so the coercion, the JSONB casts and every later
change to that helper reach imported tasks too.

This test reads the whole service tree and fails if:

1. any module writes ``INSERT INTO pm_tasks`` as raw SQL, or
2. a module outside the reviewed list creates a ``pm_tasks`` row through
   ``insert_row``.

A new creator of tasks is a design decision. Add it to :data:`CREATORS` in the
same PR, and say why in the PR body.
"""

from __future__ import annotations

import pathlib
import re

SERVICES = pathlib.Path(__file__).resolve().parents[2] / "apps" / "services"

#: The modules that create task rows, each through ``insert_row``.
CREATORS: dict[str, str] = {
    "gateway/gateway/routes/projects/tasks.py": "POST /projects/tasks",
    "gateway/gateway/routes/projects/personal.py": "a task in my own tree (My Tasks capture)",
    "gateway/gateway/routes/projects/recurrence.py": "the next occurrence of a recurring task",
    "gateway/gateway/routes/projects/intake.py": "an accepted intake request",
    "gateway/gateway/routes/projects/import_writer.py": "the file importer (D80, WS-41 I-3)",
}

_RAW = re.compile(r"INSERT\s+INTO\s+pm_tasks\b(?!_)", re.IGNORECASE)
_HELPER = re.compile(r"insert_row\(\s*\w+\s*,\s*[\"']pm_tasks[\"']", re.MULTILINE)


def _sources() -> list[pathlib.Path]:
    return [p for p in SERVICES.rglob("*.py") if ".venv" not in p.parts and "tests" not in p.parts]


def test_the_scan_sees_the_tree() -> None:
    assert len(_sources()) > 100


def test_no_module_writes_pm_tasks_as_raw_sql() -> None:
    offenders = [
        str(p.relative_to(SERVICES)).replace("\\", "/")
        for p in _sources()
        if _RAW.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"raw INSERT INTO pm_tasks — use core.insert_row: {offenders}"


def test_only_the_reviewed_modules_create_tasks() -> None:
    creators = {
        str(p.relative_to(SERVICES)).replace("\\", "/")
        for p in _sources()
        if _HELPER.search(p.read_text(encoding="utf-8"))
    }
    assert creators == set(CREATORS), (
        f"unreviewed task creators: {sorted(creators - set(CREATORS))}; "
        f"listed but gone: {sorted(set(CREATORS) - creators)}"
    )


def test_the_patterns_see_what_they_claim() -> None:
    """Verified-red: each pattern matches its own form, and not a neighbour."""
    assert _RAW.search('text("INSERT INTO pm_tasks (id) VALUES (1)")')
    assert not _RAW.search('text("INSERT INTO pm_task_assignees (task_id) VALUES (1)")')
    assert _HELPER.search('row = await insert_row(db, "pm_tasks", values)')
    assert _HELPER.search('row = await insert_row(\n    db,\n    "pm_tasks", values)')
    assert not _HELPER.search('await insert_row(db, "pm_task_statuses", {})')
