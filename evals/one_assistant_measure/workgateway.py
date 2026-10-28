"""The fake gateway for the CRM and Projects turns of the measure (WS-67 OA-8e).

The email turns have their own fake (:mod:`.mailbox`). It replaces the verbs
of the email module. The CRM tools and the Projects tools reach the gateway
through ``httpx``, so this fake sits one level lower: it answers at the
transport. A request to the gateway URL for ``/crm/...`` or ``/projects/...``
gets an answer from the fixtures below. Every other request goes on to the
transport that the harness installed first (the scripted trap, or the live
allow-list), so a stray send still fails the run.

All data is synthetic: no real CRM, no real mail, no member data.

The fake keeps ``calls``, one ``(method, path, params or body)`` for each
request that it answered. The checks read the writes in it. A route that it
does not know answers 404 and is kept in ``unknown``, so a test shows a tool
that reached a route without a fixture.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

__all__ = [
    "CRM_DEALS",
    "CRM_STAGES",
    "NODE_WEB",
    "WorkGateway",
    "cid",
    "pid",
]


def cid(n: int) -> str:
    """A fixed CRM record id. The tools refuse an id that is not a UUID."""
    return f"7a5e0000-0000-4000-a000-{n:012d}"


def pid(n: int) -> str:
    """A fixed Projects id (a node, a task or a status)."""
    return f"7a5e0000-0000-4000-9000-{n:012d}"


ME = "member@example.com"
ANANYA = "ananya@hathilabs.test"
KIRAN = "kiran@hathilabs.test"

# ── the CRM ──────────────────────────────────────────────────────────────────

CRM_STAGES: tuple[dict[str, Any], ...] = (
    {"id": cid(901), "name": "Qualified", "type": "open"},
    {"id": cid(902), "name": "Proposal", "type": "open"},
    {"id": cid(903), "name": "Negotiation", "type": "open"},
    {"id": cid(904), "name": "Closed Won", "type": "won"},
    {"id": cid(905), "name": "Closed Lost", "type": "lost"},
)
LOST_REASONS: tuple[dict[str, Any], ...] = (
    {"id": cid(911), "label": "Price"}, {"id": cid(912), "label": "No budget"},
)
CRM_DEALS: tuple[dict[str, Any], ...] = (
    {"id": cid(1), "name": "Orbit Labs sensor retrofit", "amount": 450000, "currency": "INR",
     "organization_name": "Orbit Labs", "status": "Negotiation", "owner_email": ME,
     "expected_close_date": "2026-11-20", "next_step": "Send the revised quote"},
    {"id": cid(2), "name": "Kestrel pilot", "amount": 120000, "currency": "INR",
     "organization_name": "Kestrel Labs", "status": "Proposal", "owner_email": ME,
     "expected_close_date": "2026-12-05", "next_step": "Book the pilot call"},
    {"id": cid(3), "name": "Vantage rollout", "amount": 800000, "currency": "INR",
     "organization_name": "Vantage Systems", "status": "Qualified", "owner_email": KIRAN,
     "expected_close_date": "2027-01-15", "next_step": "Map the sites"},
    {"id": cid(4), "name": "Helix annual contract", "amount": 300000, "currency": "INR",
     "organization_name": "Helix Foods", "status": "Closed Won", "owner_email": ME,
     "expected_close_date": "2026-10-10"},
)
CRM_LEADS: tuple[dict[str, Any], ...] = (
    {"id": cid(21), "lead_name": "Meera Joshi", "organization_name": "Vantage Systems",
     "email": "meera@vantage.example", "owner_email": ME},
)
CRM_CONTACTS: tuple[dict[str, Any], ...] = (
    {"id": cid(31), "first_name": "Priya", "last_name": "Menon", "title": "Head of Plant",
     "email": "priya@orbitlabs.example", "organization_name": "Orbit Labs"},
)
CRM_ORGS: tuple[dict[str, Any], ...] = (
    {"id": cid(41), "name": "Orbit Labs", "industry": "Instruments",
     "website": "orbitlabs.example"},
    {"id": cid(42), "name": "Kestrel Labs", "industry": "Software"},
)
CRM_TIMELINE: dict[str, list[dict[str, Any]]] = {
    cid(1): [
        {"kind": "activity", "at": "2026-10-24T10:00:00Z",
         "activity": {"type": "call", "subject": "Quote call with Priya",
                      "body": "Priya wants the price cut by 5 percent and a later start.",
                      "created_by": ME}},
        {"kind": "status_change", "at": "2026-10-12T09:00:00Z",
         "status_change": {"from_status": "Proposal", "to_status": "Negotiation",
                           "dwell_seconds": 1209600, "changed_by": ME}},
    ],
}

_CRM_TABLES = {"leads": CRM_LEADS, "deals": CRM_DEALS, "contacts": CRM_CONTACTS,
               "organizations": CRM_ORGS}

# ── Projects ─────────────────────────────────────────────────────────────────

NODE_SPACE = pid(1)
NODE_WEB = pid(2)
NODE_MOBILE = pid(3)
STATUS_TODO, STATUS_DOING, STATUS_REVIEW, STATUS_DONE = pid(11), pid(12), pid(13), pid(14)
STATUSES: tuple[dict[str, Any], ...] = (
    {"id": STATUS_TODO, "name": "To do", "category": "todo", "position": 0},
    {"id": STATUS_DOING, "name": "In progress", "category": "in_progress", "position": 1},
    {"id": STATUS_REVIEW, "name": "In review", "category": "in_progress", "position": 2},
    {"id": STATUS_DONE, "name": "Done", "category": "done", "position": 3},
)
_SPACE_NAME = "Product"


def _task(n: int, title: str, node: str, status: str, *, who: tuple[str, ...] = (),
          due: str = "", done: bool = False) -> dict[str, Any]:
    name = {NODE_WEB: "Website Relaunch", NODE_MOBILE: "Mobile App"}[node]
    category = {STATUS_TODO: "todo", STATUS_DOING: "in_progress", STATUS_REVIEW: "in_progress",
                STATUS_DONE: "done"}[status]
    status_name = {STATUS_TODO: "To do", STATUS_DOING: "In progress", STATUS_REVIEW: "In review",
                   STATUS_DONE: "Done"}[status]
    row: dict[str, Any] = {
        "id": pid(100 + n), "task_number": n, "title": title, "project_id": node,
        "status_name": status_name,
        "project_name": name, "root_project_id": NODE_SPACE, "status_id": status,
        "category": category, "assignees": list(who),
    }
    if due:
        row["due_at"] = due
    if done:
        row["completed_at"] = "2026-10-20T12:00:00Z"
    return row


TASKS: tuple[dict[str, Any], ...] = (
    _task(11, "Draft the launch brief", NODE_WEB, STATUS_TODO, who=(ME,), due="2026-10-30"),
    _task(12, "Review landing page copy", NODE_WEB, STATUS_DOING, who=(ME,), due="2026-10-29"),
    _task(13, "Set up analytics", NODE_WEB, STATUS_DONE, who=(ANANYA,), done=True),
    _task(14, "Collect brand assets", NODE_WEB, STATUS_DONE, who=(ANANYA,), done=True),
    _task(15, "Pick the CDN", NODE_WEB, STATUS_DONE, who=(KIRAN,), done=True),
    _task(21, "Ship the onboarding screens", NODE_MOBILE, STATUS_DOING, who=(ANANYA,),
          due="2026-10-20"),
    _task(22, "Vendor call prep", NODE_MOBILE, STATUS_TODO, who=(ME,), due="2026-10-31"),
    _task(23, "Fix the login crash", NODE_MOBILE, STATUS_REVIEW, who=(ANANYA,), due="2026-10-25"),
)
TASK_IDS = {t["task_number"]: t["id"] for t in TASKS}


def _node(n: str, name: str, parent: str | None = None, kind: str = "project",
          children: list[dict[str, Any]] | None = None, **more: Any) -> dict[str, Any]:
    return {"id": n, "name": name, "kind": kind, "status": "active", "lead": ME,
            "parent_project_id": parent, "root_project_id": NODE_SPACE,
            "children": children or [], **more}


def _tree() -> list[dict[str, Any]]:
    web = _node(NODE_WEB, "Website Relaunch", NODE_SPACE, tasks=5, done=3, cancelled=0)
    mobile = _node(NODE_MOBILE, "Mobile App", NODE_SPACE, tasks=3, done=0, cancelled=0)
    return [_node(NODE_SPACE, _SPACE_NAME, None, children=[web, mobile], tasks=8, done=3,
                  cancelled=0)]


NODES = {NODE_SPACE: _node(NODE_SPACE, _SPACE_NAME),
         NODE_WEB: _node(NODE_WEB, "Website Relaunch", NODE_SPACE),
         NODE_MOBILE: _node(NODE_MOBILE, "Mobile App", NODE_SPACE)}


class WorkGateway:
    """Answers the ``/crm`` and ``/projects`` routes of the measure's chats."""

    def __init__(self, *, mode: str = "native") -> None:
        #: ``native`` or ``mirror`` (CRM Mirror mode, where Zoho owns the records).
        self.mode = mode
        self.calls: list[tuple[str, str, Any]] = []
        self.unknown: list[str] = []
        self._made = 900

    # ── the transport ────────────────────────────────────────────────────────

    @staticmethod
    def base() -> str:
        return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")

    def install(self, mp: Any) -> None:
        """Answer the gateway's ``/crm`` and ``/projects`` at the transport.

        The harness installed a transport guard first. A request that is not
        for the fake goes to that guard, so a stray send still fails."""
        previous = httpx.AsyncHTTPTransport.handle_async_request
        fake = self

        async def handle(transport: Any, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(fake.base() + "/"):
                path = request.url.path
                if path.startswith(("/crm/", "/projects/")):
                    return fake.answer(request, path)
            return await previous(transport, request)

        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)

    def answer(self, request: httpx.Request, path: str) -> httpx.Response:
        params = dict(request.url.params)
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = None
        method = request.method.upper()
        self.calls.append((method, path, body if body is not None else params))
        found = self.route(method, path, params, body)
        if found is None:
            self.unknown.append(f"{method} {path}")
            return httpx.Response(404, json={"detail": "not found"}, request=request)
        status, payload = found if isinstance(found, tuple) else (200, found)
        return httpx.Response(status, json=payload, request=request)

    # ── the writes the checks read ───────────────────────────────────────────

    def writes(self, since: int = 0) -> list[tuple[str, str, Any]]:
        """Each POST, PATCH, PUT and DELETE that the fake answered."""
        return [c for c in self.calls[since:] if c[0] in ("POST", "PATCH", "PUT", "DELETE")]

    # ── the routes ───────────────────────────────────────────────────────────

    def route(self, method: str, path: str, params: dict[str, Any], body: Any) -> Any:
        if path.startswith("/crm/"):
            return self._crm(method, path, params, body)
        return self._projects(method, path, params, body)

    def _crm(self, method: str, path: str, params: dict[str, Any], body: Any) -> Any:
        rest = path[len("/crm/"):].strip("/").split("/")
        head = rest[0]
        if method == "GET":
            if head == "mode":
                return {"mode": self.mode}
            if head == "statuses" and rest[1:] == ["deal"]:
                return list(CRM_STAGES)
            if head == "lost-reasons":
                return list(LOST_REASONS)
            if head == "pipeline":
                return self._pipeline(params.get("owner"))
            found = self._crm_read_family(rest, params)
            if found is not None:
                return found
            if head in _CRM_TABLES and len(rest) == 1:
                return self._search(head, str(params.get("q") or ""))
            if head in _CRM_TABLES and len(rest) == 2:
                return self._record(head, rest[1])
            if head in _CRM_TABLES and rest[2:] == ["timeline"]:
                return {"entries": CRM_TIMELINE.get(rest[1], []), "has_more": False}
            return None
        if self.mode == "mirror" and (
                (method == "POST" and path == "/crm/leads")
                or (method == "PATCH" and head == "deals")
                or (method == "POST" and rest[-1] == "convert")):
            return 409, {"detail": {"code": "mirrored_record", "message": "Zoho owns this."}}
        if method == "POST" and path == "/crm/leads" and isinstance(body, dict):
            self._made += 1
            return {"id": cid(self._made), "owner_email": ME, **body}
        if method == "PATCH" and head == "deals" and len(rest) == 2:
            return {**(self._row("deals", rest[1]) or {}), **(body or {})}
        if method == "POST" and rest[2:] == ["activities"]:
            self._made += 1
            return {"id": cid(self._made), **(body or {})}
        if method == "POST" and head == "leads" and rest[2:] == ["convert"]:
            return {"deal": {"id": cid(950), "name": "Meera Joshi"},
                    "contact": {"id": cid(951)}, "organization": {"id": cid(952)}}
        return None

    def _crm_read_family(self, rest: list[str], params: dict[str, Any]) -> Any:
        """The routes of the injected ``crm_*`` read tools (OA-8e fix round 2).

        The live models of the first run called these routes, and the fake had
        none, so both arms read 404. Each answer is a small synthetic body in
        the shape that ``acb_skills.crm_tools`` reads. None: not one of them."""
        head = rest[0]
        if rest == ["sync", "short-reads"]:
            return []  # nothing is short
        if head == "fields" and len(rest) == 2 and rest[1] in _CRM_TABLES:
            return []  # no custom fields
        if rest == ["pipelines"]:
            return [{"id": cid(920), "name": "Sales", "is_default": True, "stages": [
                {**s, "probability": p}
                for s, p in zip(CRM_STAGES, (20, 40, 70, 100, 0), strict=True)]}]
        if rest == ["owners"]:
            return {"owners": [{"name": "Member", "email": ME}, {"name": "Kiran", "email": KIRAN}]}
        if head == "deals" and rest[2:] == ["contacts"]:
            rows = [{"contact": CRM_CONTACTS[0], "role": "Decision maker", "is_primary": True}]
            return {"rows": rows if rest[1] == cid(1) else []}
        if head == "reports" and len(rest) == 2:
            return self._crm_report(rest[1])
        if rest == ["aggregate"]:
            return {"groups": [
                {"label": s["name"], "type": s["type"], "value": sum(
                    1 for d in CRM_DEALS if d["status"] == s["name"])} for s in CRM_STAGES]}
        if rest == ["aggregate", "stage-dwell"]:
            return {"stages": [{"name": "Negotiation", "samples": 1, "median_days": 14,
                                "p90_days": 14, "max_dwell_days": 21}],
                    "overdue_total": 0, "overdue": []}
        if rest == ["catalogue"]:
            # The catalogue is off: the tools then read the old routes.
            return 404, {"detail": {"code": "catalogue_off"}}
        return None

    @staticmethod
    def _crm_report(kind: str) -> Any:
        stages = [{"status": s, "count": sum(1 for d in CRM_DEALS if d["status"] == s["name"]),
                   "amount": sum(d["amount"] for d in CRM_DEALS if d["status"] == s["name"]),
                   "weighted": 0.0, "currency": "INR"} for s in CRM_STAGES]
        if kind == "pipeline":
            return {"stages": stages, "count": len(CRM_DEALS), "currency": "INR",
                    "amount": sum(d["amount"] for d in CRM_DEALS), "weighted": 0.0}
        if kind == "funnel":
            return {"stages": [{"status": s["status"], "entered": s["count"],
                                "advanced": 0, "conversion_forward": 0,
                                "median_dwell_days": None, "dwell_samples": 0} for s in stages]}
        if kind == "win-loss":
            return {"since": "2026-07-30", "won": 1, "lost": 0, "win_rate": 100,
                    "won_amount": 300000, "lost_amount": 0, "closed_without_date": 0,
                    "currency": "INR", "lost_reasons": []}
        if kind == "owners":
            return {"window_days": 90, "owners": [
                {"owner_email": ME, "open_deals": 2, "weighted": 0.0, "won_amount": 300000,
                 "currency": "INR"},
                {"owner_email": KIRAN, "open_deals": 1, "weighted": 0.0, "won_amount": 0,
                 "currency": "INR"}]}
        return None

    @staticmethod
    def _row(table: str, rid: str) -> dict[str, Any] | None:
        return next((r for r in _CRM_TABLES[table] if r["id"] == rid), None)

    def _record(self, table: str, rid: str) -> Any:
        row = self._row(table, rid)
        return row if row is not None else (404, {"detail": "not found"})

    @staticmethod
    def _search(table: str, query: str) -> dict[str, Any]:
        words = [w for w in re.findall(r"[a-z0-9@.]+", query.lower()) if len(w) > 1]
        rows = [r for r in _CRM_TABLES[table]
                if not words or all(w in json.dumps(r).lower() for w in words)]
        return {"rows": rows, "total": len(rows)}

    @staticmethod
    def _pipeline(owner: Any) -> dict[str, Any]:
        lanes = []
        for stage in CRM_STAGES:
            rows = [d for d in CRM_DEALS if d["status"] == stage["name"]
                    and (not owner or d["owner_email"] == owner)]
            lanes.append({"status": stage, "count": len(rows),
                          "amount": sum(d["amount"] for d in rows), "rows": rows})
        return {"lanes": lanes}

    def _projects(self, method: str, path: str, params: dict[str, Any], body: Any) -> Any:
        rest = path[len("/projects/"):].strip("/")
        parts = rest.split("/")
        if method == "GET":
            return self._projects_read(rest, parts, params)
        if method == "POST" and rest == "my/tasks" and isinstance(body, dict):
            self._made += 1
            return {"id": pid(self._made), "task_number": 900 + self._made - 900,
                    "title": body.get("title"), "category": "todo", "assignees": [ME]}
        if method == "POST" and rest == "tasks/bulk" and isinstance(body, dict):
            ids = list(body.get("task_ids") or [])
            return {"applied": len(ids), "requested": len(ids), "skipped": [], "failed": [],
                    "results": [{"task_id": i} for i in ids],
                    "subtasks_archived": 0}
        if method == "POST" and parts[0] == "tasks" and parts[2:] == ["archive"]:
            task = next((t for t in TASKS if t["id"] == parts[1]), {})
            return {**task, "archived_at": "2026-10-28T09:00:00Z"}
        if method == "POST" and rest == "nodes" and isinstance(body, dict):
            self._made += 1
            return {"id": pid(self._made), "name": body.get("name"), "kind": "project",
                    "status": "active", "children": []}
        if method == "POST" and parts[0] == "tasks" and parts[2:] == ["complete"]:
            task = next((t for t in TASKS if t["id"] == parts[1]), {})
            return {**task, "completed_at": "2026-10-28T09:00:00Z", "category": "done"}
        if method == "POST" and rest == "tasks" and isinstance(body, dict):
            self._made += 1
            return {"id": pid(self._made), "task_number": 30 + self._made - 900,
                    "title": body.get("title"), "category": "todo",
                    "project_id": body.get("project_id")}
        if method == "POST" and parts[0] == "nodes" and parts[2:] in (["tags"], ["types"]):
            self._made += 1
            return {"id": pid(self._made), "name": (body or {}).get("name")}
        if method == "PATCH" and parts[0] == "statuses":
            row = next((s for s in STATUSES if s["id"] == parts[1]), {})
            return {**row, **(body or {})}
        return None

    def _projects_read(self, rest: str, parts: list[str], params: dict[str, Any]) -> Any:
        if rest == "tree":
            return {"rows": _tree(), "total": 3}
        if rest == "summary":
            return self._summary(None)
        if rest == "my/today":
            return {"today": "2026-10-28", "timezone": "Asia/Kolkata", "stored": True,
                    "valid": True}
        if rest == "my/project":
            return 404, {"detail": "not found"}
        if rest == "assigned-to-me":
            rows = [t for t in TASKS if ME in t["assignees"] and not t.get("completed_at")]
            return {"rows": rows, "total": len(rows)}
        if rest == "search":
            return self._find(str(params.get("q") or ""))
        if rest == "tasks":
            return self._list(params)
        if rest.startswith("analytics/"):
            return self._analytics(parts[1])
        found = self._projects_read_more(rest)
        if found is not None:
            return found
        if parts[0] == "nodes" and len(parts) >= 2 and parts[1] in NODES:
            return self._node_read(parts)
        if parts[0] == "tasks" and len(parts) >= 2:
            task = next((t for t in TASKS if t["id"] == parts[1]), None)
            if task is None:
                return 404, {"detail": "not found"}
            return {"subtasks": [], "progress": {}} if parts[2:] == ["relations"] else task
        return None

    @staticmethod
    def _projects_read_more(rest: str) -> Any:
        """The Projects routes that the live models called and the fake lacked
        (OA-8e fix round 2). None: not one of them."""
        due = [t for t in TASKS if t.get("due_at") and not t.get("completed_at")]
        if rest in ("calendar", "my/calendar"):
            rows = due if rest == "calendar" else [t for t in due if ME in t["assignees"]]
            return {"rows": rows, "truncated": False}
        if rest == "my/inbox":
            rows = [t for t in TASKS if ME in t["assignees"] and not t.get("completed_at")]
            return {"rows": rows, "total": len(rows)}
        if rest in ("my/reminders", "intake"):
            return {"rows": [], "total": 0}
        if rest == "my/areas":
            return {"rows": []}
        if rest == "assignees":
            return {"people": [
                {"name": "Ananya", "assignee": ANANYA, "load": {"open": 2}},
                {"name": "Member", "assignee": ME, "load": {"open": 3}},
                {"name": "Kiran", "assignee": KIRAN, "load": {"open": 0}}], "agents": []}
        if rest == "people/names":
            return {"names": {ANANYA: "Ananya", ME: "Member", KIRAN: "Kiran"}}
        if rest == "needs-attention":
            return {"rows": [], "total": 0, "counts": {}}
        if rest == "agent-runs":
            return {"rows": []}
        return None

    def _node_read(self, parts: list[str]) -> Any:
        node = NODES[parts[1]]
        tail = parts[2:]
        if not tail:
            return node
        if tail == ["statuses"]:
            return {"rows": list(STATUSES)}
        if tail == ["status-set"]:
            return {"may_edit": True}
        if tail == ["summary"]:
            return self._summary(parts[1])
        if tail in (["types"], ["tags"], ["fields"]):
            return {"rows": []}
        return None

    @staticmethod
    def _summary(node: str | None) -> dict[str, Any]:
        mine = [t for t in TASKS if node in (None, NODE_SPACE, t["project_id"])]
        over = [t for t in mine if t.get("due_at", "9") < "2026-10-28" and not t.get("completed_at")]
        return {"name": NODES[node]["name"] if node else "Portfolio", "level": "project",
                "tasks": len(mine), "overdue": len(over), "projects": 2,
                "by_category": {"todo": 2, "in_progress": 3, "done": 3}, "children": []}

    @staticmethod
    def _find(query: str) -> dict[str, Any]:
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        rows = [t for t in TASKS if words and all(w in t["title"].lower() for w in words)]
        if query.strip().lstrip("#").isdigit():
            rows = [t for t in TASKS if t["task_number"] == int(query.strip().lstrip("#"))]
        return {"rows": rows, "truncated": False}

    @staticmethod
    def _list(params: dict[str, Any]) -> dict[str, Any]:
        rows = list(TASKS)
        if params.get("project_id") and params["project_id"] != NODE_SPACE:
            rows = [t for t in rows if t["project_id"] == params["project_id"]]
        if params.get("status_category"):
            want = set(str(params["status_category"]).split(","))
            rows = [t for t in rows if t["category"] in want]
        if params.get("assignees"):
            rows = [t for t in rows if params["assignees"] in t["assignees"]]
        if params.get("overdue"):
            rows = [t for t in rows if t.get("due_at", "9") < "2026-10-28"
                    and not t.get("completed_at")]
        if params.get("q"):
            words = str(params["q"]).lower().split()
            rows = [t for t in rows if all(w in t["title"].lower() for w in words)]
        return {"rows": rows, "total": len(rows)}

    @staticmethod
    def _analytics(kind: str) -> dict[str, Any]:
        if kind == "load":
            return {"scope": "portfolio", "total_tasks": 5, "people": [
                {"assignee": ANANYA, "open_tasks": 2, "overdue": 2, "due_next_7d": 0, "later": 0},
                {"assignee": ME, "open_tasks": 3, "overdue": 0, "due_next_7d": 3, "later": 0},
            ]}
        if kind == "stuck":
            return {"scope": "portfolio", "stale": [{"band": "7 days", "n": 2}],
                    "blocked_total": 1, "blocked": [TASKS[7]],
                    "overdue": [{"name": "Mobile App", "overdue": 2, "project_id": NODE_MOBILE}]}
        return {"scope": "portfolio"}
