"""R8 — the fill rank, against a real Postgres (WS-53 CRM-Q17a-1, CRM-Q17a-1b).

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

CRM-Q17a-1b adds the "still empty" check of a stale core entity, at the end.
Its six cases are the item 9 list of the spec. It also measures the check on
30,000 contacts.
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
            for table in ("crm_field_fill", "crm_field_rules"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
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
    """Let *hours* pass: each stamp of the fill row AND the start of each run
    move back by *hours*, so their order stays as it was."""
    state = _state(p, org) or {}
    shift = timedelta(hours=hours)

    def aged(key: str) -> dict[str, str]:
        return {
            e: (datetime.fromisoformat(v) - shift).isoformat()
            for e, v in (state.get(key) or {}).items()
        }

    _write_state(p, org, {**state, "at": aged("at"), "checked": aged("checked")})
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET started_at = started_at - make_interval(hours => :h) "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org, "h": hours})


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


async def test_the_fields_routes_serve_fill_distinct_and_empty_columns(fz, monkeypatch) -> None:
    from acb_common import get_settings

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

    # Org A leaves the list. Its rows stay, and no route reads them.
    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", "", raising=False)
    layout_off = (await _get(p.org_a, "/crm/fields/organizations?layout=1")).json()
    assert layout_off["empty_columns"] is None
    assert {f["fill"] for f in layout_off["fields"]} == {None}
    plain_off = (await _get(p.org_a, "/crm/fields/organizations")).json()
    assert [f["fill"] for f in plain_off] == [None]
    module_off = (await _get(p.org_a, "/crm/modules/Products/records")).json()
    assert {d["fill"] for d in module_off["field_defs"]} == {None}


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

    # Outside the list, the body of before: no labels key at all.
    assert (await _get(p.org_b, "/crm/mode")).json() == {"mode": "mirror"}

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    assert (await _get(p.org_a, "/crm/mode")).json() == {"mode": "native"}


# ── The cost bound: one statement on 30,000 module rows ─────────────────────

#: DEWiN's Invoices: 31,924 records and 236 fields (§18.9.2), mostly unique.
COST_ROWS = 30_000
COST_FIELDS = 236


async def test_the_statement_on_30000_unique_module_rows_meets_its_bound(fz, capsys) -> None:
    """Review P1: the real shape is mostly unique values. A third of the
    fields are empty, and each other value differs from all the others."""
    from acb_common.db import tenant_session
    from gateway.crm_sources.zoho import mapping
    from gateway.crm_sync import fill

    p, _a, _b = fz
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, fields) "
            "SELECT CAST(:o AS uuid), 'Invoices', 'zoho', 'inv' || g, 'Invoice ' || g, "
            "(SELECT jsonb_object_agg('F_' || f, CASE WHEN f % 3 = 0 THEN 'null'::jsonb "
            " ELSE to_jsonb('u' || g || '-' || f) END) "
            " FROM generate_series(1, :fields) f) "
            "FROM generate_series(1, :rows) g"),
            {"o": p.org_a, "rows": COST_ROWS, "fields": COST_FIELDS})
        c.execute(text("ANALYZE crm_module_records"))

    started = time.monotonic()
    async with tenant_session(p.org_a) as db:
        numbers = await fill.read(db, p.org_a, "module:Invoices", mapping)
    elapsed = time.monotonic() - started
    async with tenant_session(p.org_a) as db:
        written, gone = await fill.write(db, p.org_a, "module:Invoices", numbers)
    with capsys.disabled():
        print(f"\n[CRM-Q17a-1 cost] {COST_ROWS} rows x {COST_FIELDS} fields, unique "
              f"values: {elapsed:.2f} s, {written} keys")

    assert (written, gone) == (COST_FIELDS, 0)
    got = _fill(p, p.org_a, "module:Invoices")
    assert got["F_3"] == (0, COST_ROWS, 0)
    # distinct reads the sample of DISTINCT_SAMPLE records, so it stops there.
    assert got["F_1"] == (COST_ROWS, COST_ROWS, fill.DISTINCT_SAMPLE)
    assert elapsed < fill.TIMEOUT_MS / 1000, f"{elapsed:.1f} s is past the timeout"


# ── The failure path, the cap, the order (verifier F3 and F4, review P1, P2) ─


async def test_a_timeout_logs_stamps_waits_and_never_fails_the_run(
    fz, monkeypatch, capsys,
) -> None:
    from gateway.crm_sync import fill

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    calls: list[str] = []
    real = fill.statement

    def slow(entity: str, mapping: Any) -> tuple[str, dict[str, Any]]:
        calls.append(entity)
        return "SELECT pg_sleep(2) WHERE CAST(:org AS uuid) IS NOT NULL", {}

    monkeypatch.setattr(fill, "TIMEOUT_MS", 100)
    monkeypatch.setattr(fill, "statement", slow)
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert calls, "the fill step ran no statement"
    assert "crm.sync.fill_failed" in capsys.readouterr().out
    assert _q(p, "SELECT count(*) FROM crm_field_fill WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a) == [(0,)]
    state = _state(p, p.org_a)
    assert set(calls) <= set(state["at"]) and set(calls) <= set(state["pending"])

    # Inside 24 hours, the next run does not try again.
    tried = len(calls)
    assert (await _run(p.org_a)).status == "ok"
    assert len(calls) == tried

    # Past the bound, with the real statement, the numbers land.
    monkeypatch.setattr(fill, "statement", real)
    monkeypatch.setattr(fill, "TIMEOUT_MS", 30_000)
    _age_fill(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _fill(p, p.org_a, "module:Products")["Unit_Price"] == (2, 2, 2)
    assert _state(p, p.org_a)["pending"] == []


async def test_a_run_computes_one_large_entity_and_the_rest_wait(fz, monkeypatch) -> None:
    from gateway.crm_sync import fill

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    monkeypatch.setattr(fill, "LARGE_ROWS", 0)  # each entity with a record is large

    def computed() -> set[str]:
        return {r[0] for r in _q(p, "SELECT DISTINCT entity FROM crm_field_fill "
                                    "WHERE organization_id = CAST(:o AS uuid)", o=p.org_a)}

    assert (await _run(p.org_a)).status == "ok"
    assert "company" in computed()
    assert not computed() & {"module:Install_Base", "module:Products"}
    assert {"module:Install_Base", "module:Products"} <= set(_state(p, p.org_a)["pending"])
    assert (await _run(p.org_a)).status == "ok"
    assert "module:Install_Base" in computed() and "module:Products" not in computed()
    assert (await _run(p.org_a)).status == "ok"
    assert "module:Products" in computed()
    assert _state(p, p.org_a)["pending"] == []


async def test_the_stamp_commits_first_the_read_holds_no_settings_lock_and_beats(
    fz, monkeypatch,
) -> None:
    """P2: the attempt is committed before the statement. P2: the statement
    holds no lock on crm_settings. F4: each entity writes the run heartbeat."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    real = fill.read
    seen: list[tuple[str, Any]] = []

    async def watched(db: Any, org: str, entity: str, mapping: Any) -> dict[str, Any]:
        state = _state(p, p.org_a) or {}
        assert entity in state.get("at", {}) and entity in state.get("pending", [])
        with p.admin_engine.begin() as c:
            # NOWAIT fails at once if the fill step held the row FOR SHARE.
            c.execute(text("SELECT mode FROM crm_settings WHERE organization_id = "
                           "CAST(:o AS uuid) FOR UPDATE NOWAIT"), {"o": org})
            beat = c.execute(text(
                "SELECT heartbeat_at FROM crm_sync_runs WHERE organization_id = "
                "CAST(:o AS uuid) AND status = 'running'"), {"o": org}).scalar()
        seen.append((entity, beat))
        return await real(db, org, entity, mapping)

    monkeypatch.setattr(fill, "read", watched)
    assert (await _run(p.org_a)).status == "ok"
    assert len(seen) >= 3
    beats = [b for _e, b in seen]
    assert all(b is not None for b in beats)
    assert beats == sorted(beats) and len(set(beats)) == len(beats)


# ── Stale numbers never hide a column (review P1) ────────────────────────────


async def _empty_and_header(org: str) -> tuple[list[str] | None, list[str]]:
    layout = (await _get(org, "/crm/fields/organizations?layout=1")).json()
    response = await _get(org, "/crm/export/organizations.csv")
    body = response.content.decode("utf-8-sig")
    return layout["empty_columns"], next(csv.reader(io.StringIO(body)))


async def test_a_pending_entity_serves_no_empty_columns(fz) -> None:
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    empty, header = await _empty_and_header(p.org_a)
    assert "industry" in empty and "Industry" not in header

    _write_state(p, p.org_a, {**(_state(p, p.org_a) or {}), "pending": ["company"]})
    empty, header = await _empty_and_header(p.org_a)
    assert empty == [] and "Industry" in header


async def test_a_run_that_raised_after_its_pages_makes_the_numbers_stale(
    fz, monkeypatch,
) -> None:
    from gateway.crm_sync import engine

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    a.add("Accounts", "a2", 30, Account_Name="A-Company-2", Industry="Retail")

    async def boom(ctx: Any) -> None:
        raise RuntimeError("after the pages")

    with monkeypatch.context() as m:
        m.setattr(engine, "_module_records", boom)
        failed = await _run(p.org_a)
    assert failed.status == "failed"
    assert _q(p, "SELECT count(*) FROM crm_companies WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a) == [(2,)]
    empty, header = await _empty_and_header(p.org_a)
    assert empty == [] and "Industry" in header

    # The next run past the bound computes it again.
    _age_fill(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _fill(p, p.org_a, "company")["industry"] == (1, 2, 1)
    empty, _header = await _empty_and_header(p.org_a)
    assert "industry" not in empty and "phone" in empty


async def test_a_change_while_the_flag_was_off_makes_the_numbers_stale(
    fz, monkeypatch,
) -> None:
    from acb_common import get_settings

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", "", raising=False)
    a.add("Accounts", "a2", 30, Account_Name="A-Company-2", Industry="Retail")
    assert (await _run(p.org_a)).status == "ok"
    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", p.org_a, raising=False)

    empty, header = await _empty_and_header(p.org_a)
    assert empty == [] and "Industry" in header
    _age_fill(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert _fill(p, p.org_a, "company")["industry"] == (1, 2, 1)


async def test_the_export_keeps_a_column_that_a_row_fills(fz) -> None:
    """A change the staleness rule cannot see (a direct write here) still
    never drops a value: the export checks the rows it renders."""
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_companies SET industry = 'Retail' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    empty, header = await _empty_and_header(p.org_a)
    assert "industry" in empty
    assert "Industry" in header and "Phone" not in header


# ── Fix round 3: field rules, relinks, the checked mark, the clock ───────────


def _write_rules(p: Any, org: str, state: dict[str, Any]) -> None:
    """The cursor row of the field rules, as the sync would leave it."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, cursor_state, "
            "last_status, initial_done_at) VALUES (CAST(:o AS uuid), 'zoho:field_rules', "
            "CAST(:s AS jsonb), 'ok', now()) ON CONFLICT (organization_id, module) "
            "DO UPDATE SET cursor_state = EXCLUDED.cursor_state"),
            {"o": org, "s": json.dumps(state)})


def _clear_pending(p: Any, org: str) -> None:
    """Only the run counts can make an entity stale after this. CRM-Q17a-1b:
    it also removes the "still empty" checks, because a check serves its keys
    while the numbers are stale. The tests of the check are below."""
    state = _state(p, org) or {}
    _write_state(p, org, {**state, "pending": [], "still_empty": {}})


async def _layout_empty(org: str, slug: str) -> list[str] | None:
    return (await _get(org, f"/crm/fields/{slug}?layout=1")).json()["empty_columns"]


async def test_a_field_rule_applied_after_the_fill_step_serves_no_empty_columns(fz) -> None:
    """Verifier R7: the rule branch of ``fill_rank.stale_entities``."""
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert "industry" in await _layout_empty(p.org_a, "organizations")

    _write_rules(p, p.org_a, {"applied": [
        {"id": "r-q17", "entity": "company", "field": "Region", "column": "industry"},
    ], "reread": []})
    assert await _layout_empty(p.org_a, "organizations") == []
    # Another entity keeps its numbers.
    assert _state(p, p.org_a)["pending"] == []


async def test_the_next_run_puts_the_entity_of_a_changed_rule_in_pending(
    fz, monkeypatch,
) -> None:
    """Verifier R7: the rule branch of ``fill._due``. The fill step stops at
    once here, so only that branch can keep the change: the new rule marks
    replace the old ones in the same write."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'company', 'Region', "
            "'text', 1, 'industry', 'approved', 'admin')"), {"o": p.org_a})

    stop = [False]
    real = fill.refresh

    async def stopping(ctx: Any, *, every: Any) -> None:
        stop[0] = True  # a shutdown that comes during the fill step
        await real(ctx, every=every)

    monkeypatch.setattr(fill, "refresh", stopping)
    assert (await _run(p.org_a, should_stop=lambda: stop[0])).status == "ok"
    state = _state(p, p.org_a)
    assert "company" in state["pending"]
    assert [m[1] for m in state["rules"]] == ["company"]
    assert await _layout_empty(p.org_a, "organizations") == []


async def test_a_lead_relink_counts_and_makes_the_leads_stale(fz) -> None:
    """Review P2: ``_relink_leads`` changes ``converted_*``. The run counts it
    under ``relinked``, and the stale rule reads that count."""
    p, a, _b = fz
    _seed_fill(a)
    a.add("Leads", "l1", 5, Last_Name="Lead", Company="Acme")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_leads SET custom = COALESCE(custom, '{}'::jsonb) || "
            "'{\"_converted\": {\"company\": \"a1\"}}'::jsonb "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})

    second = await _run(p.org_a)
    assert second.status == "ok"
    assert second.counts["leads"]["relinked"] == 1
    assert second.counts["leads"]["upserted"] == 0
    # The run count alone makes the leads stale: no pending entry here.
    _clear_pending(p, p.org_a)
    assert await _layout_empty(p.org_a, "leads") == []


async def test_a_deal_relink_counts_and_makes_the_deals_stale(fz) -> None:
    """Review P2: ``_relink_deals`` sets ``pipeline_id`` in the discovery
    step. The run counts it under ``relinked``."""
    p, a, _b = fz
    _seed_fill(a)
    a.add("Deals", "d1", 20, Deal_Name="A-Deal", Stage="Qualify")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_deals SET pipeline_id = NULL WHERE "
                       "organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
        c.execute(text(
            "UPDATE crm_source_profiles SET fetched_at = now() - interval '48 hours' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})

    second = await _run(p.org_a)
    assert second.status == "ok"
    assert second.counts["deals"]["relinked"] == 1
    _clear_pending(p, p.org_a)
    assert await _layout_empty(p.org_a, "deals") == []


async def test_a_run_with_no_change_moves_the_checked_mark_to_its_start(fz) -> None:
    """Review P2: the scan of an entity that never changes starts at the last
    run that found no change, and a later change is still seen."""
    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert (await _run(p.org_a)).status == "ok"

    def newest_start() -> datetime:
        return _q(p, "SELECT max(started_at) FROM crm_sync_runs WHERE "
                     "organization_id = CAST(:o AS uuid)", o=p.org_a)[0][0]

    state = _state(p, p.org_a)
    assert datetime.fromisoformat(state["checked"]["company"]) == newest_start()
    assert datetime.fromisoformat(state["checked"]["company"]) > datetime.fromisoformat(
        state["at"]["company"])

    # A change after the mark is still stale.
    a.add("Accounts", "a2", 30, Account_Name="A-Company-2", Industry="Retail")
    assert (await _run(p.org_a)).status == "ok"
    _clear_pending(p, p.org_a)
    assert await _layout_empty(p.org_a, "organizations") == []


async def test_the_stamp_and_the_bound_take_the_database_clock(fz, monkeypatch) -> None:
    """Review P2: a stamp compares with ``crm_sync_runs.started_at``, so it
    takes that clock, and so does the 24-hour bound. An app clock 74 years
    ahead changes nothing."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    monkeypatch.setattr(fill, "_now", lambda: datetime(2100, 1, 1, tzinfo=UTC))
    assert (await _run(p.org_a)).status == "ok"
    start = _q(p, "SELECT max(started_at) FROM crm_sync_runs WHERE "
                  "organization_id = CAST(:o AS uuid)", o=p.org_a)[0][0]
    stamps = {datetime.fromisoformat(v) for v in _state(p, p.org_a)["at"].values()}
    assert stamps and all(start <= s < start + timedelta(hours=1) for s in stamps)

    # A change inside the bound waits by the database clock.
    p2 = next(r for r in a.data["Products"] if r["id"] == "p2")
    p2.update(Vendor_Name={"id": "v2", "name": "Vendor Two"},
              Modified_Time="2026-01-01T00:40:00+00:00")
    _age_module_reads(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert "module:Products" in _state(p, p.org_a)["pending"]
    assert _fill(p, p.org_a, "module:Products")["Vendor_Name"] == (1, 2, 1)


# ── CRM-Q17a-1b: the "still empty" check of a stale entity ───────────────────
#
# Spec: crm_platform.md §18.9.6, CRM-Q17a-1b. Item 9 names six cases. The
# tests below prove them, and the fences of items 5 to 8.


def _checks(p: Any, org: str) -> dict[str, Any]:
    return (_state(p, org) or {}).get("still_empty") or {}


async def _company_changes_inside_the_bound(p: Any, a: Any) -> None:
    """Run 1 computes the company. Run 2, inside 24 hours, adds a company with
    an industry, so the company waits in ``pending`` and the check runs."""
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert "industry" in await _layout_empty(p.org_a, "organizations")
    a.add("Accounts", "a2", 30, Account_Name="A-Company-2", Industry="Retail")
    second = await _run(p.org_a)
    assert (second.status, second.error) == ("ok", None)
    assert second.counts["organizations"]["upserted"] == 1
    assert "company" in _state(p, p.org_a)["pending"]
    # The stored numbers are the old ones: industry is empty there.
    assert _fill(p, p.org_a, "company")["industry"] == (0, 1, 0)


def _spy_checks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, list[str]]]:
    """Record each call of ``fill.check``: the org, the entity and the keys."""
    from gateway.crm_sync import fill

    calls: list[tuple[str, str, list[str]]] = []
    real = fill.check

    async def spy(db: Any, org: str, entity: str, keys: list[str], mapping: Any) -> Any:
        calls.append((org, entity, list(keys)))
        return await real(db, org, entity, keys, mapping)

    monkeypatch.setattr(fill, "check", spy)
    return calls


async def test_a_stale_entity_serves_the_keys_that_stayed_empty(fz) -> None:
    """Item 9, case 1. Items 3 and 4: the entry, its time, and the route."""
    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)

    entry = _checks(p, p.org_a)["company"]
    assert {"phone", "website", "email"} <= set(entry["keys"])
    start = _q(p, "SELECT max(started_at) FROM crm_sync_runs WHERE "
                  "organization_id = CAST(:o AS uuid)", o=p.org_a)[0][0]
    assert datetime.fromisoformat(entry["at"]) > start  # the database clock

    empty, header = await _empty_and_header(p.org_a)
    assert {"phone", "website", "email"} <= set(empty)
    assert not set(empty) & {"name", "last_activity_at"}
    assert "Phone" not in header and "Website" not in header


async def test_a_key_that_a_run_filled_before_the_check_is_not_served(fz) -> None:
    """Item 9, case 2. The stored numbers still give industry as empty."""
    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    assert "industry" not in _checks(p, p.org_a)["company"]["keys"]
    empty, header = await _empty_and_header(p.org_a)
    assert "industry" not in empty and "Industry" in header
    assert "phone" in empty


async def test_a_backfilling_rule_of_the_entity_serves_no_empty_columns(fz) -> None:
    """Item 9, case 3. The saved marks equal the rule marks, so only the
    ``backfilling`` test can give ``[]``."""
    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    rule = {"id": "r-bf", "entity": "company", "field": "Region", "column": "industry"}

    def rules(backfilling: bool) -> None:
        _write_rules(p, p.org_a, {"applied": [{**rule, "backfilling": backfilling}],
                                  "reread": []})
        _write_state(p, p.org_a, {**_state(p, p.org_a),
                                  "rules": [["r-bf", "company", "Region", "industry",
                                             backfilling]]})

    rules(True)
    assert await _layout_empty(p.org_a, "organizations") == []
    rules(False)
    assert "phone" in await _layout_empty(p.org_a, "organizations")


async def test_a_run_after_the_check_that_changes_a_row_serves_no_empty_columns(
    fz, monkeypatch,
) -> None:
    """Item 9, case 4. The run fails after its pages, so its fill step makes
    no new check. The next good run checks again."""
    from gateway.crm_sync import engine

    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    assert "phone" in await _layout_empty(p.org_a, "organizations")

    a.add("Accounts", "a3", 40, Account_Name="A-Company-3", Phone="555 0100")

    async def boom(ctx: Any) -> None:
        raise RuntimeError("after the pages")

    with monkeypatch.context() as m:
        m.setattr(engine, "_module_records", boom)
        failed = await _run(p.org_a)
    assert failed.status == "failed"
    assert "phone" in _checks(p, p.org_a)["company"]["keys"]  # the old entry
    empty, header = await _empty_and_header(p.org_a)
    assert empty == [] and "Phone" in header

    assert (await _run(p.org_a)).status == "ok"
    empty = await _layout_empty(p.org_a, "organizations")
    assert "phone" not in empty and "website" in empty


async def test_a_failed_check_writes_no_entry_and_serves_no_empty_columns(
    fz, monkeypatch, capsys,
) -> None:
    """Item 9, case 5, and the LOCAL timeout of item 6. The slow statement
    would give each key as empty if it ran to its end."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    assert "phone" in _checks(p, p.org_a)["company"]["keys"]

    def slow(entity: str, mapping: Any) -> str:
        return ("SELECT k AS key, false AS filled FROM unnest(CAST(:keys AS text[])) k,"
                " pg_sleep(2) WHERE CAST(:org AS uuid) IS NOT NULL")

    monkeypatch.setattr(fill, "check_statement", slow)
    monkeypatch.setattr(fill, "CHECK_TIMEOUT_MS", 100)
    capsys.readouterr()
    a.add("Accounts", "a3", 40, Account_Name="A-Company-3")
    assert (await _run(p.org_a)).status == "ok"
    assert "crm.sync.fill_check_failed" in capsys.readouterr().out
    assert "company" not in _checks(p, p.org_a)
    empty, header = await _empty_and_header(p.org_a)
    assert empty == [] and "Phone" in header


async def test_an_org_outside_the_list_gets_no_check(fz, monkeypatch) -> None:
    """Item 9, case 6."""
    p, _a, b = fz
    calls = _spy_checks(monkeypatch)
    _seed_fill(b)
    await _connect_all(p, p.org_b, b)
    assert (await _run(p.org_b)).status == "ok"
    b.add("Accounts", "a2", 30, Account_Name="B-Company-2", Industry="Retail")
    assert (await _run(p.org_b)).status == "ok"
    assert calls == []
    assert _state(p, p.org_b) is None
    assert await _layout_empty(p.org_b, "organizations") is None


async def test_a_key_the_stored_numbers_give_as_filled_is_never_checked_or_served(
    fz, monkeypatch,
) -> None:
    """Item 5. The check reads only the stored empty keys. A key that the
    stored numbers give as filled leaves the served list, even when an entry
    names it."""
    from gateway.routes.crm.fill_rank import NEVER_EMPTY, standard_keys

    p, a, _b = fz
    calls = _spy_checks(monkeypatch)
    await _company_changes_inside_the_bound(p, a)
    stored = _fill(p, p.org_a, "company")
    empty_in_numbers = {
        k for k in standard_keys("organizations")
        if k not in NEVER_EMPTY and stored[k][1] > 0 and stored[k][0] == 0
    }
    assert [(e, set(k)) for _o, e, k in calls] == [("company", empty_in_numbers)]

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_field_fill SET filled = 1 WHERE organization_id = "
                       "CAST(:o AS uuid) AND entity = 'company' AND key = 'phone'"),
                  {"o": p.org_a})
    assert "phone" in _checks(p, p.org_a)["company"]["keys"]
    empty = await _layout_empty(p.org_a, "organizations")
    assert "phone" not in empty and "website" in empty


async def test_one_check_for_each_changed_core_entity_and_none_for_a_module(
    fz, monkeypatch,
) -> None:
    """Items 6 and 8. A run with no change makes no statement, and its entry
    stays. A generic module gets no check."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    calls = _spy_checks(monkeypatch)
    _seed_fill(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert calls == []  # nothing is stale after the first computation

    a.add("Accounts", "a2", 30, Account_Name="A-Company-2", Industry="Retail")
    p2 = next(r for r in a.data["Products"] if r["id"] == "p2")
    p2.update(Vendor_Name={"id": "v2", "name": "Vendor Two"},
              Modified_Time="2026-01-01T00:40:00+00:00")
    _age_module_reads(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert {"company", "module:Products"} <= set(_state(p, p.org_a)["pending"])
    assert [e for _o, e, _k in calls] == ["company"]
    assert set(_checks(p, p.org_a)) == {"company"}
    assert len(calls) <= len(fill.CORE_ORDER)

    before = _checks(p, p.org_a)["company"]
    assert (await _run(p.org_a)).status == "ok"
    assert len(calls) == 1  # no run changed the company since its check
    assert _checks(p, p.org_a)["company"] == before
    assert "phone" in await _layout_empty(p.org_a, "organizations")


async def test_a_computed_entity_drops_its_check(fz) -> None:
    """New numbers make the check old: past the bound, the next run computes
    the company, and its entry leaves."""
    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    assert "company" in _checks(p, p.org_a)
    _age_fill(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert "company" not in _checks(p, p.org_a)
    assert _fill(p, p.org_a, "company")["industry"] == (1, 2, 1)
    empty = await _layout_empty(p.org_a, "organizations")
    assert "industry" not in empty and "phone" in empty


async def test_a_changed_rule_drops_the_check_of_its_entity(fz, monkeypatch) -> None:
    """A rule writes rows that no run count shows. The fill step that takes
    the new marks drops the entry of the entity in the same write. The fill
    step stops at once here, so no new check can replace the entry."""
    from gateway.crm_sync import fill

    p, a, _b = fz
    await _company_changes_inside_the_bound(p, a)
    assert "company" in _checks(p, p.org_a)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'company', 'Region', "
            "'text', 1, 'phone', 'approved', 'admin')"), {"o": p.org_a})

    stop = [False]
    real = fill.refresh

    async def stopping(ctx: Any, *, every: Any) -> None:
        stop[0] = True  # a shutdown that comes during the fill step
        await real(ctx, every=every)

    monkeypatch.setattr(fill, "refresh", stopping)
    assert (await _run(p.org_a, should_stop=lambda: stop[0])).status == "ok"
    state = _state(p, p.org_a)
    assert [m[1] for m in state["rules"]] == ["company"]
    assert "company" not in state["still_empty"]
    assert await _layout_empty(p.org_a, "organizations") == []


# ── The cost bound of the check: 30,000 contacts (item 7) ───────────────────

#: The rows and the custom fields of the cost test. 236 custom fields of
#: unique values is the widest shape that DEWiN shows (§18.9.2).
CHECK_ROWS = 30_000
CHECK_CUSTOM = 236
#: The 10 keys of the check: the 9 contact keys that can be empty, and
#: ``last_activity_at``, so the statement reads 10 keys as the spec says.
CHECK_KEYS = ["title", "email", "phone", "owner_email", "last_name", "mobile",
              "company_id", "linkedin_url", "description", "last_activity_at"]
#: The bound that the build measured, with room for a slow CI box. The
#: "Built as" note of CRM-Q17a-1b records the measured time.
CHECK_BOUND_S = 4.0


async def test_the_check_on_30000_contacts_meets_its_bound(fz, capsys) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sources.zoho import mapping
    from gateway.crm_sync import fill

    p, _a, _b = fz
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_contacts (organization_id, first_name, custom) "
            "SELECT CAST(:o AS uuid), 'Contact ' || g, "
            "(SELECT jsonb_object_agg('C_' || f, 'u' || g || '-' || f) "
            " FROM generate_series(1, :fields) f) "
            "FROM generate_series(1, :rows) g"),
            {"o": p.org_a, "rows": CHECK_ROWS, "fields": CHECK_CUSTOM})
        c.execute(text("ANALYZE crm_contacts"))
    try:
        started = time.monotonic()
        async with tenant_session(p.org_a) as db:
            still, at = await fill.check(db, p.org_a, "contact", CHECK_KEYS, mapping)
        elapsed = time.monotonic() - started
        with capsys.disabled():
            print(f"\n[CRM-Q17a-1b cost] {CHECK_ROWS} contacts x {CHECK_CUSTOM} custom "
                  f"fields, {len(CHECK_KEYS)} empty keys: {elapsed:.2f} s")
        assert still == sorted(CHECK_KEYS)
        assert at is not None
        assert elapsed < CHECK_BOUND_S, f"{elapsed:.2f} s is past the bound"
        assert CHECK_BOUND_S < fill.CHECK_TIMEOUT_MS / 1000
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM crm_contacts WHERE organization_id = "
                           "CAST(:o AS uuid)"), {"o": p.org_a})
