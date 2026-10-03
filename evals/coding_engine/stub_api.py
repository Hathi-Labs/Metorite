"""A stub of the Projects API that serves the eval dataset (WS-43v).

The tools of projects-assistant are thin wrappers over the gateway's
``/projects/*`` routes (``skill_projects.client``). This stub answers the two
routes that the coding tasks need, from :mod:`evals.coding_engine.dataset`:

* ``GET /projects/tree``: the spaces and their projects.
* ``GET /projects/analytics/dataset``: the rows or the groups of
  ``task_dataset``, with the HR gate of the real route
  (``gateway/routes/projects/analytics_dataset.py``). Without the HR grant a
  row never carries ``estimate_mins`` or ``cycle_hours``. A group by person
  never carries an HR measure, and a group of fewer than three people hides it.

Every other route answers 404 with a reason that names the two routes. The
caller is the ``X-User-Email`` of the request, as on the gateway. The stub
never takes the member from the query (R11).

The stub is a plain ``ThreadingHTTPServer`` on ``127.0.0.1``, so it has no
event loop of its own and never shares one with the run. The runner points
``skill_projects.client.gateway_url`` at it, so the model still reaches the
Router through the agent's own client.
"""
from __future__ import annotations

import json
import statistics
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from evals.coding_engine.dataset import Dataset, Member, Task

#: The row columns of the real route, in its order.
COLUMNS: tuple[str, ...] = (
    "number", "full_id", "title", "project", "root_project", "status",
    "status_category", "type", "tags", "assignees", "estimate_mins", "start",
    "due", "completed_at", "created_at", "cycle_hours", "blockers",
)
HR_ROW_COLUMNS: tuple[str, ...] = ("estimate_mins", "cycle_hours")
HR_MEASURES = frozenset({"estimate_sum", "cycle_hours_median", "cycle_hours_p90"})
GROUP_BY = ("tag", "status", "status_category", "project", "assignee", "type",
            "created_week", "completed_week")
MEASURES = ("count", "estimate_sum", "cycle_hours_median", "cycle_hours_p90")
MIN_GROUP_PEOPLE = 3
CYCLE_WEEKS = 26
_KNOWN_KEYS = frozenset({
    "project_id", "include_subtree", "state", "columns", "limit", "group_by", "measure",
    "status_category", "assignee", "assignees", "tags", "tags_all", "overdue",
    "unassigned", "due_before", "created_after", "completed_after", "completed_before", "q",
})
NOT_SERVED = (
    "The eval stub serves only GET /projects/tree and GET /projects/analytics/dataset. "
    "Use projects_tree and task_dataset."
)


class StubError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class StubRequest:
    method: str
    path: str
    query: dict[str, str]
    member: str
    status: int


@dataclass
class ProjectsStub:
    """The routes, over one :class:`Dataset`. Pure, so a test can call it directly."""

    dataset: Dataset
    requests: list[StubRequest] = field(default_factory=list)

    def handle(self, method: str, raw_path: str, member: str) -> tuple[int, dict[str, Any]]:
        parts = urlsplit(raw_path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        try:
            who = self._member(member)
            body = self._route(method, parts.path, query, who)
            status = 200
        except StubError as exc:
            status, body = exc.status, {"detail": exc.detail}
        self.requests.append(StubRequest(method, parts.path, query, member, status))
        return status, body

    def _member(self, email: str) -> Member:
        found = self.dataset.member(email or "")
        if found is None:
            raise StubError(403, "No acting member, or not a member of this organization.")
        return found

    def _route(self, method: str, path: str, query: dict[str, str], who: Member) -> dict[str, Any]:
        if method == "GET" and path == "/projects/tree":
            return self.tree()
        if method == "GET" and path == "/projects/analytics/dataset":
            return self.task_dataset(query, who)
        raise StubError(404, NOT_SERVED)

    # ── GET /projects/tree ──────────────────────────────────────────────────

    def tree(self) -> dict[str, Any]:
        spaces: dict[str, dict[str, Any]] = {}
        for p in self.dataset.projects:
            space = spaces.setdefault(p.space_id, {
                "id": p.space_id, "name": p.space, "kind": "space", "status": "active",
                "children": [],
            })
            space["children"].append({
                "id": p.id, "name": p.name, "kind": "project", "status": "active",
                "children": [],
            })
        rows = list(spaces.values())
        return {"rows": rows, "total": len(rows) + len(self.dataset.projects)}

    # ── GET /projects/analytics/dataset ─────────────────────────────────────

    def task_dataset(self, query: dict[str, str], who: Member) -> dict[str, Any]:
        unknown = sorted(set(query) - _KNOWN_KEYS)
        if unknown:
            raise StubError(422, f"Unknown query keys: {', '.join(unknown)}.")
        state = query.get("state", "open")
        if state not in ("open", "closed", "all"):
            raise StubError(422, "state is open, closed or all.")
        tasks = [t for t in self._scope(query) if _in_state(t, state)]
        tasks = [t for t in tasks if self._matches(t, query)]
        head = {
            "project_id": query.get("project_id") or None,
            "scope": "node" if query.get("project_id") else "portfolio",
            "include_subtree": _truthy(query.get("include_subtree", "true")),
            "state": state,
            "hr_visible": who.hr_read,
            "cycle_window": {
                "weeks": CYCLE_WEEKS,
                "starts_on": (self.dataset.today - timedelta(weeks=CYCLE_WEEKS)).isoformat(),
                "basis": "first in_progress to first done, on the activity spine",
            },
        }
        if query.get("group_by"):
            return {**head, **self._groups(tasks, query, who)}
        return {**head, **self._rows(tasks, query, who)}

    def _scope(self, query: dict[str, str]) -> list[Task]:
        pid = (query.get("project_id") or "").strip()
        if not pid:
            return list(self.dataset.tasks)
        names = {p.name for p in self.dataset.projects if pid in (p.id, p.space_id)}
        if not names:
            raise StubError(404, "Not found, or not visible to you.")
        return [t for t in self.dataset.tasks if t.project in names]

    def _matches(self, task: Task, q: dict[str, str]) -> bool:
        return all(check(task, q) for check in _FILTERS(self.dataset.today))

    def _rows(self, tasks: list[Task], query: dict[str, str], who: Member) -> dict[str, Any]:
        asked = [c.strip() for c in (query.get("columns") or "").split(",") if c.strip()]
        bad = [c for c in asked if c not in COLUMNS]
        if bad:
            raise StubError(422, f"Unknown columns: {', '.join(bad)}.")
        hidden = [] if who.hr_read else [c for c in HR_ROW_COLUMNS if c in asked]
        columns = [c for c in asked if c not in hidden]
        limit = max(1, min(500, int(query.get("limit") or 200)))
        ordered = sorted(tasks, key=lambda t: t.number)
        rows = [self._row(t, columns) for t in ordered[:limit]]
        return {
            "columns": columns, "rows": rows, "total": len(ordered),
            "truncated": len(ordered) > limit, "hidden_columns": hidden,
        }

    def _row(self, task: Task, columns: Iterable[str]) -> dict[str, Any]:
        project = self.dataset.project(task.project)
        values: dict[str, Any] = {
            "number": task.number, "full_id": task.id, "title": task.title,
            "project": task.project, "root_project": project.space, "status": task.status,
            "status_category": task.status_category, "type": "Task", "tags": [],
            "assignees": list(task.assignees), "estimate_mins": task.estimate_mins,
            "start": None, "due": task.due.isoformat() if task.due else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "created_at": task.created_at.isoformat(), "cycle_hours": task.cycle_hours,
            "blockers": [],
        }
        return {c: values[c] for c in columns}

    def _groups(self, tasks: list[Task], query: dict[str, str], who: Member) -> dict[str, Any]:
        group_by = query["group_by"]
        measure = query.get("measure") or "count"
        if group_by not in GROUP_BY or measure not in MEASURES:
            raise StubError(422, "Unknown group_by or measure.")
        gated = measure in HR_MEASURES and not who.hr_read
        per_person = group_by == "assignee" or bool(query.get("assignee") or query.get("assignees"))
        hidden = gated and per_person
        buckets: dict[str | None, list[Task]] = {}
        for task in tasks:
            for key in _group_keys(task, group_by):
                buckets.setdefault(key, []).append(task)
        groups = [
            self._group(key, members, group_by, measure, hidden=hidden, gated=gated)
            for key, members in sorted(buckets.items(), key=lambda kv: str(kv[0]))
        ]
        out: dict[str, Any] = {
            "group_by": group_by, "measure": measure, "total": len(tasks),
            "groups_total": len(groups), "groups_truncated": False, "groups": groups,
            "basis": "server",
        }
        if hidden:
            out["measure_hidden"] = True
        return out

    def _group(
        self, key: str | None, tasks: list[Task], group_by: str, measure: str,
        *, hidden: bool, gated: bool,
    ) -> dict[str, Any]:
        group: dict[str, Any] = {"key": key, "label": self._label(key, group_by), "n": len(tasks)}
        people = {a for t in tasks for a in t.assignees}
        if hidden:
            return group
        if gated and len(people) < MIN_GROUP_PEOPLE:
            group["measure_hidden"] = True
            return group
        value, measured = _measure(measure, tasks)
        group["value"] = value
        group["measured"] = measured
        return group

    def _label(self, key: str | None, group_by: str) -> str:
        if key is None:
            return "unassigned" if group_by == "assignee" else "none"
        if group_by == "assignee":
            member = self.dataset.member(key)
            return member.name if member else key
        return key


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _in_state(task: Task, state: str) -> bool:
    if state == "open":
        return task.is_open
    if state == "closed":
        return not task.is_open
    return True


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise StubError(422, f"{value!r} is not an ISO date.") from exc


def _csv(value: str) -> set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def _FILTERS(today: date) -> list[Callable[[Task, dict[str, str]], bool]]:
    def done_on(t: Task) -> date | None:
        return t.completed_at.date() if t.completed_at and t.status_category == "done" else None

    def completed_after(t: Task, q: dict[str, str]) -> bool:
        if not q.get("completed_after"):
            return True
        d = done_on(t)
        return d is not None and d >= _day(q["completed_after"])

    def completed_before(t: Task, q: dict[str, str]) -> bool:
        if not q.get("completed_before"):
            return True
        d = done_on(t)
        return d is not None and d < _day(q["completed_before"])

    return [
        lambda t, q: not q.get("status_category") or t.status_category in _csv(q["status_category"]),
        lambda t, q: not q.get("assignee") or q["assignee"].strip().lower() in t.assignees,
        lambda t, q: not q.get("assignees") or bool(_csv(q["assignees"]) & set(t.assignees)),
        lambda t, q: not (q.get("tags") or q.get("tags_all")),
        lambda t, q: not _truthy(q.get("overdue"))
        or (t.is_open and t.due is not None and t.due < today),
        lambda t, q: not _truthy(q.get("unassigned")) or not t.assignees,
        lambda t, q: not q.get("due_before")
        or (t.due is not None and t.due < _day(q["due_before"])),
        lambda t, q: not q.get("created_after") or t.created_at.date() >= _day(q["created_after"]),
        completed_after,
        completed_before,
        lambda t, q: not q.get("q") or q["q"].strip().lower() in t.title.lower(),
    ]


def _week(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    monday = moment.date() - timedelta(days=moment.weekday())
    return monday.isoformat()


def _group_keys(task: Task, group_by: str) -> list[str | None]:
    if group_by == "assignee":
        return list(task.assignees) or [None]
    if group_by == "tag":
        return [None]
    simple: dict[str, str | None] = {
        "status": task.status, "status_category": task.status_category,
        "project": task.project, "type": "Task",
        "created_week": _week(task.created_at), "completed_week": _week(task.completed_at),
    }
    return [simple[group_by]]


def _measure(measure: str, tasks: list[Task]) -> tuple[Any, int]:
    if measure == "count":
        return len(tasks), len(tasks)
    if measure == "estimate_sum":
        values = [t.estimate_mins for t in tasks if t.estimate_mins is not None]
        return int(sum(values)), len(values)
    cycles = sorted(float(t.cycle_hours) for t in tasks if t.cycle_hours is not None)
    if not cycles:
        return None, 0
    if measure == "cycle_hours_median":
        return round(statistics.median(cycles), 1), len(cycles)
    index = max(0, min(len(cycles) - 1, round(0.9 * (len(cycles) - 1))))
    return round(cycles[index], 1), len(cycles)


# ── the HTTP server ─────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    stub: ProjectsStub  # set on the subclass that serve() builds

    def _answer(self) -> None:
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            status, body = 401, {"detail": "No bearer."}
        else:
            status, body = self.stub.handle(
                self.command, self.path, self.headers.get("X-User-Email", ""),
            )
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _answer
    do_POST = _answer
    do_PATCH = _answer
    do_PUT = _answer
    do_DELETE = _answer

    def log_message(self, format: str, *args: Any) -> None:
        del format, args  # quiet: the stub records each request itself


@dataclass
class RunningStub:
    stub: ProjectsStub
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def serve(dataset: Dataset) -> RunningStub:
    """Start the stub on a free port of ``127.0.0.1``. Call ``close()`` after."""
    stub = ProjectsStub(dataset)
    handler = type("_BoundHandler", (_Handler,), {"stub": stub})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="projects-stub", daemon=True)
    thread.start()
    return RunningStub(stub=stub, server=server, thread=thread)
