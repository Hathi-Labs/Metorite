"""F2 — every request FIELD of a mapped Projects route has a tool or a reason.

Spec: ``project-docs/specs/projects_agent_parity.md`` §6.3 (WS-46 P4, D91).

D-PM-37 (``test_projects_chat_coverage.py``) holds each ROUTE to a decision.
This file holds each FIELD of a mapped route's request to one. The fields are
read from the real router, through FastAPI's own ``route.dependant``: the
body model's fields, the query parameters of the route and its dependencies,
and the headers the route itself declares. A hand list would drift, and that
drift is what a recurring task without its rule looked like (§4).

1. Each field is in exactly one of ``manifest.SENDS``, ``FIELD_EXEMPT`` and
   ``FIELD_PLANNED``. A new field fails, and the message names the route and
   the field.
2. No table names a field the router does not serve.
3. Each ``SENDS`` witness names a real argument of an exported tool that may
   reach the route, and the claim is TRUE ON THE WIRE: the tool is called
   through the fake gateway, the card is approved, and the request it sends
   to that route carries the field.
4. Each ``FIELD_PLANNED`` gap is a row of the spec's gap table, and its slice
   is a slice of §12 that is not yet built. A slice that ships removes its
   rows, so the list only goes down.

No database and no gateway process: the router imports, and the client is
patched at httpx.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import skill_projects
from skill_projects import manifest as m

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    REPO_ROOT,
    approve,
    fake_gateway,
    form_stub,
)

Route = tuple[str, str]
SPEC = REPO_ROOT / "project-docs" / "specs" / "projects_agent_parity.md"
TABLES = ("SENDS", "FIELD_EXEMPT", "FIELD_PLANNED")


# ── The fields, as FastAPI reports them ─────────────────────────────────────


def _query_params(dependant: Any) -> list[Any]:
    out = list(dependant.query_params)
    for sub in dependant.dependencies:
        out.extend(_query_params(sub))
    return out


def _dependency_headers(dependant: Any) -> list[str]:
    out: list[str] = []
    for sub in dependant.dependencies:
        out.extend(h.alias for h in sub.header_params)
        out.extend(_dependency_headers(sub))
    return out


def _body_fields(dependant: Any) -> list[str]:
    from pydantic import BaseModel

    out: list[str] = []
    for param in dependant.body_params:
        model = param.field_info.annotation
        if isinstance(model, type) and issubclass(model, BaseModel):
            out.extend(f.alias or name for name, f in model.model_fields.items())
        else:
            # A dict body with no model is one field: the parameter itself.
            out.append(param.alias)
    return out


def _mapped_routes() -> list[tuple[Route, Any]]:
    """Each router route the manifest gives class A, B or C, with its dependant."""
    from gateway.routes.projects import router

    rows = {(r.method, r.path): r for r in m.MANIFEST}
    out: list[tuple[Route, Any]] = []
    for route in router.routes:
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            row = rows.get((method, route.path))
            # A route with no row is F1's failure. A class X route has no tool.
            if row is None or row.cls == "X":
                continue
            out.append(((method, route.path), route.dependant))
    return out


def route_fields() -> dict[Route, dict[str, str]]:
    """``{(method, path): {wire name: "query" | "header" | "body"}}``."""
    out: dict[Route, dict[str, str]] = {}
    for key, dependant in _mapped_routes():
        fields: dict[str, str] = {}
        for q in _query_params(dependant):
            fields[q.alias] = "query"
        for h in dependant.header_params:
            fields[h.alias] = "header"
        for name in _body_fields(dependant):
            fields[name] = "body"
        out[key] = fields
    return out


def _recorded(table: str) -> dict[Route, dict[str, str]]:
    return getattr(m, table)


def unrecorded(fields: dict[Route, dict[str, str]]) -> list[str]:
    """Each field that is in no table, as ``METHOD path · field``."""
    missing: list[str] = []
    for key, names in sorted(fields.items()):
        for name in sorted(names):
            if not any(name in _recorded(t).get(key, {}) for t in TABLES):
                missing.append(f"{key[0]} {key[1]} · {name}")
    return missing


# ── 1 and 2. Each field in exactly one table, and no stale row ──────────────


def test_every_field_is_sent_exempt_or_planned() -> None:
    missing = unrecorded(route_fields())
    assert not missing, (
        "These Projects request fields have no decision. Add each one to "
        "apps/skills/skill-projects/skill_projects/manifest.py: SENDS with the "
        "tool argument that sets it, FIELD_EXEMPT with a reason, or "
        "FIELD_PLANNED with its gap id:\n  " + "\n  ".join(missing)
    )


def test_a_new_field_fails_by_name() -> None:
    """The fence names the route and the field, so the author knows what to add."""
    fields = route_fields()
    fields[("POST", "/projects/tasks")] = {
        **fields[("POST", "/projects/tasks")],
        "zz_new_field": "body",
    }
    assert unrecorded(fields) == ["POST /projects/tasks · zz_new_field"]


def test_no_field_is_in_two_tables() -> None:
    both: list[str] = []
    keys = {k for t in TABLES for k in _recorded(t)}
    for key in sorted(keys):
        seen: dict[str, list[str]] = {}
        for t in TABLES:
            for name in _recorded(t).get(key, {}):
                seen.setdefault(name, []).append(t)
        both.extend(f"{key[0]} {key[1]} · {n} in {ts}" for n, ts in seen.items() if len(ts) > 1)
    assert not both, "a field has one decision, not two:\n  " + "\n  ".join(both)


def test_no_table_names_a_field_the_router_does_not_serve() -> None:
    fields = route_fields()
    stale: list[str] = []
    for t in TABLES:
        for key, names in _recorded(t).items():
            served = fields.get(key)
            if served is None:
                stale.append(f"{t}: {key[0]} {key[1]} is not a mapped route")
                continue
            stale.extend(f"{t}: {key[0]} {key[1]} · {n}" for n in names if n not in served)
    assert not stale, "these rows name fields the router does not take:\n  " + "\n  ".join(stale)


def test_a_dependency_adds_no_header_but_identity() -> None:
    """A dependency's headers are the auth seam. The client sets them from
    the run binding (R11), and a tool argument never does. A new one fails."""
    other = sorted(
        {
            f"{key[0]} {key[1]} · {h}"
            for key, dependant in _mapped_routes()
            for h in _dependency_headers(dependant)
            if h.lower() not in m.IDENTITY_HEADERS
        }
    )
    assert not other, f"a dependency declares a header that is not identity: {other}"


def test_every_exemption_carries_a_reason() -> None:
    for key, names in m.FIELD_EXEMPT.items():
        for name, reason in names.items():
            assert len(reason.strip()) >= 20, f"{key} · {name} has no real reason: {reason!r}"


# ── 3. Each SENDS witness is real ───────────────────────────────────────────


def _witnesses() -> list[tuple[Route, str, str, str]]:
    """``(route, field, tool, argument)`` for each SENDS entry. ``argument`` is
    empty when the tool sets the field by itself."""
    out = []
    for key, names in sorted(m.SENDS.items()):
        for name, witness in sorted(names.items()):
            tool, _, arg = witness.partition(".")
            out.append((key, name, tool, arg))
    return out


def test_every_witness_is_an_argument_of_a_tool_that_reaches_the_route() -> None:
    rows = {(r.method, r.path): r for r in m.MANIFEST}
    for key, name, tool, arg in _witnesses():
        assert tool in skill_projects.__all__, f"{key} · {name}: {tool} is not an exported tool"
        if arg:
            params = inspect.signature(getattr(skill_projects, tool)).parameters
            assert arg in params, f"{key} · {name}: {tool} has no argument {arg!r}"
        assert m.reaches(tool, rows[key].tool), (
            f"{key} · {name}: {tool} may not reach a route the manifest gives {rows[key].tool}"
        )


# The call for each witness. `_BASE` is the smallest call that reaches the
# tool's routes. A witness with an argument adds `_SAMPLE[argument]`, unless
# `_CALL` gives the whole call for that route and field.
UUID, OTHER = tw.UUID, tw.OTHER
_BASE: dict[str, dict[str, Any]] = {
    "analytics_finished": {},
    "analytics_load": {},
    "analytics_outlook": {},
    "analytics_stuck": {},
    "analytics_throughput": {},
    # WS-46 P6: `LIVE` is not archived, and `UUID` is not done.
    "archive_task": {"task_id": tw.LIVE},
    "assign": {"task_id": UUID, "assignees": "priya@x.io"},
    "bulk_update": {"task_ids": f"{UUID},{OTHER}", "status": "done"},
    "calendar": {"start": "2026-09-22", "end": "2026-09-29"},
    "capture_intake": {"title": "Vendor called"},
    "comment": {"task_id": UUID, "body": "Waiting on legal."},
    "complete": {"task_id": UUID},
    "create_field": {"project_id": UUID, "name": "Region", "field_type": "select", "options": "EU"},
    "create_personal_task": {"title": "Renew the domain"},
    "create_project": {"name": "Q4 launch"},
    "create_status": {"project_id": UUID, "name": "Blocked"},
    "create_tag": {"project_id": UUID, "name": "q4"},
    "create_task": {"project_id": UUID, "title": "Call the vendor"},
    "create_type": {"project_id": UUID, "name": "Chore"},
    "defer": {"task_id": UUID, "until": "2026-10-06"},
    "delete_status": {"project_id": UUID, "status": "to do", "move_to": "done"},
    "edit_comment": {"task_id": UUID, "comment_id": tw.LINK, "body": "Waiting on finance."},
    "find_conflicts": {},
    "find_tasks": {"query": "extruder"},
    "fit_for_task": {"title": "Weld the frame"},
    "intake_queue": {},
    "link_tasks": {"task_id": UUID, "other_task_id": OTHER},
    "list_tasks": {},
    "mark_notifications_read": {"ids": OTHER},
    "merge_tags": {"project_id": UUID, "tag": "urgent", "into": "p0"},
    "merge_tasks": {"target_task_id": UUID, "source_task_ids": OTHER},
    "move_project": {"project_id": UUID, "parent_project_id": tw.SALES},
    "move_task": {"task_ids": UUID, "destination_project_id": OTHER},
    "my_work": {},
    # WS-46 P7 (G17)
    "my_areas": {},
    "notifications": {},
    "people_for": {"query": "pri"},
    "propose_plan": {"name": "Q4 launch", "tasks": tw.PLAN_TASKS},
    "rebalance": {},
    "render_report": {"template": "team pulse"},
    "render_timeline": {"task_id": UUID},
    "report_save": {"name": "Weekly", "sections": "finished"},
    "save_view": {"project_id": UUID, "name": "Mine"},
    "set_my_overlay": {"task_id": UUID, "disposition": "someday"},
    "set_recurrence": {"task_id": UUID, "freq": "daily"},
    "set_status_set": {"project_id": UUID, "mode": "own"},
    "task_detail": {"task_id": UUID},
    "team_capacity": {},
    "triage_intake": {"task_id": UUID, "action": "accept"},
    "update_field": {"project_id": UUID, "field": "customer", "name": "Client"},
    "update_project": {"project_id": UUID, "name": "Ops v2"},
    "update_status": {"project_id": UUID, "status": "to do", "name": "Backlog"},
    "update_tag": {"project_id": UUID, "tag": "urgent", "name": "p1"},
    "update_task": {"task_id": UUID, "title": "Fix the extruder now"},
    "update_type": {"project_id": UUID, "type_name": "bug", "name": "Defect"},
}
_SAMPLE: dict[str, Any] = {
    "all_unread": True,
    "assignee": "priya@x.io",
    "assignees_add": "Priya",
    "assignees_remove": "a@x.io",
    "category": "done",
    "color": "red",
    "context": "@home",
    "copy_from": OTHER,
    "day_of_month": 15,
    "description": "The full brief.",
    "destination_project_id": OTHER,
    "disposition": "someday",
    "due": "2026-10-09",
    "due_before": "2026-10-09",
    "emails": "a@x.io",
    "end": "2026-09-29",
    "energy": "high",
    "epic": "yes",
    "estimate_mins": 30,
    "field_type": "select",
    # WS-46 P6: a field and a type the fake project defines.
    "fields": '{"Customer": "SMB"}',
    "horizon_days": 21,
    "icon": "bug",
    "ids": OTHER,
    "important": "yes",
    "include_archived": True,
    "include_done": True,
    "include_subtasks": "yes",
    "include_subtree": False,
    "interval": 2,
    "into": "p0",
    "is_default": True,
    "is_epic": True,
    "lead": "Priya",
    "leveraged": "yes",
    "limit": 5,
    "link_type": "blocks",
    "make_default": True,
    "max_occurrences": 4,
    "move_to": "done",
    "name": "Renamed",
    "next_action": "Call Priya",
    "notes": "From the call.",
    "options": "EU, US",
    "org_wide": True,
    "overdue": True,
    "page": 2,
    "page_size": 10,
    "parent_project_id": tw.SALES,
    "parent_task_id": OTHER,
    "position": 2,
    "project_id": UUID,
    "query": "extruder",
    "reply_to": tw.LINK,
    "required": "yes",
    "skip_current_week": True,
    "source_task_ids": OTHER,
    "start": "2026-10-01",
    "status": "in progress",
    "status_category": "todo",
    "tags": "urgent",
    "tags_add": "q4",
    "tags_remove": "urgent",
    "task_ids": UUID,
    "template": "team pulse",
    "title": "Weld the frame",
    "two_minute": "yes",
    "type": "bug",
    "unassigned": True,
    "unread_only": False,
    "until": "2026-12-31",
    "view_type": "board",
    "watching": True,
    "weekdays": "1,3",
    "weeks": 3,
    # WS-46 P7: a space's settings, the member's overlay, a view and a place.
    "icon_slot": 3,
    "archive_after_months": 6,
    "close_after_months": 12,
    "timezone": "Asia/Kolkata",
    "untriaged": True,
    "block_start": "2026-10-07 14:00",
    "block_end": "2026-10-07 15:00",
    "flexible": "yes",
    "hard_date": "yes",
    "actual_start": "2026-10-07 14:05",
    "actual_end": "2026-10-07 14:50",
    "deep_work": "yes",
    "waiting_on": "Priya",
    "waiting_since": "2026-10-05",
    "expected_by": "2026-10-09",
    "filters": '{"overdue": true}',
    "personal": '{"disposition": "someday", "context": "@home"}',
}
#: The whole call, where the base call takes another branch of the tool.
_CALL: dict[tuple[str, str, str], dict[str, Any]] = {
    ("POST", "/projects/tasks/{task_id}/move", "parent_task_id"): {
        "task_ids": UUID,
        "parent_task_id": OTHER,
    },
    # WS-46 P6 (G8): with answers for the destination, one task moves
    # through the promote door's route.
    ("POST", "/projects/tasks/{task_id}/move", "project_id"): {
        "task_ids": UUID,
        "destination_project_id": OTHER,
        "fields": '{"Customer": "SMB"}',
    },
    ("POST", "/projects/tasks/{task_id}/move", "include_subtasks"): {
        "task_ids": UUID,
        "destination_project_id": OTHER,
        "fields": '{"Customer": "SMB"}',
        "include_subtasks": "yes",
    },
    # WS-46 P6 (G9): the flag rides with a move into a Done lane.
    ("PATCH", "/projects/tasks/{task_id}", "include_subtasks"): {
        "task_id": UUID,
        "status": "done",
        "include_subtasks": "yes",
    },
    ("POST", "/projects/tasks/bulk", "action"): {"task_ids": UUID, "action": "archive"},
    ("POST", "/projects/tasks/bulk", "assignees_add"): {"task_ids": UUID, "assignees_add": "Priya"},
    ("POST", "/projects/tasks/bulk", "assignees_remove"): {
        "task_ids": UUID,
        "assignees_remove": "a@x.io",
    },
    ("POST", "/projects/tasks/bulk", "tags_add"): {"task_ids": UUID, "tags_add": "q4"},
    ("POST", "/projects/tasks/bulk", "tags_remove"): {"task_ids": UUID, "tags_remove": "urgent"},
    ("GET", "/projects/my/inbox", "include_done"): {"view": "inbox", "include_done": True},
    ("GET", "/projects/my/inbox", "page"): {"view": "inbox", "page": 2},
    ("GET", "/projects/my/inbox", "page_size"): {"view": "inbox"},
    ("GET", "/projects/my/calendar", "start"): {
        "start": "2026-09-22",
        "end": "2026-09-29",
        "mine": True,
    },
    ("GET", "/projects/my/calendar", "end"): {
        "start": "2026-09-22",
        "end": "2026-09-29",
        "mine": True,
    },
    # WS-46 P5: the flag rides with a project, never without one.
    ("GET", "/projects/calendar", "include_subtree"): {
        "start": "2026-09-22",
        "end": "2026-09-29",
        "project_id": UUID,
        "include_subtree": True,
    },
    ("POST", "/projects/intake/{task_id}/duplicate", "duplicate_of_task_id"): {
        "task_id": UUID,
        "action": "duplicate",
        "duplicate_of": OTHER,
    },
    ("POST", "/projects/intake/{task_id}/snooze", "until"): {
        "task_id": UUID,
        "action": "snooze",
        "until": "2026-10-06",
    },
    ("POST", "/projects/intake/{task_id}/accept", "status_id"): {
        "task_id": UUID,
        "action": "accept",
        "status": "done",
    },
    ("PATCH", "/projects/nodes/{project_id}", "status"): {"project_id": UUID, "status": "paused"},
    ("PATCH", "/projects/reports/{report_id}", "payload"): {
        "report_id": UUID,
        "name": "Weekly v2",
    },
    ("PATCH", "/projects/views/{view_id}", "name"): {
        "project_id": UUID,
        "name": "Board v2",
        "view_id": tw.VIEW_ID,
    },
    ("PUT", "/projects/tasks/{task_id}/recurrence", "weekdays"): {
        "task_id": UUID,
        "freq": "weekly",
        "weekdays": "1,3",
    },
    ("PUT", "/projects/tasks/{task_id}/recurrence", "day_of_month"): {
        "task_id": UUID,
        "freq": "monthly",
        "day_of_month": 15,
    },
    ("PUT", "/projects/tasks/{task_id}/recurrence", "month_of_year"): {
        "task_id": UUID,
        "freq": "yearly",
        "day_of_month": 15,
        "month_of_year": 3,
    },
    ("PUT", "/projects/tasks/{task_id}/recurrence", "anchor"): {
        "task_id": UUID,
        "freq": "daily",
        "anchor": "completed",
    },
    ("POST", "/projects/notifications/read", "all"): {"all_unread": True},
    ("GET", "/projects/candidates", "title"): {"title": "Weld the frame"},
    ("GET", "/projects/tasks/{task_id}/timeline", "kind"): {"task_id": UUID, "kind": "comments"},
    ("POST", "/projects/nodes", "kind"): {"name": "Q4 launch", "kind": "folder"},
    # WS-46 P7: a Space Settings icon, not a task type's.
    ("PATCH", "/projects/nodes/{project_id}", "icon"): {"project_id": UUID, "icon": "Rocket"},
    # WS-46 P7 (G13): a reorder alone. Ops is first of two top-level nodes in
    # the fake tree, and neither has a position, so the siblings spread too.
    ("POST", "/projects/nodes/{project_id}/move", "position"): {
        "project_id": UUID,
        "place": "last",
    },
    ("PATCH", "/projects/views/{view_id}", "config"): {
        "project_id": UUID,
        "view_id": tw.VIEW_ID,
        "filters": '{"overdue": true}',
    },
    # WS-46 P7 (G16): the overlay goes on its own, with no status.
    ("POST", "/projects/tasks/bulk", "personal"): {
        "task_ids": f"{UUID},{OTHER}",
        "personal": '{"disposition": "someday"}',
    },
}


def _call_for(key: Route, name: str, tool: str, arg: str) -> dict[str, Any]:
    full = _CALL.get((key[0], key[1], name))
    if full is not None:
        return full
    base = dict(_BASE.get(tool, {}))
    if arg and arg not in base:
        base[arg] = _SAMPLE[arg]
    return base


def _responder(call: dict) -> Any:
    """The write fence's gateway, with the report catalogue it lacks."""
    if call["path"] == "/projects/reports/templates":
        return {
            "templates": [
                {"key": "team_pulse", "name": "Team pulse", "available": True, "weeks": 4}
            ]
        }
    if call["path"] == "/projects/reports/preview":
        return {"name": "Team pulse", "sections": {}}
    return tw.responder(call)


def _carries(call: dict, name: str, where: str) -> bool:
    if where == "query":
        return name in (call["params"] or {})
    if where == "header":
        return name.lower() in {h.lower() for h in (call["headers"] or {})}
    body = call["json"]
    if name == "payload":
        return isinstance(body, dict) and bool(body)
    return isinstance(body, dict) and name in body


@pytest.fixture
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


@pytest.mark.parametrize(
    ("key", "name", "tool", "arg"),
    _witnesses(),
    ids=[f"{k[0]} {k[1]} {n}" for k, n, _t, _a in _witnesses()],
)
async def test_every_sends_claim_is_true_on_the_wire(
    key: Route, name: str, tool: str, arg: str, monkeypatch, _an_open_run
) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, tw.FORM_ANSWERS)
    calls = fake_gateway(monkeypatch, _responder)
    kwargs = _call_for(key, name, tool, arg)
    out = await getattr(skill_projects, tool)(**kwargs)
    where = route_fields()[key][name]
    sent = [
        c for c in calls if (r := m.route_for(c["method"], c["path"])) and (r.method, r.path) == key
    ]
    assert sent, f"{tool}({kwargs}) sent nothing to {key[0]} {key[1]}. It said: {out!r}"
    assert any(_carries(c, name, where) for c in sent), (
        f"SENDS says {tool}{'.' + arg if arg else ''} sets {name!r} on {key[0]} {key[1]}, "
        f"and the request carries no such {where} field: {sent}"
    )


# ── 4. Each planned gap is open in the spec ─────────────────────────────────


def _spec_gaps() -> dict[str, str]:
    """``{gap id: slice}`` from the spec's §3.3 gap table."""
    out: dict[str, str] = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 3 and re.fullmatch(r"G\d+", cells[1]):
            out[cells[1]] = cells[-2]
    return out


def _built_slices() -> set[str]:
    """Each slice of §12 that the spec marks built."""
    built: set[str] = set()
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        hit = re.match(r"\|\s*\*\*(P\d+) ·([^|]*)\|", line)
        if hit and "BUILT" in hit.group(2).upper():
            built.add(hit.group(1))
    return built


def test_the_spec_tables_parse() -> None:
    """A spec edit that breaks the parse would pass every check below."""
    assert len(_spec_gaps()) == 22
    assert {"P1", "P2", "P3"} <= _built_slices()


def test_every_planned_field_names_an_open_gap_and_its_slice() -> None:
    gaps, built = _spec_gaps(), _built_slices()
    for gap, slice_id in m.FIELD_GAPS.items():
        assert gap in gaps, f"{gap} is not a row of the gap table in {SPEC.name}"
        assert gaps[gap] == slice_id, f"{gap}: the spec closes it in {gaps[gap]}, not {slice_id}"
        assert slice_id not in built, (
            f"{slice_id} is built, so {gap} is closed. Move its FIELD_PLANNED rows to SENDS."
        )
    used = {g for names in m.FIELD_PLANNED.values() for g in names.values()}
    assert used <= set(m.FIELD_GAPS), f"unknown gap ids: {sorted(used - set(m.FIELD_GAPS))}"
    assert set(m.FIELD_GAPS) <= used, f"gaps with no field: {sorted(set(m.FIELD_GAPS) - used)}"
