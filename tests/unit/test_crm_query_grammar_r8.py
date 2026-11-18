"""R8 — SEM-1b-1 (task 3736): ``query`` on a module total, on a real Postgres.

Spec: ``project-docs/specs/semantic_model.md`` §5.1, "Acceptance of
SEM-1b-1" (a) to (h).

Against a REAL Postgres, through the real ``/crm`` router, as the app role
of the phase-4 rehearsal, so FORCE RLS binds each read. The seed goes in as
the superuser.

(a) Each seed gives the same total and count three ways: the old parameters
    off the flag, the old parameters on the flag, and the equal ``query``.
(b) An ``any`` group ORs its leaves.
(c) A datetime day in a CRM zone other than UTC counts across midnight as
    the old range does.
(d) A currency stored as a numeric string sums and filters like a number,
    and "NA" counts as no value.
(e) A lookup by name and by Metorite id matches as ``field.<key>`` does.
(f) The records of org B never count for org A.
(g) An unknown key gives a 422 that names the legal fields.
(h) A ``query`` on a core entity, and from an org off the flag, each give a
    422 that names a working step.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import (  # noqa: F401
    _admin,
    _call,
    _defs,
    _mirror_org,
    _module,
    _path,
    _set_zone,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ZONE = "Asia/Kolkata"

_INVOICE_DEFS = [
    ("Grand_Total", "currency", None, False, False),
    ("Status", "select", None, False, False),
    ("Paid_On", "datetime", None, False, False),
    ("Invoice_Date", "date", None, False, False),
    ("Subject", "text", None, False, False),
    ("Account", "lookup", {"lookup_module": "Vendors"}, False, False),
]

_ACME = {"id": "z-a1", "name": "Acme"}
_BETA = {"id": "z-a2", "name": "Beta"}

#: The invoices of org A. ``Grand_Total`` holds a JSON number, a numeric
#: string and "NA". ``Paid_On`` holds instants on each side of midnight in
#: the CRM zone (Asia/Kolkata), so a UTC day and a zone day differ.
_INVOICES: list[tuple[str, dict[str, Any]]] = [
    ("i1", {"Grand_Total": 100, "Status": "Paid", "Paid_On": "2026-11-01T00:30:00+05:30",
            "Invoice_Date": "2026-11-01", "Subject": "Rent", "Account": _ACME}),
    ("i2", {"Grand_Total": "250.5", "Status": "paid ", "Paid_On": "2026-10-31T23:30:00+05:30",
            "Invoice_Date": "2026-10-31", "Subject": "Fees", "Account": _ACME}),
    ("i3", {"Grand_Total": "NA", "Status": "Sent", "Paid_On": "2026-11-02T10:00:00+05:30",
            "Invoice_Date": "2026-11-02", "Subject": "Rent", "Account": _BETA}),
    ("i4", {"Grand_Total": 40, "Status": "Draft", "Paid_On": "2026-10-31T20:00:00Z",
            "Invoice_Date": "2026-11-01", "Subject": "Power", "Account": _BETA}),
    ("i5", {"Grand_Total": 7, "Status": "Sent", "Subject": "Rent"}),
]


def _record(c: Any, org: str, module: str, ext_id: str, name: str,
            fields: dict[str, Any]) -> str:
    return str(c.execute(text(
        "INSERT INTO crm_module_records (organization_id, module, ext_source, "
        "ext_id, name, owner_email, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', "
        ":e, :n, 'a@sem1b.test', CAST(:f AS jsonb)) RETURNING id"),
        {"o": org, "m": module, "e": ext_id, "n": name, "f": json.dumps(fields)}).scalar())


@pytest.fixture
def sem(ns, monkeypatch):
    """Org A and org B each mirror Invoices and Vendors, in the CRM zone
    Asia/Kolkata. Org B holds an invoice that matches each filter, with a
    total no org A sum can reach. The flag list starts empty."""
    from acb_common import get_settings

    a, b = ns.org_a, ns.org_b
    for org in (a, b):
        _mirror_org(ns, org, [_module("Invoices"), _module("Vendors")])
        _defs(ns, org, "module:Invoices", _INVOICE_DEFS)
        _set_zone(ns, org, _ZONE)
    with ns.admin_engine.begin() as c:
        for ext_id, fields in _INVOICES:
            _record(c, a, "Invoices", ext_id, ext_id, fields)
        ns.acme_a = _record(c, a, "Vendors", "z-a1", "Acme", {})
        _record(c, b, "Invoices", "b1", "b1", {
            "Grand_Total": 99999, "Status": "Paid", "Paid_On": "2026-11-01T00:30:00+05:30",
            "Invoice_Date": "2026-11-01", "Subject": "Rent", "Account": _ACME})
        ns.acme_b = _record(c, b, "Vendors", "z-a1", "Acme", {})
    settings = get_settings()

    def flag(value: str) -> None:
        monkeypatch.setattr(settings, "semantic_model_orgs", value, raising=False)

    ns.flag = flag
    flag("")
    return ns


async def _sum(p: Any, org: str, **query: Any) -> tuple[Any, int, int]:
    """``(total, records, values)`` of the sum of ``Grand_Total``."""
    res = await _call(p, _admin(org), "GET", _path(
        entity="module:Invoices", measure="sum", field="Grand_Total", **query))
    assert res.status_code == 200, (query, res.text)
    body = res.json()
    return body["total"], body["records"], body["values"]


async def _refusal(p: Any, org: str, **query: Any) -> dict[str, Any]:
    res = await _call(p, _admin(org), "GET", _path(**query))
    assert res.status_code == 422, (query, res.text)
    detail = res.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail


def _leaf(key: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": key, "op": op, "value": value}


# ── (a) The same total and count three ways ──────────────────────────────────

#: Each seed: the old parameters, and the equal query by the lowering table.
_SEEDS: list[tuple[dict[str, str], list[dict[str, Any]]]] = [
    ({"field.Grand_Total.from": "50"}, [_leaf("Grand_Total", "gte", 50)]),
    ({"field.Grand_Total.to": "100"}, [_leaf("Grand_Total", "lte", "100")]),
    ({"field.Grand_Total": "40"}, [_leaf("Grand_Total", "eq", 40)]),
    ({"field.Status": "Paid"}, [_leaf("Status", "eq", "Paid")]),
    ({"field.Subject": "rent"}, [_leaf("Subject", "eq", "rent")]),
    ({"field.Invoice_Date": "2026-11-01"}, [_leaf("Invoice_Date", "eq", "2026-11-01")]),
    ({"field.Invoice_Date.from": "2026-11-01", "field.Invoice_Date.to": "2026-11-02"},
     [_leaf("Invoice_Date", "gte", "2026-11-01"), _leaf("Invoice_Date", "lte", "2026-11-02")]),
    ({"field.Paid_On.from": "2026-11-01", "field.Paid_On.to": "2026-11-01"},
     [_leaf("Paid_On", "gte", "2026-11-01"), _leaf("Paid_On", "lte", "2026-11-01")]),
    ({"field.Account": "Acme"}, [_leaf("Account", "eq", "Acme")]),
    ({"field.Status": "Sent", "field.Grand_Total.from": "1"},
     [_leaf("Status", "eq", "Sent"), _leaf("Grand_Total", "gte", 1)]),
]


@pytest.mark.parametrize(("old", "query"), _SEEDS)
async def test_a_the_same_total_and_count_three_ways(sem, app_engine, old: dict,
                                                     query: list) -> None:
    a = sem.org_a
    off = await _sum(sem, a, **old)
    sem.flag(a)
    on = await _sum(sem, a, **old)
    by_query = await _sum(sem, a, query=json.dumps(query))
    assert off == on == by_query, (old, off, on, by_query)
    # The query and the old parameters join by AND: the same filter twice
    # changes nothing.
    assert await _sum(sem, a, query=json.dumps(query), **old) == off


async def test_a_the_counts_are_the_known_ones(sem, app_engine) -> None:
    """The three ways agree, and they agree on the right numbers."""
    sem.flag(sem.org_a)
    a = sem.org_a
    assert await _sum(sem, a, query=json.dumps([_leaf("Status", "eq", "Paid")])) == (
        350.5, 2, 2)
    assert await _sum(sem, a, query=json.dumps([_leaf("Subject", "eq", "rent")])) == (
        107.0, 3, 2)


# ── (b) An `any` group ORs its leaves ────────────────────────────────────────


async def test_b_an_any_group_ors_its_leaves(sem, app_engine) -> None:
    a = sem.org_a
    sem.flag(a)
    paid = await _sum(sem, a, query=json.dumps([_leaf("Status", "eq", "Paid")]))
    sent = await _sum(sem, a, query=json.dumps([_leaf("Status", "eq", "Sent")]))
    either = await _sum(sem, a, query=json.dumps(
        [{"any": [_leaf("Status", "eq", "Paid"), _leaf("Status", "eq", "Sent")]}]))
    assert either[1] == paid[1] + sent[1] == 4
    assert either[0] == 350.5 + 7
    # `in` is the same group.
    assert await _sum(sem, a, query=json.dumps(
        [_leaf("Status", "in", ["Paid", "Sent"])])) == either
    # An AND of the two leaves is empty, so the group is no AND.
    both = await _sum(sem, a, query=json.dumps(
        [_leaf("Status", "eq", "Paid"), _leaf("Status", "eq", "Sent")]))
    assert both[1] == 0
    # A group ANDs with the other nodes.
    narrowed = await _sum(sem, a, query=json.dumps([
        {"any": [_leaf("Status", "eq", "Paid"), _leaf("Status", "eq", "Sent")]},
        _leaf("Grand_Total", "gt", 100)]))
    assert narrowed == (250.5, 1, 1)


async def test_b_a_group_mixes_fields_and_ops(sem, app_engine) -> None:
    a = sem.org_a
    sem.flag(a)
    body = await _sum(sem, a, query=json.dumps([{"any": [
        _leaf("Grand_Total", "lt", 10), _leaf("Account", "eq", "Acme"),
    ]}]))
    # i5 (7), and i1 and i2 of Acme.
    assert body == (357.5, 3, 3)


# ── (c) A datetime day in the CRM zone ───────────────────────────────────────


async def test_c_a_datetime_day_counts_in_the_crm_zone(sem, app_engine) -> None:
    """i1 (00:30) and i4 (01:30) fall on 1 November in Asia/Kolkata. Each is
    still 31 October in UTC. i2 (23:30 on 31 October) does not count."""
    a = sem.org_a
    old = await _sum(sem, a, **{"field.Paid_On.from": "2026-11-01",
                                "field.Paid_On.to": "2026-11-01"})
    sem.flag(a)
    day = [_leaf("Paid_On", "gte", "2026-11-01"), _leaf("Paid_On", "lte", "2026-11-01")]
    assert await _sum(sem, a, query=json.dumps(day)) == old == (140.0, 2, 2)
    strict = [_leaf("Paid_On", "gt", "2026-10-31"), _leaf("Paid_On", "lt", "2026-11-02")]
    assert await _sum(sem, a, query=json.dumps(strict)) == old


async def test_c_in_utc_the_same_day_holds_no_record(sem, app_engine) -> None:
    """The fence on the fence: with no zone, the day of (c) is empty."""
    a = sem.org_a
    _set_zone(sem, a, None)
    sem.flag(a)
    day = [_leaf("Paid_On", "gte", "2026-11-01"), _leaf("Paid_On", "lte", "2026-11-01")]
    assert (await _sum(sem, a, query=json.dumps(day)))[1] == 0


# ── (d) A currency as a numeric string, and "NA" ─────────────────────────────


async def test_d_a_numeric_string_filters_and_sums_like_a_number(sem, app_engine) -> None:
    a = sem.org_a
    sem.flag(a)
    for value in (200, "200", 200.0):
        assert await _sum(sem, a, query=json.dumps([_leaf("Grand_Total", "gte", value)])) == (
            250.5, 1, 1)
    # "NA" is no value: no end of a range takes it, and the sum skips it.
    every = await _sum(sem, a, query=json.dumps([_leaf("Grand_Total", "gte", -1000)]))
    assert every == (397.5, 4, 4)
    allrows = await _sum(sem, a)
    assert allrows == (397.5, 5, 4)


async def test_d_a_bool_on_a_currency_is_a_422(sem, app_engine) -> None:
    sem.flag(sem.org_a)
    detail = await _refusal(sem, sem.org_a, entity="module:Invoices",
                            query=json.dumps([_leaf("Grand_Total", "eq", True)]))
    assert detail["code"] == "query_value_invalid"


# ── (e) A lookup by name and by Metorite id ──────────────────────────────────


async def test_e_a_lookup_by_name_and_by_metorite_id(sem, app_engine) -> None:
    a = sem.org_a
    by_name_old = await _sum(sem, a, **{"field.Account": "Acme"})
    by_id_old = await _sum(sem, a, **{"field.Account": sem.acme_a})
    sem.flag(a)
    by_name = await _sum(sem, a, query=json.dumps([_leaf("Account", "eq", "Acme")]))
    by_id = await _sum(sem, a, query=json.dumps([_leaf("Account", "eq", sem.acme_a)]))
    assert by_name == by_name_old == by_id == by_id_old == (350.5, 2, 2)
    # The id of org B's Acme is no record of org A, so it finds no Zoho id.
    other = await _sum(sem, a, query=json.dumps([_leaf("Account", "eq", sem.acme_b)]))
    assert other[1] == 0


# ── (f) Org B never counts for org A ─────────────────────────────────────────


async def test_f_org_b_never_counts_for_org_a(sem, app_engine) -> None:
    sem.flag("*")
    for query in (
        [_leaf("Status", "eq", "Paid")],
        [_leaf("Grand_Total", "gte", 1000)],
        [_leaf("Account", "eq", "Acme")],
        [{"any": [_leaf("Status", "eq", "Paid"), _leaf("Subject", "eq", "Rent")]}],
    ):
        total, _records, _values = await _sum(sem, sem.org_a, query=json.dumps(query))
        assert total is None or total < 99999, (query, total)
        b_total, b_records, _ = await _sum(sem, sem.org_b, query=json.dumps(query))
        assert b_total == 99999.0 and b_records == 1, query


# ── (g) An unknown key ───────────────────────────────────────────────────────


async def test_g_an_unknown_key_names_the_legal_fields(sem, app_engine) -> None:
    sem.flag(sem.org_a)
    detail = await _refusal(sem, sem.org_a, entity="module:Invoices",
                            query=json.dumps([_leaf("Grand_Totl", "gte", 1)]))
    assert detail["code"] == "unknown_field"
    assert "Its fields are:" in detail["message"]
    assert "Grand_Total" in detail["message"]
    assert "Account" in detail["message"]


@pytest.mark.parametrize("op", ["ne", "is_empty", "contains", "matches"])
async def test_g_a_deferred_op_names_the_legal_ops(sem, app_engine, op: str) -> None:
    sem.flag(sem.org_a)
    detail = await _refusal(sem, sem.org_a, entity="module:Invoices",
                            query=json.dumps([_leaf("Status", op, "Paid")]))
    assert detail["code"] == "query_op_not_allowed"
    assert "eq, in, gte, lte, gt, lt" in detail["message"]


# ── (h) A core entity, and an org off the flag ───────────────────────────────


async def test_h_a_query_on_a_core_entity_names_crm_list_records(sem, app_engine) -> None:
    sem.flag(sem.org_a)
    detail = await _refusal(sem, sem.org_a, entity="deals",
                            query=json.dumps([_leaf("Amount", "gte", 1)]))
    assert detail["code"] == "query_needs_module"
    assert "crm_list_records" in detail["message"]


async def test_h_a_query_off_the_flag_names_field_filters(sem, app_engine) -> None:
    sem.flag(sem.org_b)
    detail = await _refusal(sem, sem.org_a, entity="module:Invoices",
                            query=json.dumps([_leaf("Status", "eq", "Paid")]))
    assert detail["code"] == "query_off"
    assert "field_filters" in detail["message"]
    # Off the flag, a total with no query answers as before.
    assert await _sum(sem, sem.org_a, **{"field.Status": "Paid"}) == (350.5, 2, 2)
