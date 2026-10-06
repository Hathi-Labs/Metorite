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
from datetime import date
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from evals.coding_engine import stub_api as coding_stub
from evals.projects_ops.dataset import CLOSED, Dataset, Project, ident

NOT_SERVED = "The eval stub does not serve this route."
NOT_VISIBLE = "Not found, or not visible to you."

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
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
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
            self.requests.append(OpsRequest(
                method.upper(), parts.path, query, member, status, copy.deepcopy(body),
                copy.deepcopy(answer),
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
            ("GET", f"/projects/tasks/{_ID}/attachments",
             lambda i, _q, _b: self._exists(i, {"rows": []})),
            ("GET", f"/projects/tasks/{_ID}/timeline",
             lambda i, _q, _b: self._exists(i, {"rows": [], "total": 0})),
            ("GET", f"/projects/nodes/{_ID}", lambda i, _q, _b: self.node(i)),
            ("GET", f"/projects/nodes/{_ID}/statuses", lambda i, _q, _b: self.statuses(i)),
            ("GET", f"/projects/nodes/{_ID}/types", lambda i, _q, _b: self.types(i)),
            ("GET", f"/projects/nodes/{_ID}/fields", lambda i, _q, _b: self.fields(i)),
            ("GET", "/projects/my/today", lambda _i, _q, _b: self.today()),
            ("GET", f"/projects/nodes/{_ID}/views", lambda i, _q, _b: self.list_views(i)),
            ("POST", f"/projects/nodes/{_ID}/views", lambda i, _q, b: self.create_view(i, b)),
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
                return {"id": p.id, "name": p.name, "kind": "project",
                        "parent_id": self.dataset.space_id, "status": "active"}
        for n in self.nodes:
            if n["id"] == node_id:
                return dict(n)
        raise StubError(404, NOT_VISIBLE)

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

    def list_tasks(self, q: dict[str, str]) -> dict[str, Any]:
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
        size = max(1, min(50, int(q.get("page_size") or 25)))
        page = max(1, int(q.get("page") or 1))
        shown = rows[(page - 1) * size: page * size]
        return {"rows": [self._public(r) for r in shown], "total": len(rows)}

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


class _BodyHandler(coding_stub._Handler):
    """The coding stub's handler, with the JSON body read and passed on."""

    stub: OpsStub  # set on the subclass that serve() builds

    def _answer(self) -> None:
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            status, answer = 401, {"detail": "No bearer."}
        else:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            status, answer = self.stub.handle(
                self.command, self.path, self.headers.get("X-User-Email", ""), body,
            )
        data = json.dumps(answer).encode("utf-8")
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


def serve(dataset: Dataset) -> coding_stub.RunningStub:
    """Start the stub on a free port of ``127.0.0.1``. Call ``close()`` after."""
    stub = OpsStub(dataset)
    handler = type("_BoundOpsHandler", (_BodyHandler,), {"stub": stub})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="projects-ops-stub", daemon=True)
    thread.start()
    return coding_stub.RunningStub(stub=stub, server=server, thread=thread)  # type: ignore[arg-type]
