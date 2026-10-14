"""R8 — the lead-conversion mapping of a Native CRM, against a real Postgres
(WS-53 CRM-C6a, ``crm_platform.md`` §20.9).

Migration 261 adds ``crm_convert_mappings``. ``GET`` and ``PUT
/crm/conversion-mapping`` read and write the map, and ``POST
/crm/leads/{id}/convert`` applies it.

Each test runs through the real ``/crm`` router, as the non-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations.
One test, or a group, for each "Done when" item of CRM-C6a:

1. two mapped lead fields land on the contact and the deal, a skipped value
   is not copied, and org B does not change;
2. ``PUT`` refuses an incompatible pair, a missing and a retired target with
   422, and the stored map stays;
3. "auto" copies a same-key field, and a NULL row and no row agree;
4. a matched contact keeps each value it holds and gets each one it lacks;
5. a mapped value wins over a default, an inactive option is skipped with
   its reason, a hidden target is filled and a retired target is skipped;
6. 403 for a member, 404 for an org off the flag, 409 in Mirror mode;
7. the migration replays (the static RLS fence is in
   ``test_crm_convert_mapping.py``).

Also: a lead with no company makes no company, and with the flag off a
conversion copies nothing.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_field_studio_r8 import (  # noqa: F401
    _as_admin,
    _custom,
    _field,
    _record,
    studio,
)
from tests.unit.test_crm_pipelines_r8 import _member as _member_of
from tests.unit.test_crm_pipelines_r8 import _req
from tests.unit.test_crm_sync_engine_r8 import _q, ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
PATH = "/crm/conversion-mapping"


@pytest.fixture
def mapped(studio):
    """The studio fixture, and the mapping rows of both orgs removed after."""
    yield studio
    with studio.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_convert_mappings"))


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _put(org: str, rows: list[dict[str, Any]]) -> Any:
    return await _as_admin(org, "PUT", PATH, {"rows": rows})


async def _get(org: str) -> dict[str, Any]:
    got = await _as_admin(org, "GET", PATH)
    assert got.status_code == 200, got.text
    return got.json()


async def _convert(org: str, lead_id: str, body: dict[str, Any] | None = None) -> dict:
    got = await _as_admin(org, "POST", f"/crm/leads/{lead_id}/convert", body or {})
    assert got.status_code == 200, got.text
    return got.json()


def _stored(p: Any, org: str) -> list[Any]:
    return _q(p, "SELECT source_key, target_entity, target_key, skip FROM "
                 "crm_convert_mappings WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY source_key, target_entity", o=org)


def _org_b_state(p: Any) -> tuple[Any, ...]:
    records = tuple(
        _q(p, f"SELECT id, custom FROM {t} WHERE organization_id = CAST(:o AS uuid) "
              "ORDER BY id", o=p.org_b)
        for t in ("crm_leads", "crm_contacts", "crm_companies", "crm_deals")
    )
    return (*records, _stored(p, p.org_b))


def _cell(read: dict[str, Any], key: str, entity: str) -> dict[str, Any]:
    field = next(f for f in read["fields"] if f["key"] == key)
    return field["mapping"][entity]


# ── 1. Two mapped fields, a skip, and org B ─────────────────────────────────


async def test_two_mapped_fields_land_on_the_contact_and_the_deal(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Budget", "type": "number"})
    await _field(p.org_a, "leads", {"label": "Region", "type": "select", "options": [
        {"label": "North"}, {"label": "South"}]})
    await _field(p.org_a, "leads", {"label": "Secret", "type": "text"})
    await _field(p.org_a, "deals", {"label": "Deal budget", "type": "decimal"})
    await _field(p.org_a, "contacts", {"label": "Region", "type": "select", "options": [
        {"label": "North"}, {"label": "South"}]})
    await _field(p.org_a, "contacts", {"label": "Secret", "type": "text"})
    before_b = _org_b_state(p)

    put = await _put(p.org_a, [
        {"source_key": "budget", "target_entity": "deal", "target_key": "deal_budget"},
        {"source_key": "secret", "target_entity": "contact", "skip": True},
    ])
    assert put.status_code == 200, put.text
    read = put.json()
    assert _cell(read, "budget", "deal")["reason"] == "explicit"
    assert _cell(read, "region", "contact") == {
        "target_key": "region", "reason": "auto", "problem": None, "choices": ["region"],
    }
    assert _cell(read, "secret", "contact")["reason"] == "skip"
    assert _cell(read, "secret", "contact")["target_key"] is None

    lead = await _record(p.org_a, "leads", {
        "first_name": "Asha", "email": "asha@c6a.test",
        "custom": {"budget": 5000, "region": "north", "secret": "do not copy"},
    })
    body = await _convert(p.org_a, lead["id"])
    assert _custom(p, "deals", body["deal"]["id"]) == {"deal_budget": 5000}
    assert _custom(p, "contacts", body["contact"]["id"]) == {"region": "north"}
    assert body["skipped"] == []
    assert _custom(p, "leads", lead["id"])["secret"] == "do not copy", "the lead keeps it"
    assert _org_b_state(p) == before_b, "org B did not change"
    # Org B reads its own empty map. Row level security keeps org A's rows out.
    assert _q(p, "SELECT count(*) FROM crm_convert_mappings WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_b)[0][0] == 0
    assert (await _get(p.org_b))["fields"] == []


# ── 2. The PUT refuses, and writes nothing ──────────────────────────────────


async def test_the_put_refuses_a_bad_pair_and_keeps_the_map(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Note", "type": "text"})
    await _field(p.org_a, "deals", {"label": "Score", "type": "number"})
    await _field(p.org_a, "deals", {"label": "Memo", "type": "textarea"})
    old = await _field(p.org_a, "deals", {"label": "Old", "type": "text"})
    retired = await _as_admin(p.org_a, "DELETE", f"/crm/fields/deals/{old['key']}")
    assert retired.status_code == 200, retired.text
    ok = await _put(p.org_a, [
        {"source_key": "note", "target_entity": "deal", "target_key": "memo"}])
    assert ok.status_code == 200, ok.text
    kept = _stored(p, p.org_a)
    for target, code in (
        ("score", "mapping_type"),
        ("nope", "mapping_target"),
        ("old", "mapping_target_retired"),
    ):
        got = await _put(p.org_a, [
            {"source_key": "note", "target_entity": "deal", "target_key": target}])
        assert got.status_code == 422, (target, got.text)
        assert got.json()["detail"]["code"] == code, target
        assert _stored(p, p.org_a) == kept, f"{target}: the map did not change"


# ── 3. Auto, with a NULL row and with no row ────────────────────────────────


async def test_auto_copies_a_same_key_field_with_or_without_a_null_row(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Sector", "type": "text"})
    await _field(p.org_a, "deals", {"label": "Sector", "type": "textarea"})
    await _field(p.org_a, "leads", {"label": "Size", "type": "number"})
    await _field(p.org_a, "deals", {"label": "Size", "type": "text"})
    results = []
    for rows in ([], [{"source_key": "sector", "target_entity": "deal"},
                      {"source_key": "size", "target_entity": "deal"}]):
        assert (await _put(p.org_a, rows)).status_code == 200
        if rows:
            assert _stored(p, p.org_a) == [("sector", "deal", None, False),
                                           ("size", "deal", None, False)]
        lead = await _record(p.org_a, "leads", {
            "first_name": f"Auto {len(rows)}",
            "custom": {"sector": "Robotics", "size": 40},
        })
        body = await _convert(p.org_a, lead["id"])
        results.append(_custom(p, "deals", body["deal"]["id"]))
    # A number never fills a text field, so "size" is not copied.
    assert results == [{"sector": "Robotics"}, {"sector": "Robotics"}]


# ── 4. A matched contact keeps its values ───────────────────────────────────


async def test_a_matched_contact_keeps_its_values_and_gets_the_empty_ones(mapped) -> None:
    p = mapped
    for label in ("Region", "Tier"):
        await _field(p.org_a, "leads", {"label": label, "type": "text"})
        await _field(p.org_a, "contacts", {"label": label, "type": "text"})
    await _field(p.org_a, "organizations", {"label": "Tier", "type": "text"})
    contact = await _record(p.org_a, "contacts", {
        "first_name": "Held", "email": "held@c6a.test", "custom": {"region": "South"},
    })
    company = await _record(p.org_a, "organizations", {
        "name": "Held Co", "custom": {"tier": "Gold"},
    })
    lead = await _record(p.org_a, "leads", {
        "first_name": "Held", "email": "HELD@c6a.test", "organization_name": "Held Co",
        "custom": {"region": "North", "tier": "Silver"},
    })
    body = await _convert(p.org_a, lead["id"])
    assert body["contact"]["id"] == contact["id"], "matched by email"
    assert body["organization"]["id"] == company["id"], "matched by name"
    assert _custom(p, "contacts", contact["id"]) == {"region": "South", "tier": "Silver"}
    assert _custom(p, "organizations", company["id"]) == {"tier": "Gold"}
    reasons = {(s["entity"], s["target_key"], s["reason"]) for s in body["skipped"]}
    assert reasons == {
        ("contact", "region", "record_has_value"),
        ("company", "tier", "record_has_value"),
    }


async def test_a_record_that_the_caller_names_is_filled_the_same_way(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Tier", "type": "text"})
    await _field(p.org_a, "contacts", {"label": "Tier", "type": "text"})
    chosen = await _record(p.org_a, "contacts", {"first_name": "Chosen"})
    lead = await _record(p.org_a, "leads", {
        "first_name": "Other", "custom": {"tier": "Bronze"},
    })
    body = await _convert(p.org_a, lead["id"], {"contact_id": chosen["id"]})
    assert body["contact"]["id"] == chosen["id"]
    assert _custom(p, "contacts", chosen["id"]) == {"tier": "Bronze"}


# ── 5. Precedence and the skipped list ──────────────────────────────────────


async def test_precedence_options_hidden_and_retired_targets(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Stage note", "type": "text"})
    await _field(p.org_a, "leads", {"label": "Empty note", "type": "text"})
    await _field(p.org_a, "leads", {"label": "Grade", "type": "select", "options": [
        {"label": "Gold"}, {"label": "Silver"}]})
    await _field(p.org_a, "leads", {"label": "Quiet", "type": "text"})
    await _field(p.org_a, "leads", {"label": "Gone", "type": "text"})
    await _field(p.org_a, "deals", {"label": "Stage note", "type": "text",
                                    "default_value": "from default"})
    await _field(p.org_a, "deals", {"label": "Empty note", "type": "text",
                                    "default_value": "kept default"})
    # The deal picklist has no "gold" option: the value is skipped.
    await _field(p.org_a, "deals", {"label": "Grade", "type": "multiselect", "options": [
        {"label": "Silver"}, {"label": "Bronze"}]})
    await _field(p.org_a, "deals", {"label": "Quiet", "type": "text", "visible": False})
    await _field(p.org_a, "deals", {"label": "Gone", "type": "text"})
    put = await _put(p.org_a, [
        {"source_key": "gone", "target_entity": "deal", "target_key": "gone"}])
    assert put.status_code == 200, put.text
    # The target retires after the map names it.
    assert (await _as_admin(p.org_a, "DELETE", "/crm/fields/deals/gone")).status_code == 200
    assert _cell(await _get(p.org_a), "gone", "deal")["problem"] == "target_retired"

    lead = await _record(p.org_a, "leads", {
        "first_name": "Prec",
        "custom": {"stage_note": "from lead", "grade": "gold", "quiet": "hidden ok",
                   "gone": "no place"},
    })
    body = await _convert(p.org_a, lead["id"])
    assert _custom(p, "deals", body["deal"]["id"]) == {
        "stage_note": "from lead",       # the mapped value wins over the default
        "empty_note": "kept default",    # no lead value, so the default applies
        "quiet": "hidden ok",            # a hidden target is still filled
    }
    reasons = {(s["source_key"], s["reason"]) for s in body["skipped"]}
    assert reasons == {("grade", "option_inactive"), ("gone", "target_retired")}


async def test_a_select_fills_a_multiselect_as_a_list_of_one(mapped) -> None:
    p = mapped
    opts = {"options": [{"label": "Gold"}, {"label": "Silver"}]}
    await _field(p.org_a, "leads", {"label": "Grade", "type": "select", **opts})
    await _field(p.org_a, "deals", {"label": "Grade", "type": "multiselect", **opts})
    lead = await _record(p.org_a, "leads", {"first_name": "G", "custom": {"grade": "gold"}})
    body = await _convert(p.org_a, lead["id"])
    assert _custom(p, "deals", body["deal"]["id"]) == {"grade": ["gold"]}


async def test_a_lead_with_no_company_makes_no_company(mapped) -> None:
    p = mapped
    await _field(p.org_a, "leads", {"label": "Tier", "type": "text"})
    await _field(p.org_a, "organizations", {"label": "Tier", "type": "number"})
    await _field(p.org_a, "organizations", {"label": "Band", "type": "text"})
    await _put(p.org_a, [
        {"source_key": "tier", "target_entity": "company", "target_key": "band"}])
    before = _q(p, "SELECT count(*) FROM crm_companies WHERE organization_id = "
                   "CAST(:o AS uuid)", o=p.org_a)[0][0]
    lead = await _record(p.org_a, "leads", {"first_name": "Solo", "custom": {"tier": "A"}})
    body = await _convert(p.org_a, lead["id"])
    assert body["organization"] is None
    assert body["skipped"] == []
    assert _q(p, "SELECT count(*) FROM crm_companies WHERE organization_id = "
                 "CAST(:o AS uuid)", o=p.org_a)[0][0] == before


async def test_with_the_flag_off_a_conversion_copies_nothing(mapped, monkeypatch) -> None:
    from acb_common import get_settings

    p = mapped
    await _field(p.org_a, "leads", {"label": "Sector", "type": "text"})
    await _field(p.org_a, "deals", {"label": "Sector", "type": "text"})
    lead = await _record(p.org_a, "leads", {"first_name": "F", "custom": {"sector": "X"}})
    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", p.org_b, raising=False)
    body = await _convert(p.org_a, lead["id"])
    assert _custom(p, "deals", body["deal"]["id"]) == {}
    assert body["skipped"] == []


# ── 6. 403, 404 and 409 ─────────────────────────────────────────────────────


async def test_each_route_gives_403_404_and_409(mapped, monkeypatch) -> None:
    from acb_common import get_settings

    p = mapped
    await _field(p.org_a, "leads", {"label": "Note", "type": "text"})
    await _put(p.org_a, [{"source_key": "note", "target_entity": "deal", "skip": True}])
    kept = _stored(p, p.org_a)
    routes = [("GET", None), ("PUT", {"rows": []})]

    member = _member_of(p.org_a)
    for method, body in routes:
        got = await _req(member, method, PATH, body)
        assert got.status_code == 403, (method, got.text)

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", p.org_b, raising=False)
    for method, body in routes:
        got = await _as_admin(p.org_a, method, PATH, body)
        assert got.status_code == 404, (method, got.text)

    monkeypatch.setattr(get_settings(), "crm_field_studio_orgs", "*", raising=False)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'mirror' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    for method, body in routes:
        got = await _as_admin(p.org_a, method, PATH, body)
        assert got.status_code == 409, (method, got.text)
        assert got.json()["detail"]["code"] == "mirror_mode_settings"
    assert _stored(p, p.org_a) == kept


# ── 7. The migration ────────────────────────────────────────────────────────


def test_the_mapping_migration_is_found_by_content_and_replays(mapped) -> None:
    p = mapped
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and "CREATE TABLE IF NOT EXISTS crm_convert_mappings"
             in f.read_text(encoding="utf-8")]
    assert len(found) == 1
    with p.admin_engine.begin() as c:
        _exec_file(c, found[0])
        forced = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass('crm_convert_mappings')")).one()
    assert tuple(forced) == (True, True)


async def test_the_database_refuses_a_skip_row_with_a_target(mapped) -> None:
    """The CHECK of the migration, as the non-privileged role."""
    from acb_common.db import tenant_session
    from sqlalchemy.exc import DBAPIError

    p = mapped
    with pytest.raises(DBAPIError):
        async with tenant_session(p.org_a) as db:
            await db.execute(text(
                "INSERT INTO crm_convert_mappings (source_key, target_entity, "
                "target_key, skip) VALUES ('a', 'deal', 'b', true)"))
    with pytest.raises(DBAPIError):
        async with tenant_session(p.org_a) as db:
            await db.execute(text(
                "INSERT INTO crm_convert_mappings (organization_id, source_key, "
                "target_entity) VALUES (CAST(:o AS uuid), 'a', 'deal')"),
                {"o": p.org_b})
    assert uuid.UUID(p.org_a) != uuid.UUID(p.org_b)
