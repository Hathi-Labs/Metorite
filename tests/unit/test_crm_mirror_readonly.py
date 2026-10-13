"""R7 fence — Mirror mode makes a provider's records read-only (WS-53 CRM-Z6).

Spec: ``project-docs/specs/crm_platform.md`` §3.2, §3.3, §3.5 rule 2, §13 CRM-Z6.

Against a REAL Postgres, through the real ``/crm`` router, as the NON-privileged
role (no superuser, no BYPASSRLS):

- in Mirror mode, each write to a row with ``ext_source`` set is 409
  ``mirrored_record`` (edit, status move, delete), for all four entities;
- a create of a lead, deal, contact or company is 409 ``mirror_mode_create``;
- a Metorite-only activity (``ext_source`` NULL) saves, and so does an edit and a
  delete of it, even when it hangs on a mirrored record;
- a write to the stages or the lost reasons is 409 ``mirror_mode_settings``;
- in Native mode, and with no mode, the same writes work as before;
- a static check: each write route in ``routes/crm/`` reaches the guard.

The static checks run with no database. The rest need ``_DB_GATE``.
"""

from __future__ import annotations

import ast
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

_CRM = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/routes/crm"

ENTITIES = {
    "leads": "crm_leads", "deals": "crm_deals",
    "contacts": "crm_contacts", "organizations": "crm_companies",
}
_CREATE = {
    "leads": {"first_name": "Ravi", "last_name": "Rao"},
    "deals": {"name": "New deal"},
    "contacts": {"first_name": "Priya"},
    "organizations": {"name": "Acme"},
}
_EDIT = {
    "leads": {"first_name": "X"}, "deals": {"name": "X"},
    "contacts": {"first_name": "X"}, "organizations": {"name": "X"},
}
_TABLES = (
    "crm_status_changes", "crm_activities", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_deal_statuses", "crm_lead_statuses",
    "crm_lost_reasons", "crm_settings",
    # CRM-Q6a: the generic record store. No native writer exists.
    "crm_module_records",
)


# ── The static fence (no database) ──────────────────────────────────────────

#: A route reaches the guard when it, or a same-module helper it calls, makes
#: one of these calls. ``insert_row`` and ``update_row`` call the guard inside.
_GUARD_NAMES = {"assert_writable", "insert_row", "update_row"}

#: Write routes that may skip the guard, each with the reason.
#: ⚠️ The legacy Zoho sync must be OFF in Mirror mode, and CRM-Z5 replaces it.
#: The legacy import and the stage repair refuse Mirror mode up front.
_EXEMPT = {
    # The legacy Zoho importer and sync trigger. The sync engine (CRM-Z5)
    # replaces them, and `upsert_from_source` is the mirror's own bypass.
    "import_zoho.py", "sync_zoho.py",
    # Writes the organization's own mode. It is not a CRM record write.
    "settings.py",
    # CRM-Z2: writes the connection registry and sets the mode to Mirror. It
    # is not a CRM record write.
    "connection.py",
    # CRM-Z5: starts a sync run. Its records go through upsert_from_source,
    # the one bypass, and never through a route.
    "sync.py",
    # CRM-Q8: saved views. A view is a member's saved list, not a CRM record, so
    # it works in Mirror mode. It writes `crm_views` and no record table.
    "views.py",
    # CRM-Q7a: the Field map. It writes `crm_field_rules` and no record table,
    # and it works in Mirror mode only. The sync applies a rule through the
    # field-rule door, which the raw-write fence below allows by name.
    "field_rules.py",
}


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


def _write_routes() -> list[tuple[str, str, set[str]]]:
    found = []
    for path in sorted(_CRM.glob("*.py")):
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
                ):
                    found.append((path.name, fn.name, _reach(fn, funcs)))
    return found


def test_every_write_route_reaches_the_guard() -> None:
    routes = _write_routes()
    assert len(routes) >= 20, "the scan found too few write routes"
    bare = [
        f"{mod}::{fn}" for mod, fn, calls in routes
        if mod not in _EXEMPT and not (calls & _GUARD_NAMES)
    ]
    assert not bare, (
        "write routes that skip the mirror guard (call assert_writable, "
        f"insert_row or update_row): {bare}"
    )


def test_core_insert_and_update_call_the_guard() -> None:
    tree = ast.parse((_CRM / "core.py").read_text(encoding="utf-8"))
    for name in ("insert_row", "update_row"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        assert "assert_writable" in _calls(fn), name


def test_raw_deletes_in_routes_call_the_guard() -> None:
    """A DELETE is raw SQL and never meets ``update_row``. Its function must guard."""
    missing = []
    for path in sorted(_CRM.glob("*.py")):
        if path.name in _EXEMPT | {"core.py", "sync_zoho.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            src = ast.get_source_segment(path.read_text(encoding="utf-8"), fn) or ""
            if "DELETE FROM" in src and "assert_writable" not in _calls(fn):
                missing.append(f"{path.name}::{fn.name}")
    # The deal-contact unlink guards its deal; the cleanup helpers are not routes.
    assert not missing, f"raw DELETE without assert_writable: {missing}"


def test_the_mirror_upsert_does_not_call_the_guard() -> None:
    tree = ast.parse((_CRM / "core.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "upsert_from_source")
    assert "assert_writable" not in _calls(fn)


# ── The database tests ──────────────────────────────────────────────────────

def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-z6.test", org, "feature:crm", "admin:access:manage")


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
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
        dsn = p.app_url.render_as_string(hide_password=False)
        async with (
            tenant_engine_scope(dsn),
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


def _set_mode(p: Any, mode: str | None) -> None:
    if mode is None:
        return
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode) "
            "VALUES (CAST(:o AS uuid), :m) ON CONFLICT (organization_id) "
            "DO UPDATE SET mode = EXCLUDED.mode"), {"o": p.org_a, "m": mode})


def _seed(p: Any, *, mirrored: bool) -> dict[str, str]:
    """Stages, then one row per entity. Returns the ids by slug."""
    src = "zoho" if mirrored else None
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        def one(sql: str, **kw: Any) -> str:
            return str(c.execute(text(sql), {"o": p.org_a, **kw}).scalar())

        def ext(tag: str) -> dict[str, Any]:
            return {"s": src, "x": f"{tag}-{uuid.uuid4().hex[:6]}" if src else None}

        for key, table, name, kind, pos in (
            ("deal_status", "crm_deal_statuses", "Open", "open", 0),
            ("deal_status2", "crm_deal_statuses", "Next", "ongoing", 1),
            ("lead_status", "crm_lead_statuses", "New", "open", 0),
            ("lead_status2", "crm_lead_statuses", "Working", "ongoing", 1),
        ):
            ids[key] = one(
                f"INSERT INTO {table} (organization_id, name, type, position) "
                "VALUES (CAST(:o AS uuid), :n, :t, :p) RETURNING id",
                n=name, t=kind, p=pos)
        ids["lost"] = one(
            "INSERT INTO crm_lost_reasons (organization_id, label, position) "
            "VALUES (CAST(:o AS uuid), 'Price', 0) RETURNING id")
        ids["organizations"] = one(
            "INSERT INTO crm_companies (organization_id, name, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), 'Acme', :s, :x) RETURNING id", **ext("c"))
        ids["contacts"] = one(
            "INSERT INTO crm_contacts (organization_id, first_name, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), 'Priya', :s, :x) RETURNING id", **ext("p"))
        ids["leads"] = one(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id, "
            "ext_source, ext_id) VALUES (CAST(:o AS uuid), 'Ravi', "
            "CAST(:st AS uuid), :s, :x) RETURNING id",
            st=ids["lead_status"], **ext("l"))
        ids["deals"] = one(
            "INSERT INTO crm_deals (organization_id, name, status_id, "
            "ext_source, ext_id) VALUES (CAST(:o AS uuid), 'Big', "
            "CAST(:st AS uuid), :s, :x) RETURNING id",
            st=ids["deal_status"], **ext("d"))
    return ids


async def _run(ns: Any, calls: list[tuple[str, str, dict | None]]) -> list[Any]:
    return [await _call(ns, _admin(ns.org_a), m, p, b) for m, p, b in calls]


@_DB_GATE
@pytest.mark.parametrize("slug", list(ENTITIES))
async def test_mirror_refuses_edit_move_and_delete_of_a_mirrored_row(
    ns, app_engine, slug,  # noqa: F811
) -> None:
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    rid = ids[slug]
    calls: list[tuple[str, str, dict | None]] = [
        ("PATCH", f"/crm/{slug}/{rid}", _EDIT[slug]),
        ("DELETE", f"/crm/{slug}/{rid}", None),
    ]
    if slug in ("leads", "deals"):
        move = ids["lead_status2"] if slug == "leads" else ids["deal_status2"]
        calls.append(("PATCH", f"/crm/{slug}/{rid}", {"status_id": move}))
    if slug == "leads":
        calls.append(("POST", f"/crm/leads/{rid}/convert", {}))
    if slug == "deals":
        calls.append(("POST", f"/crm/deals/{rid}/contacts",
                      {"contact_id": ids["contacts"]}))
    for r in await _run(ns, calls):
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "mirrored_record"
        assert detail["source"] == "zoho"
        assert "Zoho" in detail["message"]
    with ns.admin_engine.connect() as c:  # nothing moved
        n = c.execute(text(
            f"SELECT count(*) FROM {ENTITIES[slug]} WHERE id = CAST(:i AS uuid)"),
            {"i": rid}).scalar()
        moves = c.execute(text("SELECT count(*) FROM crm_status_changes")).scalar()
    assert n == 1 and moves == 0


@_DB_GATE
@pytest.mark.parametrize("slug", list(ENTITIES))
async def test_mirror_refuses_a_create(ns, app_engine, slug) -> None:  # noqa: F811
    _set_mode(ns, "mirror")
    _seed(ns, mirrored=True)
    r = await _call(ns, _admin(ns.org_a), "POST", f"/crm/{slug}", _CREATE[slug])
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "mirror_mode_create"


@_DB_GATE
async def test_a_metorite_only_note_saves_on_a_mirrored_record(ns, app_engine) -> None:  # noqa: F811
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    admin = _admin(ns.org_a)
    for slug in ENTITIES:
        r = await _call(ns, admin, "POST", f"/crm/{slug}/{ids[slug]}/activities",
                        {"type": "note", "subject": "n", "body": "mine"})
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        edit = await _call(ns, admin, "PATCH", f"/crm/activities/{aid}", {"body": "b"})
        assert edit.status_code == 200, edit.text
        gone = await _call(ns, admin, "DELETE", f"/crm/activities/{aid}")
        assert gone.status_code == 200, gone.text


@_DB_GATE
async def test_a_mirrored_activity_is_read_only(ns, app_engine) -> None:  # noqa: F811
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    with ns.admin_engine.begin() as c:
        aid = str(c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, deal_id, created_by, "
            "ext_source, ext_id) VALUES (CAST(:o AS uuid), 'note', 's', "
            "CAST(:d AS uuid), 'sync', 'zoho', 'a-1') RETURNING id"),
            {"o": ns.org_a, "d": ids["deals"]}).scalar())
    admin = _admin(ns.org_a)
    for method, body in (("PATCH", {"body": "x"}), ("DELETE", None)):
        r = await _call(ns, admin, method, f"/crm/activities/{aid}", body)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "mirrored_record"


@_DB_GATE
async def test_mirror_refuses_pipeline_settings(ns, app_engine) -> None:  # noqa: F811
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    calls: list[tuple[str, str, dict | None]] = [
        ("POST", "/crm/statuses/deal", {"name": "Z", "type": "open"}),
        ("PATCH", f"/crm/statuses/deal/{ids['deal_status']}", {"name": "Z"}),
        ("DELETE", f"/crm/statuses/deal/{ids['deal_status2']}", None),
        ("POST", "/crm/lost-reasons", {"label": "Z"}),
        ("PATCH", f"/crm/lost-reasons/{ids['lost']}", {"label": "Z"}),
        ("DELETE", f"/crm/lost-reasons/{ids['lost']}", None),
    ]
    for r in await _run(ns, calls):
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "mirror_mode_settings"


@_DB_GATE
@pytest.mark.parametrize("mode", ["native", None])
async def test_native_mode_and_no_mode_are_unchanged(ns, app_engine, mode) -> None:  # noqa: F811
    _set_mode(ns, mode)
    ids = _seed(ns, mirrored=False)
    admin = _admin(ns.org_a)
    for slug in ENTITIES:
        r = await _call(ns, admin, "PATCH", f"/crm/{slug}/{ids[slug]}", _EDIT[slug])
        assert r.status_code == 200, r.text
        assert r.json()["ext_source"] is None and r.json()["custom"] == {}
    made = await _call(ns, admin, "POST", "/crm/contacts", _CREATE["contacts"])
    assert made.status_code == 201, made.text
    assert (await _call(ns, admin, "POST", "/crm/lost-reasons",
                        {"label": "Z"})).status_code == 201
    # A row that looks mirrored is editable outside Mirror mode.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_contacts SET ext_source='zoho', ext_id='k' "
            "WHERE id = CAST(:i AS uuid)"), {"i": ids["contacts"]})
    r = await _call(ns, admin, "PATCH", f"/crm/contacts/{ids['contacts']}",
                    {"first_name": "Y"})
    assert r.status_code == 200, r.text
    assert (await _call(ns, admin, "DELETE",
                        f"/crm/contacts/{ids['contacts']}")).status_code == 200


@_DB_GATE
async def test_the_wire_carries_the_mirror_fields(ns, app_engine) -> None:  # noqa: F811
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET ext_modified_at = now(), "
            "custom = CAST(:j AS jsonb) WHERE id = CAST(:i AS uuid)"),
            {"i": ids["deals"], "j": '{"k": 1}'})
    admin = _admin(ns.org_a)
    for slug in ENTITIES:
        one = await _call(ns, admin, "GET", f"/crm/{slug}/{ids[slug]}")
        assert one.status_code == 200, one.text
        assert one.json()["ext_source"] == "zoho"
        listed = (await _call(ns, admin, "GET", f"/crm/{slug}")).json()
        assert listed["rows"][0]["ext_source"] == "zoho"
    deal = (await _call(ns, admin, "GET", f"/crm/deals/{ids['deals']}")).json()
    assert deal["custom"] == {"k": 1}
    assert deal["ext_modified_at"] and deal["ext_id"]


# ── Raw SQL writes (review P2-a) ────────────────────────────────────────────

_RAW_WRITE = re.compile(
    r"(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+(\{[a-z_.]+\}|crm_(leads|deals|contacts|"
    r"companies|activities|deal_statuses|lead_statuses|lost_reasons|pipelines|"
    r"pipeline_stages|module_records))",
    re.IGNORECASE,
)
#: Functions that write raw SQL to a guarded table and are not guarded. Each has
#: a reason. A bookkeeping write on a row that a guarded function already loaded
#: is fine. A new entry needs a reason a reviewer accepts.
_RAW_ALLOW = {
    # Bump `last_activity_at` after a guarded activity write.
    "core.py::bump_last_activity",
    # The mirror's own bypass and the legacy upsert helpers (not routes).
    "core.py::upsert_by_zoho_id", "core.py::upsert_from_source",
    # CRM-Z5: the delete half of the same bypass. Only `crm_sync/` calls it,
    # and `test_crm_upsert_from_source_fence.py` keeps routes, agents and
    # skills from naming it.
    "core.py::delete_from_source",
    # CRM-Q2: the stages of a mirrored pipeline. The third door of the same
    # bypass, fenced by name in `test_crm_upsert_from_source_fence.py`.
    "core.py::set_pipeline_stages_from_source",
    # CRM-Q6a: the two doors of the generic record store. Only `crm_sync/`
    # calls them, and the same name fence keeps routes, agents and skills out.
    # `crm_module_records` has no native writer, so any other raw write to it
    # from a route fails here.
    "core.py::upsert_module_records_from_source",
    "core.py::delete_module_records_from_source",
    # CRM-C3: the merge of two stage rows after a Zoho rename. The seventh
    # door. Only files under `crm_sync/` may name it, by the same name fence.
    "core.py::merge_status_from_source",
    # CRM-Q7a: the field-rule door. It fills only a NULL column of a mirrored
    # row, or clears a free column, and only `crm_sync/` names it
    # (`test_crm_upsert_from_source_fence.py`).
    "core.py::apply_field_rule_from_source",
    "core.py::insert_row", "core.py::update_row",
    # The legacy importer, the sync and the stage repair refuse Mirror mode.
    "import_zoho.py", "sync_zoho.py",
    # The seed runs when the mode becomes Native only.
    "seed.py",
    # CRM-U5b: Change CRM setup. Deletes the org's stages and lost reasons
    # only after the route proved 0 records and no connected Zoho.
    "settings.py::_reset_setup",
    # CRM-X0: the start over. It removes mirrored rows on purpose, after the
    # admin check, the typed DELETE and the stop of the sync. Only
    # `settings.py` may call it (`test_crm_upsert_from_source_fence.py`).
    "core.py::reset_crm_from_admin",
}
_REFUSES = {"assert_writable", "refuse_in_mirror_mode"}


def _unguarded_raw_writes(source: str, name: str) -> list[str]:
    tree = ast.parse(source)
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        # Only a function's own strings: a nested def is checked on its own.
        body = ast.Module(body=fn.body, type_ignores=[])
        text_ = " ".join(
            ast.unparse(c) for c in ast.walk(body)
            if isinstance(c, ast.JoinedStr)
            or (isinstance(c, ast.Constant) and isinstance(c.value, str))
        )
        if _RAW_WRITE.search(text_) and not (_calls(fn) & _REFUSES):
            key = f"{name}::{fn.name}"
            if key not in _RAW_ALLOW and name not in _RAW_ALLOW:
                bad.append(key)
    return bad


def test_every_raw_write_to_a_guarded_table_is_guarded() -> None:
    bad = []
    for path in sorted(_CRM.glob("*.py")):
        bad += _unguarded_raw_writes(path.read_text(encoding="utf-8"), path.name)
    assert not bad, f"raw writes with no guard and no allowlist reason: {bad}"


def test_the_raw_write_fence_catches_a_planted_update() -> None:
    nl = chr(10)
    planted = (
        "async def sneaky(db):" + nl
        + "    await db.execute(text('UPDATE crm_deals SET name = 1'))" + nl
    )
    assert _unguarded_raw_writes(planted, "planted.py") == ["planted.py::sneaky"]
    ignored = (
        "async def sneaky(db):" + nl
        + "    await is_mirror_mode(db, None)" + nl
        + "    await db.execute(text('UPDATE crm_deals SET name = 1'))" + nl
    )
    assert _unguarded_raw_writes(ignored, "planted.py") == ["planted.py::sneaky"]
    guarded = planted.replace(
        "await db.execute", "await assert_writable(db)" + nl + "    await db.execute",
    )
    assert _unguarded_raw_writes(guarded, "planted.py") == []
    # CRM-Q6a: a route that writes the generic record store is caught too.
    module_write = (
        "async def sneaky(db):" + nl
        + "    await db.execute(text('DELETE FROM crm_module_records'))" + nl
    )
    assert _unguarded_raw_writes(module_write, "planted.py") == ["planted.py::sneaky"]


@_DB_GATE
async def test_a_deal_contact_unlink_on_a_mirrored_deal_is_refused(
    ns, app_engine,  # noqa: F811
) -> None:
    _set_mode(ns, "mirror")
    ids = _seed(ns, mirrored=True)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id, "
            "is_primary) VALUES (CAST(:o AS uuid), CAST(:d AS uuid), "
            "CAST(:c AS uuid), true)"),
            {"o": ns.org_a, "d": ids["deals"], "c": ids["contacts"]})
    r = await _call(ns, _admin(ns.org_a), "DELETE",
                    f"/crm/deals/{ids['deals']}/contacts/{ids['contacts']}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "mirrored_record"
    with ns.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM crm_deal_contacts")).scalar() == 1


@_DB_GATE
async def test_the_legacy_zoho_routes_refuse_mirror_mode(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.routes.crm import stage_metadata

    async def _meta(_client):
        return stage_metadata.Metadata(layout={"id": "1", "name": "L"}, pipelines=[{}])

    monkeypatch.setattr(stage_metadata, "fetch_metadata", _meta)
    monkeypatch.setattr(stage_metadata.import_zoho, "_client", lambda: object())
    monkeypatch.setattr(stage_metadata, "pipeline_name", lambda _p: "Standard")
    _set_mode(ns, "mirror")
    _seed(ns, mirrored=True)
    admin = _admin(ns.org_a)
    stages = await _call(ns, admin, "POST", "/crm/import/zoho/stages?apply=true")
    assert stages.status_code == 409, stages.text
    assert stages.json()["detail"]["code"] == "mirror_mode_settings"
    for body in ({}, {"dry_run": True}):
        r = await _call(ns, admin, "POST", "/crm/import/zoho", body)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "mirror_mode_legacy_import"


# ── CRM-C3: the overlay columns (crm_platform.md §20.6) ─────────────────────

_GATEWAY = _CRM.parents[1]

#: The overlay columns of each settings table, as §20.6 item 1 names them. The
#: admin door writes them, in Mirror mode too. The sync never writes them.
_OVERLAY = {
    "crm_deal_statuses": frozenset({
        "hidden", "ui_position", "ui_probability", "ui_color", "max_dwell_days",
    }),
    "crm_lead_statuses": frozenset({"hidden", "ui_position", "ui_color"}),
    "crm_lost_reasons": frozenset({"hidden"}),
}

#: Each overlay column, and `color`, which the sync never writes either
#: (§20.6 item 7).
_OVERLAY_WORDS = re.compile(
    r"\b(" + "|".join(sorted({c for cs in _OVERLAY.values() for c in cs} | {"color"}))
    + r")\b",
)


def test_the_overlay_columns_are_exactly_these() -> None:
    """CRM-C3 done when 15: one map, named exactly."""
    from gateway.routes.crm import core

    assert {t: frozenset(cs) for t, cs in core.MIRROR_OVERLAY_COLUMNS.items()} == _OVERLAY


def _sql_strings(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` of each module-level ``str`` constant, and of each literal
    argument of a ``text()`` call, in *source*.

    A Python NAME is out of scope: ``crm_sync/engine.py`` uses ``hidden`` as a
    local name for an offset-paged module, and that is not SQL.
    """
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in tree.body:
        value = getattr(node, "value", None)
        if isinstance(node, ast.Assign | ast.AnnAssign) and value is not None:
            for sub in ast.walk(value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    found.append((sub.lineno, sub.value))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "text":
            continue
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    found.append((sub.lineno, sub.value))
    return found


def _overlay_sql(roots: tuple[Path, ...]) -> list[str]:
    """``path:line: word`` for each SQL string under *roots* that names an
    overlay column or ``color``."""
    bad: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for line, value in _sql_strings(path.read_text(encoding="utf-8-sig")):
                for match in _OVERLAY_WORDS.finditer(value):
                    bad.append(f"{path.name}:{line}: {match.group(1)}")
    return bad


def test_no_sql_of_the_sync_names_an_overlay_column() -> None:
    """CRM-C3 done when 15: the sync and the connector never write the overlay."""
    roots = (_GATEWAY / "crm_sync", _GATEWAY / "crm_sources")
    assert all(r.is_dir() for r in roots)
    assert _overlay_sql(roots) == []


def test_the_scan_reads_the_routes_too() -> None:
    """The same scan finds the overlay in ``routes/crm/``, so it reads SQL."""
    found = _overlay_sql((_CRM,))
    assert any(f.startswith("core.py:") and "ui_position" in f for f in found), found
    assert any(f.startswith("admin.py:") and "ui_position" in f for f in found), found


def test_the_overlay_scan_proves_itself(tmp_path: Path) -> None:
    """CRM-C3 done when 16: a planted literal that names ``ui_position``."""
    nl = chr(10)
    (tmp_path / "planted.py").write_text(
        "_SQL = 'UPDATE crm_deal_statuses SET ui_position = :p'" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == ["planted.py:1: ui_position"]
    (tmp_path / "planted.py").write_text(
        "async def go(db):" + nl
        + "    await db.execute(text(f'UPDATE {t} SET ui_color = :c'))" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == ["planted.py:2: ui_color"]
    # A Python name is not SQL.
    (tmp_path / "planted.py").write_text(
        "def go():" + nl + "    hidden = True" + nl + "    return hidden" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == []
