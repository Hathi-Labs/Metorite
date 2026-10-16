"""R8 — the executor and the preview of a draft, against a real Postgres
(WS-53 CRM-Q19, ``crm_platform.md`` §18.9.6, "CRM-Q19, the missing items").

``POST /crm/mapping/preview`` (``routes/crm/mapping_plans.py``) reads up to
``PREVIEW_ROWS`` mirrored records of each entity of a draft, applies the draft
in memory through ``crm_sync/executor.py``, and returns the rows in the shape
of the list route. The sync runs the same executor.

What this proves, with two organizations and the non-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), by item of the narrowed
contract:

4. the preview applies the draft in memory, in the shape of the list route,
   reads the lowest ids up to the bound, and shows a dropped live rule as
   NULL while the value of the fixed map wins;
5. the preview writes nothing: no row of any table changes, and the
   transaction is READ ONLY;
6. the preview counts each value that did not map, by entity and target
   column, and names no value;
7. a run counts the same values under ``unmapped`` of the entity, from the
   backfill and from ``map_record``, and Sync health passes it through
   ``last_run``;
8. the route has the guards of the plan routes, and orgs stay apart.

``tests/unit/test_crm_executor.py`` proves items 1 to 3 with no database.
``tests/unit/test_crm_field_rules_r8.py`` holds the fixture and the fake Zoho.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _leads,
    _member_of,
    _post,
    _req,
    _run,
    fr,
)
from tests.unit.test_crm_map_plan_r8 import _draft, _plans, _put, _recorded, _remove
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _q,
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

PREVIEW = "/crm/mapping/preview"


async def _preview(org: str, version: int, user: Any = None) -> Any:
    return await _req(user or _admin_of(org), "POST", PREVIEW, {"version": version})


async def _drafted(org: str, *changes: dict[str, Any]) -> int:
    made = await _draft(org, *changes)
    assert made.status_code == 201, made.text
    return int(made.json()["version"])


def _entity(body: dict[str, Any], word: str) -> dict[str, Any]:
    (found,) = [e for e in body["entities"] if e["entity"] == word]
    return found


def _by_ext(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["ext_id"]: r for r in rows}


def _fingerprint(p: Any) -> dict[str, str]:
    """The row count and a hash of the rows of each table, read as the owner,
    so row level security hides nothing."""
    tables = [r[0] for r in _q(
        p, "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
           "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
           "AND c.relkind IN ('r', 'p') ORDER BY 1")]
    out: dict[str, str] = {}
    with p.admin_engine.connect() as c:
        for table in tables:
            out[table] = str(c.execute(text(
                f'SELECT count(*)::text || \':\' || COALESCE(md5(string_agg('
                f'md5(t::text), \'\' ORDER BY md5(t::text))), \'\') FROM "{table}" t',
            )).scalar())
    return out


def _last_counts(p: Any, org: str) -> dict[str, Any]:
    rows = _q(p, "SELECT counts FROM crm_sync_runs WHERE organization_id = "
                 "CAST(:o AS uuid) ORDER BY started_at DESC LIMIT 1", o=org)
    return rows[0][0]


# ── 4. The draft, in memory, in the shape of the list route ──────────────────


async def test_4_the_preview_applies_a_draft_in_the_shape_of_the_list_route(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    version = await _drafted(
        p.org_a, _put("Custom_Industry", "industry"), _put("Why_Lost", "lost_note"))

    got = await _preview(p.org_a, version)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["version"] == version
    assert [e["entity"] for e in body["entities"]] == ["lead"]
    leads = _entity(body, "lead")
    assert leads["slug"] == "leads"
    rows = _by_ext(leads["rows"])
    assert rows["l1"]["industry"] == "Robotics"
    assert rows["l1"]["lost_note"] == "Price"
    # l2 holds a value in the standard field, so the fixed map wins.
    assert rows["l2"]["industry"] == "Standard"

    # The rows have the shape of the list route.
    listed = await _req(_admin_of(p.org_a), "GET", "/crm/leads")
    assert listed.status_code == 200, listed.text
    shape = {r["ext_id"]: set(r) for r in listed.json()["rows"]}
    assert {ext: set(row) for ext, row in rows.items()} == shape

    # Nothing changed in the database.
    stored = _leads(p, p.org_a)
    assert stored["l1"][1] is None and stored["l1"][3] is None


async def test_4_the_preview_reads_the_lowest_ids_up_to_the_bound(fr, monkeypatch) -> None:
    from gateway.routes.crm import mapping_plans

    p, a, _b = fr
    await _imported(p, p.org_a, a)
    version = await _drafted(p.org_a, _put("Why_Lost", "lost_note"))
    assert mapping_plans.PREVIEW_ROWS == 200
    monkeypatch.setattr(mapping_plans, "PREVIEW_ROWS", 1)
    body = (await _preview(p.org_a, version)).json()
    (row,) = _entity(body, "lead")["rows"]
    lowest = _q(p, "SELECT min(id::text) FROM crm_leads WHERE organization_id = "
                   "CAST(:o AS uuid)", o=p.org_a)[0][0]
    assert row["id"] == lowest


async def test_4_the_preview_reads_only_the_mirrored_rows(fr) -> None:
    """A rule applies only to a mirrored row, so a native row of the org is
    not in the sample."""
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id, custom) "
            "SELECT organization_id, 'Native lead', status_id, "
            "'{\"Why_Lost\": \"Native\"}'::jsonb FROM crm_leads "
            "WHERE organization_id = CAST(:o AS uuid) LIMIT 1"), {"o": p.org_a})
    version = await _drafted(p.org_a, _put("Why_Lost", "lost_note"))
    rows = _entity((await _preview(p.org_a, version)).json(), "lead")["rows"]
    assert {r["ext_id"] for r in rows} == {"l1", "l2"}
    assert "Native lead" not in {r["lead_name"] for r in rows}


async def test_4_a_draft_that_drops_a_live_rule_shows_its_free_column_empty(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Why_Lost", "lost_note")).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    assert _leads(p, p.org_a)["l1"][3] == "Price", "the live rule filled the column"

    version = await _drafted(p.org_a, _remove("Why_Lost"))
    body = (await _preview(p.org_a, version)).json()
    # The draft names no lead rule, but the live set does, so leads show.
    leads = _entity(body, "lead")
    assert _by_ext(leads["rows"])["l1"]["lost_note"] is None
    assert leads["unmapped"] == {}
    assert _leads(p, p.org_a)["l1"][3] == "Price", "the preview wrote nothing"


# ── 5. The preview writes nothing ────────────────────────────────────────────


async def test_5_no_row_of_any_table_changes(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    version = await _drafted(
        p.org_a, _put("Why_Lost", "lost_note"), _put("Headcount", "no_of_employees"))
    approved = (await _plans(p.org_a))["approved"]

    before = _fingerprint(p)
    with _recorded() as seen:
        assert (await _preview(p.org_a, version)).status_code == 200
        assert (await _preview(p.org_a, approved)).status_code == 409
        assert (await _preview(p.org_a, 999)).status_code == 404
    assert _fingerprint(p) == before

    assert "SET TRANSACTION READ ONLY" in seen
    writes = [s for s in seen
              if s.split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE")]
    assert writes == []


# ── 6. The count of values that did not map ──────────────────────────────────


async def test_6_the_preview_counts_what_did_not_map_and_names_no_value(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    version = await _drafted(
        p.org_a, _put("Headcount", "no_of_employees"), _put("Why_Lost", "lost_note"))
    leads = _entity((await _preview(p.org_a, version)).json(), "lead")
    # l1 holds "lots", which is no number. l2 holds 40.
    assert leads["unmapped"] == {"no_of_employees": 1, "lost_note": 0}
    rows = _by_ext(leads["rows"])
    assert rows["l1"]["no_of_employees"] is None
    assert rows["l2"]["no_of_employees"] == 40
    assert "lots" not in json.dumps(leads["unmapped"])


# ── 7. The run counts unmapped, and Sync health shows it ─────────────────────


async def test_7_a_run_counts_unmapped_and_sync_health_passes_it_through(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert _last_counts(p, p.org_a)["leads"]["unmapped"] == 0

    # The backfill path: l1 holds "lots".
    assert (await _post(p.org_a, "Headcount", "no_of_employees")).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    assert _last_counts(p, p.org_a)["leads"]["unmapped"] == 1
    health = await _req(_admin_of(p.org_a), "GET", "/crm/sync/health")
    assert health.status_code == 200, health.text
    (row,) = [e for e in health.json()["entities"] if e["key"] == "leads"]
    assert row["last_run"]["unmapped"] == 1

    # The map_record path: a new record whose value does not map.
    a.add("Leads", "l9", 90, Last_Name="Nine", Headcount="many")
    assert (await _run(p.org_a)).status == "ok"
    assert _last_counts(p, p.org_a)["leads"]["unmapped"] == 1
    assert _leads(p, p.org_a)["l9"][2] is None
    assert "many" not in json.dumps(_last_counts(p, p.org_a))


# ── 8. The guards, and orgs stay apart ───────────────────────────────────────


async def test_8_the_route_is_admin_only_mirror_only_and_dark(fr, monkeypatch) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    version = await _drafted(p.org_a, _put("Why_Lost", "lost_note"))
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    member = _member_of(p.org_a)
    assert (await _req(member, "GET", "/crm/fields/leads")).status_code == 200
    assert (await _preview(p.org_a, version, member)).status_code == 403

    # A body that names an organization is refused (R11).
    forged = await _req(_admin_of(p.org_a), "POST", PREVIEW,
                        {"version": version, "organization_id": p.org_b})
    assert forged.status_code == 422

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    native = await _preview(p.org_a, version)
    assert native.status_code == 409
    assert native.json()["detail"]["code"] == "not_mirror"

    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    assert (await _preview(p.org_a, version)).status_code == 404


async def test_8_a_version_that_is_not_a_draft_has_no_preview(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    approved = (await _plans(p.org_a))["approved"]
    got = await _preview(p.org_a, approved)
    assert got.status_code == 409
    assert got.json()["detail"]["code"] == "not_draft"
    assert (await _preview(p.org_a, 999)).status_code == 404


async def test_8_orgs_stay_apart(fr, app_engine) -> None:
    p, a, b = fr
    _assert_non_priv(app_engine)
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    version_a = await _drafted(p.org_a, _put("LinkedIn", "linkedin_url", "contacts"))
    assert [v["version"] for v in (await _plans(p.org_b))["versions"]] == [1]
    # B cannot preview A's draft: the version is not in B's plan.
    assert (await _preview(p.org_b, version_a)).status_code == 404

    version_b = await _drafted(p.org_b, _put("LinkedIn", "linkedin_url", "contacts"))
    contacts = _entity((await _preview(p.org_b, version_b)).json(), "contact")
    # A contact with a surname only keeps it in ``first_name``.
    names = {r["first_name"] for r in contacts["rows"]}
    assert names and all(n.startswith("B-") for n in names), names
    assert {r["linkedin_url"] for r in contacts["rows"]} == {
        "https://linkedin.example/B-ann"}
    seen_a = _entity((await _preview(p.org_a, version_a)).json(), "contact")
    assert {r["first_name"] for r in seen_a["rows"]} == {"A-Ann"}
