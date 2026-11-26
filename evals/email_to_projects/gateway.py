"""The fake gateway of the email-to-Projects eval.

It EXTENDS ``evals.one_assistant_measure.workgateway.WorkGateway`` and does not
copy it. The parent answers the ``/projects`` routes at the httpx transport.
This class adds three things:

* ``GET /search/company``. The answer is the REAL text of
  ``acb_skills.company_search_tools.format_answer``, so a change to that
  formatter flows into this eval. The hits come from the synthetic mailbox.
  A hit needs EVERY word of the query in one mail (subject, body, sender name
  or address), as the route's full-text match does.
* A project tree of the fixtures, and ``POST /projects/nodes``.
* ``/projects/nodes/<id>/updates``. With ``updates_on`` False, the read answers
  404, and the real tool answers "Project updates are not on for this
  organization yet." before it asks a card.

The fake keeps ``searches`` (the query of each company search), ``created`` and
``updates`` (the bodies it took).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from evals.email_to_projects import fixtures as fx
from evals.one_assistant_measure import workgateway as wg
from evals.one_assistant_measure.mailbox import Mail

_WORD = re.compile(r"[a-z0-9][a-z0-9.@\-]*")


def words_of(query: str) -> list[str]:
    return [w for w in _WORD.findall(query.lower()) if len(w) > 1]


def mail_text(m: Mail) -> str:
    return f"{m.subject} {m.body} {m.sender} {m.address}".lower()


def mails_for(query: str, mails: tuple[Mail, ...] = fx.MAILS) -> list[Mail]:
    """The mails that hold EVERY word of *query*."""
    want = words_of(query)
    return [m for m in mails if want and all(w in mail_text(m) for w in want)]


class ChatGateway(wg.WorkGateway):
    """The Projects fake with company search, the fixture tree and the updates route."""

    def __init__(self, *, updates_on: bool = True, mails: tuple[Mail, ...] = fx.MAILS) -> None:
        super().__init__()
        self.updates_on = updates_on
        self.mails = mails
        self.searches: list[str] = []
        #: The id of each mail that each search returned, as ``(query, ids)``.
        self.found: list[tuple[str, tuple[str, ...]]] = []
        self.created: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.nodes: dict[str, dict[str, Any]] = {
            fx.SPACE: wg._node(fx.SPACE, "Hathi Labs", None, personal=False),
            **{pid: wg._node(pid, name, fx.SPACE, personal=False)
               for name, pid in fx.PROJECT_IDS.items()},
        }

    # ── the transport ────────────────────────────────────────────────────────

    def install(self, mp: Any) -> None:
        previous = httpx.AsyncHTTPTransport.handle_async_request
        fake = self

        async def handle(transport: Any, request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(fake.base() + "/"):
                path = request.url.path
                if path.startswith(("/crm/", "/projects/", "/search/")):
                    return fake.answer(request, path)
            return await previous(transport, request)

        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)

    # ── the routes ───────────────────────────────────────────────────────────

    def route(self, method: str, path: str, params: dict[str, Any], body: Any) -> Any:
        if path == "/search/company" and method == "GET":
            return self._search_company(str(params.get("q") or ""))
        if path.startswith("/projects/"):
            found = self._mine(method, path[len("/projects/"):].strip("/").split("/"), body)
            if found is not None:
                return found
        return super().route(method, path, params, body)

    def _search_company(self, query: str) -> dict[str, Any]:
        self.searches.append(" ".join(query.split()))
        found = mails_for(query, self.mails)
        self.found.append((self.searches[-1], tuple(m.id for m in found)))
        hits = [{
            "app": "email", "kind": "email", "id": m.id, "title": m.subject, "hint": m.sender,
            "when": m.received_at(datetime.now(UTC)),
            "href": f"/email?email={m.id}",
            "snippet": " ".join(m.body.split())[:90],
        } for m in found]
        return {"hits": hits, "skipped": []}

    def _tree_rows(self) -> list[dict[str, Any]]:
        space = dict(self.nodes[fx.SPACE])
        kids = [dict(n, tasks=0, done=0, cancelled=0) for k, n in self.nodes.items()
                if n.get("parent_project_id") == fx.SPACE]
        for kid in kids:
            kid["children"] = [dict(n, tasks=0, done=0, cancelled=0) for n in self.nodes.values()
                               if n.get("parent_project_id") == kid["id"]]
        space["children"] = kids
        return [space]

    def _mine(self, method: str, parts: list[str], body: Any) -> Any:
        if method == "GET" and parts == ["tree"]:
            return {"rows": self._tree_rows(), "total": len(self.nodes)}
        if parts[0] == "nodes" and len(parts) >= 2 and parts[1] in self.nodes:
            node = self.nodes[parts[1]]
            tail = parts[2:]
            if method == "GET" and not tail:
                return node
            if method == "GET" and tail == ["summary"]:
                return {"name": node["name"], "level": "project", "tasks": 0, "overdue": 0,
                        "projects": 0, "by_category": {}, "children": []}
            if tail == ["updates"]:
                if not self.updates_on:
                    return 404, {"detail": "project_updates_off"}
                if method == "GET":
                    return {"rows": [], "total": 0}
                if method == "POST" and isinstance(body, dict):
                    self.updates.append({"project_id": parts[1], "name": node["name"], **body})
                    return {"id": f"upd-{len(self.updates)}", **body}
        if method == "POST" and parts == ["nodes"] and isinstance(body, dict):
            self._made += 1
            made = wg._node(wg.pid(self._made), str(body.get("name")),
                            body.get("parent_project_id") or None, personal=False)
            self.nodes[made["id"]] = made
            self.created.append({"id": made["id"], **body})
            return made
        return None
