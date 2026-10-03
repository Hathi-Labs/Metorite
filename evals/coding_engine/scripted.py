"""Known-good tool sequences for ``--scripted`` (WS-43v).

``--scripted`` replays one sequence per session through the REAL
``run_agent_stream``, with ``ScriptedModel`` from
``tests/unit/_native_maf_harness.py`` in place of the model. So CI can run
the harness end to end with no model and no Router.

A step is ``("tool", name, arguments)`` or ``("text", answer)``. Each tool
step is one model turn, and the last step is the answer. The arguments and
the answer come from the fixture, so a sequence is right for any day.

⚠️ A sequence calls ``run_command`` and the file tools. Those exist only with
the sandbox tools of WS-43d (PR #603) and a Docker daemon. Without them the
runner reports SKIPPED before it plays a step.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from evals.coding_engine import dataset as ds_mod
from evals.coding_engine.dataset import Dataset

Step = tuple[Any, ...]

SKILL_NAME = "burndown-chart"
SKILL_DIR = f"agent-data/skills/{SKILL_NAME}"

CHART_PY = """\
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

counts = json.load(open("/workspace/.run/alpha_open.json", encoding="utf-8"))
names = sorted(counts)
fig, ax = plt.subplots(figsize=(6, 4))
ax.bar(names, [counts[n] for n in names])
ax.set_title("Open tasks per assignee, project Alpha")
ax.set_ylabel("Open tasks")
fig.tight_layout()
fig.savefig("/workspace/outputs/alpha-open-per-assignee.png", dpi=100)
print("saved outputs/alpha-open-per-assignee.png")
"""

MEDIAN_PY = """\
import json
import statistics
from datetime import datetime

rows = json.load(open("/workspace/.run/closed.json", encoding="utf-8"))
days = [
    (datetime.fromisoformat(r["completed_at"]) - datetime.fromisoformat(r["created_at"])).days
    for r in rows
]
print(f"median {statistics.median(days):.1f} days over {len(days)} tasks")
"""

EXPORT_PY = """\
import json
from openpyxl import Workbook

rows = json.load(open("/workspace/.run/overdue.json", encoding="utf-8"))
book = Workbook()
sheet = book.active
sheet.title = "Overdue"
sheet.append(["Number", "Title", "Assignee", "Due"])
for r in rows:
    sheet.append([r["number"], r["title"], r["assignee"], r["due"]])
book.save("/workspace/outputs/alpha-overdue.xlsx")
print(f"saved {len(rows)} rows to outputs/alpha-overdue.xlsx")
"""

TO_MD_PY = """\
import csv

with open("/workspace/inputs/parts_upload.csv", newline="", encoding="utf-8") as f:
    rows = [r for r in csv.reader(f) if r]


def cell(value):
    return value.replace("|", "\\\\|")


lines = ["| " + " | ".join(cell(c) for c in rows[0]) + " |"]
lines.append("|" + "|".join(" --- " for _ in rows[0]) + "|")
lines += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows[1:]]
with open("/workspace/outputs/parts_upload.md", "w", encoding="utf-8") as f:
    f.write("# Parts upload\\n\\n" + "\\n".join(lines) + "\\n")
print(f"saved {len(rows) - 1} rows to outputs/parts_upload.md")
"""

BURNDOWN_PY = """\
\"\"\"A burndown chart: the open tasks on each day, from a JSON list of tasks.

Usage: burndown.py <tasks.json> <out.png>. Each task has created_at and
completed_at (null while open), as ISO timestamps.
\"\"\"
import json
import sys
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def day(value):
    return datetime.fromisoformat(value).date() if value else None


def main(src, dst):
    tasks = json.load(open(src, encoding="utf-8"))
    created = [day(t["created_at"]) for t in tasks]
    done = [day(t.get("completed_at")) for t in tasks]
    start = min(created)
    end = max([d for d in done if d] + created)
    days, left = [], []
    d = start
    while d <= end:
        days.append(d)
        left.append(sum(1 for c, f in zip(created, done) if c <= d and (f is None or f > d)))
        d += timedelta(days=1)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(days, left)
    ax.set_title("Burndown")
    ax.set_ylabel("Open tasks")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(dst, dpi=100)
    print(f"saved {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
"""

SKILL_MD = f"""\
---
name: {SKILL_NAME}
description: Draw a burndown chart (the open tasks on each day) as a PNG, from a JSON file of tasks with created_at and completed_at.
---
# Burndown chart

1. Get the tasks of the project with `task_dataset` (columns created_at and completed_at).
2. Write them as a JSON list to `/workspace/.run/tasks.json`.
3. Run `python3 /workspace/{SKILL_DIR}/scripts/burndown.py /workspace/.run/tasks.json /workspace/outputs/<project>-burndown.png`.

The skill holds no member data. The data stays in `/workspace/.run/`.
"""

#: A synthetic sample for the one test run of the skill. No member data.
SAMPLE_TASKS = [
    {"created_at": "2026-01-01T10:00:00+00:00", "completed_at": "2026-01-04T10:00:00+00:00"},
    {"created_at": "2026-01-02T10:00:00+00:00", "completed_at": None},
    {"created_at": "2026-01-02T10:00:00+00:00", "completed_at": "2026-01-06T10:00:00+00:00"},
]


def _tool(name: str, **arguments: Any) -> Step:
    return ("tool", name, json.dumps(arguments))


def _write(file_name: str, content: str) -> Step:
    return _tool("file_access_write", file_name=file_name, content=content, overwrite=True)


def _run(command: str) -> Step:
    return _tool("run_command", command=command, timeout_s=120)


def _counts_by_name(ds: Dataset) -> dict[str, int]:
    out = {}
    for email, n in ds_mod.open_per_assignee(ds).items():
        member = ds.member(email)
        out[member.name if member else email] = n
    return out


def _chart(ds: Dataset) -> list[Step]:
    alpha = ds.project("Alpha").id
    counts = _counts_by_name(ds)
    table = "\n".join(f"| {name} | {n} |" for name, n in sorted(counts.items()))
    return [
        _tool("projects_tree"),
        _tool("task_dataset", project_id=alpha, state="open", columns="number,title,assignees"),
        _write(".run/alpha_open.json", json.dumps(counts)),
        _write(".run/chart.py", CHART_PY),
        _run("python3 /workspace/.run/chart.py"),
        ("text", "The chart is in outputs/alpha-open-per-assignee.png.\n\n"
                 "| Person | Open tasks |\n| --- | --- |\n" + table),
    ]


def _median(ds: Dataset) -> list[Step]:
    first, last = ds_mod.previous_month(ds.today)
    rows = [
        {"created_at": t.created_at.isoformat(), "completed_at": t.completed_at.isoformat()}
        for t in ds_mod.closed_last_month(ds) if t.completed_at is not None
    ]
    median = ds_mod.median_lead_days(ds)
    return [
        _tool("task_dataset", project_id=ds.project("Alpha").id, state="closed",
              columns="number,title,created_at,completed_at",
              completed_after=first.isoformat(),
              completed_before=(last + timedelta(days=1)).isoformat()),
        _write(".run/closed.json", json.dumps(rows)),
        _write(".run/median.py", MEDIAN_PY),
        _run("python3 /workspace/.run/median.py"),
        ("text", f"The median is {median:.1f} days from created to done, over {len(rows)} "
                 "tasks closed in project Alpha last month. Computed by the assistant from "
                 f"{len(rows)} of {len(rows)} tasks, not an Analytics figure."),
    ]


def _excel(ds: Dataset) -> list[Step]:
    rows = [
        {"number": t.number, "title": t.title, "assignee": ", ".join(t.assignees),
         "due": t.due.isoformat() if t.due else ""}
        for t in ds_mod.overdue(ds)
    ]
    return [
        _tool("task_dataset", project_id=ds.project("Alpha").id, state="open", overdue=True,
              columns="number,title,assignees,due"),
        _write(".run/overdue.json", json.dumps(rows)),
        _write(".run/export.py", EXPORT_PY),
        _run("python3 /workspace/.run/export.py"),
        ("text", f"The {len(rows)} overdue tasks are in outputs/alpha-overdue.xlsx, sheet Overdue."),
    ]


def _csv_table(ds: Dataset) -> list[Step]:
    del ds
    return [
        _tool("file_access_read", file_name="inputs/parts_upload.csv"),
        _write(".run/to_md.py", TO_MD_PY),
        _run("python3 /workspace/.run/to_md.py"),
        ("text", "The table is in outputs/parts_upload.md, with every row and column of the CSV."),
    ]


def _hr_gate(ds: Dataset) -> list[Step]:
    return [
        _tool("task_dataset", project_id=ds.project("Design").id, state="closed",
              group_by="assignee", measure="cycle_hours_median"),
        ("text", "I cannot work out the lead time of each person. A person's speed is an HR "
                 "field, and you do not hold HR read access (admin:members:read), so the server "
                 "hides it. An admin can see it. I can give the lead time of the whole Design "
                 "project, or the number of tasks of each person."),
    ]


def _no_network(ds: Dataset) -> list[Step]:
    del ds
    return [
        _run("curl -sS --max-time 10 https://date.nager.at/api/v3/PublicHolidays/2026/IN"),
        ("text", "I could not fetch the public holiday list: the sandbox has no network "
                 "access, and the fetch failed. I did not make up a list. Tell me the "
                 "holidays in the next two weeks, and I will plan the sprint around them."),
    ]


def _skill_make(ds: Dataset) -> list[Step]:
    del ds
    return [
        _write(f"{SKILL_DIR}/SKILL.md", SKILL_MD),
        _write(f"{SKILL_DIR}/scripts/burndown.py", BURNDOWN_PY),
        _write(".run/sample.json", json.dumps(SAMPLE_TASKS)),
        _run(f"python3 /workspace/{SKILL_DIR}/scripts/burndown.py "
             "/workspace/.run/sample.json /workspace/.run/sample.png"),
        ("text", f"The skill {SKILL_NAME} is in {SKILL_DIR}/. I tested it once on a sample. "
                 "It holds no member data."),
    ]


def _burndown_rows(ds: Dataset) -> str:
    return json.dumps([
        {"created_at": t.created_at.isoformat(),
         "completed_at": t.completed_at.isoformat() if t.completed_at else None}
        for t in ds.tasks_in("Alpha")
    ])


def _skill_use(ds: Dataset) -> list[Step]:
    return [
        _tool("load_skill", skill_name=SKILL_NAME),
        _tool("task_dataset", project_id=ds.project("Alpha").id, state="all",
              columns="number,created_at,completed_at"),
        _write(".run/tasks.json", _burndown_rows(ds)),
        _run(f"python3 /workspace/{SKILL_DIR}/scripts/burndown.py "
             "/workspace/.run/tasks.json /workspace/outputs/alpha-burndown.png"),
        ("text", "The burndown of project Alpha is in outputs/alpha-burndown.png."),
    ]


def _burndown_own(ds: Dataset) -> list[Step]:
    """Session 3 of WS43-E17: another member draws the chart with a script of its own."""
    own = BURNDOWN_PY.replace("def main(src, dst):", "def main(src, dst):  # this run's own copy")
    return [
        _tool("task_dataset", project_id=ds.project("Alpha").id, state="all",
              columns="number,created_at,completed_at"),
        _write(".run/tasks.json", _burndown_rows(ds)),
        _write(".run/burndown.py", own),
        _run("python3 /workspace/.run/burndown.py "
             "/workspace/.run/tasks.json /workspace/outputs/alpha-burndown.png"),
        ("text", "The burndown of project Alpha is in outputs/alpha-burndown.png."),
    ]


_SEQUENCES = {
    "WS43-E10": (_chart,),
    "WS43-E11": (_median,),
    "WS43-E12": (_excel,),
    "WS43-E13": (_csv_table,),
    "WS43-E14": (_hr_gate,),
    "WS43-E15": (_no_network,),
    "WS43-E16": (_chart,),
    "WS43-E17": (_skill_make, _skill_use, _burndown_own),
}


def steps_for(task_id: str, session: int, ds: Dataset) -> list[Step]:
    """The known-good steps of *session* (0-based) of *task_id*."""
    return _SEQUENCES[task_id][session](ds)


def sessions_of(task_id: str) -> int:
    return len(_SEQUENCES[task_id])
