"""ClickUp workspace export (CSV) → :class:`ImportBundle`.

Spec: ``project_import.md`` §4.1 and §4.1.1 (measured on one real export,
2026-09-27), §5.3 (where the rows land) and §6.4 (priority).

The file comes from Settings → Imports / Exports → Export Items. It is one row
per task, and subtasks are rows too. Every rule below answers a fact measured
in the real file. §4.1.1 numbers the facts, and the comments cite them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter

from gateway.routes.projects.importer.bundle import (
    BundleWarning,
    Checklist,
    Comment,
    Container,
    ImportBundle,
    Loss,
    Person,
    StatusSeen,
    Task,
)
from gateway.routes.projects.importer.text import decode, read_csv

SOURCE = "clickup"

#: The columns a workspace export must carry to be read at all.
REQUIRED = (
    "Task ID",
    "Task Name",
    "Status",
    "Date Created",
    "Parent ID",
    "List Name",
    "Space Name",
)

#: Every column the measured export carries. Anything else is reported,
#: because a later ClickUp release may add custom-field columns.
KNOWN = frozenset(
    {
        "Task ID",
        "Task Link",
        "Task Type",
        "Task Custom ID",
        "Task Name",
        "Task Content",
        "Status",
        "Date Created",
        "Date Created Text",
        "Due Date",
        "Due Date Text",
        "Start Date",
        "Start Date Text",
        "Parent ID",
        "Subtasks IDs",
        "Attachments",
        "Assignees",
        "Tags",
        "Priority",
        "List Name",
        "Folder Name/Path",
        "Space Name",
        "Time Estimated",
        "Time Estimated Text",
        "Checklists",
        "Comments",
        "Assigned Comments",
        "Time Spent",
        "Time Spent Text",
        "Rolled Up Time",
        "Rolled Up Time Text",
        "Home Location ID",
        "Home Location",
        "Other Location IDs",
        "Other Locations",
    }
)

#: Fact 7 — ClickUp writes the number. 1 is Urgent and 4 is Low. §6.4 maps
#: it onto ``importance`` 0-3 (D78).
PRIORITY = {"1": 3, "2": 2, "3": 1, "4": 0}

#: What the workspace export never carries (§4.1.1 "does NOT hold").
LOSSES = (
    Loss(
        what="custom fields",
        why="the workspace export has no custom-field column; add a view export (I-5)",
    ),
    Loss(what="completion date", why="no column says when a task closed; §6.6 sets the rule"),
    Loss(what="status type", why="the file names each status and never says whether it means done"),
    Loss(what="checklist ticks", why="a checklist item is text only, with no ticked state"),
    Loss(
        what="attachment files",
        why="the file lists names and links only, and the importer never fetches a link",
    ),
    Loss(what="dependencies", why="the workspace export has no dependency column"),
    Loss(what="watchers", why="the workspace export has no watcher column"),
)

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

#: ``GMT+5:30``, ``UTC-4`` or a bare ``GMT`` (a zero offset has no sign).
_OFFSET = re.compile(r"\b(?:GMT|UTC)(?:([+-])(\d{1,2})(?::(\d{2}))?)?\s*$")
_TEXT_DATE = re.compile(
    r"^\s*(\d{1,2})/(\d{1,2})/(\d{4}),\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]m)\s*"
    r"((?:GMT|UTC)(?:[+-]\d{1,2}(?::\d{2})?)?)?\s*$",
    re.IGNORECASE,
)


def looks_like(header: list[str]) -> bool:
    """True when ``header`` is a ClickUp workspace export."""
    return all(col in header for col in REQUIRED) and "Task Link" in header


def parse(files: list[tuple[str, bytes]]) -> ImportBundle:
    """Parse one or more workspace exports into one bundle.

    Several files are allowed because a large workspace exports one Space at a
    time (§4.1.1 "Hazards that remain", item 3). A task id seen in two files
    merges like a duplicate row.
    """
    bundle = ImportBundle(source=SOURCE, losses=list(LOSSES))
    rows: list[dict[str, str]] = []
    unknown: set[str] = set()
    for name, raw in files:
        text, encoding = decode(raw)
        if encoding != "utf-8":
            bundle.encoding = encoding
        header, body, wide = read_csv(text)
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise ValueError(
                f"{name}: not a ClickUp workspace export — missing {', '.join(missing)}"
            )
        unknown.update(h for h in header if h not in KNOWN)
        repeated = sorted(h for h, n in Counter(header).items() if n > 1)
        if repeated:
            # ClickUp never repeats a header, so a dict keeps the last copy.
            # A Jira adapter must not copy this: Jira repeats on purpose.
            _warn(
                bundle,
                "repeated_columns",
                "columns named twice; the last one is read",
                len(repeated),
                repeated,
            )
        if wide:
            _warn(
                bundle,
                "wide_rows",
                "rows with more cells than the header; the extra cells are cut",
                wide,
                [name],
            )
        for cells in body:
            rows.append(dict(zip(header, cells, strict=True)))
    bundle.rows_read = len(rows)
    if unknown:
        _warn(
            bundle,
            "unknown_columns",
            "columns this importer does not read",
            len(unknown),
            sorted(unknown),
        )

    bundle.utc_offset = _file_offset(bundle, rows)
    merged = _merge_duplicates(bundle, rows)
    _containers(bundle, merged)
    people: dict[str, Person] = {}
    for row in merged.values():
        bundle.tasks.append(_task(bundle, row, people))
    _parents(bundle)
    _statuses(bundle)
    bundle.people = sorted(people.values(), key=lambda p: p.ref)
    _multi_home(bundle, merged)
    return bundle


# ── rows ────────────────────────────────────────────────────────────────────


def _merge_duplicates(
    bundle: ImportBundle, rows: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Fact 1 — one Task ID can sit on two rows. The copies differ only in
    ``Assignees`` and ``Subtasks IDs``, so merge those as a union and keep the
    first row for everything else. A copy that differs anywhere else is
    reported, because the first row then wins a real disagreement."""
    merged: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    conflicts: list[str] = []
    for row in rows:
        tid = row["Task ID"].strip()
        if not tid:
            continue
        first = merged.get(tid)
        if first is None:
            merged[tid] = dict(row)
            continue
        duplicates.append(tid)
        first["Assignees"] = _join_list(
            _bracket_list(first.get("Assignees", "")) + _bracket_list(row.get("Assignees", ""))
        )
        first["Subtasks IDs"] = ",".join(
            _unique(
                _split_ids(first.get("Subtasks IDs", "")) + _split_ids(row.get("Subtasks IDs", ""))
            )
        )
        if any(
            first.get(k, "") != row.get(k, "")
            for k in row
            if k not in ("Assignees", "Subtasks IDs")
        ):
            conflicts.append(tid)
    if duplicates:
        _warn(
            bundle,
            "duplicate_rows",
            "tasks that appear on more than one row, merged into one",
            len(set(duplicates)),
            duplicates,
        )
    if conflicts:
        _warn(
            bundle,
            "duplicate_conflict",
            "duplicate rows that disagree; the first row wins",
            len(set(conflicts)),
            conflicts,
        )
    return merged


def _containers(bundle: ImportBundle, merged: dict[str, dict[str, str]]) -> None:
    """§5.3 — Space → space, Folder → folder, List → project.

    Only the List has an id (``Home Location ID``, fact list §4.1.1). A Space
    and a Folder are keyed by name. A nested Folder path ``A/B`` flattens to one
    folder named ``A / B``, because the Projects tree allows one folder level
    between a space and a project."""
    seen: dict[str, Container] = {}
    list_names: dict[str, set[str]] = {}
    for row in merged.values():
        space = row["Space Name"].strip() or "(no space)"
        space_ref = f"space:{space}"
        if space_ref not in seen:
            seen[space_ref] = Container(ref=space_ref, kind="space", name=space)
        parent = space_ref
        folder = _folder_path(row.get("Folder Name/Path", ""))
        if folder:
            folder_ref = f"folder:{space}/{folder}"
            if folder_ref not in seen:
                seen[folder_ref] = Container(
                    ref=folder_ref, kind="folder", name=folder, parent_ref=space_ref
                )
            parent = folder_ref
        list_id = row.get("Home Location ID", "").strip()
        list_name = row["List Name"].strip() or "(no list)"
        list_ref = f"list:{list_id}" if list_id else f"list:{parent}/{list_name}"
        list_names.setdefault(list_ref, set()).add(list_name)
        if list_ref not in seen:
            seen[list_ref] = Container(
                ref=list_ref,
                kind="project",
                name=list_name,
                parent_ref=parent,
                source_id=list_id or None,
            )
        row["_container"] = list_ref
    clashes = [ref for ref, names in list_names.items() if len(names) > 1]
    if clashes:
        _warn(
            bundle,
            "list_name_clash",
            "one List id carries two names; the first name wins",
            len(clashes),
            clashes,
        )
    bundle.containers = list(seen.values())


def _task(bundle: ImportBundle, row: dict[str, str], people: dict[str, Person]) -> Task:
    tid = row["Task ID"].strip()
    task = Task(
        ref=tid,
        container_ref=row["_container"],
        parent_ref=_nullable(row.get("Parent ID", "")),
        title=row["Task Name"].strip() or "(untitled)",
        description_md=row.get("Task Content", "").strip() or None,
        status_name=row["Status"].strip() or None,
        importance=_priority(bundle, tid, row.get("Priority", "")),
        tags=_bracket_list(row.get("Tags", "")),
        created_at=_epoch(row.get("Date Created", "")),
        estimate_mins=_ms_to_mins(row.get("Time Estimated", "")),
        time_spent_mins=_ms_to_mins(row.get("Time Spent", "")),
        checklists=_checklists(bundle, tid, row.get("Checklists", "")),
        attachment_names=_attachment_names(bundle, tid, row.get("Attachments", "")),
        custom_id=_nullable(row.get("Task Custom ID", "")),
        url=_nullable(row.get("Task Link", "")),
    )
    start = _epoch(row.get("Start Date", ""))
    if start is not None:
        task.start_date = _row_local(bundle, start, row.get("Start Date Text", "")).date()
    due = _epoch(row.get("Due Date", ""))
    if due is not None:
        local = _row_local(bundle, due, row.get("Due Date Text", ""))
        # Fact 9 — ClickUp writes a due date with no time at 04:00 local. The
        # row's own Text twin carries the offset in force on THAT date, so a
        # zone with summer time decides each row correctly.
        if local.tzinfo is not None and (local.hour, local.minute, local.second) == (4, 0, 0):
            task.due_date = local.date()
        else:
            task.due_at = due
    for name in _bracket_list(row.get("Assignees", "")):
        task.assignee_refs.append(_person(people, name))
    for comment in _comments(bundle, tid, row.get("Comments", "")):
        if comment.author_ref:
            comment.author_ref = _person(people, comment.author_ref)
        bundle.comments.append(comment)
    return task


def _parents(bundle: ImportBundle) -> None:
    """Facts 2-4 — ``Parent ID`` is the truth. A parent missing from the file
    lands the task at the top of its List. A cycle is cut at the task that
    closes it, which a well-formed export never holds."""
    by_ref = {t.ref: t for t in bundle.tasks}
    orphans = [t.ref for t in bundle.tasks if t.parent_ref and t.parent_ref not in by_ref]
    for ref in orphans:
        by_ref[ref].parent_ref = None
    if orphans:
        _warn(
            bundle,
            "missing_parent",
            "subtasks whose parent is not in the file; they land at the top of their List",
            len(orphans),
            orphans,
        )
    # One pass, O(n): each task is walked once. ``state`` is 1 while a task is
    # on the current chain and 2 once its chain is known to end.
    cut: list[str] = []
    state: dict[str, int] = {}
    for task in bundle.tasks:
        chain: list[Task] = []
        node: Task | None = task
        while node is not None and state.get(node.ref) is None:
            state[node.ref] = 1
            chain.append(node)
            parent = by_ref[node.parent_ref] if node.parent_ref else None
            if parent is not None and state.get(parent.ref) == 1:
                node.parent_ref = None
                cut.append(node.ref)
                parent = None
            node = parent
        for done in chain:
            state[done.ref] = 2
    if cut:
        _warn(bundle, "parent_cycle", "parent chains that loop; the loop is cut", len(cut), cut)
    # A subtask lands in its parent's List, so the tree holds together. Walk
    # from the top so a whole chain follows its root.
    moved: list[str] = []
    homes: dict[str, str] = {}

    def home(t: Task) -> str:
        chain: list[Task] = []
        while t.ref not in homes and t.parent_ref:
            chain.append(t)
            t = by_ref[t.parent_ref]
        found = homes.get(t.ref, t.container_ref)
        for seen in chain:
            homes[seen.ref] = found
        homes[t.ref] = found
        return found

    for task in bundle.tasks:
        if task.parent_ref:
            root_home = home(task)
            if root_home != task.container_ref:
                task.container_ref = root_home
                moved.append(task.ref)
    if moved:
        _warn(
            bundle,
            "parent_other_list",
            "subtasks in a different List from their parent; they move to the parent's List",
            len(moved),
            moved,
        )


def _statuses(bundle: ImportBundle) -> None:
    """Fact 11 — each List keeps its own set, in first-seen order. The file
    shows only the statuses some task uses."""
    counts: Counter[tuple[str, str]] = Counter()
    order: list[tuple[str, str]] = []
    for task in bundle.tasks:
        if task.status_name is None:
            continue
        key = (task.container_ref, task.status_name)
        if key not in counts:
            order.append(key)
        counts[key] += 1
    bundle.statuses = [
        StatusSeen(container_ref=c, name=n, task_count=counts[(c, n)]) for c, n in order
    ]


def _multi_home(bundle: ImportBundle, merged: dict[str, dict[str, str]]) -> None:
    """A task in several Lists keeps its home List (§4.1.1 hazard 4)."""
    refs = [
        tid
        for tid, row in merged.items()
        if row.get("Other Location IDs", "").strip() not in ("", "[]")
    ]
    if refs:
        _warn(
            bundle,
            "other_locations",
            "tasks that also sit in other Lists; they land in their home List only",
            len(refs),
            refs,
        )


# ── cells ───────────────────────────────────────────────────────────────────


def _nullable(value: str) -> str | None:
    """Fact 6 — ClickUp writes the four letters ``null`` for no value."""
    value = value.strip()
    return None if value in ("", "null", "NaN") else value


def _bracket_list(value: str) -> list[str]:
    """``[a,b]`` → ``["a", "b"]``. Assignee and tag cells use this form.
    A name that holds a comma splits in two; the measured file holds none."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return _unique([part.strip() for part in value.split(",") if part.strip()])


def _join_list(values: list[str]) -> str:
    return "[" + ",".join(_unique(values)) + "]"


def _split_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip() and part.strip() != "null"]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _folder_path(value: str) -> str | None:
    """``["A"]`` → ``A``. ``["A/B"]`` → ``A / B``. Empty → no folder."""
    value = _nullable(value) or ""
    if not value:
        return None
    try:
        parts = json.loads(value)
    except (ValueError, RecursionError):
        parts = [value]
    if isinstance(parts, str):
        parts = [parts]
    if not isinstance(parts, list):
        parts = [value]
    names = [
        seg.strip()
        for part in parts
        if isinstance(part, str)
        for seg in part.split("/")
        if seg.strip()
    ]
    return " / ".join(names) or None


def _priority(bundle: ImportBundle, tid: str, value: str) -> int | None:
    value = value.strip()
    if value in ("", "null"):
        return None
    if value in PRIORITY:
        return PRIORITY[value]
    _warn(
        bundle,
        "unknown_priority",
        "priority values this importer does not know; the task gets none",
        1,
        [tid],
    )
    return None


def _epoch(value: str) -> dt.datetime | None:
    value = value.strip().strip('"').strip()
    if not value or value in ("null", "NaN"):
        return None
    try:
        # Arithmetic, not ``fromtimestamp``: Windows refuses a negative epoch.
        return _EPOCH + dt.timedelta(milliseconds=int(value))
    except (ValueError, OverflowError):
        return None


def _ms_to_mins(value: str) -> int | None:
    """``Time Estimated`` and ``Time Spent`` are both milliseconds. ``Time
    Spent`` arrives as ``' "1569"'`` — a space, quotes, then the number — and
    its twin ``Time Spent Text`` reads ``0.03 m``."""
    value = value.strip().strip('"').strip()
    if not value.isdigit():
        return None
    return round(int(value) / 60000)


def _row_local(bundle: ImportBundle, moment: dt.datetime, text: str) -> dt.datetime:
    """``moment`` in the offset its own ``… Text`` twin names, else in the
    file's most common offset. With no offset at all it returns a moment with
    no zone, which turns the 04:00 rule off: a date-only guess needs a zone."""
    offset = _offset(text)
    if offset is None:
        offset = bundle.utc_offset
    if offset is None:
        return moment.replace(tzinfo=None)
    return moment.astimezone(dt.timezone(offset))


def _offset(text: str) -> dt.timedelta | None:
    """``GMT+5:30`` → +5:30. A bare ``GMT`` or ``UTC`` is +0: the exporter's
    browser writes a zero offset with no sign."""
    match = _OFFSET.search(text or "")
    if not match:
        return None
    if match.group(1) is None:
        return dt.timedelta(0)
    sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3) or 0)
    delta = dt.timedelta(hours=hours, minutes=minutes)
    return -delta if sign == "-" else delta


def _file_offset(bundle: ImportBundle, rows: list[dict[str, str]]) -> dt.timedelta | None:
    """Fact 8 — every ``… Text`` column ends with the exporter's offset. Take
    the most common one. More than one means a mixed upload, which is reported."""
    seen = Counter(
        o for o in (_offset(r.get("Date Created Text", "")) for r in rows) if o is not None
    )
    if not seen:
        return None
    if len(seen) > 1:
        _warn(
            bundle,
            "mixed_timezones",
            "rows carry more than one UTC offset (summer time, or files from two zones); "
            "each date uses the offset of its own row",
            len(seen),
            [str(o) for o in seen],
        )
    return seen.most_common(1)[0][0]


def _text_date(value: str, fallback: dt.timedelta | None) -> dt.datetime | None:
    """Fact 12 — ``9/26/2026, 3:04:05 pm GMT+5:30``, month first."""
    match = _TEXT_DATE.match(value or "")
    if not match:
        return None
    month, day, year, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    hour = hour % 12 + (12 if match.group(7).lower() == "pm" else 0)
    offset = _offset(match.group(8).upper()) if match.group(8) else fallback
    try:
        local = dt.datetime(
            year, month, day, hour, minute, second, tzinfo=dt.timezone(offset or dt.timedelta(0))
        )
    except ValueError:
        return None
    return local.astimezone(dt.UTC)


def _json_cell(bundle: ImportBundle, tid: str, column: str, value: str, empty: object) -> object:
    value = value.strip()
    if not value:
        return empty
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        _warn(
            bundle,
            f"unreadable_{column}",
            f"{column} cells that are not valid JSON; skipped",
            1,
            [tid],
        )
        return empty


def _checklists(bundle: ImportBundle, tid: str, value: str) -> list[Checklist]:
    """``{"name": ["item", ...]}``. An item is text, with no ticked state."""
    data = _json_cell(bundle, tid, "checklists", value, {})
    if not isinstance(data, dict):
        return []
    out = []
    for name, items in data.items():
        texts = [str(i).strip() for i in items if str(i).strip()] if isinstance(items, list) else []
        out.append(Checklist(name=str(name).strip() or "Checklist", items=texts))
    return out


def _attachment_names(bundle: ImportBundle, tid: str, value: str) -> list[str]:
    """``[{"title", "url"}]`` → the titles. The URL is dropped here, on
    purpose (§6.8): it can be a public link to the customer's file."""
    data = _json_cell(bundle, tid, "attachments", value, [])
    if not isinstance(data, list):
        return []
    return [str(a.get("title") or "attachment").strip() for a in data if isinstance(a, dict)]


def _comments(bundle: ImportBundle, tid: str, value: str) -> list[Comment]:
    """``[{"text", "by", "date", ...}]``. ``by`` is an email (fact 10)."""
    data = _json_cell(bundle, tid, "comments", value, [])
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        body = str(item.get("text") or "").strip()
        if not body:
            continue
        author = str(item.get("by") or "").strip() or None
        out.append(
            Comment(
                task_ref=tid,
                author_ref=author,
                created_at=_text_date(str(item.get("date") or ""), bundle.utc_offset),
                body_md=body,
            )
        )
    return out


def _person(people: dict[str, Person], name: str) -> str:
    """Return the person ref for a name or an email, and record the person.
    An assignee with no display name shows as an email (fact 10)."""
    name = name.strip()
    if "@" in name:
        ref = name.lower()
        people.setdefault(ref, Person(ref=ref, display_name=name, email=ref))
    else:
        ref = f"name:{name.lower()}"
        people.setdefault(ref, Person(ref=ref, display_name=name))
    return ref


def _warn(bundle: ImportBundle, code: str, message: str, count: int, refs: list[str]) -> None:
    """One warning per code. A second call adds to its count."""
    for existing in bundle.warnings:
        if existing.code == code:
            existing.count += count
            existing.sample_refs = _unique(existing.sample_refs + refs)[:10]
            return
    bundle.warnings.append(
        BundleWarning(code=code, message=message, count=count, sample_refs=_unique(refs)[:10])
    )
