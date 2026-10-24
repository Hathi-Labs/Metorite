"""A stub of the Projects API with write routes, for the operations eval (WS-46 P3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.1, item 1.

The coding stub (:mod:`evals.coding_engine.stub_api`) serves two GETs and
answers 404 to every other route. The operations tasks write, so this stub
serves the reads and the writes that the Projects tools reach for PO-1 to
PO-7, over :mod:`evals.projects_ops.dataset`, and it RECORDS each request:
the verb, the path, the query, the acting member, the status and the JSON
body. A checker reads those records, never only the model's words.

It reuses the coding stub's server (:class:`~evals.coding_engine.stub_api.RunningStub`)
and its handler. Only the body reader is new, because the coding stub reads
no request body.

Three rules of the real gateway hold here too:

* The caller is the ``X-User-Email`` of the request. A stranger gets 403.
  The stub never takes the member from the query or the body (R11).
* A repeat rule goes through the route's own check,
  ``gateway.routes.projects.recurrence.validate_rule``. So a rule that the
  gateway refuses with a 422 is refused here with the same words.
* A custom value goes through the route's own merge,
  ``gateway.routes.projects.custom_fields.apply_values``, at the create
  (#679) and at the edit (WS-46 P6).
* A view's config goes through the route's own normaliser,
  ``gateway.routes.projects.filters.normalise_view_config``, which drops a key
  it does not know (WS-46 P7).
* ``GET /projects/my/today`` answers the dataset's date and zone, as the
  route answers the member's own (WS-46 P7).
* A route this stub does not serve answers 404, with a reason that says so.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from evals.coding_engine import stub_api as coding_stub
from evals.projects_ops.dataset import (
    AGENT_ARCHIVE,
    AGENT_NAME,
    AGENT_QUESTION,
    AREAS,
    CLOSED,
    DELEGATED,
    LED_PROJECTS,
    REPORT_BOARD,
    REPORT_WEEKLY,
    Dataset,
    Project,
    ident,
)

NOT_SERVED = "The eval stub does not serve this route."
NOT_VISIBLE = "Not found, or not visible to you."

#: WS-64 OP-6c. The export route's cap (``export.MAX_EXPORT_ROWS``). A module
#: value, so a test can lower it to see the 422.
EXPORT_CAP = 5000
#: The upload route's limits (``projects/attachments.py``, ``tasks/attachments.py``).
FILE_MAX = 15 * 1024 * 1024
TASK_FILE_BUDGET = 10 * 1024 * 1024
BLOCKED_TYPES = frozenset({".exe", ".dll", ".so", ".bat", ".cmd", ".sh", ".ps1", ".msi"})
#: The export's columns, in the order the route draws them (``export.FIELD_LABELS``).
EXPORT_LABELS = {
    "status": "Status", "type": "Type", "assignees": "Assignees", "start_date": "Start",
    "due_at": "Due", "importance": "Priority", "tags": "Tags", "estimate": "Estimate",
}


@dataclass
class RawAnswer:
    """A route that answers a file, not JSON (the export)."""

    body: bytes
    content_type: str
    headers: dict[str, str]
    rows: int

    def summary(self) -> dict[str, Any]:
        return {"rows": self.rows, "bytes": len(self.body)}

#: The fields ``PATCH /projects/tasks/{id}`` and a bulk ``patch`` may set here.
#: ``type_id`` and ``custom_fields`` since WS-46 P6.
_PATCHABLE = frozenset({
    "title", "description", "status_id", "due_at", "start_date", "estimate_mins", "tags",
    "importance", "leveraged", "type_id", "custom_fields",
})

_ID = r"(?P<id>[0-9a-fA-F-]{36})"


class StubError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _ago(minutes: int) -> str:
    """A stamp *minutes* before now, for the age of a waiting run."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


@dataclass
class OpsRequest:
    """One request the stub saw."""

    method: str
    path: str
    query: dict[str, str]
    member: str
    status: int
    body: Any = None
    #: What the stub answered.
    response: Any = None


@dataclass
class OpsStub:
    """The routes, over one :class:`Dataset`, with state. Pure, so a test can call it."""

    dataset: Dataset
    requests: list[OpsRequest] = field(default_factory=list)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    views: list[dict[str, Any]] = field(default_factory=list)
    #: H-273: the tags each project registers (the stub starts with none).
    tags: list[dict[str, Any]] = field(default_factory=list)
    #: WS-64 OP-6a: the acting member's own areas.
    areas: list[dict[str, Any]] = field(default_factory=list)
    #: WS-64 OP-6c: the files on each task, as the list route draws them.
    attachments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: WS-64 OP-6c: each nudge sent.
    nudges: list[dict[str, Any]] = field(default_factory=list)
    #: WS-64 OP-6b: the grants of each project, by project id.
    grants: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: WS-64 OP-6d: the saved reports, and who each one goes to. The stub has no
    #: mail route at all, so a request for one answers 404 and `mail` stays empty.
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    recipients: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: WS-64 OP-6e: the agent runs of the acting member, newest first. The stub has no route
    #: that answers a run, so a request for one answers 404 and shows as a write.
    agent_runs: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        me, priya_member = self.dataset.members[0], self.dataset.members[1]
        for name, author in ((REPORT_WEEKLY, me), (REPORT_BOARD, priya_member)):
            rid = ident("report", name)
            self.reports[rid] = {
                "id": rid, "name": name, "project_id": None, "scope": "portfolio",
                "config": {}, "schedule": None, "enabled": False,
                "created_by": author.email, "created_at": "2026-10-01T09:00:00+00:00",
            }
        self.recipients = {ident("report", REPORT_WEEKLY): [self._recipient(me.email, me.email)],
                           ident("report", REPORT_BOARD): []}
        self.agent_runs = self._seed_agent_runs()
        launch = self.dataset.project("Launch").id
        priya = self.dataset.members[1].email
        self.grants = {launch: [{
            "id": ident("grant", launch, priya), "project_id": launch, "subject": priya,
            "created_by": self.dataset.members[0].email,
        }]}
        self.areas = [
            {"id": a.id, "name": a.name, "archived": False, "open_tasks": a.open_tasks,
             "held": a.held}
            for a in AREAS
        ]
        for t in self.dataset.tasks:
            project = self.dataset.project(t.project)
            self.tasks[t.id] = {
                "id": t.id, "task_number": t.number, "title": t.title,
                "project_id": project.id, "root_project_id": project.id,
                "project_name": project.name, "status_id": project.lane(t.lane).id,
                "due_at": t.due.isoformat() if t.due else None, "start_date": None,
                "assignees": list(t.assignees), "tags": [], "description": "",
                "source": "manual", "archived_at": None, "completed_at": None,
            }

    # ── the door ────────────────────────────────────────────────────────────

    def handle(
        self, method: str, raw_path: str, member: str, body: Any = None,
    ) -> tuple[int, Any]:
        parts = urlsplit(raw_path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        with self._lock:
            try:
                if self.dataset.member(member) is None:
                    raise StubError(403, "No acting member, or not a member of this organization.")
                answer = self._route(method.upper(), parts.path, query, body)
                status = 200
            except StubError as exc:
                status, answer = exc.status, {"detail": exc.detail}
            recorded = answer.summary() if isinstance(answer, RawAnswer) else answer
            self.requests.append(OpsRequest(
                method.upper(), parts.path, query, member, status, copy.deepcopy(body),
                copy.deepcopy(recorded),
            ))
        return status, answer

    def _route(self, method: str, path: str, query: dict[str, str], body: Any) -> Any:
        for verb, pattern, fn in self._table():
            if verb != method:
                continue
            match = re.fullmatch(pattern, path)
            if match:
                return fn(match.groupdict().get("id") or "", query, body or {})
        raise StubError(404, NOT_SERVED)

    def _table(self) -> list[tuple[str, str, Callable[[str, dict[str, str], Any], Any]]]:
        return [
            ("GET", "/projects/tree", lambda _i, _q, _b: self.tree()),
            ("GET", "/projects/tasks", lambda _i, q, _b: self.list_tasks(q)),
            ("GET", "/projects/search", lambda _i, q, _b: self.search(q)),
            ("GET", f"/projects/tasks/{_ID}", lambda i, _q, _b: self._public(self._task(i))),
            ("GET", f"/projects/tasks/{_ID}/recurrence", lambda i, _q, _b: self.rule_of(i)),
            ("GET", f"/projects/tasks/{_ID}/relations",
             lambda i, _q, _b: self._exists(i, {"subtasks": [], "links": [], "blocked_by": []})),
            ("GET", f"/projects/tasks/{_ID}/attachments", lambda i, _q, _b: self.files(i)),
            ("POST", f"/projects/tasks/{_ID}/attachments", lambda i, _q, b: self.attach(i, b)),
            ("GET", f"/projects/my/tasks/{_ID}", lambda i, _q, _b: self.my_task(i)),
            ("POST", f"/projects/tasks/{_ID}/nudge", lambda i, _q, _b: self.nudge(i)),
            ("GET", "/projects/export/tasks.csv", lambda _i, q, _b: self.export_csv(q)),
            ("GET", f"/projects/tasks/{_ID}/timeline",
             lambda i, _q, _b: self._exists(i, {"rows": [], "total": 0})),
            ("GET", f"/projects/nodes/{_ID}", lambda i, _q, _b: self.node(i)),
            ("GET", f"/projects/nodes/{_ID}/grants", lambda i, _q, _b: self.list_grants(i)),
            ("POST", f"/projects/nodes/{_ID}/grants", lambda i, _q, b: self.add_grant(i, b)),
            # The grant id is the named group. The project id is in the path too.
            ("DELETE", rf"/projects/nodes/[0-9a-fA-F-]{{36}}/grants/{_ID}",
             lambda i, _q, _b: self.remove_grant(i)),
            ("GET", "/projects/agent-runs", lambda _i, q, _b: self.list_agent_runs(q)),
            ("GET", "/projects/reports", lambda _i, _q, _b: self.list_reports()),
            ("GET", f"/projects/reports/{_ID}", lambda i, _q, _b: self.report(i)),
            ("GET", f"/projects/reports/{_ID}/recipients",
             lambda i, _q, _b: self.list_recipients(i)),
            ("POST", f"/projects/reports/{_ID}/recipients",
             lambda i, _q, b: self.add_recipient(i, b)),
            # The id group holds `<report id>/recipients/<email>`, split in the handler.
            ("DELETE", r"/projects/reports/(?P<id>[0-9a-fA-F-]{36}/recipients/[^/]+)",
             lambda i, _q, _b: self.remove_recipient(i)),
            ("PATCH", f"/projects/reports/{_ID}/schedule",
             lambda i, _q, b: self.set_schedule(i, b)),
            ("GET", f"/projects/nodes/{_ID}/statuses", lambda i, _q, _b: self.statuses(i)),
            ("GET", f"/projects/nodes/{_ID}/types", lambda i, _q, _b: self.types(i)),
            ("GET", f"/projects/nodes/{_ID}/fields", lambda i, _q, _b: self.fields(i)),
            ("GET", "/projects/my/today", lambda _i, _q, _b: self.today()),
            ("GET", f"/projects/nodes/{_ID}/views", lambda i, _q, _b: self.list_views(i)),
            ("POST", f"/projects/nodes/{_ID}/views", lambda i, _q, b: self.create_view(i, b)),
            ("GET", f"/projects/nodes/{_ID}/tags", lambda i, _q, _b: self.list_tags(i)),
            ("POST", f"/projects/nodes/{_ID}/tags", lambda i, _q, b: self.create_tag(i, b)),
            ("GET", "/projects/my/areas", lambda _i, _q, _b: self.list_areas()),
            ("POST", "/projects/my/areas", lambda _i, _q, b: self.create_area(b)),
            ("PATCH", f"/projects/my/areas/{_ID}", lambda i, _q, b: self.rename_area(i, b)),
            ("DELETE", f"/projects/my/areas/{_ID}", lambda i, _q, _b: self.remove_area(i)),
            ("GET", "/projects/assignees", lambda _i, q, _b: self.assignees(q)),
            ("GET", "/projects/people/names", lambda _i, q, _b: self.people_names(q)),
            ("POST", "/projects/tasks", lambda _i, _q, b: self.create_task(b)),
            ("POST", "/projects/tasks/bulk", lambda _i, _q, b: self.bulk(b)),
            ("POST", "/projects/nodes", lambda _i, _q, b: self.create_node(b)),
            ("PATCH", f"/projects/tasks/{_ID}", lambda i, _q, b: self.patch_task(i, b)),
            ("PUT", f"/projects/tasks/{_ID}/assignees", lambda i, _q, b: self.assign(i, b)),
            ("PUT", f"/projects/tasks/{_ID}/recurrence", lambda i, _q, b: self.set_rule(i, b)),
            ("DELETE", f"/projects/tasks/{_ID}/recurrence", lambda i, _q, _b: self.clear_rule(i)),
        ]

    # ── the tree and the lanes ──────────────────────────────────────────────

    def _project_of(self, node_id: str) -> Project:
        """The project of a node: the project itself, or a subproject's parent."""
        for p in self.dataset.projects:
            if p.id == node_id:
                return p
        for n in self.nodes:
            if n["id"] == node_id:
                return self._project_of(str(n["parent_id"]))
        raise StubError(404, NOT_VISIBLE)

    def tree(self) -> dict[str, Any]:
        def children(parent: str) -> list[dict[str, Any]]:
            return [
                {"id": n["id"], "name": n["name"], "kind": "project", "status": "active",
                 "children": children(n["id"])}
                for n in self.nodes if n["parent_id"] == parent
            ]

        projects = [
            {"id": p.id, "name": p.name, "kind": "project", "status": "active",
             "children": children(p.id)}
            for p in self.dataset.projects
        ]
        space = {"id": self.dataset.space_id, "name": self.dataset.space, "kind": "space",
                 "status": "active", "children": projects}
        return {"rows": [space], "total": 1 + len(projects) + len(self.nodes)}

    def node(self, node_id: str) -> dict[str, Any]:
        for p in self.dataset.projects:
            if p.id == node_id:
                row = {"id": p.id, "name": p.name, "kind": "project",
                       "parent_id": self.dataset.space_id, "status": "active"}
                return {**row, **self._grant_flags(p)}
        for n in self.nodes:
            if n["id"] == node_id:
                return dict(n)
        raise StubError(404, NOT_VISIBLE)

    # ── who can see a project (WS-64 OP-6b) ─────────────────────────────────

    @staticmethod
    def _grant_refused() -> str:
        """The 403 words of the two grant routes, from the server's own module."""
        from gateway.routes.projects.authority import GRANT_WRITE_REFUSED

        return GRANT_WRITE_REFUSED

    def _grant_flags(self, project: Project) -> dict[str, Any]:
        """``can_write_grants`` as ``GET /projects/nodes/{id}`` draws it (D-OP-11)."""
        if project.name in LED_PROJECTS:
            return {"can_write_grants": True}
        return {"can_write_grants": False, "grant_refusal": self._grant_refused()}

    def _may_write_grant(self, node_id: str) -> Project:
        project = next((p for p in self.dataset.projects if p.id == node_id), None)
        if project is None:
            raise StubError(404, NOT_VISIBLE)
        if project.name not in LED_PROJECTS:
            raise StubError(403, self._grant_refused())
        return project

    def list_grants(self, node_id: str) -> dict[str, Any]:
        self._project_of(node_id)
        rows = sorted(copy.deepcopy(self.grants.get(node_id, [])), key=lambda g: g["subject"])
        return {"rows": rows, "total": len(rows)}

    def add_grant(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """The route's order: the grammar (422), the project (404), the rule (403)."""
        from acb_auth.permissions import normalize_grant_subject

        try:
            subject = normalize_grant_subject(str(body.get("subject") or ""))
        except ValueError as exc:
            raise StubError(422, str(exc)) from None
        project = self._may_write_grant(node_id)
        held = self.grants.setdefault(project.id, [])
        row = next((g for g in held if g["subject"] == subject), None)
        if row is None:
            row = {"id": ident("grant", project.id, subject), "project_id": project.id,
                   "subject": subject, "created_by": self.dataset.members[0].email}
            held.append(row)
        return copy.deepcopy(row)

    def remove_grant(self, grant_id: str) -> dict[str, Any]:
        for project_id, held in self.grants.items():
            row = next((g for g in held if g["id"] == grant_id.lower()), None)
            if row is not None:
                self._may_write_grant(project_id)
                held.remove(row)
                return {"deleted": row["id"], "subject": row["subject"]}
        raise StubError(404, "Grant not found")

    # ── the agent runs that wait for the member (WS-64 OP-6e) ───────────────

    def _seed_agent_runs(self) -> list[dict[str, Any]]:
        me = self.dataset.members[0].email
        by_number = {t.number: t for t in self.dataset.tasks}

        def run(number: int, status: str, minutes: int, ask: dict[str, Any] | None) -> dict:
            then = _ago(minutes)
            return {
                "id": ident("agent-run", str(number)), "task_id": by_number[number].id,
                "member_email": me, "client_via": "mcp:claude.ai:claude-code",
                "client_name": AGENT_NAME, "status": status, "state": status,
                "created_at": then, "last_activity_at": then, "open_elicitation": ask,
            }

        archive = {"type": "elicitation", "content": {"body": "The release shipped. May I archive this?"},
                   "content_metadata": {"request": {"kind": "archive_task",
                                                    "expires_at": "2099-01-01T00:00:00+00:00"}},
                   "created_at": _ago(60)}
        question = {"type": "elicitation", "content": {"body": "Which printer should I clean first?"},
                    "content_metadata": None, "created_at": _ago(120)}
        return [
            run(AGENT_ARCHIVE, "awaiting", 60, archive),
            run(AGENT_QUESTION, "awaiting", 120, question),
            run(3, "completed", 600, None),
        ]

    def list_agent_runs(self, query: dict[str, str]) -> dict[str, Any]:
        rows = [r for r in self.agent_runs if not query.get("status") or r["status"] == query["status"]]
        return {"rows": copy.deepcopy(rows), "next_cursor": None}

    # ── who gets a report, and when (WS-64 OP-6d) ───────────────────────────

    @staticmethod
    def _recipient(email: str, by: str) -> dict[str, Any]:
        return {"recipient": email, "added_by": by, "added_at": "2026-10-01T09:00:00+00:00"}

    def _acting(self) -> str:
        return self.dataset.members[0].email

    def _report(self, report_id: str) -> dict[str, Any]:
        row = self.reports.get(report_id.lower())
        if row is None:
            raise StubError(404, "Report not found")
        return row

    def _report_for_change(self, report_id: str) -> dict[str, Any]:
        """The route's rule: the author or an admin. The member is no admin."""
        from gateway.routes.projects.reports import CHANGE_REFUSED

        row = self._report(report_id)
        if row["created_by"] != self._acting():
            raise StubError(403, CHANGE_REFUSED)
        return row

    def report(self, report_id: str) -> dict[str, Any]:
        row = copy.deepcopy(self._report(report_id))
        may = row["created_by"] == self._acting()
        return {**row, "mine": may, "can_edit": may, "can_delete": may}

    def list_reports(self) -> dict[str, Any]:
        return {"reports": [self.report(rid) for rid in self.reports]}

    def list_recipients(self, report_id: str) -> dict[str, Any]:
        self._report(report_id)
        rows = sorted(self.recipients[report_id.lower()], key=lambda r: r["recipient"])
        return {"report_id": report_id, "recipients": copy.deepcopy(rows)}

    def add_recipient(self, report_id: str, body: dict[str, Any]) -> dict[str, Any]:
        email = str(body.get("recipient") or "").strip().lower()
        if not email or "@" not in email:
            raise StubError(422, "A recipient must be an email address.")
        self._report_for_change(report_id)
        if email not in {m.email for m in self.dataset.members}:
            raise StubError(422, f"{email} is not an address this directory knows.")
        held = self.recipients[report_id.lower()]
        if all(r["recipient"] != email for r in held):
            held.append(self._recipient(email, self._acting()))
        return {"report_id": report_id, "recipient": email}

    def remove_recipient(self, tail: str) -> Any:
        report_id, _, email = tail.partition("/recipients/")
        self._report_for_change(report_id)
        held = self.recipients[report_id.lower()]
        held[:] = [r for r in held if r["recipient"] != unquote(email).strip().lower()]
        return {}

    def set_schedule(self, report_id: str, body: dict[str, Any]) -> dict[str, Any]:
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise StubError(422, "enabled must be true or false.")
        row = self._report_for_change(report_id)
        if enabled and not self.recipients[report_id.lower()]:
            raise StubError(422, "Add at least one recipient before you turn this on.")
        row["enabled"], row["schedule"] = enabled, ("weekly" if enabled else None)
        return {**copy.deepcopy(row), "delivery_armed_on_this_deployment": False}

    def statuses(self, node_id: str) -> dict[str, Any]:
        project = self._project_of(node_id)
        rows = [{"id": lane.id, "name": lane.name, "category": lane.category, "position": i}
                for i, lane in enumerate(project.lanes)]
        return {"rows": rows, "total": len(rows)}

    def types(self, node_id: str) -> dict[str, Any]:
        project = self._project_of(node_id)
        rows = [{"id": t.id, "name": t.name, "is_epic": False, "project_id": project.id}
                for t in project.types]
        return {"rows": rows, "total": len(rows)}

    def fields(self, node_id: str) -> dict[str, Any]:
        project = self._project_of(node_id)
        rows = [f.definition(project.id) for f in project.fields]
        return {"rows": rows, "total": len(rows)}

    def _check_type(self, project: Project, type_id: Any) -> None:
        if type_id is not None and str(type_id) not in {t.id for t in project.types}:
            raise StubError(422, [{"loc": ["body", "type_id"],
                                   "msg": "That type is not in this project."}])

    def _category(self, row: dict[str, Any]) -> str:
        project = self._project_of(str(row["project_id"]))
        lane = next((lane for lane in project.lanes if lane.id == row["status_id"]), None)
        return lane.category if lane else "todo"

    # ── tasks: reads ────────────────────────────────────────────────────────

    def _task(self, task_id: str) -> dict[str, Any]:
        row = self.tasks.get(task_id.lower())
        if row is None:
            raise StubError(404, NOT_VISIBLE)
        return row

    def _exists(self, task_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        self._task(task_id)
        return answer

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**copy.deepcopy(row), "category": self._category(row)}

    def _overdue(self, row: dict[str, Any]) -> bool:
        due = row.get("due_at")
        return (
            self._category(row) not in CLOSED and bool(due)
            and date.fromisoformat(str(due)[:10]) < self.dataset.today
        )

    def _in_scope(self, row: dict[str, Any], project_id: str) -> bool:
        """The task is in *project_id*, or in a subproject under it."""
        if not project_id or row["project_id"] == project_id:
            return True
        try:
            return self._project_of(str(row["project_id"])).id == project_id
        except StubError:
            return False

    def _matching(self, q: dict[str, str]) -> list[dict[str, Any]]:
        """The tasks a list or an export filter picks, by number."""
        pid = (q.get("project_id") or "").strip()
        if pid:
            self._project_of(pid)  # 404 for a node the member cannot see
        cats = {c.strip() for c in (q.get("status_category") or "").split(",") if c.strip()}
        words = (q.get("q") or "").strip().lower()
        rows = [
            r for r in self.tasks.values()
            if self._in_scope(r, pid)
            and (not cats or self._category(r) in cats)
            and (q.get("overdue") not in ("True", "true", "1") or self._overdue(r))
            and (not words or words in str(r["title"]).lower())
            and not r.get("archived_at")
        ]
        rows.sort(key=lambda r: int(r["task_number"]))
        return rows

    def list_tasks(self, q: dict[str, str]) -> dict[str, Any]:
        rows = self._matching(q)
        size = max(1, min(50, int(q.get("page_size") or 25)))
        page = max(1, int(q.get("page") or 1))
        shown = rows[(page - 1) * size: page * size]
        return {"rows": [self._public(r) for r in shown], "total": len(rows)}

    # ── the nudge, the export and the files (WS-64 OP-6c) ───────────────────

    def my_task(self, task_id: str) -> dict[str, Any]:
        """The member's own view of a task: the route answers 404 for one that is not theirs."""
        row = self._task(task_id)
        me = self.dataset.members[0].email
        number = int(row["task_number"])
        delegated = number == DELEGATED
        if not delegated and me not in row["assignees"]:
            raise StubError(404, NOT_VISIBLE)
        priya = self.dataset.members[1]
        last = next((n["at"] for n in reversed(self.nudges) if n["task_id"] == row["id"]), None)
        return {
            "id": row["id"], "title": row["title"], "task_number": number,
            "disposition": "WAITING" if delegated else "NEXT",
            "waiting_on": {"name": priya.name, "email": priya.email} if delegated else None,
            "last_nudged_at": last,
        }

    def nudge(self, task_id: str) -> dict[str, Any]:
        """The route's rules: a done task and an undelegated task are 409, a nudge is in-app."""
        row = self._task(task_id)
        if self._category(row) in CLOSED:
            raise StubError(409, "This task is done. There is nobody to chase.")
        if int(row["task_number"]) != DELEGATED:
            raise StubError(409, "You are not waiting on anybody for this task.")
        who = self.dataset.members[1].email
        at = datetime.now(UTC).isoformat(timespec="seconds")
        self.nudges.append({"task_id": row["id"], "to": who, "at": at})
        return {"notified": [who], "skipped": [], "last_nudged_at": at}

    def export_csv(self, q: dict[str, str]) -> RawAnswer:
        """The filtered list as CSV, or the route's 422 above the cap."""
        from gateway.csv_export import csv_document

        rows = self._matching(q)
        if len(rows) > EXPORT_CAP:
            raise StubError(422, (
                f"{len(rows)} tasks match these filters and the export cap is {EXPORT_CAP}. "
                "Narrow the filter (a status, an assignee, a single project) and export "
                "again — this endpoint will not hand back a partial file."
            ))
        wanted = {k.strip() for k in (q.get("shown_fields") or "").split(",") if k.strip()}
        keys = [k for k in EXPORT_LABELS if k in wanted]

        def cell(row: dict[str, Any], key: str) -> Any:
            if key == "status":
                project = self._project_of(str(row["project_id"]))
                return next((lane.name for lane in project.lanes if lane.id == row["status_id"]), "")
            if key in ("assignees", "tags"):
                return ", ".join(row.get(key) or [])
            return row.get({"estimate": "estimate_mins"}.get(key, key)) or ""

        body = csv_document(
            ["#", "Title", *(EXPORT_LABELS[k] for k in keys)],
            ([r["task_number"], r["title"], *(cell(r, k) for k in keys)] for r in rows),
        )
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        return RawAnswer(
            data, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="projects-tasks.csv"',
             "X-Export-Rows": str(len(rows))},
            len(rows),
        )

    def files(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        rows = copy.deepcopy(self.attachments.get(task_id.lower(), []))
        return {"rows": rows, "total": len(rows)}

    def attach(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """The upload route's checks, in its order, over the file part the handler parsed."""
        row = self._task(task_id)
        part = body.get("file") if isinstance(body, dict) else None
        if not isinstance(part, dict):
            raise StubError(422, [{"loc": ["body", "file"], "msg": "Field required"}])
        name = str(part.get("name") or "attachment")
        size = int(part.get("size") or 0)
        if re.search(r"\.[A-Za-z0-9]+$", name) and "." + name.rsplit(".", 1)[1].lower() in BLOCKED_TYPES:
            raise StubError(400, f"File type not allowed: {name}")
        if not size:
            raise StubError(400, "Empty file")
        if size > FILE_MAX:
            raise StubError(413, f"Attachment too large ({size} bytes; max {FILE_MAX}).")
        held = self.attachments.setdefault(row["id"], [])
        if sum(int(f["size"]) for f in held) + size > TASK_FILE_BUDGET:
            raise StubError(413, "This task's attachments would pass the limit of 10.0 MB.")
        mime = str(part.get("mime") or "application/octet-stream")
        att_id = ident("attachment", row["id"], name)
        held.append({
            "attachment_id": att_id, "kind": "image" if mime.startswith("image/") else "file",
            "name": name, "mime": mime, "size": size,
            "added_by": self.dataset.members[0].email, "created_at": None,
            "url": f"/api/projects/attachments/{att_id}/{name}",
        })
        return copy.deepcopy(held[-1])

    def search(self, q: dict[str, str]) -> dict[str, Any]:
        term = (q.get("q") or "").strip().lower()
        number = term.lstrip("#")
        rows = [
            r for r in self.tasks.values()
            if (number.isdigit() and int(number) == int(r["task_number"]))
            or (not number.isdigit() and term and term in str(r["title"]).lower())
        ]
        rows.sort(key=lambda r: int(r["task_number"]))
        return {"rows": [self._public(r) for r in rows], "truncated": False}

    def rule_of(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        return {"rule": copy.deepcopy(self.rules.get(task_id.lower()))}

    def today(self) -> dict[str, Any]:
        return {"today": self.dataset.today.isoformat(), "timezone": self.dataset.timezone,
                "stored": True}

    # ── saved views (WS-46 P7) ──────────────────────────────────────────────

    def list_views(self, node_id: str) -> dict[str, Any]:
        self._project_of(node_id)
        rows = [copy.deepcopy(v) for v in self.views if v["project_id"] == node_id]
        return {"rows": rows, "total": len(rows)}

    def create_view(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._project_of(node_id)
        name = str(body.get("name") or "").strip()
        if not name:
            raise StubError(422, "A view needs a name.")
        kind = body.get("view_type") or "list"
        if kind not in ("list", "board"):
            raise StubError(422, f"Unknown view type '{kind}'.")
        view = {"id": ident("view", node_id, name), "project_id": node_id, "name": name,
                "view_type": kind, "config": normalise_config(body.get("config"))}
        self.views.append(view)
        return copy.deepcopy(view)

    # ── tags (H-273) ────────────────────────────────────────────────────────

    def list_tags(self, node_id: str) -> dict[str, Any]:
        root = self._project_of(node_id).id
        rows = [copy.deepcopy(t) for t in self.tags if t["project_id"] == root]
        return {"rows": rows, "total": len(rows)}

    def create_tag(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """The route's own rules (``tags.create_tag``): a name is trimmed with
        its inner spaces collapsed, and a clash in the tree is a 409."""
        root = self._project_of(node_id).id
        name = " ".join(str(body.get("name") or "").split())
        if not name:
            raise StubError(422, "A tag needs a name.")
        clash = next((t for t in self.tags
                      if t["project_id"] == root and t["name"].lower() == name.lower()), None)
        if clash is not None:
            raise StubError(409, f"'{clash['name']}' already exists here.")
        tag = {"id": ident("tag", root, name.lower()), "project_id": root, "name": name,
               "color": body.get("color") or "gray", "description": body.get("description")}
        self.tags.append(tag)
        return copy.deepcopy(tag)

    # ── areas (WS-64 OP-6a) ─────────────────────────────────────────────────

    @staticmethod
    def _area_row(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("id", "name", "archived", "open_tasks")}

    def list_areas(self) -> dict[str, Any]:
        rows = [self._area_row(a) for a in self.areas if not a["archived"]]
        return {"rows": rows, "total": len(rows)}

    def _area(self, area_id: str) -> dict[str, Any]:
        """The route's lookup: another member's area is not found, and so is a gone one."""
        row = next((a for a in self.areas if a["id"] == area_id), None)
        if row is None:
            raise StubError(404, "No such area")
        return row

    @staticmethod
    def _area_name(body: dict[str, Any]) -> str:
        name = str(body.get("name") or "").strip()
        if not name:
            raise StubError(422, "An area needs a name.")
        if len(name) > 120:
            raise StubError(422, "An area name is at most 120 characters.")
        return name

    def create_area(self, body: dict[str, Any]) -> dict[str, Any]:
        name = self._area_name(body)
        if any(a["name"].lower() == name.lower() for a in self.areas if not a["archived"]):
            raise StubError(409, f"You already have an area called {name}.")
        row = {"id": ident("area", name), "name": name, "archived": False, "open_tasks": 0,
               "held": 0}
        self.areas.append(row)
        return self._area_row(row)

    def rename_area(self, area_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = self._area_name(body)
        row = self._area(area_id)
        row["name"] = name
        return self._area_row(row)

    def remove_area(self, area_id: str) -> dict[str, Any]:
        """The route's two outcomes: an area with no task goes, any other is archived."""
        row = self._area(area_id)
        if row["held"] == 0:
            self.areas.remove(row)
            outcome = "deleted"
        else:
            row["archived"] = True
            outcome = "archived"
        return {"id": area_id, "outcome": outcome, "tasks": row["held"]}

    def assignees(self, q: dict[str, str]) -> dict[str, Any]:
        words = (q.get("q") or "").strip().lower()
        people = [{"name": m.name, "assignee": m.email} for m in self.dataset.members
                  if words in m.name.lower() or words in m.email]
        return {"people": people, "agents": []}

    def people_names(self, q: dict[str, str]) -> dict[str, Any]:
        wanted = {e.strip().lower() for e in (q.get("emails") or "").split(",") if e.strip()}
        return {"names": {m.email: m.name for m in self.dataset.members if m.email in wanted}}

    # ── tasks: writes ───────────────────────────────────────────────────────

    def create_task(self, body: dict[str, Any]) -> dict[str, Any]:
        title = str(body.get("title") or "").strip()
        if not title:
            raise StubError(422, [{"loc": ["body", "title"], "msg": "A task needs a title."}])
        project = self._project_of(str(body.get("project_id") or ""))
        lane_ids = {lane.id for lane in project.lanes}
        status_id = str(body.get("status_id") or project.lanes[0].id)
        if status_id not in lane_ids:
            raise StubError(422, [{"loc": ["body", "status_id"],
                                   "msg": "That status is not in this project."}])
        self._check_type(project, body.get("type_id"))
        number = 1 + max(int(r["task_number"]) for r in self.tasks.values())
        tid = ident("created", str(number))
        row = {
            "id": tid, "task_number": number, "title": title,
            "project_id": str(body["project_id"]), "root_project_id": project.id,
            "project_name": project.name, "status_id": status_id,
            "due_at": body.get("due_at"), "start_date": body.get("start_date"),
            "assignees": [], "tags": list(body.get("tags") or []),
            "description": str(body.get("description") or ""),
            "source": str(body.get("source") or "manual"), "archived_at": None,
            "completed_at": None, "type_id": body.get("type_id"), "custom_fields": {},
            # WS-46 P13: create_tasks reads the newest tasks before its card.
            "created_at": datetime.now(UTC).isoformat(),
        }
        if body.get("custom_fields") is not None:
            # The create route's own check (#679), before the row exists, so a
            # refused value leaves no task.
            row["custom_fields"] = self._merge_values(project, row, body["custom_fields"])
        self.tasks[tid] = row
        return self._public(row)

    def assign(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self._task(task_id)
        row["assignees"] = [str(a).lower() for a in body.get("assignees") or []]
        return self._public(row)

    def patch_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self._task(task_id)
        unknown = sorted(set(body) - _PATCHABLE)
        if unknown:
            raise StubError(422, [{"loc": ["body", k], "msg": "Not a task field."} for k in unknown])
        if "status_id" in body:
            lanes = {lane.id for lane in self._project_of(str(row["project_id"])).lanes}
            if body["status_id"] not in lanes:
                raise StubError(422, [{"loc": ["body", "status_id"],
                                       "msg": "That status is not in this project."}])
        project = self._project_of(str(row["project_id"]))
        self._check_type(project, body.get("type_id"))
        update = copy.deepcopy(body)
        if "custom_fields" in update:
            update["custom_fields"] = self._merge_values(project, row, update["custom_fields"])
        row.update(update)
        return self._public(row)

    def _merge_values(self, project: Project, row: dict[str, Any], given: Any) -> dict:
        """The route's own merge and check of custom values (``apply_values``)."""
        from fastapi import HTTPException
        from gateway.routes.projects.custom_fields import apply_values

        definitions = [f.definition(project.id) for f in project.fields]
        try:
            merged, _changes = apply_values(row.get("custom_fields") or {}, given, definitions)
        except HTTPException as exc:
            raise StubError(exc.status_code, exc.detail) from exc
        return merged

    def bulk(self, body: dict[str, Any]) -> dict[str, Any]:
        ids = [str(i).lower() for i in body.get("task_ids") or []]
        patch = dict(body.get("patch") or {})
        unknown = sorted(set(patch) - _PATCHABLE - {"status"})
        if unknown or body.get("action"):
            raise StubError(422, "The eval stub applies a patch of task fields only.")
        results, skipped = [], []
        for tid in ids:
            row = self.tasks.get(tid)
            if row is None:
                skipped.append({"task_id": tid, "reason": "not_found"})
                continue
            changed = [k for k, v in patch.items() if row.get(k) != v]
            row.update(copy.deepcopy(patch))
            if changed:
                results.append({"task_id": tid, "changed": changed})
            else:
                skipped.append({"task_id": tid, "reason": "unchanged"})
        return {"requested": len(ids), "applied": len(results), "results": results,
                "skipped": skipped, "failed": []}

    def set_rule(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._task(task_id)
        from fastapi import HTTPException
        from gateway.routes.projects.recurrence import validate_rule

        given = {k: v for k, v in dict(body).items() if v is not None}
        try:
            rule = validate_rule(given)
        except HTTPException as exc:
            raise StubError(exc.status_code, exc.detail) from exc
        self.rules[task_id.lower()] = dict(rule)
        return {"rule": copy.deepcopy(rule)}

    def clear_rule(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        self.rules.pop(task_id.lower(), None)
        return {"ok": True}

    def create_node(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        parent = str(body.get("parent_id") or "")
        if not name:
            raise StubError(422, [{"loc": ["body", "name"], "msg": "A node needs a name."}])
        self._project_of(parent)  # 404 for a parent the member cannot see
        node = {"id": ident("node", name, parent), "name": name, "kind": "project",
                "parent_id": parent, "status": "active"}
        self.nodes.append(node)
        return dict(node)


def normalise_config(config: Any) -> dict[str, Any]:
    """The route's own normaliser of a view's config (``views.create_view``).
    A module function, so a test can make the route drop a key."""
    from gateway.routes.projects.filters import normalise_view_config

    return normalise_view_config(config)


# ── the HTTP server ─────────────────────────────────────────────────────────


def multipart_file(raw: bytes, content_type: str) -> dict[str, Any]:
    """The one file part of a multipart body, as ``{"file": {name, size, mime, sha256}}``.

    The stub keeps the facts a checker needs and not the bytes.
    """
    import hashlib
    from email.parser import BytesParser
    from email.policy import HTTP

    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + raw
    )
    out: dict[str, Any] = {}
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        content = part.get_payload(decode=True) or b""
        out[str(field_name)] = {
            "name": part.get_filename(), "size": len(content),
            "mime": part.get_content_type(), "sha256": hashlib.sha256(content).hexdigest(),
        }
    return out


class _BodyHandler(coding_stub._Handler):
    """The coding stub's handler, with the JSON body read and passed on."""

    stub: OpsStub  # set on the subclass that serve() builds

    def _answer(self) -> None:
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            status, answer = 401, {"detail": "No bearer."}
        else:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            kind = self.headers.get("Content-Type", "")
            if kind.startswith("multipart/form-data"):
                body = multipart_file(raw, kind)
            else:
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
            status, answer = self.stub.handle(
                self.command, self.path, self.headers.get("X-User-Email", ""), body,
            )
        headers = {"Content-Type": "application/json"}
        if isinstance(answer, RawAnswer):
            data = answer.body
            headers = {"Content-Type": answer.content_type, **answer.headers}
        else:
            data = json.dumps(answer).encode("utf-8")
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _answer
    do_POST = _answer
    do_PATCH = _answer
    do_PUT = _answer
    do_DELETE = _answer


def serve(dataset: Dataset) -> coding_stub.RunningStub:
    """Start the stub on a free port of ``127.0.0.1``. Call ``close()`` after."""
    stub = OpsStub(dataset)
    handler = type("_BoundOpsHandler", (_BodyHandler,), {"stub": stub})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="projects-ops-stub", daemon=True)
    thread.start()
    return coding_stub.RunningStub(stub=stub, server=server, thread=thread)  # type: ignore[arg-type]
