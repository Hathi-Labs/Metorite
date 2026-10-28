"""R8 — the cutover preview (WS-53 CRM-Z10a, ``crm_platform.md`` §3.4, D133).

What this proves against a REAL Postgres, through the real ``/crm`` router, as
the NON-privileged role of the phase-4 rehearsal:

- **A test proves no row is written** (spec fence). A hash of each ``crm_*``
  table and of ``integration_connections`` is equal before and after the call,
  and the fake Zoho records 0 calls;
- the counts split Zoho rows from native rows, and never show org B;
- the Zoho counts come from the last good run, with its time;
- each Zoho owner with no member is listed with its record count;
- a held, unavailable or failed module is "not complete";
- a Native org gets 409 ``not_mirror``, and a member without the admin
  permission gets 403.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_access_r8 import (
    _admin,
    _assert_non_priv,
    _call,
    _member,
)
from tests.unit.test_crm_reset_r8 import _seed_both, x0  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see test_crm_reset_r8).
pytestmark = _DB_GATE

PATH = "/crm/cutover/preview"

_TABLES_SQL = (
    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
    "AND (tablename LIKE 'crm!_%' ESCAPE '!' OR tablename = 'integration_connections') "
    "ORDER BY tablename"
)


def _hashes(p: Any) -> dict[str, str]:
    """One hash for each ``crm_*`` table and ``integration_connections``, all orgs."""
    with p.admin_engine.connect() as c:
        tables = [r[0] for r in c.execute(text(_TABLES_SQL))]
        assert len(tables) > 20, tables
        return {
            t: c.execute(text(
                f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x::text), '')) "
                f"FROM {t} x")).scalar()
            for t in tables
        }


def _shape_org_a(p: Any) -> None:
    """A run with counts, a hold, a profile with labels, and unmatched owners."""
    o = {"o": p.org_a}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET counts = CAST(:c AS jsonb), "
            "estimate = CAST(:e AS jsonb) WHERE organization_id = CAST(:o AS uuid)"),
            {**o,
             "c": json.dumps({"deals": {"seen": 547, "failed": 2},
                              "leads": {"seen": 1},
                              "module:Vendors": {"seen": 0, "unavailable": 1}}),
             "e": json.dumps({"deals": 550, "leads": 1, "module:Vendors": 9})})
        c.execute(text(
            "UPDATE crm_settings SET sync_holds = CAST(:h AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {**o, "h": json.dumps({"module:Products": {
                "held": True, "by": "admin", "count": 1,
                "at": "2026-10-01T00:00:00+00:00"}})})
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = CAST(:p AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {**o, "p": json.dumps({"modules": [
                {"api_name": "Deals", "label": "Projects", "singular_label": "Project"},
                {"api_name": "Accounts", "label": "Existing Sites"},
                {"api_name": "Products", "label": "Products", "mirrored": True},
                {"api_name": "Vendors", "label": "Vendors", "mirrored": True},
                {"api_name": "Quotes", "label": "Quotes", "api_supported": True},
            ]})})
        c.execute(text(
            "UPDATE crm_deals SET custom = jsonb_build_object('_owner_name', "
            "CASE WHEN ext_id::bigint % 2 = 0 THEN 'Asha Rao' ELSE 'Vik Shah' END) "
            "WHERE organization_id = CAST(:o AS uuid) AND ext_id::bigint % 100 < 5"), o)
        c.execute(text(
            "UPDATE crm_leads SET custom = jsonb_build_object('_owner_name', 'Asha Rao') "
            "WHERE organization_id = CAST(:o AS uuid)"), o)
        # A native lead: it did not come from Zoho.
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
            "SELECT CAST(:o AS uuid), 'Native lead', status_id FROM crm_leads "
            "WHERE organization_id = CAST(:o AS uuid) LIMIT 1"), o)


async def test_preview_counts_and_writes_nothing(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    _shape_org_a(x0)
    before = _hashes(x0)

    res = await _call(x0, _admin(x0.org_a), "GET", PATH)
    assert res.status_code == 200, res.text
    body = res.json()

    # The spec fence: a test proves no row is written.
    assert _hashes(x0) == before
    assert x0.active["zoho"].requests == [], "the preview called the source"

    rows = {r["key"]: r for r in body["records"]}
    # The label of the source, and Zoho's count from the last good run.
    assert rows["deals"]["label"] == "Projects"
    assert rows["deals"]["zoho"] == 550
    assert rows["deals"]["metorite"] == {"from_zoho": 547, "native": 0}
    assert rows["deals"]["state"] == "failed" and rows["deals"]["failed"] == 2
    # Org B holds 3 deals. They never count here.
    assert rows["leads"]["metorite"] == {"from_zoho": 1, "native": 1}
    assert rows["organizations"]["label"] == "Existing Sites"
    assert rows["activities"]["parts"][0]["label"] == "Notes"
    assert rows["module:Products"]["state"] == "held"
    assert rows["module:Products"]["metorite"]["from_zoho"] == 1
    assert rows["module:Vendors"]["state"] == "unavailable"
    assert sorted(body["not_complete"]) == ["Products", "Projects", "Vendors"]
    assert body["sync"]["last_good_at"] is not None
    assert body["sync"]["age_seconds"] >= 2 * 86400 - 60
    assert body["sync"]["running"] is False

    owners = {o["name"]: o["records"] for o in body["unmatched_owners"]}
    assert owners["Asha Rao"] >= 1 and owners["Vik Shah"] >= 1
    assert body["unmatched_owner_names"] == 2
    # 1 lead, plus the deals whose id ends in 00 to 04.
    assert sum(owners.values()) == 1 + sum(
        1 for g in range(1, 548) if (910_000_000 + g) % 100 < 5)

    other = {o["key"]: o["metorite"] for o in body["other"]}
    assert other["pipelines"] == {"from_zoho": 1, "native": 0}
    assert other["stages"]["native"] == 1
    assert other["deal_contacts"]["from_zoho"] + other["deal_contacts"]["native"] == 1
    assert other["fields"]["from_zoho"] == 1 and other["fields"]["native"] == 2
    assert other["views"]["native"] == 1
    assert [x["label"] for x in body["labels_kept"]] == ["Projects", "Existing Sites"]
    assert body["stays_in_zoho"] == ["Attachments", "Quotes"]


async def test_no_good_run_says_so(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    with x0.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_sync_runs WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": x0.org_a})
    before = _hashes(x0)
    res = await _call(x0, _admin(x0.org_a), "GET", PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sync"]["last_good_at"] is None and body["sync"]["age_seconds"] is None
    assert all(r["zoho"] is None for r in body["records"])
    assert _hashes(x0) == before


async def test_native_org_gets_409(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    with x0.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native', connection_id = NULL "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": x0.org_a})
    res = await _call(x0, _admin(x0.org_a), "GET", PATH)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_mirror"


async def test_member_without_admin_gets_403(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    res = await _call(x0, _member("m@crm-t5-r8.test", x0.org_a), "GET", PATH)
    assert res.status_code == 403
