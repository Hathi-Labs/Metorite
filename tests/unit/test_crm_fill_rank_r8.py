"""R8 — the fill rank, against a real Postgres (WS-53 CRM-Q17a-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3 and the CRM-Q17a-1
done-when list of §18.9.6. Migration 274 adds ``crm_field_fill``. The sync
writes it (``crm_sync/fill.py``), and ``routes/crm/fill_rank.py`` reads it.

Each test runs the real sync and the real ``/crm`` router as the
non-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS).
Org A is on ``CRM_FILL_RANK_ORGS``. Org B is not.

What this proves, one test for each done-when item:

1. the migration is found by content, has FORCE RLS, and replays;
2. a run writes ``filled``, ``total`` and ``distinct_count`` for each key, by
   the rule of "filled"; an unchanged entity keeps its numbers; a change
   inside 24 hours waits, and the next run after the bound computes it; a
   first import in the run passes the bound; org B reads none of A's rows;
3. the fields routes and the module routes serve ``fill`` and ``distinct``;
4. the layout serves ``empty_columns``, never the name or the last activity;
5. the CSV export leaves out the same keys;
6. ``GET /crm/mode`` serves the source labels in Mirror mode, none in Native;
7. org B gets none of it: no row, no cursor, and ``null`` on each route.

It also measures the one statement on 30,000 module rows with 236 fields,
the shape of DEWiN's Invoices.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import csv
import io
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_module_records_r8 import (  # noqa: F401
    _age_module_reads,
    _connect_all,
    _run,
    _seed,
    mz,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _admin,
    _assert_non_priv,
    _call,
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


@pytest.fixture
def fz(mz, monkeypatch):
    """The module-store fixture, with org A (only) on ``CRM_FILL_RANK_ORGS``."""
    from acb_common import get_settings

    p, a, b = mz
    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", p.org_a, raising=False)
    yield p, a, b
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            c.execute(text(
                "DELETE FROM crm_field_fill WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org})


def _fill(p: Any, org: str, entity: str) -> dict[str, tuple[int, int, int]]:
    rows = _q(p, "SELECT key, filled, total, distinct_count FROM crm_field_fill "
                 "WHERE organization_id = CAST(:o AS uuid) AND entity = :e",
              o=org, e=entity)
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _stamps(p: Any, org: str, entity: str) -> set[datetime]:
    return {r[0] for r in _q(
        p, "SELECT computed_at FROM crm_field_fill WHERE organization_id = "
           "CAST(:o AS uuid) AND entity = :e", o=org, e=entity)}


def _state(p: Any, org: str) -> dict[str, Any] | None:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = 'zoho:fill_rank'", o=org)
    return rows[0][0] if rows else None


def _write_state(p: Any, org: str, state: dict[str, Any]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, cursor_state, "
            "last_status, initial_done_at) VALUES (CAST(:o AS uuid), 'zoho:fill_rank', "
            "CAST(:s AS jsonb), 'ok', now()) ON CONFLICT (organization_id, module) "
            "DO UPDATE SET cursor_state = EXCLUDED.cursor_state"),
            {"o": org, "s": json.dumps(state)})


def _age_fill(p: Any, org: str, hours: int = 25) -> None:
    """Move each stamp of the fill row back by *hours*."""
    state = _state(p, org) or {}
    old = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    _write_state(p, org, {**state, "at": dict.fromkeys(state.get("at") or {}, old)})


def _seed_fill(z: Any) -> None:
    """``_seed``, plus values that test the rule of "filled" on Products."""
    _seed(z)
    p1, p2 = z.data["Products"]
    p1.update(Flag=False, Tags=[], Blank="  ", Zero=0)
    p2.update(Flag=True, Tags=["x"], Blank="", Zero=0)


async def _get(org: str, path: str) -> Any:
    return await _call(_admin("admin@crm-fill.test", org), "GET", path)


# ── 1. The migration ─────────────────────────────────────────────────────────


def test_the_migration_is_found_by_content_and_replays(promoted) -> None:
    """R1: the file is found by its content, never by its number."""
    found = [
        path for path in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS crm_field_fill" in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    with promoted.admin_engine.begin() as c:
        _exec_file(c, found[0])
        _exec_file(c, found[0])
    rows = _q(promoted, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass('crm_field_fill')")
    assert rows == [(True, True)]
    policies = _q(promoted, "SELECT polname FROM pg_policy WHERE polrelid = "
                            "to_regclass('crm_field_fill')")
    assert [r[0] for r in policies] == ["crm_field_fill_tenant_isolation"]


@pytest.mark.parametrize("slug", ["leads", "contacts", "organizations", "deals"])
def test_each_standard_key_names_a_column_of_its_table(promoted, slug: str) -> None:
    """A standard key that is not a column would read as never filled, and
    ``empty_columns`` would hide it for ever."""
    from gateway.crm_sources.zoho import mapping
    from gateway.routes.crm.core import FIELD_ENTITY_BY_SLUG
    from gateway.routes.crm.fill_rank import standard_keys

    table = mapping.ENTITY_TABLES[FIELD_ENTITY_BY_SLUG[slug]]
    columns = {r[0] for r in _q(
        promoted, "SELECT column_name FROM information_schema.columns "
                  "WHERE table_schema = 'public' AND table_name = :t", t=table)}
    assert set(standard_keys(slug)) - columns == set()


# ── 2. The sync writes the numbers ───────────────────────────────────────────


async def test_a_run_writes_the_fill_of_each_key(fz) -> None:
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)

    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)

    products = _fill(p, p.org_a, "module:Products")
    assert products["Product_Name"] == (2, 2, 2)
    assert products["Unit_Price"] == (2, 2, 2)
    assert products["Vendor_Name"] == (1, 2, 1)
    # The rule of "filled": false, [], a blank string and "" are empty, 0 is not.
    assert products["Flag"] == (1, 2, 1)
    assert products["Tags"] == (1, 2, 1)
    assert products["Blank"] == (0, 2, 0)
    assert products["Zero"] == (2, 2, 1)
    assert _fill(p, p.org_a, "module:Install_Base")["Name"] == (1, 1, 1)

    company = _fill(p, p.org_a, "company")
    assert company["name"] == (1, 1, 1)
    assert company["industry"] == (0, 1, 0)
    assert company["last_activity_at"][1] == 1
    # A custom field with a definition and no value has a row of its own.
    assert company["custom.Region"] == (0, 1, 0)
    # An entity with no record still has its standard keys, with total 0.
    assert _fill(p, p.org_a, "contact")["email"] == (0, 0, 0)

    state = _state(p, p.org_a)
    assert state is not None and state["pending"] == []
    assert {"company", "contact", "lead", "deal", "module:Products",
            "module:Install_Base"} <= set(state["at"])


async def test_an_org_outside_the_list_gets_no_fill_work(fz) -> None:
    p, _a, b = fz
    _seed_fill(b)
    await _connect_all(p, p.org_b, b)
    assert (await _run(p.org_b)).status == "ok"
    assert _q(p, "SELECT count(*) FROM crm_field_fill WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_b) == [(0,)]
    assert _state(p, p.org_b) is None


async def test_an_unchanged_entity_keeps_its_numbers_and_a_change_waits(fz) -> None:
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    products = _stamps(p, p.org_a, "module:Products")
    company = _stamps(p, p.org_a, "company")

    # No change: nothing is computed again.
    assert (await _run(p.org_a)).status == "ok"
    assert _stamps(p, p.org_a, "module:Products") == products
    assert _stamps(p, p.org_a, "company") == company

    # A change inside the bound waits: the old numbers stay, the entity is pending.
    p2 = next(r for r in a.data["Products"] if r["id"] == "p2")
    p2.update(Vendor_Name={"id": "v2", "name": "Vendor Two"},
              Modified_Time="2026-01-01T00:40:00+00:00")
    _age_module_reads(p, p.org_a)
    changed = await _run(p.org_a)
    assert changed.counts["module:Products"]["upserted"] == 1
    assert _fill(p, p.org_a, "module:Products")["Vendor_Name"] == (1, 2, 1)
    assert _stamps(p, p.org_a, "module:Products") == products
    assert _state(p, p.org_a)["pending"] == ["module:Products"]

    # Past the bound, the next run computes the waiting change, and only it.
    _age_fill(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _fill(p, p.org_a, "module:Products")["Vendor_Name"] == (2, 2, 2)
    assert _stamps(p, p.org_a, "module:Products") != products
    assert _stamps(p, p.org_a, "company") == company
    assert _state(p, p.org_a)["pending"] == []


async def test_a_first_import_in_this_run_passes_the_bound(fz) -> None:
    p, a, _b = fz
    _seed_fill(a)
    now = datetime.now(UTC).isoformat()
    _write_state(p, p.org_a, {"at": {
        e: now for e in ("company", "contact", "lead", "deal", "module:Products")
    }, "pending": []})
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert _fill(p, p.org_a, "module:Products")["Unit_Price"] == (2, 2, 2)
    assert _fill(p, p.org_a, "company")["name"] == (1, 1, 1)


async def test_org_b_reads_none_of_org_a_fill_rows(fz, app_engine) -> None:
    p, a, _b = fz
    _assert_non_priv(app_engine)
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    def count_as(org: str | None) -> int:
        fresh = create_engine(p.app_url, poolclass=NullPool)
        try:
            with fresh.begin() as c:
                if org is not None:
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": org})
                return int(c.execute(text("SELECT count(*) FROM crm_field_fill")).scalar())
        finally:
            fresh.dispose()

    assert count_as(p.org_a) > 0
    assert count_as(p.org_b) == 0
    assert count_as(None) == 0


# ── 3 and 4. The routes ──────────────────────────────────────────────────────


async def _both_synced(p: Any, a: Any, b: Any) -> None:
    for org, z in ((p.org_a, a), (p.org_b, b)):
        _seed_fill(z)
        await _connect_all(p, org, z)
        assert (await _run(org)).status == "ok"


async def test_the_fields_routes_serve_fill_distinct_and_empty_columns(fz) -> None:
    p, a, b = fz
    await _both_synced(p, a, b)

    layout = (await _get(p.org_a, "/crm/fields/organizations?layout=1")).json()
    by_key = {f["key"]: f for f in layout["fields"]}
    assert (by_key["name"]["fill"], by_key["name"]["distinct"]) == (1.0, 1)
    assert (by_key["industry"]["fill"], by_key["industry"]["distinct"]) == (0.0, 0)
    assert by_key["Region"]["fill"] == 0.0
    empty = set(layout["empty_columns"])
    assert {"industry", "phone", "website", "email"} <= empty
    assert not empty & {"name", "last_activity_at"}

    plain = (await _get(p.org_a, "/crm/fields/organizations")).json()
    assert {f["key"]: f["fill"] for f in plain} == {"Region": 0.0}

    # Org B is outside the list: the same routes, with nothing new.
    layout_b = (await _get(p.org_b, "/crm/fields/organizations?layout=1")).json()
    assert layout_b["empty_columns"] is None
    assert {f["fill"] for f in layout_b["fields"]} == {None}
    assert {f["distinct"] for f in layout_b["fields"]} == {None}
    plain_b = (await _get(p.org_b, "/crm/fields/organizations")).json()
    assert [f["fill"] for f in plain_b] == [None]


async def test_the_deal_layout_names_a_joined_column_in_both_words(fz) -> None:
    """A deal with no company: ``company_id`` is empty, so the list key
    ``organization_name`` is in the set too."""
    p, a, b = fz
    a.add("Deals", "d1", 20, Deal_Name="A-Deal", Stage="Qualify")
    await _both_synced(p, a, b)
    layout = (await _get(p.org_a, "/crm/fields/deals?layout=1")).json()
    empty = set(layout["empty_columns"])
    assert {"company_id", "organization_name"} <= empty
    assert "status_id" not in empty and "status_name" not in empty
    assert "name" not in empty


async def test_the_module_routes_serve_fill_and_distinct(fz) -> None:
    p, a, b = fz
    await _both_synced(p, a, b)

    listed = (await _get(p.org_a, "/crm/modules/Products/records")).json()
    defs = {d["key"]: d for d in listed["field_defs"]}
    assert (defs["Unit_Price"]["fill"], defs["Unit_Price"]["distinct"]) == (1.0, 2)
    assert defs["Vendor_Name"]["fill"] == 0.5
    record_id = _q(p, "SELECT id FROM crm_module_records WHERE organization_id = "
                      "CAST(:o AS uuid) AND ext_id = 'p1'", o=p.org_a)[0][0]
    one = (await _get(p.org_a, f"/crm/modules/Products/records/{record_id}")).json()
    assert {d["key"]: d["fill"] for d in one["field_defs"]}["Vendor_Name"] == 0.5

    listed_b = (await _get(p.org_b, "/crm/modules/Products/records")).json()
    assert {d["fill"] for d in listed_b["field_defs"]} == {None}
    # The list of modules keeps its shape (crmQ6b.test.ts pins it).
    modules = (await _get(p.org_a, "/crm/modules")).json()
    assert set(modules[0]) == {"api_name", "label", "singular_label", "count"}


# ── 5. The export ────────────────────────────────────────────────────────────


async def test_the_export_leaves_out_the_same_empty_columns(fz) -> None:
    from gateway.routes.crm.export import COLUMNS

    p, a, b = fz
    await _both_synced(p, a, b)
    layout = (await _get(p.org_a, "/crm/fields/organizations?layout=1")).json()
    empty = set(layout["empty_columns"])

    header: dict[str, list[str]] = {}
    for org in (p.org_a, p.org_b):
        response = await _get(org, "/crm/export/organizations.csv")
        assert response.status_code == 200
        body = response.content.decode("utf-8-sig")
        header[org] = next(csv.reader(io.StringIO(body)))

    kept = [h for k, h in COLUMNS["organizations"] if k not in empty]
    dropped = [h for k, h in COLUMNS["organizations"] if k in empty]
    assert dropped, "the seed must leave a list column empty"
    assert header[p.org_a][:len(kept)] == kept
    assert not set(dropped) & set(header[p.org_a])
    assert "Company" in header[p.org_a] and "Last touched" in header[p.org_a]
    # Org B keeps each column.
    full = [h for _k, h in COLUMNS["organizations"]]
    assert header[p.org_b][:len(full)] == full


# ── 6. The labels ────────────────────────────────────────────────────────────


async def test_the_mode_route_serves_the_source_labels_in_mirror_mode(fz) -> None:
    p, a, b = fz
    for z in (a, b):
        z.modules.append({
            "api_name": "Contacts", "module_name": "Contacts",
            "plural_label": "Existing Sites", "singular_label": "Existing Site",
            "generated_type": "default", "api_supported": True,
        })
    await _both_synced(p, a, b)

    mode = (await _get(p.org_a, "/crm/mode")).json()
    assert mode["mode"] == "mirror"
    assert mode["labels"]["contacts"] == {
        "label": "Existing Sites", "singular_label": "Existing Site",
    }
    assert mode["labels"]["deals"]["label"] == "Deals"
    assert "organizations" not in mode["labels"]  # the profile names no Accounts

    assert (await _get(p.org_b, "/crm/mode")).json() == {"mode": "mirror", "labels": None}

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    assert (await _get(p.org_a, "/crm/mode")).json() == {"mode": "native", "labels": None}


# ── The cost bound: one statement on 30,000 module rows ─────────────────────

#: DEWiN's Invoices: 31,924 records and 236 fields (§18.9.2).
COST_ROWS = 30_000
COST_FIELDS = 236
#: The bound the statement must meet on a developer machine. It runs at most
#: once in 24 hours for each entity, in its own transaction.
COST_SECONDS = 60.0


async def test_the_statement_on_30000_module_rows_meets_its_bound(fz, capsys) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sources.zoho import mapping
    from gateway.crm_sync import fill

    p, _a, _b = fz
    with p.admin_engine.begin() as c:
        # A third of the fields are empty, a fifth hold a number, the rest
        # text with 50 different values.
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, fields) "
            "SELECT CAST(:o AS uuid), 'Invoices', 'zoho', 'inv' || g, 'Invoice ' || g, "
            "(SELECT jsonb_object_agg('F_' || f, CASE WHEN f % 3 = 0 THEN 'null'::jsonb "
            " WHEN f % 5 = 0 THEN to_jsonb(g % 7) ELSE to_jsonb('v' || (g % 50)) END) "
            " FROM generate_series(1, :fields) f) "
            "FROM generate_series(1, :rows) g"),
            {"o": p.org_a, "rows": COST_ROWS, "fields": COST_FIELDS})
        c.execute(text("ANALYZE crm_module_records"))

    started = time.monotonic()
    async with tenant_session(p.org_a) as db:
        written, gone = await fill.compute(db, p.org_a, "module:Invoices", mapping)
    elapsed = time.monotonic() - started
    with capsys.disabled():
        print(f"\n[CRM-Q17a-1 cost] {COST_ROWS} rows x {COST_FIELDS} fields: "
              f"{elapsed:.2f} s, {written} keys")

    assert (written, gone) == (COST_FIELDS, 0)
    numbers = _fill(p, p.org_a, "module:Invoices")
    assert numbers["F_3"] == (0, COST_ROWS, 0)
    assert numbers["F_5"] == (COST_ROWS, COST_ROWS, 7)
    assert numbers["F_1"] == (COST_ROWS, COST_ROWS, 50)
    assert elapsed < COST_SECONDS, f"{elapsed:.1f} s is past the bound"
