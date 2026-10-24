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
        # The member reads product words, never a tool name (owner, 2026-10-07).
        ("text", "Task #7 now repeats every week on Monday."),
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


#: PO-10's rows, as a model writes them from the prompt.
PO10_ROWS: tuple[dict[str, Any], ...] = (
    {"title": "Book the caterer", "assignees": "Priya Menon"},
    {"title": "Print the badges"},
    {"title": "Test the projector"},
)


def _po10(ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("create_tasks", project_id=ds.project("Launch").id, tasks=json.dumps(PO10_ROWS)),
        ("text", "I added 3 tasks to Launch: «Book the caterer» for Priya Menon, «Print the "
                 "badges» and «Test the projector»."),
    ]


def _po11(ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("create_tags", project_id=ds.project("Launch").id,
             tags=json.dumps(["vendor", "print", "av"])),
        ("text", "I registered 3 tags in Launch: «vendor», «print» and «av»."),
    ]


def _po12(ds: Dataset) -> list[Step]:
    return [
        tool("my_areas"),
        tool("create_area", name="Garden"),
        ("text", "I made the area «Garden». Only you see it."),
    ]


def _po13(ds: Dataset) -> list[Step]:
    return [
        tool("my_areas"),
        tool("rename_area", area="Home", new_name="House"),
        ("text", "I renamed the area «Home» to «House». Its 2 open tasks stay in it."),
    ]


def _po14(ds: Dataset) -> list[Step]:
    return [
        tool("my_areas"),
        tool("delete_area", area="Finance"),
        ("text", "I archived the area «Finance», and did not delete it. It holds 2 finished "
                 "tasks, and they stay."),
    ]


def _po15(ds: Dataset) -> list[Step]:
    return [
        tool("find_tasks", query="#4"),
        tool("nudge_task", task_id=ds.task(ds_mod.DELEGATED).id),
        ("text", "I nudged «Priya Menon» about #4. She sees it in her Metorite bell. "
                 "I sent no email."),
    ]


def _po16(ds: Dataset) -> list[Step]:
    launch = ds.project("Launch")
    count = len([t for t in ds_mod.overdue(ds) if t.project == "Launch"])
    return [
        tool("projects_tree"),
        tool("export_tasks", project_id=launch.id, overdue=True),
        ("text", f"I exported the {count} overdue tasks of Launch to a CSV file. "
                 "The file card in this chat shows it."),
    ]


def _po17(ds: Dataset) -> list[Step]:
    return [
        tool("find_tasks", query="#6"),
        tool("attach_to_task", task_id=ds.task(6).id, file_name=ds_mod.UPLOAD_NAME),
        ("text", f"I attached «{ds_mod.UPLOAD_NAME}» to #6."),
    ]


def _po18(ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("grant_access", project_id=ds.project("Launch").id, subject="org"),
        ("text", "Everyone in the organization can now see «Launch» and every "
                 "sub-project under it."),
    ]


def _po19(ds: Dataset) -> list[Step]:
    launch = ds.project("Launch").id
    return [
        tool("projects_tree"),
        tool("project_access", project_id=launch),
        tool("revoke_access", project_id=launch,
             grant=ds_mod.ident("grant", launch, ds_mod.PRIYA.email)),
        ("text", "I removed the grant of «Priya Menon» on «Launch»."),
    ]


def _po20(ds: Dataset) -> list[Step]:
    from gateway.routes.projects.authority import GRANT_WRITE_REFUSED

    return [
        tool("projects_tree"),
        tool("grant_access", project_id=ds.project("Ops").id, subject="org"),
        ("text", f"I did not change who can see «Ops». The server said: {GRANT_WRITE_REFUSED}"),
    ]


def _weekly() -> str:
    return ds_mod.ident("report", ds_mod.REPORT_WEEKLY)


def _po21(_ds: Dataset) -> list[Step]:
    return [
        tool("report_list"),
        tool("report_recipients", report_id=_weekly()),
        ("text", "Only Asha Iyer receives «Weekly delivery» now."),
    ]


def _po22(_ds: Dataset) -> list[Step]:
    return [
        tool("report_recipients", report_id=_weekly()),
        tool("add_report_recipient", report_id=_weekly(), recipient="Priya Menon"),
        ("text", "Priya Menon is on the list of «Weekly delivery». Delivery may be "
                 "switched off, so nothing is emailed yet."),
    ]


def _po23(_ds: Dataset) -> list[Step]:
    return [
        tool("report_recipients", report_id=_weekly()),
        tool("remove_report_recipient", report_id=_weekly(), recipient=ds_mod.MEMBER.email),
        ("text", "You are off the list of «Weekly delivery»."),
    ]


def _po24(_ds: Dataset) -> list[Step]:
    return [
        tool("report_recipients", report_id=_weekly()),
        tool("set_report_schedule", report_id=_weekly(), enabled=True),
        ("text", "The schedule of «Weekly delivery» is on. Reports are not emailed yet: "
                 "delivery is switched off, and the owner decides when."),
    ]


def _po25(_ds: Dataset) -> list[Step]:
    from gateway.routes.projects.reports import CHANGE_REFUSED

    return [
        tool("report_list"),
        tool("set_report_schedule", report_id=ds_mod.ident("report", ds_mod.REPORT_BOARD),
             enabled=True),
        ("text", f"I did not change «Board pack». The server said: {CHANGE_REFUSED}"),
    ]


def _link(ds: Dataset, number: int) -> str:
    return f"/projects?task={next(t.id for t in ds.tasks if t.number == number)}"


def _po26(ds: Dataset) -> list[Step]:
    return [
        tool("waiting_agent_requests"),
        ("text", "Two agents wait for you. Claude Code asks to archive «Ship the samples»: "
                 f"{_link(ds, ds_mod.AGENT_ARCHIVE)}. Claude Code asks which printer to clean "
                 f"first on «Clean the demo printer»: {_link(ds, ds_mod.AGENT_QUESTION)}. "
                 "Open each card to answer."),
    ]


def _po27(ds: Dataset) -> list[Step]:
    return [
        tool("waiting_agent_requests"),
        ("text", "I cannot approve it for you. Only you can answer, on the task card. "
                 f"Open the card: {_link(ds, ds_mod.AGENT_ARCHIVE)}."),
    ]


def _po28(_ds: Dataset) -> list[Step]:
    return [
        tool("projects_tree"),
        tool("convert_folder_to_project", project_id=ds_mod.ident("folder", ds_mod.FOLDER)),
        ("text", f"«{ds_mod.FOLDER}» is a project now. «Acme rollout» and «Globex pilot» "
                 "are its subprojects, and their tasks did not change."),
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
    "PO-10": _po10,
    "PO-11": _po11,
    "PO-12": _po12,
    "PO-13": _po13,
    "PO-14": _po14,
    "PO-15": _po15,
    "PO-16": _po16,
    "PO-17": _po17,
    "PO-18": _po18,
    "PO-19": _po19,
    "PO-20": _po20,
    "PO-21": _po21,
    "PO-22": _po22,
    "PO-23": _po23,
    "PO-24": _po24,
    "PO-25": _po25,
    "PO-26": _po26,
    "PO-27": _po27,
    "PO-28": _po28,
}

SCRIPTED_IDS: tuple[str, ...] = tuple(_SEQUENCES)


def steps_for(task_id: str, ds: Dataset) -> list[Step]:
    """The known-good steps of *task_id*."""
    return _SEQUENCES[task_id](ds)
