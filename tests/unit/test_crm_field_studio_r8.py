"""R8 — the field studio of a Native CRM, against a real Postgres (WS-53
CRM-C4a, ``crm_platform.md`` §20.7).

Migration 260 adds ``crm_field_sections`` and seven columns on
``crm_field_defs``. The routes of ``routes/crm/field_studio.py`` let an admin
of a Native CRM create, shape, retire and restore a field. The record routes
write ``custom`` per key, with the type, "required" and the default of each
field.

Each test runs through the real ``/crm`` router, as the non-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations.
One test, or a group, for each "Done when" item of CRM-C4a:

1.  a field of each type: create, rename, order, section, hide, retire and
    restore, and the values in ``custom`` survive each step. Org B sees none;
2.  the type matrix: a refused change gives 422 and changes no row;
3.  "required" and the default on ``POST`` and ``PATCH /crm/deals``, and a
    write that names a retired key;
4.  a ``PATCH`` of one key keeps the others, for the four entities;
5.  a bad value gives 422 that names the label;
6.  a lookup stores ``{"id", "name"}``, and an id of org B gives 422;
7.  a multi-select stores an array, and the custom filter finds it;
8.  a new option takes a slug value, ``-2`` on a clash, and a rename keeps it;
9.  ``PUT /crm/field-layout/{entity}`` in one transaction, and the key words;
10. the readers leave out ``is_system`` rows, and a retired key is refused;
11. a CRM-Q7a rule on a Zoho ``double`` field survives the type change;
12. the sync leaves a ``source = 'metorite'`` row alone, and the start over
    and the change of setup delete it;
13. 403 for a member, 404 for an org off the flag, 409 in Mirror mode;
14. a sync leaves the seven new columns unchanged.

``tests/unit/test_crm_field_studio.py`` holds the rules that need no database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _age_profile,
    _imported,
    _leads,
    _post,
    _rule_status,
    _run,
    _seed,
    fr,
)
from tests.unit.test_crm_pipelines_r8 import _admin as _admin_of
from tests.unit.test_crm_pipelines_r8 import _member as _member_of
from tests.unit.test_crm_pipelines_r8 import _req
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _connect,
    _q,
    _the_loop_is_not_stopping,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

TABLES = {
    "leads": "crm_leads", "deals": "crm_deals",
    "contacts": "crm_contacts", "organizations": "crm_companies",
}

#: A body that creates one record of each entity.
CREATE = {
    "leads": {"first_name": "Ravi"},
    "deals": {"name": "Deal"},
    "contacts": {"first_name": "Priya"},
    "organizations": {"name": "Acme"},
}

#: Each type word of the studio: the extra body of its create, and a good value.
#: A lookup's value is the id of a company, made in the test.
TYPES: dict[str, tuple[dict[str, Any], Any]] = {
    "text": ({}, "Hello"),
    "textarea": ({}, "Line one\nLine two"),
    "number": ({}, 42),
    "decimal": ({}, 3.5),
    "percent": ({}, 12.5),
    "currency": ({}, 1000.5),
    "date": ({}, "2026-10-13"),
    "datetime": ({}, "2026-10-13T10:00:00+05:30"),
    "boolean": ({}, True),
    "email": ({}, "a@b.co"),
    "phone": ({}, "+91 98450 12345"),
    "url": ({}, "https://fracktal.in"),
    "select": ({"options": [{"label": "Gold"}, {"label": "Silver"}]}, "gold"),
    "multiselect": (
        {"options": [{"label": "Gold"}, {"label": "Silver"}]}, ["gold", "silver"]),
    "lookup": ({"lookup_target": "organizations"}, None),
}


def _import_again(p: Any, org: str) -> None:
    """Make the next run an import, so it writes the field definitions again.

    An incremental run writes no field definition. An import and a
    reconcile do (``engine._execute``).
    """
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET initial_done_at = NULL "
                       "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
                  {"o": org})


def _clean_studio(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            for table in ("crm_field_defs", "crm_field_sections"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


@pytest.fixture
def mirror(fr):
    """The CRM-Q7a fixture (two fake Zoho tenants), and the studio rows cleaned."""
    yield fr
    _clean_studio(fr[0])


@pytest.fixture
async def studio(ns, monkeypatch):
    """Two Native organizations with their starting pipeline, every member
    admitted, and the field studio on for both."""
    from acb_common import get_settings
    from acb_common.db import tenant_session
    from gateway.routes.crm.seed import seed_native_pipeline

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", "*", raising=False)
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            c.execute(text(
                "INSERT INTO crm_settings (organization_id, mode, access) VALUES "
                "(CAST(:o AS uuid), 'native', 'everyone') ON CONFLICT (organization_id) "
                "DO UPDATE SET mode = 'native', access = 'everyone'"), {"o": org})
    for org in (ns.org_a, ns.org_b):
        async with tenant_session(org) as db:
            await seed_native_pipeline(db, org)
    yield ns
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            for table in (
                "crm_activities", "crm_deal_contacts", "crm_status_changes",
                "crm_deals", "crm_leads", "crm_contacts", "crm_companies",
                "crm_pipeline_stages", "crm_pipelines", "crm_deal_statuses",
                "crm_lead_statuses", "crm_lost_reasons", "crm_field_defs",
                "crm_field_sections", "crm_views", "crm_settings",
            ):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _as_admin(org: str, method: str, path: str, body: Any = None) -> Any:
    return await _req(_admin_of(org), method, path, body)


async def _field(org: str, entity: str, body: dict[str, Any]) -> dict[str, Any]:
    made = await _as_admin(org, "POST", f"/crm/fields/{entity}", body)
    assert made.status_code == 201, made.text
    return made.json()


async def _record(org: str, entity: str, body: dict[str, Any]) -> dict[str, Any]:
    made = await _as_admin(org, "POST", f"/crm/{entity}", body)
    assert made.status_code == 201, made.text
    return made.json()


def _custom(p: Any, entity: str, record_id: str) -> dict[str, Any]:
    return _q(p, f"SELECT custom FROM {TABLES[entity]} WHERE id = CAST(:i AS uuid)",
              i=record_id)[0][0]


_DEF_COLUMNS = (
    "key, label, type, options, position, visible, section_id, required, "
    "help_text, default_value, lookup_target, is_system, retired_at, source"
)


def _def(p: Any, org: str, key: str) -> Any:
    rows = _q(p, f"SELECT {_DEF_COLUMNS} FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND key = :k", o=org, k=key)
    return rows[0] if rows else None


def _defs(p: Any, org: str) -> list[Any]:
    return _q(p, f"SELECT {_DEF_COLUMNS} FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) ORDER BY entity, key", o=org)


async def _layout(org: str, entity: str = "deals") -> dict[str, Any]:
    got = await _as_admin(org, "GET", f"/crm/fields/{entity}?layout=1")
    assert got.status_code == 200, got.text
    return got.json()


def _rows_as(p: Any, org: str | None, sql: str) -> list[Any]:
    fresh = create_engine(p.app_url, poolclass=NullPool)
    try:
        with fresh.begin() as c:
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return list(c.execute(text(sql)).all())
    finally:
        fresh.dispose()


async def _all_types(p: Any) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """A field of each type on deals, and a deal of org A that holds a value
    in each. Returns ``(key by type, the deal, its stored custom)``."""
    company = await _record(p.org_a, "organizations", {"name": "Acme"})
    keys: dict[str, str] = {}
    for kind, (extra, _value) in TYPES.items():
        made = await _field(p.org_a, "deals", {"label": f"F {kind}", "type": kind, **extra})
        assert made["type"] == kind and made["source"] == "native"
        keys[kind] = made["key"]
    values = {
        keys[kind]: (value if kind != "lookup" else {"id": company["id"]})
        for kind, (_extra, value) in TYPES.items()
    }
    deal = await _record(p.org_a, "deals", {"name": "Big", "custom": values})
    stored = _custom(p, "deals", deal["id"])
    assert stored[keys["lookup"]] == {"id": company["id"], "name": "Acme"}
    assert stored[keys["multiselect"]] == ["gold", "silver"]
    assert stored[keys["number"]] == 42 and stored[keys["boolean"]] is True
    assert len(stored) == len(TYPES)
    return keys, deal, stored


# ── 1. A field of each type, through every step ─────────────────────────────


async def test_each_type_survives_rename_order_section_hide_retire_restore(
    studio, app_engine,
) -> None:
    p = studio
    _assert_non_priv(app_engine)
    keys, deal, stored = await _all_types(p)
    for key in keys.values():
        assert _def(p, p.org_a, key)[-1] == "metorite"

    def survives(step: str) -> None:
        assert _custom(p, "deals", deal["id"]) == stored, step

    for kind, key in keys.items():
        renamed = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}",
                                  {"label": f"Renamed {kind}"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["label"] == f"Renamed {kind}" and renamed.json()["key"] == key
    survives("rename")

    section = await _as_admin(p.org_a, "POST", "/crm/field-sections/deals", {"label": "Main"})
    assert section.status_code == 201, section.text
    sid = section.json()["id"]
    for key in keys.values():
        moved = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}",
                                {"section_id": sid})
        assert moved.status_code == 200 and moved.json()["section_id"] == sid
    survives("section")

    layout = await _layout(p.org_a)
    order = [f["key"] for f in reversed(layout["fields"])]
    put = await _as_admin(p.org_a, "PUT", "/crm/field-layout/deals", {
        "sections": [sid],
        "fields": [{"key": k, "section_id": sid if k in keys.values() else None}
                   for k in order],
    })
    assert put.status_code == 200, put.text
    assert [f["key"] for f in put.json()["fields"]] == order
    assert [f["key"] for f in (await _layout(p.org_a))["fields"]] == order
    survives("order")

    for key in keys.values():
        hidden = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}",
                                 {"visible": False})
        assert hidden.status_code == 200 and hidden.json()["visible"] is False
    survives("hide")

    for key in keys.values():
        retired = await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{key}")
        assert retired.status_code == 200, retired.text
        assert retired.json()["retired"] is True and retired.json()["visible"] is False
        assert _def(p, p.org_a, key)[12] is not None
    survives("retire")
    plain = await _as_admin(p.org_a, "GET", "/crm/fields/deals")
    assert {f["key"] for f in plain.json() if f["retired"]} == set(keys.values())

    for key in keys.values():
        back = await _as_admin(p.org_a, "POST", f"/crm/fields/deals/{key}/restore")
        assert back.status_code == 200, back.text
        # The field was hidden before the retire, and it comes back hidden.
        assert back.json()["retired"] is False and back.json()["visible"] is False
    survives("restore")
    read = await _as_admin(p.org_a, "GET", f"/crm/deals/{deal['id']}")
    assert read.json()["custom"] == stored

    # Org B sees none of it, through the routes and through the database.
    layout_b = await _layout(p.org_b)
    assert layout_b["sections"] == []
    assert not {f["key"] for f in layout_b["fields"]} & set(keys.values())
    assert all(f["is_system"] for f in layout_b["fields"])
    assert (await _as_admin(p.org_b, "GET", "/crm/fields/deals")).json() == []
    for key in keys.values():
        foreign = await _as_admin(p.org_b, "PATCH", f"/crm/fields/deals/{key}",
                                  {"label": "Mine"})
        assert foreign.status_code == 404
    assert (await _as_admin(p.org_b, "GET", f"/crm/deals/{deal['id']}")).status_code == 404
    taken = await _as_admin(p.org_b, "POST", "/crm/fields/deals",
                            {"label": "X", "type": "text", "section_id": sid})
    assert taken.status_code == 422, "a section of org A is no section of org B"
    gone = await _as_admin(p.org_b, "PATCH", f"/crm/field-sections/deals/{sid}",
                           {"label": "Mine"})
    assert gone.status_code == 404
    assert _rows_as(p, p.org_b, "SELECT id FROM crm_field_sections") == []
    assert _rows_as(p, p.org_b, "SELECT key FROM crm_field_defs") == []
    assert len(_rows_as(p, p.org_a, "SELECT id FROM crm_field_sections")) == 1
    assert _rows_as(p, None, "SELECT id FROM crm_field_sections") == []
    with app_engine.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": p.org_b})
        c.execute(text(
            "INSERT INTO crm_field_sections (organization_id, entity, label) "
            "VALUES (CAST(:o AS uuid), 'deal', 'X')"), {"o": p.org_a})


# ── 2. The type matrix ──────────────────────────────────────────────────────


async def test_the_type_matrix_and_a_refused_change_writes_nothing(studio) -> None:
    from gateway.routes.crm.field_studio import FIELD_TYPES, TYPE_CHANGES

    p = studio
    company = await _record(p.org_a, "organizations", {"name": "Acme"})

    def body(kind: str, label: str) -> dict[str, Any]:
        return {"label": label, "type": kind, **TYPES[kind][0]}

    sources: dict[str, str] = {}
    for kind in FIELD_TYPES:
        sources[kind] = (await _field(p.org_a, "deals", body(kind, f"From {kind}")))["key"]
    values = {
        sources[kind]: (value if kind != "lookup" else {"id": company["id"]})
        for kind, (_extra, value) in TYPES.items()
    }
    deal = await _record(p.org_a, "deals", {"name": "Held", "custom": values})
    before_custom = _custom(p, "deals", deal["id"])

    refused = 0
    for src in FIELD_TYPES:
        before = _def(p, p.org_a, sources[src])
        for dst in FIELD_TYPES:
            if dst == src or dst in TYPE_CHANGES.get(src, frozenset()):
                continue
            got = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{sources[src]}",
                                  {"type": dst})
            assert got.status_code == 422, (src, dst, got.text)
            assert got.json()["detail"]["code"] == "field_type_change", (src, dst)
            refused += 1
        assert _def(p, p.org_a, sources[src]) == before, src
    assert refused == 15 * 14 - 14
    assert _custom(p, "deals", deal["id"]) == before_custom

    allowed = 0
    for src, targets in TYPE_CHANGES.items():
        for dst in sorted(targets):
            key = (await _field(p.org_a, "deals", body(src, f"M {src} {dst}")))["key"]
            held = await _record(p.org_a, "deals", {
                "name": f"{src}-{dst}", "custom": {key: TYPES[src][1]},
            })
            got = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}",
                                  {"type": dst})
            assert got.status_code == 200, (src, dst, got.text)
            assert got.json()["type"] == dst
            expected = [TYPES[src][1]] if (src, dst) == ("select", "multiselect") \
                else TYPES[src][1]
            assert _custom(p, "deals", held["id"])[key] == expected, (src, dst)
            allowed += 1
    assert allowed == 14


# ── 3. Required, the default, and a retired key ─────────────────────────────


async def test_required_and_default_on_deals_and_a_retired_key_gives_422(studio) -> None:
    p = studio
    need = await _field(p.org_a, "deals", {"label": "PO number", "type": "text",
                                           "required": True})
    region = await _field(p.org_a, "deals", {
        "label": "Region", "type": "select", "options": [{"label": "North"}],
        "default_value": "north",
    })
    assert region["default_value"] == "north"
    # A text default that looks like a number stays text, under any driver.
    code = await _field(p.org_a, "deals", {"label": "Code", "type": "text",
                                           "default_value": "007"})
    assert code["default_value"] == "007"

    missing = await _as_admin(p.org_a, "POST", "/crm/deals", {"name": "X"})
    assert missing.status_code == 422, missing.text
    assert missing.json()["detail"]["message"] == "PO number is required."
    assert _q(p, "SELECT count(*) FROM crm_deals WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a) == [(0,)]

    deal = await _record(p.org_a, "deals", {"name": "X", "custom": {need["key"]: "PO-1"}})
    assert _custom(p, "deals", deal["id"]) == {
        need["key"]: "PO-1", region["key"]: "north", code["key"]: "007"}
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{code['key']}")).status_code == 200

    for empty in (None, "", "  "):
        cleared = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                                  {"custom": {need["key"]: empty}})
        assert cleared.status_code == 422, (empty, cleared.text)
        assert "PO number" in cleared.json()["detail"]["message"]
    other = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}", {"name": "Y"})
    assert other.status_code == 200, "a PATCH that does not name it passes"
    dropped = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                              {"custom": {region["key"]: None}})
    assert dropped.status_code == 200
    # The retired `code` keeps its value: a write never touches a key that it
    # does not name.
    assert _custom(p, "deals", deal["id"]) == {need["key"]: "PO-1", code["key"]: "007"}

    # An explicit null is not an absent key, so the default does not fill it.
    plain = await _record(p.org_a, "deals", {
        "name": "Z", "custom": {need["key"]: "PO-2", region["key"]: None},
    })
    assert _custom(p, "deals", plain["id"]) == {need["key"]: "PO-2"}

    # The owner's decision: a write that names a retired key gives 422, and
    # the stored value does not change.
    note = await _field(p.org_a, "deals", {"label": "Old note", "type": "text"})
    held = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                           {"custom": {note["key"]: "kept"}})
    assert held.status_code == 200, held.text
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{note['key']}")).status_code == 200
    for value in ("new", None):
        refused = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                                  {"custom": {note["key"]: value}})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == {
            "code": "field_retired",
            "message": "Old note is retired. Restore it to write a value.",
            "field": note["key"],
        }
    assert _custom(p, "deals", deal["id"])[note["key"]] == "kept"
    born = await _as_admin(p.org_a, "POST", "/crm/deals", {
        "name": "W", "custom": {need["key"]: "PO-3", note["key"]: "x"},
    })
    assert born.status_code == 422 and born.json()["detail"]["code"] == "field_retired"
    # A retired required field is no longer enforced.
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{need['key']}")).status_code == 200
    assert (await _as_admin(p.org_a, "POST", "/crm/deals", {"name": "V"})).status_code == 201


# ── 4. A PATCH of one key, for the four entities ────────────────────────────


@pytest.mark.parametrize("entity", sorted(TABLES))
async def test_a_patch_of_one_key_keeps_the_others(studio, entity: str) -> None:
    p = studio
    a = await _field(p.org_a, entity, {"label": "Alpha", "type": "text"})
    b = await _field(p.org_a, entity, {"label": "Beta", "type": "number"})
    c = await _field(p.org_a, entity, {"label": "Gamma", "type": "text"})
    record = await _record(p.org_a, entity, {
        **CREATE[entity], "custom": {a["key"]: "one", b["key"]: 2, c["key"]: "three"},
    })
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/{entity}/{c['key']}")).status_code == 200

    one = await _as_admin(p.org_a, "PATCH", f"/crm/{entity}/{record['id']}",
                          {"custom": {a["key"]: "uno"}})
    assert one.status_code == 200, one.text
    assert one.json()["custom"] == {a["key"]: "uno", b["key"]: 2, c["key"]: "three"}
    assert _custom(p, entity, record["id"]) == {
        a["key"]: "uno", b["key"]: 2, c["key"]: "three"}

    gone = await _as_admin(p.org_a, "PATCH", f"/crm/{entity}/{record['id']}",
                           {"custom": {b["key"]: None}})
    assert gone.status_code == 200, gone.text
    assert _custom(p, entity, record["id"]) == {a["key"]: "uno", c["key"]: "three"}

    # Two keys in one PATCH, and a plain field beside them.
    both = await _as_admin(p.org_a, "PATCH", f"/crm/{entity}/{record['id']}", {
        "custom": {a["key"]: "dos", b["key"]: 5}, "description": "edited",
    })
    assert both.status_code == 200, both.text
    assert _custom(p, entity, record["id"]) == {
        a["key"]: "dos", b["key"]: 5, c["key"]: "three"}
    unknown = await _as_admin(p.org_a, "PATCH", f"/crm/{entity}/{record['id']}",
                              {"custom": {"no_such_key": 1}})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "field_unknown"


# ── 5. A bad value names the label ──────────────────────────────────────────


async def test_a_bad_value_gives_422_that_names_the_label(studio) -> None:
    p = studio
    seats = await _field(p.org_a, "deals", {"label": "Seat count", "type": "number"})
    site = await _field(p.org_a, "deals", {"label": "Site", "type": "url"})
    for key, bad in (
        (seats["key"], "many"), (seats["key"], 2.5), (site["key"], "ftp://x"),
        # Review round 1: this gave 500 (OverflowError) before.
        (seats["key"], 10**400), (seats["key"], 10**15),
    ):
        made = await _as_admin(p.org_a, "POST", "/crm/deals",
                               {"name": "X", "custom": {key: bad}})
        assert made.status_code == 422, made.text
        label = "Seat count" if key == seats["key"] else "Site"
        assert made.json()["detail"]["message"].startswith(f"{label}:")
    deal = await _record(p.org_a, "deals", {"name": "Y", "custom": {seats["key"]: 3}})
    bad = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                          {"custom": {seats["key"]: "three"}, "name": "Z"})
    assert bad.status_code == 422
    assert bad.json()["detail"]["message"].startswith("Seat count:")
    row = _q(p, "SELECT name, custom FROM crm_deals WHERE id = CAST(:i AS uuid)",
             i=deal["id"])[0]
    assert row == ("Y", {seats["key"]: 3}), "a refused PATCH changes nothing"


# ── 6. A lookup ─────────────────────────────────────────────────────────────


async def test_a_lookup_stores_id_and_name_and_refuses_org_b(studio) -> None:
    p = studio
    acme = await _record(p.org_a, "organizations", {"name": "Acme"})
    other = await _record(p.org_b, "organizations", {"name": "Other"})
    partner = await _field(p.org_a, "deals", {"label": "Partner", "type": "lookup",
                                              "lookup_target": "organizations"})
    deal = await _record(p.org_a, "deals", {
        "name": "D", "custom": {partner["key"]: {"id": acme["id"], "name": "forged"}},
    })
    assert _custom(p, "deals", deal["id"]) == {
        partner["key"]: {"id": acme["id"], "name": "Acme"}}, "the gateway names it"
    plain_id = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{deal['id']}",
                               {"custom": {partner["key"]: acme["id"]}})
    assert plain_id.status_code == 200

    foreign = await _as_admin(p.org_a, "POST", "/crm/deals", {
        "name": "E", "custom": {partner["key"]: {"id": other["id"]}},
    })
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["detail"]["message"].startswith("Partner:")
    for target in ("module:Products", "projects"):
        refused = await _as_admin(p.org_a, "POST", "/crm/fields/deals", {
            "label": "Mod", "type": "lookup", "lookup_target": target,
        })
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["code"] == "lookup_target"
    no_target = await _as_admin(p.org_a, "POST", "/crm/fields/deals",
                                {"label": "Mod", "type": "lookup"})
    assert no_target.status_code == 422
    # The target stays while records hold values.
    held = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{partner['key']}",
                           {"lookup_target": "contacts"})
    assert held.status_code == 422
    assert held.json()["detail"]["code"] == "lookup_target_change"


# ── 7. A multi-select and the custom filter ─────────────────────────────────


async def test_a_multiselect_stores_an_array_that_the_filter_finds(studio) -> None:
    p = studio
    tags = await _field(p.org_a, "deals", {
        "label": "Tags", "type": "multiselect",
        "options": [{"label": "Gold"}, {"label": "Silver"}, {"label": "Bronze"}],
    })
    key = tags["key"]
    hit = await _record(p.org_a, "deals", {"name": "Hit", "custom": {key: ["gold", "silver"]}})
    await _record(p.org_a, "deals", {"name": "Miss", "custom": {key: ["bronze"]}})
    assert _custom(p, "deals", hit["id"]) == {key: ["gold", "silver"]}
    assert _q(p, "SELECT jsonb_typeof(custom -> :k) FROM crm_deals WHERE id = "
                 "CAST(:i AS uuid)", k=key, i=hit["id"]) == [("array",)]

    for query, names in (
        (f"custom.{key}=silver", ["Hit"]),
        (f"custom.{key}.in=gold,nothing", ["Hit"]),
        (f"custom.{key}.in=silver,bronze", ["Hit", "Miss"]),
        (f"custom.{key}=nothing", []),
    ):
        got = await _as_admin(p.org_a, "GET", f"/crm/deals?{query}")
        assert got.status_code == 200, (query, got.text)
        assert sorted(r["name"] for r in got.json()["rows"]) == names, query


# ── 8. Picklist options ─────────────────────────────────────────────────────


async def test_an_option_takes_a_slug_and_a_rename_keeps_its_value(studio) -> None:
    p = studio
    tier = await _field(p.org_a, "deals", {
        "label": "Tier", "type": "select",
        "options": [{"label": "Gold Tier"}, {"label": "gold tier"}],
    })
    assert [o["value"] for o in tier["options"]] == ["gold-tier", "gold-tier-2"]
    key = tier["key"]
    held = await _record(p.org_a, "deals", {"name": "H", "custom": {key: "gold-tier-2"}})

    renamed = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}", {"options": [
        {"label": "Gold", "value": "gold-tier"},
        {"label": "Old gold", "value": "gold-tier-2", "retired": True},
        {"label": "Gold Tier"},
    ]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["options"] == [
        {"label": "Gold", "value": "gold-tier"},
        {"label": "Old gold", "value": "gold-tier-2", "retired": True},
        {"label": "Gold Tier", "value": "gold-tier-3"},
    ]
    assert _custom(p, "deals", held["id"]) == {key: "gold-tier-2"}, "a record keeps it"
    refused = await _as_admin(p.org_a, "POST", "/crm/deals",
                              {"name": "N", "custom": {key: "gold-tier-2"}})
    assert refused.status_code == 422, "no new record takes a retired option"
    again = await _as_admin(p.org_a, "PATCH", f"/crm/deals/{held['id']}",
                            {"custom": {key: "gold-tier-2"}})
    assert again.status_code == 200, "a record that holds it may send it again"
    removed = await _as_admin(p.org_a, "PATCH", f"/crm/fields/deals/{key}", {"options": [
        {"label": "Gold", "value": "gold-tier"},
    ]})
    assert removed.status_code == 422 and removed.json()["detail"]["code"] == "option_removed"


# ── 9. The layout, in one transaction, and the key words ────────────────────


async def test_the_layout_writes_in_one_transaction(studio, monkeypatch) -> None:
    from gateway.routes.crm import field_studio

    p = studio
    s1 = (await _as_admin(p.org_a, "POST", "/crm/field-sections/deals", {"label": "One"})).json()
    s2 = (await _as_admin(p.org_a, "POST", "/crm/field-sections/deals", {"label": "Two"})).json()
    assert (s1["position"], s2["position"]) == (0, 1)
    a = await _field(p.org_a, "deals", {"label": "Alpha", "type": "text"})
    b = await _field(p.org_a, "deals", {"label": "Beta", "type": "text"})
    layout = await _layout(p.org_a)
    keys = [f["key"] for f in layout["fields"]]
    assert keys[-2:] == [a["key"], b["key"]], "a new field goes last"

    wanted = [b["key"], a["key"], *[k for k in keys if k not in (a["key"], b["key"])]]
    body = {"sections": [s2["id"], s1["id"]], "fields": [
        {"key": k, "section_id": s2["id"] if k == b["key"] else
         s1["id"] if k == a["key"] else None} for k in wanted
    ]}

    # A failure between two writes leaves nothing written.
    calls = {"n": 0}
    original = field_studio.write_field_position

    async def fail_on_the_second(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("a fault between two writes")
        await original(*args, **kwargs)

    monkeypatch.setattr(field_studio, "write_field_position", fail_on_the_second)
    with pytest.raises(RuntimeError):
        await _as_admin(p.org_a, "PUT", "/crm/field-layout/deals", body)
    monkeypatch.setattr(field_studio, "write_field_position", original)
    assert await _layout(p.org_a) == layout, "the section order and field order stayed"

    done = await _as_admin(p.org_a, "PUT", "/crm/field-layout/deals", body)
    assert done.status_code == 200, done.text
    now = await _layout(p.org_a)
    assert [s["id"] for s in now["sections"]] == [s2["id"], s1["id"]]
    assert [f["key"] for f in now["fields"]] == wanted
    by_key = {f["key"]: f for f in now["fields"]}
    assert by_key[b["key"]]["section_id"] == s2["id"]
    assert by_key[a["key"]]["section_id"] == s1["id"]
    assert by_key["name"]["is_system"] and by_key["name"]["required"]
    assert _q(p, "SELECT count(*) FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND is_system", o=p.org_a)[0][0] == len(keys) - 2

    # A body that leaves out a field, names one twice or names a stranger is
    # refused, and nothing moves.
    for broken in (
        {**body, "fields": body["fields"][1:]},
        {**body, "fields": [*body["fields"], body["fields"][0]]},
        {**body, "fields": [*body["fields"], {"key": "stranger"}]},
        {**body, "sections": [s2["id"]]},
    ):
        refused = await _as_admin(p.org_a, "PUT", "/crm/field-layout/deals", broken)
        assert refused.status_code == 422, refused.text
    assert await _layout(p.org_a) == now

    # A section delete moves its fields to no section.
    gone = await _as_admin(p.org_a, "DELETE", f"/crm/field-sections/deals/{s2['id']}")
    assert gone.status_code == 200 and gone.json()["moved"] == 1
    after = {f["key"]: f for f in (await _layout(p.org_a))["fields"]}
    assert after[b["key"]]["section_id"] is None
    assert after[a["key"]]["section_id"] == s1["id"]

    # The key words: a label that names one takes a number, and the route
    # never treats a route word as a field.
    for word in ("Order", "Layout", "Restore"):
        made = await _field(p.org_a, "deals", {"label": word, "type": "text"})
        assert made["key"] == f"{word.lower()}_2"
    assert (await _as_admin(p.org_a, "PATCH", "/crm/fields/deals/order",
                            {"label": "x"})).status_code == 404


# ── 10. The readers ─────────────────────────────────────────────────────────


async def test_the_readers_leave_out_a_system_row_and_refuse_a_retired_key(
    studio, monkeypatch,
) -> None:
    from acb_skills import crm_tools

    p = studio
    named = await _as_admin(p.org_a, "PATCH", "/crm/fields/deals/name",
                            {"label": "Title"})
    assert named.status_code == 200, named.text
    assert named.json()["is_system"] and named.json()["label"] == "Title"
    assert named.json()["type"] == "text" and named.json()["required"] is True
    assert _def(p, p.org_a, "name")[11] is True
    live = await _field(p.org_a, "deals", {"label": "Live", "type": "text"})
    old = await _field(p.org_a, "deals", {"label": "Old", "type": "text"})
    tier = await _field(p.org_a, "deals", {
        "label": "Tier", "type": "multiselect",
        "options": [{"label": "Gold Tier"}, {"label": "Silver"}],
    })
    quiet = await _field(p.org_a, "deals", {"label": "Quiet", "type": "text",
                                            "visible": False})
    await _record(p.org_a, "deals", {
        "name": "D", "custom": {live["key"]: "L", old["key"]: "O",
                                tier["key"]: ["silver", "gold-tier"], quiet["key"]: "Q"},
    })
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{old['key']}")).status_code == 200

    plain = (await _as_admin(p.org_a, "GET", "/crm/fields/deals")).json()
    assert [f["key"] for f in plain] == [live["key"], old["key"], tier["key"], quiet["key"]]
    assert [f["retired"] for f in plain] == [False, True, False, False]

    system_filter = await _as_admin(p.org_a, "GET", "/crm/deals?custom.name=D")
    assert system_filter.status_code == 422, "a system row is no custom key"
    retired_filter = await _as_admin(p.org_a, "GET", f"/crm/deals?custom.{old['key']}=O")
    assert retired_filter.status_code == 422
    assert retired_filter.json()["detail"]["code"] == "field_retired"
    assert (await _as_admin(p.org_a, "GET", f"/crm/deals?custom.{live['key']}=L")).json()["total"] == 1

    export = await _as_admin(p.org_a, "GET", "/crm/export/deals.csv")
    assert export.status_code == 200, export.text
    header, row = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))[:2]
    assert header[-2:] == ["Live", "Tier"], header
    assert "Old" not in header and "Title" not in header and "Quiet" not in header
    assert row[-2:] == ["L", "Silver; Gold Tier"], "the option labels, not the values"

    async def through_the_router(path: str, params: dict | None = None) -> Any:
        got = await _as_admin(p.org_a, "GET", path)
        assert got.status_code == 200, got.text
        return got.json()

    monkeypatch.setattr(crm_tools, "_get", through_the_router)
    said = await crm_tools.crm_list_fields("deals")
    assert f"key {live['key']}" in said
    assert f"key {old['key']}, text) - retired" in said
    assert "key name" not in said and "Title" not in said


async def test_the_field_map_leaves_out_a_system_row(mirror) -> None:
    from tests.unit.test_crm_pipelines_r8 import _req as req

    p, a, _b = mirror
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, is_system) VALUES (CAST(:o AS uuid), 'lead', 'industry', "
            "'Industry', 'text', 'zoho', true)"), {"o": p.org_a})
    view = await req(_admin_of(p.org_a), "GET", "/crm/field-rules?entity=leads")
    assert view.status_code == 200, view.text
    assert "industry" not in {f["key"] for f in view.json()["fields"]}
    assert "Custom_Industry" in {f["key"] for f in view.json()["fields"]}


# ── 12. The sync and a metorite row; the start over and the change of setup ─


async def test_a_sync_never_updates_a_metorite_row(mirror) -> None:
    p, a, _b = mirror
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_field_defs SET source = 'metorite', label = 'Mine', "
            "type = 'textarea', position = 99, visible = false, options = NULL "
            "WHERE organization_id = CAST(:o AS uuid) AND key = 'Why_Lost'"),
            {"o": p.org_a})
    before = _def(p, p.org_a, "Why_Lost")
    sector = _def(p, p.org_a, "Custom_Industry")
    next(f for f in a.fields["Leads"] if f["api_name"] == "Why_Lost")["field_label"] = "Z"
    next(f for f in a.fields["Leads"] if f["api_name"] == "Custom_Industry")[
        "field_label"] = "Sector 2"
    _age_profile(p, p.org_a)
    _import_again(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _def(p, p.org_a, "Why_Lost") == before, "the sync left the metorite row"
    assert _def(p, p.org_a, "Custom_Industry")[1] == "Sector 2" != sector[1], (
        "the same run did write the Zoho rows")


async def test_the_start_over_deletes_the_studio_rows(studio) -> None:
    p = studio
    section = await _as_admin(p.org_a, "POST", "/crm/field-sections/deals", {"label": "S"})
    await _field(p.org_a, "deals", {"label": "Mine", "type": "text",
                                    "section_id": section.json()["id"]})
    assert (await _as_admin(p.org_a, "PATCH", "/crm/fields/deals/name",
                            {"label": "Title"})).status_code == 200
    await _field(p.org_b, "deals", {"label": "Theirs", "type": "text"})
    reset = await _as_admin(p.org_a, "POST", "/crm/reset",
                            {"confirm": "DELETE", "then": "native"})
    assert reset.status_code == 200, reset.text
    assert _defs(p, p.org_a) == []
    assert _q(p, "SELECT count(*) FROM crm_field_sections WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a) == [(0,)]
    assert [r[0] for r in _defs(p, p.org_b)] == ["theirs"], "org B keeps its field"


async def test_the_change_of_setup_deletes_the_studio_rows(studio) -> None:
    p = studio
    section = await _as_admin(p.org_a, "POST", "/crm/field-sections/leads", {"label": "S"})
    await _field(p.org_a, "leads", {"label": "Mine", "type": "text",
                                    "section_id": section.json()["id"]})
    changed = await _as_admin(p.org_a, "PATCH", "/crm/settings", {"mode": None})
    assert changed.status_code == 200, changed.text
    assert _defs(p, p.org_a) == []
    assert _q(p, "SELECT count(*) FROM crm_field_sections WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a) == [(0,)]


# ── 13. 403, 404 and 409 on every route ─────────────────────────────────────


def _routes(section_id: str) -> list[tuple[str, str, Any]]:
    return [
        ("POST", "/crm/fields/deals", {"label": "X", "type": "text"}),
        ("PATCH", "/crm/fields/deals/x_key", {"label": "Y"}),
        ("DELETE", "/crm/fields/deals/x_key", None),
        ("POST", "/crm/fields/deals/x_key/restore", None),
        ("PUT", "/crm/field-layout/deals", {"sections": [], "fields": []}),
        ("POST", "/crm/field-sections/deals", {"label": "S"}),
        ("PATCH", f"/crm/field-sections/deals/{section_id}", {"label": "T"}),
        ("DELETE", f"/crm/field-sections/deals/{section_id}", None),
    ]


async def test_each_route_gives_403_404_and_409(studio, monkeypatch) -> None:
    from acb_common import get_settings

    p = studio
    made = await _field(p.org_a, "deals", {"label": "X key", "type": "text"})
    assert made["key"] == "x_key"
    section = (await _as_admin(p.org_a, "POST", "/crm/field-sections/deals",
                               {"label": "S"})).json()["id"]
    snapshot = (_defs(p, p.org_a), _q(p, "SELECT id, label, position FROM "
                "crm_field_sections WHERE organization_id = CAST(:o AS uuid)", o=p.org_a))

    member = _member_of(p.org_a)
    for method, path, body in _routes(section):
        got = await _req(member, method, path, body)
        assert got.status_code == 403, (method, path, got.text)

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", p.org_b, raising=False)
    for method, path, body in _routes(section):
        got = await _as_admin(p.org_a, method, path, body)
        assert got.status_code == 404, (method, path, got.text)
    # The read stays open: the flag gates the writes only.
    assert (await _layout(p.org_a))["entity"] == "deals"

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", "*", raising=False)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'mirror' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    for method, path, body in _routes(section):
        got = await _as_admin(p.org_a, method, path, body)
        assert got.status_code == 409, (method, path, got.text)
        assert got.json()["detail"]["code"] == "mirror_mode_settings"
    assert (_defs(p, p.org_a), _q(p, "SELECT id, label, position FROM crm_field_sections "
            "WHERE organization_id = CAST(:o AS uuid)", o=p.org_a)) == snapshot


# ── 14. A sync leaves the seven new columns unchanged ───────────────────────


async def test_a_sync_leaves_the_seven_studio_columns_unchanged(mirror) -> None:
    p, a, _b = mirror
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        section = c.execute(text(
            "INSERT INTO crm_field_sections (organization_id, entity, label) VALUES "
            "(CAST(:o AS uuid), 'lead', 'Kept') RETURNING id"), {"o": p.org_a}).scalar()
        c.execute(text(
            "UPDATE crm_field_defs SET section_id = CAST(:s AS uuid), required = true, "
            "help_text = 'Ask the buyer', default_value = '\"Robotics\"', "
            "lookup_target = 'leads', retired_at = '2026-10-01T00:00:00Z' "
            "WHERE organization_id = CAST(:o AS uuid) AND key = 'Custom_Industry'"),
            {"o": p.org_a, "s": str(section)})
    seven = ("SELECT section_id, required, help_text, default_value, lookup_target, "
             "is_system, retired_at, label FROM crm_field_defs WHERE organization_id = "
             "CAST(:o AS uuid) AND key = 'Custom_Industry'")
    before = _q(p, seven, o=p.org_a)[0]
    next(f for f in a.fields["Leads"] if f["api_name"] == "Custom_Industry")[
        "field_label"] = "Sector 2"
    _age_profile(p, p.org_a)
    _import_again(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    after = _q(p, seven, o=p.org_a)[0]
    assert after[:7] == before[:7], "the seven columns"
    assert after[7] == "Sector 2", "the run did write the row"


# ── The migration ───────────────────────────────────────────────────────────


def test_the_studio_migration_is_found_by_content_and_replays(studio) -> None:
    p = studio
    sql = re.compile(r"CREATE TABLE IF NOT EXISTS crm_field_sections")
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and sql.search(f.read_text(encoding="utf-8"))]
    assert len(found) == 1
    with p.admin_engine.begin() as c:
        _exec_file(c, str(found[0]))
    assert _q(p, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                 "WHERE oid = to_regclass('crm_field_sections')") == [(True, True)]
    policy = _q(p, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_field_sections'")
    assert policy and all(row == (True, True) for row in policy)
    columns = {r[0]: r[1:] for r in _q(p, (
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'crm_field_defs'"))}
    for name in ("section_id", "help_text", "default_value", "lookup_target", "retired_at"):
        assert columns[name][0] == "YES", name
    for name in ("required", "is_system"):
        assert columns[name] == ("NO", "false"), name
    fk = _q(p, "SELECT confdeltype FROM pg_constraint WHERE conrelid = "
               "'crm_field_defs'::regclass AND contype = 'f' AND confrelid = "
               "'crm_field_sections'::regclass")
    assert fk == [("n",)], "ON DELETE SET NULL"
    assert uuid.UUID(p.org_a)


# ── 11. A CRM-Q7a rule on a Zoho double survives the type change ────────────


async def test_an_approved_rule_on_a_double_field_survives_the_type_change(mirror) -> None:
    """The sync now writes ``decimal`` for a Zoho ``double``. A rule that an
    older build approved names ``number``. The next discovery reads the new
    word, and the rule stays ``approved`` (not ``type_mismatch``)."""
    p, a, _b = mirror
    a.fields["Leads"].append(
        {"api_name": "Score", "field_label": "Score", "data_type": "double"})
    _seed(a)
    next(r for r in a.data["Leads"] if r["id"] == "l1")["Score"] = 12.5
    await _connect(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert _q(p, "SELECT type FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND key = 'Score'", o=p.org_a) == [("decimal",)]

    # What an older build stored: the word `number`, in the row and the profile.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_field_defs SET type = 'number' WHERE "
                       "organization_id = CAST(:o AS uuid) AND key = 'Score'"),
                  {"o": p.org_a})
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = jsonb_set(profile, "
            "'{custom_field_types,lead,Score}', '\"number\"') "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    made = await _post(p.org_a, "Score", "annual_revenue")
    assert made.status_code == 201, made.text
    assert made.json()["field_type"] == "number"

    _age_profile(p, p.org_a)
    _import_again(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _rule_status(p, made.json()["id"]) == "approved", "not type_mismatch"
    assert _q(p, "SELECT type FROM crm_field_defs WHERE organization_id = "
                 "CAST(:o AS uuid) AND key = 'Score'", o=p.org_a) == [("decimal",)]
    assert float(_leads(p, p.org_a)["l1"][4]["Score"]) == 12.5


# ── Review round 1 ──────────────────────────────────────────────────────────


async def test_a_restore_keeps_the_visibility_the_field_had(studio) -> None:
    p = studio
    shown = await _field(p.org_a, "deals", {"label": "Shown", "type": "text"})
    hidden = await _field(p.org_a, "deals", {"label": "Hidden", "type": "text",
                                             "visible": False})
    for made, before in ((shown, True), (hidden, False)):
        key = made["key"]
        gone = await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{key}")
        assert gone.json()["retired"] is True
        assert gone.json()["visible"] is before, "a retire keeps visible"
        back = await _as_admin(p.org_a, "POST", f"/crm/fields/deals/{key}/restore")
        assert back.status_code == 200, back.text
        assert (back.json()["retired"], back.json()["visible"]) == (False, before)


async def test_the_auto_lead_writer_fills_defaults_and_skips_required(studio) -> None:
    """Owner decision (review round 1): "required" binds member writes. A
    system writer applies the defaults and skips "required"."""
    from types import SimpleNamespace

    from gateway.routes.crm import auto_lead

    p = studio
    need = await _field(p.org_a, "leads", {"label": "Budget code", "type": "text",
                                           "required": True})
    channel = await _field(p.org_a, "leads", {
        "label": "Channel", "type": "select", "options": [{"label": "Email"}],
        "default_value": "email",
    })
    member = await _as_admin(p.org_a, "POST", "/crm/leads", {"first_name": "M"})
    assert member.status_code == 422, "a member write still meets required"

    lead = await auto_lead._create_lead(
        p.org_a, SimpleNamespace(user_id="owner@crm-sync.test"),
        "buyer@example.test", "Asha Rao",
    )
    stored = _custom(p, "leads", lead["id"])
    assert stored == {channel["key"]: "email"}, "the default, and no required stop"
    assert need["key"] not in stored
    assert _q(p, "SELECT organization_id FROM crm_leads WHERE id = CAST(:i AS uuid)",
              i=lead["id"])[0][0] == uuid.UUID(p.org_a)


async def test_a_conversion_fills_defaults_and_skips_required(studio) -> None:
    p = studio
    defaults = {}
    for entity in ("deals", "contacts", "organizations"):
        made = await _field(p.org_a, entity, {
            "label": "Region", "type": "text", "default_value": f"{entity}-north",
        })
        defaults[entity] = made["key"]
        await _field(p.org_a, entity, {"label": "Must", "type": "text",
                                       "required": True})
    lead = await _record(p.org_a, "leads", {
        "first_name": "Ravi", "last_name": "Rao", "organization_name": "Rao Labs",
    })
    converted = await _as_admin(p.org_a, "POST", f"/crm/leads/{lead['id']}/convert", {})
    assert converted.status_code == 200, converted.text
    body = converted.json()
    for entity, part in (("deals", "deal"), ("contacts", "contact"),
                         ("organizations", "organization")):
        record = body[part]
        assert _custom(p, entity, record["id"]) == {
            defaults[entity]: f"{entity}-north"}, entity


async def test_a_saved_view_skips_a_retired_custom_filter(studio) -> None:
    p = studio
    tier = await _field(p.org_a, "deals", {
        "label": "Tier", "type": "select", "options": [{"label": "Gold"}],
    })
    region = await _field(p.org_a, "deals", {
        "label": "Region", "type": "select", "options": [{"label": "North"}],
    })
    await _record(p.org_a, "deals", {"name": "D", "custom": {
        tier["key"]: "gold", region["key"]: "north"}})
    made = await _as_admin(p.org_a, "POST", "/crm/views", {
        "entity": "deals", "name": "Gold north",
        "filters": {"custom": {tier["key"]: ["gold"], region["key"]: ["north"]}},
    })
    assert made.status_code == 201, made.text
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{tier['key']}")).status_code == 200

    views = (await _as_admin(p.org_a, "GET", "/crm/views?entity=deals")).json()
    (view,) = [v for v in views if v["id"] == made.json()["id"]]
    assert view["filters"]["custom"] == {region["key"]: ["north"]}
    assert view["skipped_filters"] == [tier["key"]]
    # The view's filters, as the client sends them, now load.
    query = "&".join(f"custom.{k}={v[0]}" for k, v in view["filters"]["custom"].items())
    assert (await _as_admin(p.org_a, "GET", f"/crm/deals?{query}")).json()["total"] == 1
    # The stored view keeps the key, so a restore brings the filter back.
    stored = _q(p, "SELECT filters FROM crm_views WHERE id = CAST(:i AS uuid)",
                i=made.json()["id"])[0][0]
    assert tier["key"] in stored["custom"]
    assert (await _as_admin(p.org_a, "POST", f"/crm/fields/deals/{tier['key']}/restore")).status_code == 200
    again = (await _as_admin(p.org_a, "GET", "/crm/views?entity=deals")).json()
    (view,) = [v for v in again if v["id"] == made.json()["id"]]
    assert view["skipped_filters"] == [] and tier["key"] in view["filters"]["custom"]
    # A list request that names a retired key is still 422 (§20.7 item 11).
    await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{tier['key']}")
    direct = await _as_admin(p.org_a, "GET", f"/crm/deals?custom.{tier['key']}=gold")
    assert direct.status_code == 422


# ── Gap A: the UI reads the flag from the layout ────────────────────────────


async def test_the_layout_names_the_studio_flag_for_the_caller(studio, monkeypatch) -> None:
    from acb_common import get_settings

    p = studio

    async def flags(user: Any) -> tuple[bool, bool]:
        got = await _req(user, "GET", "/crm/fields/deals?layout=1")
        assert got.status_code == 200, got.text
        return got.json()["studio"], got.json()["studio_admin"]

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", p.org_b, raising=False)
    assert await flags(_admin_of(p.org_a)) == (False, False), "flag off"
    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", p.org_a, raising=False)
    assert await flags(_admin_of(p.org_a)) == (True, True), "flag on, admin, Native"
    assert await flags(_member_of(p.org_a)) == (True, False), "flag on, a member"
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'mirror' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    assert await flags(_admin_of(p.org_a)) == (True, False), "Mirror mode"
    # The plain read keeps its list shape.
    plain = await _as_admin(p.org_a, "GET", "/crm/fields/deals")
    assert isinstance(plain.json(), list)


# ── Gap B: the live name of a lookup ────────────────────────────────────────


async def test_a_lookup_reads_the_live_name_and_never_org_b(studio) -> None:
    p = studio
    acme = await _record(p.org_a, "organizations", {"name": "Acme"})
    beta = await _record(p.org_a, "organizations", {"name": "Beta"})
    other = await _record(p.org_b, "organizations", {"name": "Org B secret"})
    partner = await _field(p.org_a, "deals", {"label": "Partner", "type": "lookup",
                                              "lookup_target": "organizations"})
    owner = await _field(p.org_a, "deals", {"label": "Reseller", "type": "lookup",
                                            "lookup_target": "organizations"})
    key, second = partner["key"], owner["key"]
    deal = await _record(p.org_a, "deals", {"name": "D", "custom": {
        key: {"id": acme["id"]}, second: {"id": beta["id"]}}})

    renamed = await _as_admin(p.org_a, "PATCH", f"/crm/organizations/{acme['id']}",
                              {"name": "Acme Two"})
    assert renamed.status_code == 200, renamed.text
    read = (await _as_admin(p.org_a, "GET", f"/crm/deals/{deal['id']}")).json()
    assert read["custom"][key] == {"id": acme["id"], "name": "Acme Two"}
    assert read["custom"][second] == {"id": beta["id"], "name": "Beta"}
    assert _custom(p, "deals", deal["id"])[key]["name"] == "Acme", "a read writes nothing"
    listed = (await _as_admin(p.org_a, "GET", "/crm/deals")).json()["rows"]
    assert [r["custom"][key]["name"] for r in listed if r["id"] == deal["id"]] == ["Acme Two"]

    # A target that is gone keeps the stored name and says so.
    assert (await _as_admin(p.org_a, "DELETE", f"/crm/organizations/{beta['id']}")).status_code == 200
    read = (await _as_admin(p.org_a, "GET", f"/crm/deals/{deal['id']}")).json()
    assert read["custom"][second] == {"id": beta["id"], "name": "Beta", "missing": True}

    # A value that names a record of org B (planted past the write check)
    # never reads org B: row level security hides it, so it reads as missing.
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET custom = jsonb_set(custom, ARRAY[CAST(:k AS text)], "
            "CAST(:v AS jsonb)) WHERE id = CAST(:i AS uuid)"),
            {"k": key, "i": deal["id"],
             "v": json.dumps({"id": other["id"], "name": "Planted"})})
    read = (await _as_admin(p.org_a, "GET", f"/crm/deals/{deal['id']}")).json()
    assert read["custom"][key] == {"id": other["id"], "name": "Planted", "missing": True}
    assert "Org B secret" not in json.dumps(read)
