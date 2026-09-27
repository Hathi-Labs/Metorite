"""Scrub a real ClickUp workspace export into a fixture that is safe to commit.

Spec: ``project-docs/specs/project_import.md`` §9 P-1.

A real export holds staff names, emails, task text and links. None of it may
enter the repo. This script replaces every one of them and keeps what the
parser depends on:

* every row, every column and the row order;
* the tree — ids are renamed consistently, so ``Parent ID``, ``Subtasks IDs``
  and ``Home Location ID`` still point where they did;
* duplicate rows, missing parents, dates, time zones, statuses, priorities,
  estimates and the JSON shape of every JSON cell.

Usage::

    uv run python scripts/import_scrub_clickup.py <real.csv> tests/unit/import_fixtures/clickup_workspace.csv

The output is deterministic: the same input gives the same bytes.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import re
import sys


class Names:
    """First-seen renaming: the same original always gets the same fake."""

    def __init__(self, fmt: str) -> None:
        self.fmt = fmt
        self.seen: dict[str, str] = {}

    def __call__(self, original: str) -> str:
        if original not in self.seen:
            self.seen[original] = self.fmt.format(n=len(self.seen) + 1)
        return self.seen[original]


task_id = Names("zz{n:07d}")
list_id = Names("9{n:011d}")
person = Names("Person {n}")
email = Names("person{n}@example.com")
tag = Names("tag {n}")
space = Names("Space {n}")
folder = Names("Folder {n}")
list_name = Names("List {n}")
status = Names("Status {n}")

#: Columns copied unchanged: numbers, dates and ClickUp's own words only. An
#: exact list, so a new column — a custom field in a view export, say — is
#: refused rather than copied because its name happens to hold "Date".
PASS_THROUGH = frozenset(
    {
        "Task Type",
        "Priority",
        "Assigned Comments",
        "Date Created",
        "Date Created Text",
        "Due Date",
        "Due Date Text",
        "Start Date",
        "Start Date Text",
        "Time Estimated",
        "Time Estimated Text",
        "Time Spent",
        "Time Spent Text",
        "Rolled Up Time",
        "Rolled Up Time Text",
    }
)
GENERIC_STATUSES = frozenset(
    {
        "open",
        "closed",
        "done",
        "complete",
        "completed",
        "to do",
        "todo",
        "in progress",
        "in process",
        "in review",
        "review",
        "backlog",
        "on hold",
        "blocked",
        "cancelled",
        "canceled",
    }
)

counter = {"custom": 0, "task": 0, "desc": 0, "comment": 0, "check": 0, "item": 0, "file": 0}


def _next(kind: str) -> int:
    counter[kind] += 1
    return counter[kind]


def _ids(value: str) -> str:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return ",".join(p if p == "null" else task_id(p) for p in parts)


def _who(value: str) -> str:
    return email(value.lower()) if "@" in value else person(value)


def _bracket(value: str, rename) -> str:
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    return "[" + ",".join(rename(p) for p in parts) + "]"


def _folder_path(value: str) -> str:
    if not value.strip():
        return value
    parts = json.loads(value)
    return json.dumps(
        ["/".join(folder(seg) for seg in p.split("/")) for p in parts], ensure_ascii=False
    )


def _home(value: str) -> str:
    segs = [s.strip() for s in value.split(" > ")]
    if len(segs) < 2:
        return value
    middle = ["/".join(folder(x) for x in s.split("/")) for s in segs[1:-1]]
    return " > ".join([space(segs[0]), *middle, list_name(segs[-1])])


def _attachments(value: str) -> str:
    items = json.loads(value) if value.strip() else []
    out = []
    for item in items:
        n = _next("file")
        ext = pathlib.PurePath(str(item.get("title", ""))).suffix[:8]
        out.append({"title": f"file{n}{ext}", "url": f"https://example.invalid/attachment/{n}"})
    return json.dumps(out, separators=(",", ":"))


def _checklists(value: str) -> str:
    data = json.loads(value) if value.strip() else {}
    out = {
        f"Checklist {_next('check')}": [f"Item {_next('item')}" for _ in items]
        for items in data.values()
    }
    return json.dumps(out, separators=(",", ":"))


def _comments(value: str) -> str:
    items = json.loads(value) if value.strip() else []
    out = []
    for item in items:
        item = dict(item)
        item["text"] = f"Comment {_next('comment')}\n"
        if item.get("by"):
            item["by"] = _who(str(item["by"]))
        out.append(item)
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def scrub(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header, body = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    rules = {
        "Task ID": task_id,
        "Task Link": lambda v: (
            re.sub(r"/t/([^/?#]+)", lambda m: "/t/" + task_id(m.group(1)), v) if v else v
        ),
        "Task Custom ID": lambda v: f"CU-{_next('custom')}" if v.strip() else v,
        "Task Name": lambda v: f"Task {_next('task')}",
        "Task Content": lambda v: f"Description {_next('desc')}" if v.strip() else v,
        "Parent ID": _ids,
        "Subtasks IDs": _ids,
        "Attachments": _attachments,
        "Assignees": lambda v: _bracket(v, _who),
        "Tags": lambda v: _bracket(v, tag),
        "List Name": list_name,
        "Folder Name/Path": _folder_path,
        "Space Name": space,
        "Checklists": _checklists,
        "Comments": _comments,
        "Home Location ID": lambda v: list_id(v) if v.strip() else v,
        "Home Location": _home,
        "Other Location IDs": lambda v: v and _bracket(v, list_id),
        "Other Locations": lambda v: v and "[redacted]",
        # A status name can name a client. Keep only the generic ones.
        "Status": lambda v: v if v.strip().lower() in GENERIC_STATUSES else status(v),
    }
    unknown = [h for h in header if h not in rules and h not in PASS_THROUGH]
    if unknown:
        raise SystemExit(f"refusing to copy columns with no scrub rule: {unknown}")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    # A task can sit on two rows (spec §4.1.1 fact 1). Both copies must scrub
    # to the same text, so a value is renamed once per (column, task, value).
    memo: dict[tuple[str, str, str], str] = {}
    for row in body:
        row = (row + [""] * len(header))[: len(header)]
        tid = row[col["Task ID"]]
        for name, rule in rules.items():
            if name in col:
                key = (name, tid, row[col[name]])
                if key not in memo:
                    memo[key] = rule(row[col[name]])
                row[col[name]] = memo[key]
        writer.writerow(row)
    return out.getvalue()


def main() -> None:
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    raw = src.read_bytes()
    text = raw.decode("utf-8-sig")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(scrub(text), encoding="utf-8", newline="")
    print(f"wrote {dst} ({dst.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
