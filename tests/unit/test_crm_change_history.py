"""R7 fence — the Change history of the CRM set-up (WS-CRM CRM-M1).

Spec: ``project-docs/specs/crm_conversational.md`` §8 CRM-M1 and §4 (D164).

**The static part (no database).**

- Each write route of ``routes/crm/`` is in exactly one list: LOGGED (its
  body reaches ``change_log.log_change``), HISTORY (the Undo itself),
  PENDING (CRM-M1 PR 2) or OUT (with a reason). A new route that is in no
  list fails, and so does a list entry that names no route (done when 1).
- ``change_log.log_change`` is the one writer of ``crm_change_log``.
- Each area has an Undo function, except ``setup``, which no Undo reverses.
- The migration (341) has the tenant key, FORCE RLS and the tenant policy, the
  one Undo index and the guard trigger. The generated phases carry the table.
- The chat reaches ``crm_change_history`` through the ``crm-records`` pack.

**The R8 part (``_DB_GATE``).** Through the real ``/crm`` router, against a
real Postgres, as the NON-privileged app role:

(a) a second org reads zero rows (done when 5), and the read names the org
    even where RLS does not bind (the superuser);
(b) a stage rename, a lost-reason edit and a saved-view edit each land with
    before and after (done when 2);
(c) an Undo restores the name and adds a row, and a second Undo is refused
    (done when 3);
(d) a refused Mirror write logs nothing, and an overlay edit logs one row;
(e) a write that rolls back leaves no row;
(f) the Undo of a created stage that holds a record is refused;

and the rules around them: a mode change is logged and cannot be undone, a
mode that changed since refuses the Undo, a thing that changed again refuses
it, a private view shows to its owner only, a member who is not an admin
reads only their own changes, and a row never changes.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_CRM = _ROOT / "apps/services/gateway/gateway/routes/crm"
_MIGRATIONS = _ROOT / "infra/postgres"

#: The seam. A LOGGED route reaches a call of this name.
SEAM = "log_change"

#: Each write route that writes one Change history row in CRM-M1 PR 1.
LOGGED: dict[str, str] = {
    "PATCH /crm/settings": "the access rule (company); a mode change is logged and not undoable",
    "PUT /crm/settings/labels": "the names of the record types (company)",
    "POST /crm/statuses/{kind}": "a new stage or status (team); Undo removes it",
    "PATCH /crm/statuses/{kind}/{status_id}": "a stage or status edit (team)",
    "POST /crm/lost-reasons": "a new lost reason (team); Undo removes it",
    "PATCH /crm/lost-reasons/{reason_id}": "a lost reason edit (team)",
    "PATCH /crm/pipelines/{pipeline_id}": "a pipeline rename (company)",
    # WS-CRM CRM-M3a: a new pipeline (company). Undo deletes it, and refuses
    # while a deal names it. Each new stage logs its own deal_stage row.
    "POST /crm/pipelines": "a new pipeline (company); Undo deletes it",
    "POST /crm/pipelines/{pipeline_id}/stages": "a stage into a pipeline (team)",
    "DELETE /crm/pipelines/{pipeline_id}/stages/{status_id}":
        "a stage out of a pipeline (team); Undo puts it back",
    "POST /crm/views": "a saved view (own)",
    "PATCH /crm/views/{view_id}": "a saved view edit (own)",
    "DELETE /crm/views/{view_id}": "a saved view delete (own); Undo brings it back",
    "POST /crm/rules": "a business rule (team, or own for a member's rule)",
    "PATCH /crm/rules/{rule_id}": "a business rule edit",
    "POST /crm/rules/pending/{rule_id}": "the confirm of a rule from the chat, via chat",
}

#: The Undo route. Each Undo function logs through the helper of its area,
#: and the R8 part below proves it: a registry hides the call from the scan.
HISTORY: dict[str, str] = {
    "POST /crm/change-history/{change_id}/undo": "the Undo of one change",
}

#: Config writes that CRM-M1 PR 2 logs. Each one is a defect until then.
PENDING: dict[str, str] = {
    "POST /crm/field-rules": "field rules",
    "PATCH /crm/field-rules/{rule_id}": "field rules",
    "PUT /crm/conversion-mapping": "the conversion map",
    "POST /crm/fields/{entity}": "the field studio",
    "PATCH /crm/fields/{entity}/{key}": "the field studio",
    "DELETE /crm/fields/{entity}/{key}": "the field studio: retire",
    "POST /crm/fields/{entity}/{key}/restore": "the field studio: restore",
    "POST /crm/field-sections/{entity}": "the field studio: sections",
    "PATCH /crm/field-sections/{entity}/{section_id}": "the field studio: sections",
    "DELETE /crm/field-sections/{entity}/{section_id}": "the field studio: sections",
    "PUT /crm/field-layout/{entity}": "the field studio: layout",
    "PUT /crm/catalogue/{module}": "the catalogue",
    "PUT /crm/sync/modules/{key}": "the sync holds",
    "POST /crm/mapping/plans": "the mapping plan (the row points to the version)",
    "POST /crm/mapping/plans/{version}/approve": "the mapping plan",
    "POST /crm/mapping/plans/{version}/restore": "the mapping plan",
    "POST /crm/mapping/suggestions/{slot}/approve": "the mapping suggestions",
    "POST /crm/mapping/suggestions/{slot}/undo": "the mapping suggestions",
    "POST /crm/mapping/suggestions/{slot}/dismiss": "the mapping suggestions",
    "POST /crm/questions/{question_id}/answer": "the open questions",
    "POST /crm/questions/{question_id}/dismiss": "the open questions",
    "DELETE /crm/questions/{question_id}/dismiss": "the open questions",
    "POST /crm/questions/accept-best": "the open questions",
    "POST /crm/questions/{question_id}/reply": "the open questions",
    "DELETE /crm/questions/{question_id}/mine": "the open questions",
    "DELETE /crm/statuses/{kind}/{status_id}": "a delete; its card says it cannot be undone",
    "DELETE /crm/lost-reasons/{reason_id}": "a delete; its card says it cannot be undone",
    "DELETE /crm/rules/{rule_id}": "a delete; its card says it cannot be undone",
    "PUT /crm/statuses/{kind}/order": "the order of the stages",
    "POST /crm/statuses/{kind}/order/reset": "the order of the stages: reset to Zoho",
    # Fix round 1: these two were OUT, and the reason was false. With
    # apply=true the stage import rewrites stage names, places and win
    # chances. The legacy import makes stages (import_zoho.ensure_status).
    "POST /crm/import/zoho/stages": "the Zoho stage import: apply=true writes the stages",
    "POST /crm/import/zoho": "the legacy Zoho import: it makes stages (ensure_status)",
}

#: Write routes that CRM-M1 never logs, each with the reason.
OUT: dict[str, str] = {
    "POST /crm/cutover/start": "the switch to Native: one-way, its own audit line",
    "POST /crm/cutover": "the switch to Native: one-way, its own audit line",
    "POST /crm/reset": "the start over: one-way, its own acb_audit line",
    "POST /crm/connection/zoho/start": "the connection, not a set-up value",
    "POST /crm/connection/disconnect": "the connection, not a set-up value",
    "POST /crm/sync/zoho": "starts a sync run; it changes no set-up value",
    "POST /crm/analysis/rerun": "starts the analysis; it changes no set-up value",
    "POST /crm/mapping/preview": "a READ ONLY preview; it writes nothing",
    "POST /crm/rules/propose": "holds a rule in Redis; it saves nothing",
    "POST /crm/leads": "a record write",
    "PATCH /crm/leads/{record_id}": "a record write",
    "DELETE /crm/leads/{record_id}": "a record write",
    "POST /crm/deals": "a record write",
    "PATCH /crm/deals/{record_id}": "a record write",
    "DELETE /crm/deals/{record_id}": "a record write",
    "POST /crm/contacts": "a record write",
    "PATCH /crm/contacts/{record_id}": "a record write",
    "DELETE /crm/contacts/{record_id}": "a record write",
    "POST /crm/organizations": "a record write",
    "PATCH /crm/organizations/{record_id}": "a record write",
    "DELETE /crm/organizations/{record_id}": "a record write",
    "POST /crm/leads/{record_id}/activities": "a record write",
    "POST /crm/deals/{record_id}/activities": "a record write",
    "POST /crm/contacts/{record_id}/activities": "a record write",
    "POST /crm/organizations/{record_id}/activities": "a record write",
    "PATCH /crm/activities/{activity_id}": "a record write",
    "DELETE /crm/activities/{activity_id}": "a record write",
    "POST /crm/deals/{deal_id}/contacts": "a record write",
    "DELETE /crm/deals/{deal_id}/contacts/{contact_id}": "a record write",
    "PATCH /crm/modules/{module}/records/{record_id}": "a record write",
    "POST /crm/leads/{lead_id}/convert": "a record write",
}


# ── The static fence (no database) ──────────────────────────────────────────


def _calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names


def _reach(start: ast.AST, funcs: dict[str, ast.AST]) -> set[str]:
    """Every call name reachable from *start* through same-module helpers."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        for name in _calls(todo.pop()):
            if name not in seen:
                seen.add(name)
                if name in funcs:
                    todo.append(funcs[name])
    return seen


def write_routes(root: Path = _CRM) -> dict[str, tuple[str, set[str]]]:
    """``"VERB /crm/path"`` → (``file::function``, the calls it reaches)."""
    found: dict[str, tuple[str, set[str]]] = {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {
            n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fn in funcs.values():
            for dec in fn.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in {"post", "patch", "put", "delete"}
                    and dec.args and isinstance(dec.args[0], ast.Constant)
                ):
                    key = f"{dec.func.attr.upper()} /crm{dec.args[0].value}"
                    assert key not in found, f"two handlers for {key}"
                    found[key] = (f"{path.name}::{fn.name}", _reach(fn, funcs))
    return found


def unclassified(routes: dict[str, Any]) -> list[str]:
    lists = (LOGGED, HISTORY, PENDING, OUT)
    return sorted(k for k in routes if sum(k in lst for lst in lists) != 1)


def unlogged(routes: dict[str, tuple[str, set[str]]]) -> list[str]:
    return sorted(
        f"{k} ({routes[k][0]})" for k in LOGGED
        if k in routes and SEAM not in routes[k][1]
    )


def test_the_scan_finds_the_write_routes() -> None:
    assert len(write_routes()) >= 70, "the scan found too few write routes"


def test_every_write_route_is_in_exactly_one_list() -> None:
    """Done when 1. A new write route of ``routes/crm/`` fails here until
    somebody decides: logged, pending for PR 2, or out with a reason."""
    routes = write_routes()
    assert not unclassified(routes), (
        "write routes in no list, or in two (LOGGED, HISTORY, PENDING, OUT): "
        f"{unclassified(routes)}"
    )
    stale = sorted(
        k for lst in (LOGGED, HISTORY, PENDING, OUT) for k in lst if k not in routes
    )
    assert not stale, f"list entries that name no route: {stale}"
    for lst in (LOGGED, PENDING, OUT):
        assert all(reason.strip() for reason in lst.values())


def test_every_logged_route_reaches_the_seam() -> None:
    """Done when 1: each LOGGED route reaches ``log_change`` in its own body
    or in a helper of its module."""
    assert not unlogged(write_routes()), (
        f"LOGGED routes that never call {SEAM}: {unlogged(write_routes())}"
    )


def test_the_fence_fails_on_a_new_route_and_on_a_dropped_call(tmp_path: Path) -> None:
    """Mutation: a new write route in no list, and a LOGGED route that lost
    its call, each fail by name."""
    for path in _CRM.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        if path.name == "views.py":
            body = body.replace(
                "        await log_change(db, user, Change(\n"
                "            area=\"view\", action=change_log.CREATE,",
                "        await _gone(db, user, Change(\n"
                "            area=\"view\", action=change_log.CREATE,",
            )
            body += (
                "\n\n@router.post(\"/zz-new\")\nasync def zz_new() -> None:\n"
                "    return None\n"
            )
        (tmp_path / path.name).write_text(body, encoding="utf-8")
    routes = write_routes(tmp_path)
    assert unclassified(routes) == ["POST /crm/zz-new"]
    assert unlogged(routes) == ["POST /crm/views (views.py::create_view)"]


def test_one_writer_of_the_table() -> None:
    """Only ``change_log.py`` writes ``crm_change_log`` in the gateway, and
    ``log_change`` holds its one INSERT. It opens no session of its own, so
    the row joins the transaction of the change."""
    gateway = _ROOT / "apps/services/gateway/gateway"
    write = re.compile(r"(INSERT INTO|UPDATE|DELETE FROM)\s+crm_change_log\b")
    writers = sorted(
        str(p.relative_to(gateway)).replace("\\", "/")
        for p in gateway.rglob("*.py")
        if write.search(p.read_text(encoding="utf-8"))
    )
    assert writers == ["routes/crm/change_log.py"], writers
    log = (_CRM / "change_log.py").read_text(encoding="utf-8")
    assert log.count("INSERT INTO crm_change_log") == 1
    tree = ast.parse(log)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == SEAM)
    assert "_INSERT_SQL" in ast.unparse(fn)
    assert "_tenant_session" not in ast.unparse(fn)


def test_the_start_over_keeps_the_history() -> None:
    from gateway.routes.crm import core

    assert "crm_change_log" in core.RESET_KEEPS
    assert "crm_change_log" not in core.RESET_TABLES


def test_each_area_has_an_undo_except_the_setup() -> None:
    from gateway.routes import crm  # noqa: F401 — registers the Undo functions
    from gateway.routes.crm import change_log

    assert change_log.undo_areas() == set(change_log.AREAS) - {"setup"}
    assert change_log.ADMIN_AREAS.issubset(change_log.AREAS)
    assert change_log.ADMIN_AREAS.isdisjoint({"view", "rule"})


#: What locks the thing of an Undo: a read ``FOR UPDATE``, the lock seam of
#: ``core.load_row``, the lock of the settings row, or ``admin._current``.
_LOCKS = ("FOR UPDATE", "lock=True", "_LOCK_SETTINGS_SQL", "_current(")


def _undo_functions(root: Path = _CRM) -> dict[str, tuple[str, dict[str, ast.AST]]]:
    """``file::function`` → (its source, the functions of its module), for
    each function that ``@change_log.undoes(...)`` registers."""
    found: dict[str, tuple[str, dict[str, ast.AST]]] = {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {
            n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fn in funcs.values():
            if any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == "undoes" for d in fn.decorator_list
            ):
                found[f"{path.name}::{fn.name}"] = (_code(fn), funcs)
    return found


def _code(fn: Any) -> str:
    """The statements of *fn*, with no docstring: its words are no code."""
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    return "\n".join(ast.unparse(s) for s in body)


def test_each_undo_function_locks_its_thing_before_the_check() -> None:
    """P3a (fix round 1). Each Undo reads its thing ``FOR UPDATE`` before it
    checks the values, so a write between the check and the Undo waits.
    Mutation: drop the lock of one Undo function → red by name."""
    found = _undo_functions()
    assert len(found) == 8, sorted(found)
    late = []
    for name, (source, funcs) in sorted(found.items()):
        first_lock = min((source.find(m) for m in _LOCKS if m in source), default=-1)
        first_check = source.find("changed_since")
        if first_lock < 0 or (0 <= first_check < first_lock):
            late.append(name)
        if "_current(" in source:
            assert "lock=True" in _code(funcs["_current"]), name
    assert not late, f"Undo functions that check before a lock: {late}"
    # Fix round 2 (P3-4): EACH read of a row by `load_row` in an Undo locks,
    # in every branch, not only the first read of the function. Mutation:
    # drop `lock=True` from the stage read of the DELETE branch of
    # `pipelines._undo_stage` → red by name.
    unlocked = sorted(
        f"{name}: {ast.unparse(call)}"
        for name, (_source, funcs) in found.items()
        for call in ast.walk(funcs[name.split("::", 1)[1]])
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "load_row"
        and not any(k.arg == "lock" and getattr(k.value, "value", None) is True
                    for k in call.keywords)
    )
    assert not unlocked, f"Undo reads with no lock: {unlocked}"


def _migration() -> str:
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS crm_change_log" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0].read_text(encoding="utf-8")


def test_the_migration_is_tenant_scoped_and_additive() -> None:
    """Done when 5 and 6, statically. The file names the tenant key, FORCE
    RLS, the policy with USING and WITH CHECK, and nothing that drops."""
    sql = _migration()
    assert re.search(
        r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) ON DELETE CASCADE\s+"
        r"DEFAULT current_setting\('app.tenant_id', true\)::uuid", sql)
    assert "ALTER TABLE crm_change_log FORCE  ROW LEVEL SECURITY;" in sql
    assert "USING      (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS crm_change_log_one_undo" in sql
    assert "CREATE TRIGGER crm_change_log_guard" in sql
    assert "FOREIGN KEY (organization_id, undoes_id)" in sql
    bare = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"\b(DROP TABLE|DROP COLUMN|TRUNCATE|RENAME)\b", bare)
    assert "DROP POLICY IF EXISTS" in bare and "DROP TRIGGER IF EXISTS" in bare


def test_the_generated_phases_carry_the_table() -> None:
    import importlib.util
    import sys

    path = _ROOT / "scripts" / "gen_tenant_migration.py"
    spec = importlib.util.spec_from_file_location("gen_tenant_migration", path)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    sys.modules["gen_tenant_migration"] = gen
    spec.loader.exec_module(gen)
    assert "crm_change_log" in gen.discover_tables()
    assert "crm_change_log" not in gen.EXEMPT
    policies = (_MIGRATIONS / "generated" / "04_policies.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE crm_change_log FORCE  ROW LEVEL SECURITY" in policies


def test_the_chat_reads_the_history_through_the_crm_pack() -> None:
    """The CRM assistant holds the read, and the ``crm-records`` pack holds it
    for the one assistant. The orchestrator's base does not: the base was at
    the 110-tool cap beside the email and Projects core packs
    (``test_projects_packs.py``). The read is in the crm family and reads
    only. ``test_tool_packs_choice.py::test_a_crm_history_question_loads_the_
    pack_that_holds_the_read`` proves that a CRM history question loads the
    pack and reaches the tool."""
    from acb_skills import crm_tools, skill_families
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    orchestrator = json.loads(
        (_ROOT / "apps/agents/agent-orchestrator/config.json").read_text(encoding="utf-8"))
    crm_agent = json.loads(
        (_ROOT / "apps/agents/agent-crm/config.json").read_text(encoding="utf-8"))
    assert "crm_change_history" not in orchestrator["tool_scope"]
    assert "crm_change_history" in crm_agent["own_tool_scope"]
    assert "crm_change_history" in skill_families.SKILL_FAMILIES["crm-records"]["tools"]
    assert skill_families.is_pack("crm-records")
    assert "crm_change_history" in crm_tools.CRM_TOOL_NAMES
    assert "crm_change_history" in skill_families.SKILL_FAMILIES["crm"]["tools"]
    risk = TOOL_ANNOTATIONS["crm_change_history"]
    assert risk["read_only"] is True and risk["open_world"] is False


def test_the_chat_parity_row_names_the_read() -> None:
    from acb_skills import chat_parity

    assert chat_parity.COVERED["GET /api/crm/change-history"] == "crm_change_history"


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_crm_change_history\.py \\?$", workflow, re.M)

# ── The database tests ──────────────────────────────────────────────────────

ADMIN = "admin@crm-m1.test"
OTHER_ADMIN = "boss@crm-m1.test"
MEMBER = "rep@crm-m1.test"

_TABLES = (
    "crm_change_log", "crm_views", "crm_deals", "crm_leads", "crm_pipeline_stages",
    "crm_pipelines", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
    "crm_business_rules", "crm_settings",
)


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str, email: str = ADMIN) -> Any:
    return _principal(email, org, "feature:crm", "admin:access:manage")


def _member(org: str) -> Any:
    return _principal(MEMBER, org, "feature:crm")


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None,
                *, dsn: str | None = None) -> Any:
    """One request through the real ``/crm`` router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        url = dsn or p.app_url.render_as_string(hide_password=False)
        async with (
            tenant_engine_scope(url),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


def _purge(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    _purge(promoted)
    yield promoted
    _purge(promoted)


def _settings(p: Any, org: str, mode: str | None, access: str = "admins") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), :m, :a) ON CONFLICT (organization_id) "
            "DO UPDATE SET mode = EXCLUDED.mode, access = EXCLUDED.access"),
            {"o": org, "m": mode, "a": access})


def _one(p: Any, sql: str, **kw: Any) -> Any:
    with p.admin_engine.begin() as c:
        return c.execute(text(sql), kw).scalar()


def _seed(p: Any, org: str) -> dict[str, str]:
    """Two deal stages and one lost reason. Returns the ids."""
    ids: dict[str, str] = {}
    for key, name, kind, pos in (("open", "Open", "open", 0), ("next", "Next", "ongoing", 10)):
        ids[key] = str(_one(
            p, "INSERT INTO crm_deal_statuses (organization_id, name, type, position) "
               "VALUES (CAST(:o AS uuid), :n, :t, :pos) RETURNING id",
            o=org, n=name, t=kind, pos=pos))
    ids["lost"] = str(_one(
        p, "INSERT INTO crm_lost_reasons (organization_id, label, position) "
           "VALUES (CAST(:o AS uuid), 'Price', 0) RETURNING id", o=org))
    return ids


def _rows(p: Any, org: str) -> list[Any]:
    with p.admin_engine.begin() as c:
        return list(c.execute(text(
            "SELECT * FROM crm_change_log WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY at, id"), {"o": org}).fetchall())


async def _history(p: Any, user: Any, query: str = "") -> dict[str, Any]:
    r = await _call(p, user, "GET", f"/crm/change-history{query}")
    assert r.status_code == 200, r.text
    return r.json()


@_DB_GATE
async def test_a_second_org_reads_zero_rows(ns, app_engine) -> None:  # noqa: F811
    """(a), done when 5: org B reads none of org A's rows, through the route
    and through a bare SELECT as the app role. The superuser, which RLS does
    not bind, still reads none through the route: the read names the org."""
    _settings(ns, ns.org_a, "native")
    _settings(ns, ns.org_b, "native")
    ids = _seed(ns, ns.org_a)
    r = await _call(ns, _admin(ns.org_a), "PATCH", f"/crm/statuses/deal/{ids['open']}",
                    {"name": "Qualify"})
    assert r.status_code == 200, r.text
    assert (await _history(ns, _admin(ns.org_a)))["total"] == 1
    assert (await _history(ns, _admin(ns.org_b))) == {"rows": [], "total": 0}
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_b})
        assert c.execute(text("SELECT count(*) FROM crm_change_log")).scalar() == 0
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        assert c.execute(text("SELECT count(*) FROM crm_change_log")).scalar() == 1
    superuser = ns.admin_engine.url.render_as_string(hide_password=False)
    sees = await _call(ns, _admin(ns.org_b), "GET", "/crm/change-history", dsn=superuser)
    assert sees.status_code == 200 and sees.json()["total"] == 0, sees.text


@_DB_GATE
async def test_a_rename_a_reason_edit_and_a_view_edit_land_with_before_and_after(
    ns, app_engine,  # noqa: F811
) -> None:
    """(b), done when 2."""
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    admin = _admin(ns.org_a)
    assert (await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}",
                        {"name": "Qualify"})).status_code == 200
    assert (await _call(ns, admin, "PATCH", f"/crm/lost-reasons/{ids['lost']}",
                        {"label": "Too dear"})).status_code == 200
    made = await _call(ns, admin, "POST", "/crm/views", {"entity": "deals", "name": "Hot"})
    assert made.status_code == 201, made.text
    view = made.json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                        {"name": "Hot deals"})).status_code == 200
    rows = (await _history(ns, admin))["rows"]
    by = {(r["area"], r["action"]): r for r in rows}
    stage = by[("deal_stage", "change")]
    assert stage["before"] == {"name": "Open"} and stage["after"] == {"name": "Qualify"}
    assert stage["summary"] == 'Renamed the deal stage "Open" to "Qualify"'
    assert stage["ladder_level"] == "team" and stage["via"] == "ui"
    assert stage["actor_email"] == ADMIN and stage["approved_by"] == ADMIN
    assert stage["mode"] == "native" and stage["reversible"] is True
    assert stage["can_undo"] is True and stage["entity_id"] == ids["open"]
    reason = by[("lost_reason", "change")]
    assert reason["before"] == {"label": "Price"} and reason["after"] == {"label": "Too dear"}
    edit = by[("view", "change")]
    assert edit["before"] == {"name": "Hot"} and edit["after"] == {"name": "Hot deals"}
    assert edit["ladder_level"] == "own"
    assert by[("view", "create")]["after"]["name"] == "Hot"
    # Newest first, and the area filter.
    assert [r["area"] for r in rows] == ["view", "view", "lost_reason", "deal_stage"]
    only = await _history(ns, admin, "?area=lost_reason")
    assert only["total"] == 1 and only["rows"][0]["area"] == "lost_reason"
    assert (await _call(ns, admin, "GET", "/crm/change-history?area=nope")).status_code == 422
    # The same value again changes nothing, and logs nothing.
    assert (await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}",
                        {"name": "Qualify"})).status_code == 200
    assert len(_rows(ns, ns.org_a)) == 4


@_DB_GATE
async def test_undo_restores_and_adds_a_row_and_a_second_undo_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """(c), done when 3."""
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    admin = _admin(ns.org_a)
    await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}", {"name": "Qualify"})
    change = (await _history(ns, admin))["rows"][0]
    undo = await _call(ns, admin, "POST", f"/crm/change-history/{change['id']}/undo")
    assert undo.status_code == 200, undo.text
    out = undo.json()
    assert out["undid"] == change["id"]
    assert out["change"]["undoes_id"] == change["id"]
    assert out["change"]["before"] == {"name": "Qualify"}
    assert out["change"]["after"] == {"name": "Open"}
    assert out["change"]["reversible"] is False and out["change"]["can_undo"] is False
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=ids["open"]) == "Open"
    rows = _rows(ns, ns.org_a)
    assert len(rows) == 2 and str(rows[0].undone_by) == out["change"]["id"]
    again = await _call(ns, admin, "POST", f"/crm/change-history/{change['id']}/undo")
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "already_undone"
    # An Undo row cannot be undone either.
    back = await _call(ns, admin, "POST", f"/crm/change-history/{out['change']['id']}/undo")
    assert back.status_code == 409 and back.json()["detail"]["code"] == "not_reversible"
    assert len(_rows(ns, ns.org_a)) == 2


@_DB_GATE
async def test_a_refused_mirror_write_logs_nothing_and_an_overlay_edit_logs_one(
    ns, app_engine,  # noqa: F811
) -> None:
    """(d). In Mirror mode only the overlay columns write."""
    _settings(ns, ns.org_a, "mirror")
    ids = _seed(ns, ns.org_a)
    admin = _admin(ns.org_a)
    refused = await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}",
                          {"name": "Qualify"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "mirror_mode_settings"
    assert (await _call(ns, admin, "POST", "/crm/lost-reasons",
                        {"label": "Z"})).status_code == 409
    assert _rows(ns, ns.org_a) == []
    ok = await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}",
                     {"ui_color": "blue"})
    assert ok.status_code == 200, ok.text
    rows = _rows(ns, ns.org_a)
    assert len(rows) == 1 and rows[0].mode == "mirror"
    assert rows[0].after == {"ui_color": "blue"} and rows[0].before == {"ui_color": None}


@_DB_GATE
async def test_a_write_that_rolls_back_leaves_no_row(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """(e). The row commits or rolls back with the change. Here a step after
    the log fails, so the rename and its row both go."""
    from gateway.routes.crm import admin as crm_admin

    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)

    async def _fails(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("a step after the log failed")

    monkeypatch.setattr(crm_admin, "read_status", _fails)
    with pytest.raises(RuntimeError):
        await _call(ns, _admin(ns.org_a), "PATCH", f"/crm/statuses/deal/{ids['open']}",
                    {"name": "Qualify"})
    assert _rows(ns, ns.org_a) == []
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=ids["open"]) == "Open"


@_DB_GATE
async def test_the_undo_of_a_created_stage_that_holds_a_record_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """(f). The Undo of a create removes the stage, so a stage that holds a
    record refuses. With the record gone, the Undo removes it."""
    _settings(ns, ns.org_a, "native")
    _seed(ns, ns.org_a)
    admin = _admin(ns.org_a)
    made = await _call(ns, admin, "POST", "/crm/statuses/deal", {"name": "Demo", "type": "open"})
    assert made.status_code == 201, made.text
    stage = made.json()["id"]
    change = (await _history(ns, admin))["rows"][0]
    assert change["action"] == "create" and change["after"]["name"] == "Demo"
    deal = str(_one(
        ns, "INSERT INTO crm_deals (organization_id, name, status_id) VALUES "
            "(CAST(:o AS uuid), 'Big', CAST(:s AS uuid)) RETURNING id",
        o=ns.org_a, s=stage))
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{change['id']}/undo")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "in_use"
    assert _one(ns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=stage) == 1
    assert len(_rows(ns, ns.org_a)) == 1 and _rows(ns, ns.org_a)[0].undone_by is None
    _one(ns, "DELETE FROM crm_deals WHERE id = CAST(:i AS uuid) RETURNING id", i=deal)
    done = await _call(ns, admin, "POST", f"/crm/change-history/{change['id']}/undo")
    assert done.status_code == 200, done.text
    assert done.json()["change"]["action"] == "delete"
    assert _one(ns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=stage) == 0


@_DB_GATE
async def test_a_mode_change_is_logged_and_cannot_be_undone(ns, app_engine) -> None:  # noqa: F811
    """Spec §8 CRM-M1: a switch of the mode is logged, and no Undo reverses it."""
    admin = _admin(ns.org_a)
    r = await _call(ns, admin, "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 200, r.text
    rows = (await _history(ns, admin))["rows"]
    assert [(x["area"], x["reversible"]) for x in rows] == [("setup", False)]
    assert rows[0]["after"]["mode"] == "native" and rows[0]["ladder_level"] == "company"
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{rows[0]['id']}/undo")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_reversible"
    # The access rule can be undone, and its Undo writes the old rule back.
    assert (await _call(ns, admin, "PATCH", "/crm/settings",
                        {"access": "everyone"})).status_code == 200
    access = (await _history(ns, admin, "?area=access"))["rows"][0]
    assert access["before"] == {"access": "admins"} and access["after"] == {"access": "everyone"}
    assert (await _call(ns, admin, "POST",
                        f"/crm/change-history/{access['id']}/undo")).status_code == 200
    assert _one(ns, "SELECT access FROM crm_settings WHERE organization_id = CAST(:o AS uuid)",
                o=ns.org_a) == "admins"


@_DB_GATE
async def test_the_undo_refuses_a_changed_mode_and_a_newer_change(ns, app_engine) -> None:  # noqa: F811
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    admin = _admin(ns.org_a)
    await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}", {"name": "B"})
    await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{ids['open']}", {"name": "C"})
    older = (await _history(ns, admin))["rows"][1]
    assert older["after"] == {"name": "B"}
    stale = await _call(ns, admin, "POST", f"/crm/change-history/{older['id']}/undo")
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "changed_since"
    _settings(ns, ns.org_a, "mirror")
    newer = (await _history(ns, admin))["rows"][0]
    moved = await _call(ns, admin, "POST", f"/crm/change-history/{newer['id']}/undo")
    assert moved.status_code == 409 and moved.json()["detail"]["code"] == "mode_changed"
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=ids["open"]) == "C"


def _undo_code(r: Any) -> tuple[int, str | None]:
    detail = r.json().get("detail") if r.status_code >= 400 else None
    return r.status_code, (detail or {}).get("code") if isinstance(detail, dict) else None


def _create_row(p: Any, org: str, area: str) -> Any:
    return next(r for r in _rows(p, org) if r.area == area and r.action == "create")


@_DB_GATE
async def test_the_undo_of_a_created_stage_that_changed_since_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2a (fix round 1). Create a stage, rename it, then undo the create:
    409 ``changed_since``, and the stage and its new name stay. Undo the
    rename first, and then the Undo of the create removes the stage."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    made = await _call(ns, admin, "POST", "/crm/statuses/deal", {"name": "Demo", "type": "open"})
    assert made.status_code == 201, made.text
    stage = made.json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{stage}",
                        {"name": "Demo 2"})).status_code == 200
    create = _create_row(ns, ns.org_a, "deal_stage")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=stage) == "Demo 2"
    assert len(_rows(ns, ns.org_a)) == 2
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))
    rename = next(r for r in _rows(ns, ns.org_a) if r.action == "change")
    assert (await _call(ns, admin, "POST",
                        f"/crm/change-history/{rename.id}/undo")).status_code == 200
    done = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert done.status_code == 200, done.text
    assert _one(ns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=stage) == 0


@_DB_GATE
async def test_the_undo_of_a_created_lost_reason_that_changed_since_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2a (fix round 1): a lost reason, relabelled after its create."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    made = await _call(ns, admin, "POST", "/crm/lost-reasons", {"label": "Budget"})
    assert made.status_code == 201, made.text
    reason = made.json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/lost-reasons/{reason}",
                        {"label": "Budget 2"})).status_code == 200
    create = _create_row(ns, ns.org_a, "lost_reason")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT label FROM crm_lost_reasons WHERE id = CAST(:i AS uuid)",
                i=reason) == "Budget 2"
    assert len(_rows(ns, ns.org_a)) == 2
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


@_DB_GATE
async def test_the_undo_of_a_created_view_that_changed_since_or_went_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2a and P3e (fix round 1): a view renamed after its create is a 409
    ``changed_since``. A view that is gone is a 409 ``gone``, not a 404."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    made = await _call(ns, admin, "POST", "/crm/views", {"entity": "deals", "name": "Hot"})
    assert made.status_code == 201, made.text
    view = made.json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                        {"name": "Hot 2"})).status_code == 200
    create = _create_row(ns, ns.org_a, "view")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT name FROM crm_views WHERE id = CAST(:i AS uuid)", i=view) == "Hot 2"
    assert len(_rows(ns, ns.org_a)) == 2
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))
    assert (await _call(ns, admin, "DELETE", f"/crm/views/{view}")).status_code == 200
    gone = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert _undo_code(gone) == (409, "gone"), gone.text
    assert len(_rows(ns, ns.org_a)) == 3


def _rules_on(monkeypatch: Any) -> None:
    """The flags of the business rules: the mirror, the catalogue, the
    questions and the answer-first list, each for every org."""
    from acb_common import get_settings

    settings = get_settings()
    for name, value in {
        "crm_mirror": True, "crm_mirror_orgs": "*", "crm_catalogue_orgs": "*",
        "crm_questions_orgs": "*", "crm_answer_first_orgs": "*",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)


@_DB_GATE
async def test_the_undo_of_a_created_rule_that_changed_since_is_refused(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """P2a (fix round 1): an org rule whose words changed after its create."""
    _rules_on(monkeypatch)
    _settings(ns, ns.org_a, "mirror")
    admin = _admin(ns.org_a)
    made = await _call(ns, admin, "POST", "/crm/rules",
                       {"body": "All amounts net of GST", "scope": "org"})
    assert made.status_code == 200, made.text
    rule = made.json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/rules/{rule}",
                        {"body": "All amounts with GST"})).status_code == 200
    create = _create_row(ns, ns.org_a, "rule")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{create.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT body FROM crm_business_rules WHERE id = CAST(:i AS uuid)",
                i=rule) == "All amounts with GST"
    assert len(_rows(ns, ns.org_a)) == 2
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


def _pipeline(p: Any, org: str, name: str = "Sales") -> str:
    return str(_one(
        p, "INSERT INTO crm_pipelines (organization_id, name, is_default) "
           "VALUES (CAST(:o AS uuid), :n, true) RETURNING id", o=org, n=name))


@_DB_GATE
async def test_the_undo_of_an_added_pipeline_stage_that_moved_since_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2a (fix round 1): a stage put in a pipeline, taken out, and put back
    at another place. The Undo of the first add is a 409, and the stage stays."""
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    pipeline = _pipeline(ns, ns.org_a)
    admin = _admin(ns.org_a)
    base = f"/crm/pipelines/{pipeline}/stages"
    assert (await _call(ns, admin, "POST", base,
                        {"status_id": ids["next"], "position": 3})).status_code == 201
    first = _create_row(ns, ns.org_a, "pipeline_stage")
    assert (await _call(ns, admin, "DELETE", f"{base}/{ids['next']}")).status_code == 200
    assert (await _call(ns, admin, "POST", base,
                        {"status_id": ids["next"], "position": 7})).status_code == 201
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{first.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT position FROM crm_pipeline_stages "
                    "WHERE pipeline_id = CAST(:pid AS uuid) AND status_id = CAST(:sid AS uuid)",
                pid=pipeline, sid=ids["next"]) == 7
    assert len(_rows(ns, ns.org_a)) == 3
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


async def _until_it_waits(p: Any, call: asyncio.Task) -> None:
    """Wait until a statement of the cluster waits on a lock, or *call* ends."""
    for _ in range(150):
        with p.admin_engine.begin() as c:
            if c.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar():
                return
        if call.done():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the request never waited on the other transaction")


_PLACE_INSERT = (
    "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, position) "
    "VALUES (CAST(:o AS uuid), CAST(:pid AS uuid), CAST(:sid AS uuid), :pos)"
)


@_DB_GATE
async def test_an_add_that_another_write_beat_logs_no_row(ns, app_engine) -> None:  # noqa: F811
    """P3b (fix round 1). Another transaction puts the stage in the pipeline
    after the check of the route and before its INSERT. The INSERT does
    nothing, so the route logs no row. Mutation: log with no ``RETURNING``
    check → one row for a change that the route did not make → red."""
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    pipeline = _pipeline(ns, ns.org_a)
    with ns.admin_engine.connect() as other:
        tx = other.begin()
        other.execute(text(_PLACE_INSERT),
                      {"o": ns.org_a, "pid": pipeline, "sid": ids["next"], "pos": 5})
        call = asyncio.create_task(_call(
            ns, _admin(ns.org_a), "POST", f"/crm/pipelines/{pipeline}/stages",
            {"status_id": ids["next"]}))
        await _until_it_waits(ns, call)
        tx.commit()
    r = await call
    assert r.status_code == 201, r.text
    assert _rows(ns, ns.org_a) == []
    assert _one(ns, "SELECT position FROM crm_pipeline_stages "
                    "WHERE pipeline_id = CAST(:pid AS uuid) AND status_id = CAST(:sid AS uuid)",
                pid=pipeline, sid=ids["next"]) == 5


@_DB_GATE
async def test_a_removal_that_another_write_beat_logs_no_row(ns, app_engine) -> None:  # noqa: F811
    """P3b (fix round 1). Another transaction takes the stage out after the
    checks of the route and before its DELETE. The DELETE takes nothing, so
    the route logs no row. Mutation: read the place before the DELETE and
    log from it → one row for a change that the route did not make → red."""
    _settings(ns, ns.org_a, "native")
    ids = _seed(ns, ns.org_a)
    pipeline = _pipeline(ns, ns.org_a)
    _one(ns, _PLACE_INSERT + " RETURNING position",
         o=ns.org_a, pid=pipeline, sid=ids["next"], pos=5)
    with ns.admin_engine.connect() as other:
        tx = other.begin()
        other.execute(text(
            "DELETE FROM crm_pipeline_stages WHERE pipeline_id = CAST(:pid AS uuid) "
            "AND status_id = CAST(:sid AS uuid)"), {"pid": pipeline, "sid": ids["next"]})
        call = asyncio.create_task(_call(
            ns, _admin(ns.org_a), "DELETE",
            f"/crm/pipelines/{pipeline}/stages/{ids['next']}"))
        await _until_it_waits(ns, call)
        tx.commit()
    r = await call
    assert r.status_code == 200, r.text
    assert _rows(ns, ns.org_a) == []


@_DB_GATE
async def test_a_member_who_lost_admin_cannot_undo_their_own_admin_change(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2b (fix round 1). A member renamed a stage as an admin, then lost
    admin. They still read their own row, and its Undo is a 403
    ``admin_only``: an admin area needs an admin. Nothing changes.
    Mutation: drop the guard in ``change_log.undo_change`` → 200 → red."""
    _settings(ns, ns.org_a, "native", access="everyone")
    ids = _seed(ns, ns.org_a)
    as_admin = _admin(ns.org_a, MEMBER)
    r = await _call(ns, as_admin, "PATCH", f"/crm/statuses/deal/{ids['open']}", {"name": "Q"})
    assert r.status_code == 200, r.text
    demoted = _member(ns.org_a)
    [row] = (await _history(ns, demoted))["rows"]
    assert row["area"] == "deal_stage" and row["can_undo"] is False
    refused = await _call(ns, demoted, "POST", f"/crm/change-history/{row['id']}/undo")
    assert _undo_code(refused) == (403, "admin_only"), refused.text
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=ids["open"]) == "Q"
    rows = _rows(ns, ns.org_a)
    assert len(rows) == 1 and rows[0].undone_by is None


@_DB_GATE
async def test_a_private_view_shows_to_its_owner_only_and_a_member_reads_their_own(
    ns, app_engine,  # noqa: F811
) -> None:
    _settings(ns, ns.org_a, "native", access="everyone")
    ids = _seed(ns, ns.org_a)
    owner, other, member = _admin(ns.org_a), _admin(ns.org_a, OTHER_ADMIN), _member(ns.org_a)
    view = (await _call(ns, member, "POST", "/crm/views",
                        {"entity": "deals", "name": "Mine"})).json()["id"]
    await _call(ns, owner, "PATCH", f"/crm/statuses/deal/{ids['open']}", {"name": "Q"})
    # The admin reads the stage change, and not the member's private view.
    assert [r["area"] for r in (await _history(ns, other))["rows"]] == ["deal_stage"]
    # The member reads their own view row only, never the admin's stage change.
    mine = (await _history(ns, member))["rows"]
    assert [(r["area"], r["can_undo"]) for r in mine] == [("view", True)]
    # The member undoes their own view, and cannot undo an admin area.
    assert (await _call(ns, member, "POST",
                        f"/crm/change-history/{mine[0]['id']}/undo")).status_code == 200
    assert _one(ns, "SELECT count(*) FROM crm_views WHERE id = CAST(:i AS uuid)", i=view) == 0
    stage_row = next(r for r in _rows(ns, ns.org_a) if r.area == "deal_stage")
    hidden = await _call(ns, member, "POST", f"/crm/change-history/{stage_row.id}/undo")
    assert hidden.status_code == 404, hidden.text


@_DB_GATE
async def test_a_view_delete_comes_back_with_its_id_and_owner(ns, app_engine) -> None:  # noqa: F811
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    view = (await _call(ns, admin, "POST", "/crm/views",
                        {"entity": "deals", "name": "Hot", "layout": "board"})).json()["id"]
    assert (await _call(ns, admin, "DELETE", f"/crm/views/{view}")).status_code == 200
    gone = (await _history(ns, admin))["rows"][0]
    assert gone["action"] == "delete" and gone["before"]["name"] == "Hot"
    back = await _call(ns, admin, "POST", f"/crm/change-history/{gone['id']}/undo")
    assert back.status_code == 200, back.text
    row = (await _call(ns, admin, "GET", "/crm/views?entity=deals")).json()
    assert [(v["id"], v["name"], v["layout"], v["owner_email"]) for v in row] == [
        (view, "Hot", "board", ADMIN)]


@_DB_GATE
async def test_a_rule_logs_its_scope_and_its_channel(ns, app_engine) -> None:  # noqa: F811
    """A business rule: an org rule at the team level, a member's own rule at
    the own level and private, and the confirm of the chat ``via`` chat."""
    from acb_common.db import bind_tenant, release_tenant, tenant_session
    from gateway.routes.crm import rules

    _settings(ns, ns.org_a, "mirror")
    admin = _admin(ns.org_a)
    token = bind_tenant(ns.org_a)
    try:
        async with (
            tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)),
            tenant_session(ns.org_a) as db,
        ):
            await rules.add_rule(db, ns.org_a, admin, "All amounts net of GST", "org",
                                 rules.ORIGIN_SETTINGS)
            await rules.add_rule(db, ns.org_a, admin, "Round to lakhs", "me",
                                 rules.ORIGIN_CHAT)
    finally:
        release_tenant(token)
    rows = {r.summary: r for r in _rows(ns, ns.org_a)}
    org_rule = rows['Added the business rule "All amounts net of GST"']
    assert (org_rule.ladder_level, org_rule.via, org_rule.private_to) == ("team", "ui", None)
    own = rows['Added the business rule "Round to lakhs"']
    assert (own.ladder_level, own.via, own.private_to) == ("own", "chat", ADMIN)


@_DB_GATE
def test_a_row_never_changes_and_one_row_is_undone_once(ns, app_engine) -> None:  # noqa: F811
    """The guard trigger and the one Undo index hold for each role."""
    from sqlalchemy.exc import DBAPIError

    def insert(c: Any, **extra: Any) -> str:
        cols = {"organization_id": ns.org_a, "actor_email": ADMIN, "via": "ui",
                "ladder_level": "team", "area": "deal_stage", "action": "change",
                "summary": "s", "before": "{}", "after": "{}", "reversible": True, **extra}
        names = ", ".join(cols)
        values = ", ".join(f"CAST(:{k} AS jsonb)" if k in ("before", "after") else f":{k}"
                           for k in cols)
        return str(c.execute(text(
            f"INSERT INTO crm_change_log ({names}) VALUES ({values}) RETURNING id"),
            cols).scalar())

    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        first = insert(c)
    for sql in ("UPDATE crm_change_log SET summary = 'x'",
                "UPDATE crm_change_log SET after = '{\"name\": \"x\"}'::jsonb"):
        with pytest.raises(DBAPIError), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
            c.execute(text(sql))
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        insert(c, undoes_id=first, reversible=False)
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        insert(c, undoes_id=first, reversible=False)
    # An Undo row cannot name a row of another org.
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_b})
        insert(c, organization_id=ns.org_b, undoes_id=first, reversible=False)
    # A row of org B cannot be written by a session of org A (WITH CHECK).
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        insert(c, organization_id=ns.org_b)
    assert uuid.UUID(first)


#: Each column of the guard of migration 341 → an SQL value that differs.
_OTHER_VALUE: dict[str, str] = {
    "id": "gen_random_uuid()",
    "organization_id": "CAST(:org_b AS uuid)",
    "at": "at - interval '1 day'",
    "actor_email": "'other@crm-m1.test'",
    "via": "'chat'",
    "approved_by": "'other@crm-m1.test'",
    "ladder_level": "'own'",
    "area": "'view'",
    "action": "'delete'",
    "entity_id": "'other'",
    "summary": "'other'",
    "before": "'{\"a\": 1}'::jsonb",
    "after": "'{\"a\": 2}'::jsonb",
    "mode": "'mirror'",
    "reversible": "NOT reversible",
    "private_to": "'other@crm-m1.test'",
    "undoes_id": "gen_random_uuid()",
}


@_DB_GATE
def test_the_guard_refuses_a_column_change_that_sets_undone_by_too(
    ns, app_engine,  # noqa: F811
) -> None:
    """P3d (fix round 1). Each UPDATE changes one other column AND sets
    ``undone_by`` on a row whose ``undone_by`` is NULL. The ``undone_by``
    block of the trigger lets that pass, so only the column block can
    refuse it. It runs as the table owner, which RLS does not bind.
    Mutation: drop one column from the guard of migration 341 → red."""
    from sqlalchemy.exc import DBAPIError

    with ns.admin_engine.begin() as c:
        row = str(c.execute(text(
            "INSERT INTO crm_change_log (organization_id, actor_email, via, approved_by, "
            "ladder_level, area, action, entity_id, summary, before, after, reversible) "
            "VALUES (CAST(:o AS uuid), :a, 'ui', :a, 'team', 'deal_stage', 'change', 'e1', "
            "'s', '{}'::jsonb, '{}'::jsonb, true) RETURNING id"),
            {"o": ns.org_a, "a": ADMIN}).scalar())
    columns = set(_OTHER_VALUE) | {"undone_by"}
    with ns.admin_engine.begin() as c:
        names = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'crm_change_log'"))}
    assert names == columns, "a new column needs a line in the guard and here"
    passed = []
    for column, value in _OTHER_VALUE.items():
        try:
            with ns.admin_engine.begin() as c:
                c.execute(text(
                    f"UPDATE crm_change_log SET {column} = {value}, "
                    "undone_by = gen_random_uuid() WHERE id = CAST(:i AS uuid)"),
                    {"i": row, "org_b": ns.org_b})
            passed.append(column)
        except DBAPIError as exc:
            if "a change row never changes" not in str(exc):
                passed.append(f"{column} (refused by another rule: {exc.orig})")
    assert not passed, f"the guard let these columns change: {passed}"
    # The control: `undone_by` alone is set once, and never again.
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_change_log SET undone_by = gen_random_uuid() "
                       "WHERE id = CAST(:i AS uuid)"), {"i": row})
    with pytest.raises(DBAPIError, match="undone_by is set once"), \
            ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_change_log SET undone_by = gen_random_uuid() "
                       "WHERE id = CAST(:i AS uuid)"), {"i": row})


@_DB_GATE
async def test_the_undo_of_a_pipeline_rename_refuses_a_newer_rename(
    ns, app_engine,  # noqa: F811
) -> None:
    """P3d (fix round 1). Rename a pipeline twice, then undo the first
    rename: 409 ``changed_since``, and the newer name stays. Mutation: drop
    the check of ``pipelines._undo_rename`` → the old name comes back → red."""
    _settings(ns, ns.org_a, "native")
    pipeline = _pipeline(ns, ns.org_a, "Sales")
    admin = _admin(ns.org_a)
    for name in ("Sales B", "Sales C"):
        r = await _call(ns, admin, "PATCH", f"/crm/pipelines/{pipeline}", {"name": name})
        assert r.status_code == 200, r.text
    first, second = _rows(ns, ns.org_a)
    assert (first.after, second.after) == ({"name": "Sales B"}, {"name": "Sales C"})
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{first.id}/undo")
    assert _undo_code(refused) == (409, "changed_since"), refused.text
    assert _one(ns, "SELECT name FROM crm_pipelines WHERE id = CAST(:i AS uuid)",
                i=pipeline) == "Sales C"
    assert len(_rows(ns, ns.org_a)) == 2
    back = await _call(ns, admin, "POST", f"/crm/change-history/{second.id}/undo")
    assert back.status_code == 200, back.text
    assert _one(ns, "SELECT name FROM crm_pipelines WHERE id = CAST(:i AS uuid)",
                i=pipeline) == "Sales B"


# ── Fix round 2: an Undo row has the readers of the row it undoes ───────────

PURGE_OWNER = "owner@crm-m1.test"


def _member_rows(p: Any, org: str) -> None:
    """``ADMIN`` and an owner in the directory of *org*, so the purge of
    ``ADMIN`` keeps an owner (invariant 1)."""
    with p.admin_engine.begin() as c:
        for email, role in ((ADMIN, "employee"), (PURGE_OWNER, "executive")):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "SELECT :e, :e, :r, 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                "(SELECT 1 FROM app_user WHERE lower(email) = :e)"),
                {"e": email, "r": role, "o": org})
        role_id = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": org}).scalar()
        if role_id is None:
            role_id = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"),
            {"r": role_id, "e": PURGE_OWNER, "o": org})


def _drop_member_rows(p: Any) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM user_role WHERE user_id IN "
            "(SELECT id FROM app_user WHERE email = ANY(:e))"), {"e": [ADMIN, PURGE_OWNER]})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                  {"e": [ADMIN, PURGE_OWNER]})


async def _purge_admin(p: Any, org: str, monkeypatch: Any) -> dict[str, int]:
    """The real member purge of ``ADMIN`` in *org*, as the app role. Returns
    ``deleted`` of the purge (``PurgeResult.deleted``)."""
    from gateway.db import tenant_session
    from gateway.routes.admin import members
    from gateway.routes.admin._common import get_member
    from gateway.routes.crm import questions

    async def _no_keys(_org: str, _email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)
    async with (
        tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
        tenant_session(org) as session,
    ):
        member = await get_member(session, org, ADMIN)
        deleted, _kept, _plans = await members.purge_member_rows(
            session, org, member, actor_email=PURGE_OWNER)
    return deleted


def _log_state(p: Any, org: str) -> dict[str, Any]:
    return {str(r.id): r for r in _rows(p, org)}


def _assert_the_purge_is_whole(
    before: dict[str, Any], after: dict[str, Any], counted: int,
) -> None:
    """The count equals the rows that went, no org row went, and no
    ``undone_by`` points at a row that is gone."""
    gone = set(before) - set(after)
    assert counted == len(gone), (counted, len(gone))
    assert all(before[i].private_to is not None for i in gone), "an org row went"
    dangling = [i for i, r in after.items()
                if r.undone_by is not None and str(r.undone_by) not in after]
    assert not dangling, f"undone_by points at a row that is gone: {dangling}"


@_DB_GATE
async def test_the_undo_of_a_private_rename_stays_private_and_the_purge_takes_both(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """P2-1 (fix round 2). An admin saves a private view, renames it,
    shares it, and undoes the rename. The view is shared now, but the Undo
    row is private, as the rename row is. The purge of the admin counts 4
    and deletes 4, and the self cascade takes no org row. Mutation: give
    the Undo row the readers of the view now, so it is an org row. The purge
    then counts 3 and deletes 4 → red."""
    _settings(ns, ns.org_a, "native")
    _member_rows(ns, ns.org_a)
    try:
        admin = _admin(ns.org_a)
        view = (await _call(ns, admin, "POST", "/crm/views",
                            {"entity": "deals", "name": "Hot"})).json()["id"]
        assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                            {"name": "Hot deals"})).status_code == 200
        assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                            {"shared": True})).status_code == 200
        rename = next(r for r in _rows(ns, ns.org_a) if r.summary.startswith("Renamed"))
        undo = await _call(ns, admin, "POST", f"/crm/change-history/{rename.id}/undo")
        assert undo.status_code == 200, undo.text
        before = _log_state(ns, ns.org_a)
        undo_row = before[undo.json()["change"]["id"]]
        assert undo_row.private_to == rename.private_to == ADMIN
        deleted = await _purge_admin(ns, ns.org_a, monkeypatch)
        after = _log_state(ns, ns.org_a)
        assert deleted["crm_private_changes"] == 4 and after == {}
        _assert_the_purge_is_whole(before, after, deleted["crm_private_changes"])
    finally:
        _drop_member_rows(ns)


@_DB_GATE
async def test_the_undo_of_a_shared_rename_stays_shared_and_the_purge_keeps_both(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """P3-1 (fix round 2), the reverse case. An admin saves a shared view,
    renames it, makes it private, and undoes the rename. The Undo row is an
    org row, as the rename row is. The purge of the admin deletes only the
    private row, and the kept rename row points at a kept Undo row.
    Mutation: give the Undo row the readers of the view now, so it is
    private. The purge then deletes it, and the rename's undone_by dangles
    → red."""
    _settings(ns, ns.org_a, "native")
    _member_rows(ns, ns.org_a)
    try:
        admin = _admin(ns.org_a)
        view = (await _call(ns, admin, "POST", "/crm/views",
                            {"entity": "deals", "name": "Team", "shared": True})).json()["id"]
        assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                            {"name": "Team deals"})).status_code == 200
        assert (await _call(ns, admin, "PATCH", f"/crm/views/{view}",
                            {"shared": False})).status_code == 200
        rename = next(r for r in _rows(ns, ns.org_a) if r.summary.startswith("Renamed"))
        assert rename.private_to is None
        undo = await _call(ns, admin, "POST", f"/crm/change-history/{rename.id}/undo")
        assert undo.status_code == 200, undo.text
        before = _log_state(ns, ns.org_a)
        assert before[undo.json()["change"]["id"]].private_to is None
        deleted = await _purge_admin(ns, ns.org_a, monkeypatch)
        after = _log_state(ns, ns.org_a)
        assert deleted["crm_private_changes"] == 1 and len(after) == 3
        _assert_the_purge_is_whole(before, after, deleted["crm_private_changes"])
    finally:
        _drop_member_rows(ns)


@_DB_GATE
async def test_log_change_gives_an_undo_row_the_readers_of_the_undone_row(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2-1 (fix round 2), on the one writer. The caller passes the opposite
    readers each time, and ``log_change`` ignores them: a private row gets
    a private Undo, and an org row gets an org Undo. Mutation: write the
    ``private_to`` of the caller → red both ways."""
    from acb_common.db import bind_tenant, release_tenant, tenant_session
    from gateway.routes.crm import change_log as cl

    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)

    def change(private_to: str | None) -> Any:
        return cl.Change(area="view", action=cl.CHANGE, summary="s", ladder=cl.OWN,
                         entity_id="e1", before={"n": 1}, after={"n": 2},
                         private_to=private_to)

    token = bind_tenant(ns.org_a)
    try:
        async with (
            tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)),
            tenant_session(ns.org_a) as db,
        ):
            private_row = await cl.log_change(db, admin, change(ADMIN))
            org_row = await cl.log_change(db, admin, change(None))
            private_undo = await cl.log_change(db, admin, change(None), undoes=private_row)
            org_undo = await cl.log_change(db, admin, change(ADMIN), undoes=org_row)
    finally:
        release_tenant(token)
    rows = _log_state(ns, ns.org_a)
    assert rows[private_row].private_to == rows[private_undo].private_to == ADMIN
    assert rows[org_row].private_to is None and rows[org_undo].private_to is None


# ── Fix round 2: the old name of an Undo is taken again ─────────────────────


@_DB_GATE
async def test_the_undo_of_a_stage_rename_to_a_taken_name_is_a_409(ns, app_engine) -> None:  # noqa: F811
    """P2-2 (fix round 2). Rename a stage, make a new stage with the old
    name, then undo the rename: 409 ``name_taken`` and not a 500. Nothing
    changes. Mutation: drop ``_name_free`` → the unique index raises → red."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    stage = (await _call(ns, admin, "POST", "/crm/statuses/deal",
                         {"name": "Demo", "type": "open"})).json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/statuses/deal/{stage}",
                        {"name": "Demo 2"})).status_code == 200
    assert (await _call(ns, admin, "POST", "/crm/statuses/deal",
                        {"name": "Demo", "type": "open"})).status_code == 201
    rename = next(r for r in _rows(ns, ns.org_a) if r.action == "change")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{rename.id}/undo")
    assert _undo_code(refused) == (409, "name_taken"), refused.text
    assert '"Demo"' in refused.json()["detail"]["message"]
    assert _one(ns, "SELECT name FROM crm_deal_statuses WHERE id = CAST(:i AS uuid)",
                i=stage) == "Demo 2"
    assert len(_rows(ns, ns.org_a)) == 3
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


@_DB_GATE
async def test_the_undo_of_a_reason_relabel_to_a_taken_label_is_a_409(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2-2 (fix round 2), the lost reason. Mutation: drop ``_name_free``
    from ``_undo_lost_reason`` → the unique index raises → red."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    reason = (await _call(ns, admin, "POST", "/crm/lost-reasons",
                          {"label": "Budget"})).json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/lost-reasons/{reason}",
                        {"label": "Budget 2"})).status_code == 200
    assert (await _call(ns, admin, "POST", "/crm/lost-reasons",
                        {"label": "Budget"})).status_code == 201
    relabel = next(r for r in _rows(ns, ns.org_a) if r.action == "change")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{relabel.id}/undo")
    assert _undo_code(refused) == (409, "name_taken"), refused.text
    assert '"Budget"' in refused.json()["detail"]["message"]
    assert _one(ns, "SELECT label FROM crm_lost_reasons WHERE id = CAST(:i AS uuid)",
                i=reason) == "Budget 2"
    assert len(_rows(ns, ns.org_a)) == 3
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


@_DB_GATE
async def test_the_undo_of_a_lead_status_rename_to_a_taken_name_is_a_409(
    ns, app_engine,  # noqa: F811
) -> None:
    """P2-2, the third name index (``uq_crm_lead_statuses_org_name``).
    Mutation: drop that line from ``_NAME_INDEX`` → a KeyError → red."""
    _settings(ns, ns.org_a, "native")
    admin = _admin(ns.org_a)
    status = (await _call(ns, admin, "POST", "/crm/statuses/lead",
                          {"name": "Fresh"})).json()["id"]
    assert (await _call(ns, admin, "PATCH", f"/crm/statuses/lead/{status}",
                        {"name": "Fresh 2"})).status_code == 200
    assert (await _call(ns, admin, "POST", "/crm/statuses/lead",
                        {"name": "Fresh"})).status_code == 201
    rename = next(r for r in _rows(ns, ns.org_a) if r.action == "change")
    refused = await _call(ns, admin, "POST", f"/crm/change-history/{rename.id}/undo")
    assert _undo_code(refused) == (409, "name_taken"), refused.text
    assert '"Fresh"' in refused.json()["detail"]["message"]
    assert _one(ns, "SELECT name FROM crm_lead_statuses WHERE id = CAST(:i AS uuid)",
                i=status) == "Fresh 2"
    assert all(r.undone_by is None for r in _rows(ns, ns.org_a))


class _DriverError(Exception):
    """The shape of an asyncpg error: the SQLSTATE and the constraint name."""

    def __init__(self, sqlstate: str, constraint_name: str | None) -> None:
        super().__init__("driver error")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity(sqlstate: str, constraint_name: str | None) -> Any:
    """An IntegrityError as the asyncpg dialect raises it. The statement text
    names a name index on purpose, so a match on the text is caught."""
    from sqlalchemy.exc import IntegrityError

    adapted = Exception("adapted")
    adapted.__cause__ = _DriverError(sqlstate, constraint_name)
    return IntegrityError(
        "UPDATE crm_deal_statuses SET name = 'uq_crm_deal_statuses_org_name'",
        {}, adapted,
    )


@pytest.mark.parametrize(("sqlstate", "constraint"), [
    ("23505", "uq_crm_deal_statuses_other"),   # another unique index
    ("23503", "uq_crm_deal_statuses_org_name"),  # not a unique violation
    ("23505", None),                            # no constraint name at all
])
async def test_name_free_lets_any_other_integrity_error_through(
    sqlstate: str, constraint: str | None,
) -> None:
    """R7 for the re-raise branch of ``_name_free``. Only a unique violation of
    the table's own name index is ``name_taken``. Any other IntegrityError
    stays an error. Mutations: map every IntegrityError to ``name_taken``, or
    match the index name in the message text → red."""
    from gateway.routes.crm import admin as crm_admin
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with crm_admin._name_free("crm_deal_statuses", "deal stage", "Open"):
            raise _integrity(sqlstate, constraint)


async def test_name_free_turns_its_own_index_into_name_taken() -> None:
    """The positive case of the same seam, with no database."""
    from fastapi import HTTPException
    from gateway.routes.crm import admin as crm_admin

    with pytest.raises(HTTPException) as refused:
        async with crm_admin._name_free("crm_deal_statuses", "deal stage", "Open"):
            raise _integrity("23505", "uq_crm_deal_statuses_org_name")
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "name_taken"
