"""R8 — two group fields and pages of groups on a real Postgres (CRM-Q34-10).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, CRM-Q34-10.

Through the real ``/crm`` router, as the non-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS). The seed goes in as the superuser.
A hermetic fake agrees with any SQL that it gets, so none of these is
proved without the database.

1. A site and a month: each group equals an independent SQL ``GROUP BY``,
   over every page.
2. The pages are disjoint and in order, and together they are every group.
   ``group_count`` is the independent count. A page past the end is empty.
3. A filter counts the same records as the module list with that filter.
4. A record with no site and a record with no date make their own groups,
   and ``total`` is the sum over every record.
5. No row of organization B shows.
6. Off ``CRM_MODULE_FIND_ORGS``: a 422 for ``group_field2``, and a call with
   one group field is the answer of the base code, byte for byte.
7. A statement past the timeout is the 503 ``aggregate_timeout``.
8. 30,000 records in 200 groups answer inside the 10 s timeout.

The hermetic rules are in ``test_crm_two_group.py``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import (  # noqa: F401
    _admin,
    _call,
    _defs,
    _mirror_org,
    _module,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_INVOICE_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Invoice_Date", "date", None, False, False),
    ("Amount", "currency", None, False, False),
    ("Status", "select", [{"label": "Paid", "value": "Paid"},
                          {"label": "Due", "value": "Due"}], False, False),
]

#: 1,000 invoices over 120 sites and 4 months, and two odd records.
_SEED_SQL = (
    "INSERT INTO crm_module_records (organization_id, module, ext_source, "
    "ext_id, name, owner_email, fields) "
    "SELECT CAST(:o AS uuid), 'Invoices', 'zoho', 'inv' || g, 'Inv ' || g, NULL, "
    "jsonb_build_object("
    "'Site', jsonb_build_object('id', 's' || (g % CAST(:sites AS int)), "
    "'name', 'Site ' || (g % CAST(:sites AS int))), "
    "'Invoice_Date', '2026-' || lpad(CAST(7 + ((g / CAST(:sites AS int)) % 4) AS text), "
    "2, '0') || '-15', "
    "'Amount', (g % 97) + 1, "
    "'Status', CASE WHEN g % 3 = 0 THEN 'Paid' ELSE 'Due' END) "
    "FROM generate_series(1, :n) AS g"
)

#: The independent answer: the site id and the month of each group, its
#: sum of Amount and its count. Written apart from ``field_sql``.
_TRUTH_SQL = (
    "SELECT CASE WHEN jsonb_typeof(fields -> 'Site') = 'object' "
    "THEN fields -> 'Site' ->> 'id' ELSE fields ->> 'Site' END AS k1, "
    "to_char(CAST(fields ->> 'Invoice_Date' AS date), 'YYYY-MM') AS k2, "
    "sum(CAST(fields ->> 'Amount' AS numeric)) AS v, count(*) AS c "
    "FROM crm_module_records WHERE organization_id = CAST(:o AS uuid) "
    "AND module = 'Invoices'{extra} GROUP BY 1, 2"
)


def _set(monkeypatch: Any, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_module_find_orgs", value, raising=False)


def _record(c: Any, org: str, ext_id: str, fields: dict[str, Any]) -> None:
    c.execute(text(
        "INSERT INTO crm_module_records (organization_id, module, ext_source, "
        "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Invoices', 'zoho', :e, :e, "
        "CAST(:f AS jsonb))"), {"o": org, "e": ext_id, "f": json.dumps(fields)})


@pytest.fixture
def invoices(ns, monkeypatch):
    """Org A: 1,000 invoices over 120 sites and 4 months, one with no site
    and one with no date. Org B: one invoice of its own site."""
    a, b = ns.org_a, ns.org_b
    for org in (a, b):
        _mirror_org(ns, org, [_module("Invoices")])
        _defs(ns, org, "module:Invoices", _INVOICE_DEFS)
    with ns.admin_engine.begin() as c:
        c.execute(text(_SEED_SQL), {"o": a, "n": 1000, "sites": 120})
        _record(c, a, "nosite", {"Invoice_Date": "2026-07-01", "Amount": 5, "Status": "Due"})
        _record(c, a, "nodate", {"Site": {"id": "s1", "name": "Site 1"}, "Amount": 7,
                                 "Status": "Paid"})
        _record(c, b, "b1", {"Site": {"id": "sB", "name": "Secret Site"},
                             "Invoice_Date": "2026-07-02", "Amount": 99999})
    _set(monkeypatch, "*")
    return ns


def _path(**query: Any) -> str:
    return "/crm/aggregate?" + urlencode(query)


async def _get(p: Any, org: str, **query: Any) -> Any:
    return await _call(p, _admin(org), "GET", _path(**query))


async def _ok(p: Any, org: str, **query: Any) -> dict:
    res = await _get(p, org, **query)
    assert res.status_code == 200, (query, res.text)
    return res.json()


_TWO = {"entity": "module:Invoices", "measure": "sum", "field": "Amount",
        "group_field": "Site", "group_field2": "Invoice_Date", "bucket": "month"}


def _truth(p: Any, org: str, extra: str = "", args: dict | None = None) -> dict:
    with p.admin_engine.begin() as c:
        rows = c.execute(text(_TRUTH_SQL.format(extra=extra)),
                         {"o": org, **(args or {})}).fetchall()
    return {(r.k1, r.k2): (float(r.v), int(r.c)) for r in rows}


async def _every_page(p: Any, org: str, size: int, **query: Any) -> tuple[list, dict]:
    first = await _ok(p, org, **query, group_page=1, group_page_size=size)
    groups = list(first["groups"])
    for page in range(2, first["group_pages"] + 1):
        body = await _ok(p, org, **query, group_page=page, group_page_size=size)
        assert body["group_count"] == first["group_count"]
        assert body["total"] == first["total"] and body["records"] == first["records"]
        assert len(body["groups"]) <= size
        groups.extend(body["groups"])
    return groups, first


# ── 1 and 2. Every group, over every page ────────────────────────────────────


async def test_each_group_of_every_page_equals_an_independent_group_by(
        invoices, app_engine) -> None:
    a = invoices.org_a
    truth = _truth(invoices, a)
    groups, first = await _every_page(invoices, a, 50, **_TWO)
    got = {(g.get("key"), g.get("key2")): g["value"] for g in groups}
    # Disjoint: no pair twice. Complete: every pair of the truth.
    assert len(got) == len(groups) == len(truth) == first["group_count"]
    assert got == {k: round(v, 2) for k, (v, _c) in truth.items()}
    assert first["group_pages"] == -(-len(truth) // 50) and first["group_page"] == 1
    # In order: the largest value first, page after page.
    values = [g["value"] for g in groups]
    assert values == sorted(values, reverse=True)
    # A wide page gives the same groups in the same order.
    wide, _first = await _every_page(invoices, a, 200, **_TWO)
    assert wide == groups


async def test_a_page_past_the_end_is_empty_with_the_counts(invoices, app_engine) -> None:
    a = invoices.org_a
    count = len(_truth(invoices, a))
    body = await _ok(invoices, a, **_TWO, group_page=99)
    assert body["groups"] == [] and body["group_count"] == count
    assert body["group_page"] == 99 and body["group_pages"] == -(-count // 50)
    assert body["records"] == 1002 and body["omitted"] == count


async def test_one_group_field_pages_past_fifty(invoices, app_engine) -> None:
    a = invoices.org_a
    one = {k: v for k, v in _TWO.items() if k not in ("group_field2", "bucket")}
    groups, first = await _every_page(invoices, a, 50, **one)
    # 120 sites, and the record with no site.
    assert first["group_count"] == 121 == len({g["key"] for g in groups})
    assert all("key2" not in g and "label2" not in g for g in groups)
    assert sum(g["value"] for g in groups) == first["total"]


# ── 3. The list counts the same records ─────────────────────────────────────


async def test_a_filter_counts_what_the_list_shows(invoices, app_engine) -> None:
    a = invoices.org_a
    body = await _ok(invoices, a, **_TWO, **{"field.Status": "Paid"})
    listed = await _call(invoices, _admin(a), "GET",
                         "/crm/modules/Invoices/records?custom.Status=Paid&page_size=1")
    assert listed.status_code == 200, listed.text
    assert body["records"] == listed.json()["total"]
    truth = _truth(invoices, a, " AND fields ->> 'Status' = :s", {"s": "Paid"})
    assert body["records"] == sum(c for _v, c in truth.values())
    assert body["group_count"] == len(truth)


# ── 4. No site, no date ─────────────────────────────────────────────────────


async def test_a_missing_value_makes_its_own_group(invoices, app_engine) -> None:
    a = invoices.org_a
    groups, first = await _every_page(invoices, a, 200, **_TWO)
    nosite = [g for g in groups if g.get("key") is None]
    nodate = [g for g in groups if g.get("key") == "s1" and "key2" not in g]
    assert [(g["label"], g["key2"], g["label2"], g["value"]) for g in nosite] == [
        ("(none)", "2026-07", "2026-07", 5.0)]
    assert [(g["label"], g["label2"], g["value"]) for g in nodate] == [
        ("Site 1", "(none)", 7.0)]
    with invoices.admin_engine.begin() as c:
        whole = c.execute(text(
            "SELECT sum(CAST(fields ->> 'Amount' AS numeric)) FROM crm_module_records "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'Invoices'"),
            {"o": a}).scalar()
    assert first["total"] == float(whole) and first["records"] == 1002


# ── 5. Org B ────────────────────────────────────────────────────────────────


async def test_org_b_never_shows(invoices, app_engine) -> None:
    a = invoices.org_a
    groups, first = await _every_page(invoices, a, 200, **_TWO)
    assert all(g.get("key") != "sB" and g["label"] != "Secret Site" for g in groups)
    assert first["records"] == 1002
    b = await _ok(invoices, invoices.org_b, **_TWO)
    assert [(g["key"], g["key2"]) for g in b["groups"]] == [("sB", "2026-07")]


# ── 6. Off the list ─────────────────────────────────────────────────────────

#: The answer of a one-field call, recorded on the base code (origin/main
#: cc9c0a584, run on 2026-11-18 with the base aggregate.py and field_sql.py
#: put back in place). This slice must not change one byte of it.
_BASE_ONE_FIELD = [
    (
        '{"entity":"module:Invoices","measure":"sum","group_by":null,"group_field":"Statu'
        's","field":"Amount","field_type":"currency","field_label":"Amount","group_field_'
        'label":"Status","keys":{},"group_field_type":"select","bucket":null,"groups":[{"'
        'key":"Due","label":"Due","value":24164.0},{"key":"Paid","label":"Paid","value":1'
        '2013.0}],"total":36177.0,"other":0.0,"omitted":0,"zone":"UTC","values":720,"reco'
        'rds":720}'
    ),
    (
        '{"entity":"module:Invoices","measure":"count","group_by":null,"group_field":"Inv'
        'oice_Date","field":null,"field_type":null,"field_label":null,"group_field_label"'
        ':"Invoice Date","keys":{},"group_field_type":"date","bucket":"month","groups":[{'
        '"key":"2026-07","label":"2026-07","value":281.0},{"key":"2026-08","label":"2026-'
        '08","value":240.0},{"key":"2026-09","label":"2026-09","value":240.0},{"key":"202'
        '6-10","label":"2026-10","value":240.0},{"key":null,"label":"(none)","value":1.0}'
        '],"total":1002.0,"other":0.0,"omitted":0,"zone":"UTC","values":null,"records":10'
        '02}'
    ),
]


async def test_off_the_list_a_second_field_is_a_422(invoices, app_engine, monkeypatch) -> None:
    a = invoices.org_a
    _set(monkeypatch, "")
    res = await _get(invoices, a, **_TWO)
    assert res.status_code == 422, res.text
    assert "filter by the other" in res.json()["detail"]
    res = await _get(invoices, a, entity="module:Invoices", group_field="Site", group_page=2)
    assert res.status_code == 422 and "filter by the other" in res.json()["detail"]


@pytest.mark.parametrize("flag", ["", "*"])
async def test_a_one_field_call_is_the_answer_of_the_base_code(
        invoices, app_engine, monkeypatch, flag) -> None:
    a = invoices.org_a
    _set(monkeypatch, flag)
    res = await _get(invoices, a, entity="module:Invoices", measure="sum", field="Amount",
                     group_field="Status", **{"field.Invoice_Date.from": "2026-08-01"})
    assert res.status_code == 200, res.text
    month = await _get(invoices, a, entity="module:Invoices", group_field="Invoice_Date",
                       bucket="month")
    assert month.status_code == 200, month.text
    assert [res.text, month.text] == _BASE_ONE_FIELD


# ── 7. The timeout ──────────────────────────────────────────────────────────


async def test_a_statement_past_the_timeout_is_the_503(invoices, app_engine, monkeypatch) -> None:
    from gateway.routes.crm import aggregate as aggregate_route

    slow = aggregate_route.paged_sql
    # Each row of the page sleeps past the LOCAL timeout of the statement.
    monkeypatch.setattr(aggregate_route, "paged_sql",
                        lambda inner, whole: slow(inner, whole)
                        + " AND pg_sleep(0.5) IS NOT NULL")
    monkeypatch.setattr(aggregate_route, "STATEMENT_TIMEOUT_MS", 200)
    res = await _get(invoices, invoices.org_a, **_TWO)
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "aggregate_timeout"


# ── 8. 30,000 records in 200 groups ─────────────────────────────────────────


async def test_thirty_thousand_records_in_two_hundred_groups(invoices, app_engine) -> None:
    a = invoices.org_a
    with invoices.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_module_records WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": a})
        c.execute(text(_SEED_SQL), {"o": a, "n": 30_000, "sites": 50})
        c.execute(text("ANALYZE crm_module_records"))
    await _ok(invoices, a, entity="module:Invoices")  # warm the engine
    start = time.perf_counter()
    body = await _ok(invoices, a, **_TWO, group_page_size=200)
    seconds = time.perf_counter() - start
    print(f"\nCRM-Q34-10 30,000 rows, 200 groups: {seconds:.3f} s")
    assert body["group_count"] == 200 and len(body["groups"]) == 200
    assert body["records"] == 30_000
    assert seconds < 10.0
