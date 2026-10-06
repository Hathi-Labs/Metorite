"""Known-good tool sequences for ``--scripted`` (WS-46 P3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.3.

``--scripted`` replays one sequence per task through the REAL
``run_agent_stream``, with ``ScriptedModel`` from
``tests/unit/_native_maf_harness.py`` in place of the model. So CI runs the
real tools, the real cards, the real refusals and the checkers, and it calls
no model and no Router.

A step is ``("tool", name, arguments)`` or ``("text", answer)``. Each tool
step is one model turn, and the last step is the answer. The ids, the dates
and the day names come from the dataset, so a sequence is right on any day.

A sequence proves the tools and the checkers. It does not prove the
instructions: the answer text here is the script's own. Only the model sweep
tests what a model does with the prompt.

PO-3 has no sequence. It is ``xfail`` until P8 and P9 ship.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from evals.projects_ops import dataset as ds_mod
from evals.projects_ops.dataset import Dataset

Step = tuple[Any, ...]


def tool(tool_name: str, /, **arguments: Any) -> Step:
    """One tool step. The tool name is positional, so an argument may be
    called ``name`` (``save_view``, PO-9)."""
    return ("tool", tool_name, json.dumps(arguments))


def _po1(ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("create_task", project_id=ds.project("Launch").id, title="Send the timesheet",
             repeat="weekly", repeat_on="5"),
        ("text", "I made the task «Send the timesheet» in Launch. It repeats every week on "
                 "Friday. The next one appears when this one is done."),
    ]


def _po2(ds: Dataset) -> list[Step]:
    day = ds_mod.weekday_name(ds.today)
    return [
        tool("projects_tree"),
        tool("create_task", project_id=ds.project("Launch").id, title="Review the backlog",
             repeat="weekly"),
        ("text", f"I made the task «Review the backlog» in Launch. It repeats every week on "
                 f"{day}, today's weekday, because you named no day. Tell me if you want "
                 "another day."),
    ]


def _overdue_ids(ds: Dataset) -> str:
    return ",".join(t.id for t in ds_mod.overdue(ds))


def _po4(ds: Dataset) -> list[Step]:
    monday = ds_mod.next_monday(ds.today).isoformat()
    numbers = ", ".join(f"#{t.number}" for t in ds_mod.overdue(ds))
    return [
        tool("list_tasks", overdue=True),
        tool("bulk_update", task_ids=_overdue_ids(ds), due=monday),
        ("text", f"I moved the {len(ds_mod.overdue(ds))} overdue tasks ({numbers}) to Monday "
                 f"{monday}."),
    ]


def _po5(ds: Dataset) -> list[Step]:
    monday = ds_mod.next_monday(ds.today).isoformat()
    return [
        tool("list_tasks", overdue=True),
        tool("bulk_update", task_ids=_overdue_ids(ds), due=monday),
        ("text", "You declined the card, so nothing changed. The overdue tasks keep their "
                 "due dates."),
    ]


def _po6(ds: Dataset) -> list[Step]:
    lanes = [lane.name for lane in ds.project("Launch").lanes]
    return [
        tool("find_tasks", query="#12"),
        tool("update_task", task_id=ds.task(12).id, status="Shipped"),
        ("text", "Launch has no lane called Shipped, so I changed nothing. The lanes are "
                 f"{', '.join(lanes[:-1])} and {lanes[-1]}. Which one do you want for #12?"),
    ]


def _po7(ds: Dataset) -> list[Step]:
    # find_tasks("#7"): a task number passes at any length (D-PM-31's
    # exception). P3 found the tool refusing it, and read the list instead.
    target = ds.task(7).id
    return [
        tool("find_tasks", query="#7"),
        tool("set_recurrence", task_id=target, rrule="FREQ=WEEKLY;BYDAY=MO"),
        tool("set_recurrence", task_id=target, freq="weekly", weekdays="1"),
        ("text", "Task #7 now repeats every week on Monday. set_recurrence takes no rrule, so "
                 "I set the rule with freq and weekdays."),
    ]


def _po8(ds: Dataset) -> list[Step]:
    monday = ds_mod.next_monday(ds.today).isoformat()
    return [
        tool("projects_tree"),
        tool("create_task", project_id=ds.project("Launch").id, title="Fix the badge scanner",
             type="Bug", start=monday, fields=json.dumps({"Customer": "Acme"})),
        ("text", f"I made the bug «Fix the badge scanner» in Launch. It starts on Monday "
                 f"{monday}, and its customer is Acme."),
    ]


def _po9(ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("save_view", project_id=ds.project("Launch").id, name="Overdue by owner",
             view_type="board", filters=json.dumps({"overdue": True}), group_by="assignee"),
        ("text", "I saved the board view «Overdue by owner» in Launch. It shows the overdue "
                 "tasks, grouped by assignee."),
    ]


_SEQUENCES: dict[str, Callable[[Dataset], list[Step]]] = {
    "PO-1": _po1,
    "PO-2": _po2,
    "PO-4": _po4,
    "PO-5": _po5,
    "PO-6": _po6,
    "PO-7": _po7,
    "PO-8": _po8,
    "PO-9": _po9,
}

SCRIPTED_IDS: tuple[str, ...] = tuple(_SEQUENCES)


def steps_for(task_id: str, ds: Dataset) -> list[Step]:
    """The known-good steps of *task_id*."""
    return _SEQUENCES[task_id](ds)
