"""R8 — the overlay of an empty core slot reaches the aggregate (WS-53 CRM-Q20-3a).

Spec: ``project-docs/specs/crm_platform.md`` §18.13.5 and the CRM-Q20-3a
done-when list of §18.13.6.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. Org A mirrors a module
Commercials, and its approved plan says that Commercials plays Deals, with a
probability target, a close-date target, a stage map and a catalogue amount.
Org A holds no deal. So a total over ``deals`` reads the module.

1. ``count``, ``sum_amount`` through the catalogue amount, and ``weighted``.
2. The lane order of a status group, with one "Unsorted" group last.
3. ``close_month``, and the ``owner``, ``q`` and ``close_from`` filters.
4. Each 422 of the projection table, never a 500.
5. Native mode, and an Undo of the role: the answer of today, byte for byte.
6. Org B never reads the overlay of org A.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import (  # noqa: F401
    _admin,
    _agg,
    _call,
    _defs,
    _mirror_org,
    _module,
    _path,
    _record,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_OPTIONS = [{"value": v, "label": label} for v, label in (
    ("Proposal", "Proposal"), ("Open", "Open stage"), ("Won", "Won"), ("Lost", "Lost"),
    ("On Hold", "On Hold"),
)]

_DEFS = [
    ("Name", "text", None, False, False),
    ("Value", "currency", None, False, False),
    ("Win_Pct", "percent", None, False, False),
    ("Close_On", "date", None, False, False),
    ("Stage", "select", _OPTIONS, False, False),
]

#: ``(ext id, owner, Value, Win_Pct, Close_On, Stage)``. "Weird" is no option.
_ROWS = [
    ("c1", "a@q203.test", 100, 50, "2026-01-10", "Proposal"),
    ("c2", "b@q203.test", 200, 25, "2026-02-05", "Open"),
    ("c3", "a@q203.test", 300, 100, "2026-02-20", "Won"),
    ("c4", "b@q203.test", 400, 0, "2026-03-01", "Lost"),
    ("c5", "a@q203.test", 50, 10, "2026-03-15", "On Hold"),
    ("c6", None, 10, 10, None, ""),
    ("c7", "a@q203.test", 5, 50, "2026-01-02", "Weird"),
]

ROLE = {"kind": "role", "slot": "deal", "module": "Commercials", "profile_version": 1}
PROBABILITY = {"kind": "target", "slot": "deal", "module": "Commercials",
               "target": "probability", "fields": ["Win_Pct"], "transform": "cast"}
CLOSE = {"kind": "target", "slot": "deal", "module": "Commercials",
         "target": "expected_close_date", "fields": ["Close_On"], "transform": "cast"}
STAGE = {"kind": "stage", "slot": "deal", "module": "Commercials", "field_api_name": "Stage",
         "values": {"Proposal": "open", "Open": "open", "Won": "won", "Lost": "lost",
                    "Weird": "open"}}
AMOUNT = {"kind": "catalogue", "module": "Commercials", "amount": "Value",
          "by": {"amount": "ai"}}
ALL = [ROLE, PROBABILITY, CLOSE, STAGE, AMOUNT]


def _plan(p: Any, org: str, items: list[dict[str, Any]]) -> None:
    """The approved plan of *org*, with *items*, written as the superuser."""
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_mapping_plans WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": org})
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, rules, "
            "created_by, approved_by, approved_at) VALUES (CAST(:o AS uuid), 1, "
            "'approved', CAST(:r AS jsonb), 'r8', 'r8', now())"),
            {"o": org, "r": json.dumps(items)})


def _seed(p: Any, org: str) -> None:
    _mirror_org(p, org, [_module("Commercials")])
    _defs(p, org, "module:Commercials", _DEFS)
    with p.admin_engine.begin() as c:
        for ext, owner, value, pct, close, stage in _ROWS:
            fields: dict[str, Any] = {"Name": ext, "Value": value, "Win_Pct": pct,
                                      "Stage": stage}
            if close:
                fields["Close_On"] = close
            _record(c, org, "Commercials", ext, owner, fields)


@pytest.fixture
def ov(ns, monkeypatch):
    """Org A: Commercials plays Deals. The catalogue serves each org."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "*", raising=False)
    _seed(ns, ns.org_a)
    _plan(ns, ns.org_a, ALL)
    yield ns
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            c.execute(text("DELETE FROM crm_mapping_plans WHERE organization_id = "
                           "CAST(:o AS uuid)"), {"o": org})


def _groups(body: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(g["key"], g["label"], g["value"], g.get("type")) for g in body["groups"]]


async def test_count_sum_amount_and_weighted_read_the_module(ov, app_engine) -> None:
    a = ov.org_a
    count = await _agg(ov, a, entity="deals", group_by="owner")
    assert count["total"] == 7 and count["entity"] == "deals"
    assert count["overlay"] == {"slot": "deal", "module": "Commercials"}
    money = await _agg(ov, a, entity="deals", measure="sum_amount", group_by="owner")
    # The catalogue amount: the sum of the real field Value.
    assert money["total"] == 1065.0
    assert (money["field"], money["field_label"]) == ("Value", "Value")
    weighted = await _agg(ov, a, entity="deals", measure="weighted", group_by="owner")
    # Proposal, Open and Weird map to open: 100 x 50% + 200 x 25% + 5 x 50%.
    assert weighted["total"] == 102.5


async def test_no_group_reads_the_module_as_one_total(ov, app_engine) -> None:
    """PR #1126: a total with no group over an overlaid slot is one row of
    the module, with the count of records. It never fails on a missing key."""
    a = ov.org_a
    count = await _agg(ov, a, entity="deals")
    assert (count["groups"], count["total"], count["records"]) == ([], 7, 7)
    assert count["overlay"] == {"slot": "deal", "module": "Commercials"}
    money = await _agg(ov, a, entity="deals", measure="sum_amount")
    assert (money["total"], money["records"]) == (1065.0, 7)
    weighted = await _agg(ov, a, entity="deals", measure="weighted")
    assert weighted["total"] == 102.5 and weighted["records"] is None
    mine = await _agg(ov, a, entity="deals", owner="b@q203.test")
    assert (mine["total"], mine["records"]) == (2, 2)


async def test_the_status_group_follows_the_lane_order(ov, app_engine) -> None:
    """Open, then won, then lost, in the option order of Zoho, then one
    Unsorted lane: a value with no entry, a value that is no option, and a
    blank value."""
    body = await _agg(ov, ov.org_a, entity="deals", group_by="status")
    assert _groups(body) == [
        ("Proposal", "Proposal", 1.0, "open"),
        ("Open", "Open stage", 1.0, "open"),
        ("Won", "Won", 1.0, "won"),
        ("Lost", "Lost", 1.0, "lost"),
        (None, "Unsorted", 3.0, "unsorted"),
    ]
    assert body["total"] == 7


async def test_close_month_and_the_filters(ov, app_engine) -> None:
    a = ov.org_a
    months = await _agg(ov, a, entity="deals", group_by="close_month")
    assert [(g["key"], g["value"]) for g in months["groups"]] == [
        ("2026-01", 2), ("2026-02", 2), ("2026-03", 2), (None, 1),
    ]
    mine = await _agg(ov, a, entity="deals", group_by="owner", owner="A@q203.test")
    assert mine["total"] == 4
    named = await _agg(ov, a, entity="deals", group_by="owner", q="c3")
    assert named["total"] == 1
    later = await _agg(ov, a, entity="deals", group_by="owner", close_from="2026-02-01")
    assert later["total"] == 4
    keyed = await _agg(ov, a, entity="deals", group_by="owner", **{"field.Stage": "Won"})
    assert keyed["total"] == 1


@pytest.mark.parametrize(("items", "query", "code"), [
    ([ROLE, PROBABILITY, CLOSE, STAGE], {"measure": "sum_amount", "group_by": "owner"},
     "overlay_no_amount"),
    ([ROLE, CLOSE, STAGE, AMOUNT], {"measure": "weighted", "group_by": "owner"},
     "overlay_no_probability"),
    ([ROLE, PROBABILITY, CLOSE, AMOUNT], {"group_by": "status"}, "overlay_no_stage"),
    ([ROLE, PROBABILITY, STAGE, AMOUNT], {"group_by": "close_month"}, "overlay_no_date"),
    ([ROLE, PROBABILITY, STAGE, AMOUNT], {"group_by": "owner", "close_to": "2026-01-01"},
     "overlay_no_date"),
    (ALL, {"group_by": "pipeline"}, "overlay_unfit"),
    (ALL, {"group_by": "source"}, "overlay_unfit"),
    (ALL, {"group_by": "owner", "status_id": str(uuid.uuid4())}, "overlay_filter"),
    (ALL, {"group_by": "owner", "custom.cf_x": "1"}, "overlay_filter"),
    (ALL, {"group_by": "owner", "pipeline_id": str(uuid.uuid4())}, "overlay_filter"),
    (ALL, {"group_by": "owner", "company_id": str(uuid.uuid4())}, "overlay_filter"),
    (ALL, {"group_by": "owner", "contact_id": str(uuid.uuid4())}, "overlay_filter"),
])
async def test_each_refusal_of_the_table_is_its_422(ov, app_engine, items, query, code) -> None:
    _plan(ov, ov.org_a, items)
    res = await _call(ov, _admin(ov.org_a), "GET", _path(entity="deals", **query))
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == code
    assert "Commercials" in res.json()["detail"]["message"]


async def test_an_undo_of_the_role_gives_the_answer_of_today(ov, app_engine) -> None:
    """Item 4 and item 6: with no overlay, byte for byte the answer of today."""
    a = ov.org_a
    _plan(ov, a, [])
    queries = ({"group_by": "owner"}, {"measure": "sum_amount", "group_by": "owner"},
               {"group_by": "status"})
    today = [(await _call(ov, _admin(a), "GET", _path(entity="deals", **q))).content
             for q in queries]
    assert b"overlay" not in b"".join(today)
    _plan(ov, a, ALL)
    assert (await _agg(ov, a, entity="deals", group_by="owner"))["total"] == 7
    _plan(ov, a, [PROBABILITY, AMOUNT])  # Undo: the role leaves the plan.
    again = [(await _call(ov, _admin(a), "GET", _path(entity="deals", **q))).content
             for q in queries]
    assert again == today


async def test_native_mode_has_no_overlay(ov, app_engine) -> None:
    """A Native CRM mirrors no module, so a role in its plan reads nothing."""
    b = ov.org_b
    res = await _call(ov, _admin(b), "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    _plan(ov, b, ALL)
    body = await _agg(ov, b, entity="deals", group_by="owner")
    assert body["total"] == 0 and "overlay" not in body


async def test_org_b_never_reads_the_overlay_of_org_a(ov, app_engine) -> None:
    """Org B mirrors a module of the same name and has no role. Its deals stay
    its own, and nothing of org A reaches it."""
    b = ov.org_b
    _mirror_org(ov, b, [_module("Commercials")])
    _defs(ov, b, "module:Commercials", _DEFS)
    with ov.admin_engine.begin() as c:
        _record(c, b, "Commercials", "b1", "x@q203.test", {"Value": 99999, "Stage": "Won"})
    body = await _agg(ov, b, entity="deals", group_by="owner")
    assert body["total"] == 0 and "overlay" not in body
    assert "a@q203.test" not in json.dumps(body)
    # Org A still reads its own module, and never org B's record.
    mine = await _agg(ov, ov.org_a, entity="deals", measure="sum_amount", group_by="owner")
    assert mine["total"] == 1065.0


async def test_the_digest_of_an_overlaid_org_reads_the_lanes(ov, app_engine) -> None:
    """Fix round, item 4: with an approved deal role, the typed parts of the
    weekly digest read the lane type of the answer. The Unsorted lane is a
    stated gap, and the part names the module."""
    from datetime import date

    from acb_common.db import bind_tenant, release_tenant
    from gateway.crm_sync import digest

    from tests.unit._tenant_ladder import tenant_engine_scope

    week = digest.Week(start=date(2026, 10, 5), end=date(2026, 10, 11), zone="UTC",
                       close_from=date(2026, 10, 1), close_to=date(2026, 12, 31))
    token = bind_tenant(ov.org_a)
    try:
        async with tenant_engine_scope(ov.app_url.render_as_string(hide_password=False)):
            numbers = await digest.gather(ov.org_a, week)
    finally:
        release_tenant(token)
    parts = {p["key"]: p for p in numbers["parts"]}
    count, amount = parts["open_deals_by_stage"], parts["open_amount_by_stage"]
    assert count["rows"] == [{"label": "Proposal", "value": "1"},
                             {"label": "Open stage", "value": "1"}]
    assert (count["unsorted"], count["from_module"]) == ("3", "Commercials")
    assert [r["label"] for r in amount["rows"]] == ["Proposal", "Open stage"]
    assert amount["unsorted"] == "INR 65"


# ── CRM-Q32-9a: the overlay reads a sum (crm_platform.md §18.14.10, item 5) ──

#: The admin stored the amount of Commercials as the sum of Value and Extra.
AMOUNT_SUM = {"kind": "catalogue", "module": "Commercials", "amount": ["Value", "Extra"],
              "by": {"amount": "admin"}}

#: Extra on some rows: a number, a numeric string, nothing, and a blank.
_EXTRA = {"c1": 10, "c2": "20", "c7": 1, "c4": ""}


def _with_extra(p: Any, org: str) -> None:
    _defs(p, org, "module:Commercials", [("Extra", "currency", None, False, False)])
    with p.admin_engine.begin() as c:
        for ext, value in _EXTRA.items():
            c.execute(text(
                "UPDATE crm_module_records SET fields = fields || CAST(:f AS jsonb) "
                "WHERE organization_id = CAST(:o AS uuid) AND ext_id = :e"),
                {"o": org, "e": ext, "f": json.dumps({"Extra": value})})


async def test_the_overlay_sum_amount_and_weighted_read_a_sum(ov, app_engine) -> None:
    """sum_amount and weighted of an overlaid slot read the sum of the
    parts, through the one builder."""
    a = ov.org_a
    _with_extra(ov, a)
    _plan(ov, a, [ROLE, PROBABILITY, CLOSE, STAGE, AMOUNT_SUM])
    money = await _agg(ov, a, entity="deals", measure="sum_amount")
    # 1065 of Value, and 10 + 20 + 1 of Extra.
    assert (money["total"], money["records"]) == (1096.0, 7)
    assert (money["field"], money["field_type"]) == ("Value+Extra", "currency")
    weighted = await _agg(ov, a, entity="deals", measure="weighted")
    # Proposal, Open and Weird are open: 110 x 50% + 220 x 25% + 6 x 50%.
    assert weighted["total"] == 113.0
    grouped = await _call(ov, _admin(a), "GET", _path(entity="deals", group_field="@amount"))
    assert grouped.status_code == 422, grouped.text
    assert grouped.json()["detail"]["code"] == "composite_group"


async def test_the_digest_reads_a_sum_with_no_change_of_its_own(ov, app_engine) -> None:
    """The digest reads its totals through the aggregate, so the sum
    reaches it: the Unsorted lane holds 50 + 10 + 5 + 1."""
    from datetime import date

    from acb_common.db import bind_tenant, release_tenant
    from gateway.crm_sync import digest

    from tests.unit._tenant_ladder import tenant_engine_scope

    _with_extra(ov, ov.org_a)
    _plan(ov, ov.org_a, [ROLE, PROBABILITY, CLOSE, STAGE, AMOUNT_SUM])
    week = digest.Week(start=date(2026, 10, 5), end=date(2026, 10, 11), zone="UTC",
                       close_from=date(2026, 10, 1), close_to=date(2026, 12, 31))
    token = bind_tenant(ov.org_a)
    try:
        async with tenant_engine_scope(ov.app_url.render_as_string(hide_password=False)):
            numbers = await digest.gather(ov.org_a, week)
    finally:
        release_tenant(token)
    amount = {p["key"]: p for p in numbers["parts"]}["open_amount_by_stage"]
    assert amount["unsorted"] == "INR 66"
