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
6. Off ``CRM_MODULE_FIND_ORGS``: a 422 for ``group_field2``, the page
   parameters change nothing, and a call with one group field is the answer
   of the base code, byte for byte. A page past 1,000,000 is a 422.
7. A statement past the timeout is the 503 ``aggregate_timeout``.
8. 30,000 records in 200 groups answer inside the 10 s timeout.
9. A text field of numbers sums in two groups as in one (CRM-Q34-11).
10. Through the chat tool: page 1 of one field is the answer of before
    (a split, the near pass of ``q``, a date group in time order), and pages
    2 and 3 continue it. 122 tied groups show once each, in the order of
    their names. Among ties of two fields, the keys give the order.

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
    # Org A also mirrors Works, the module of item 9.
    _mirror_org(ns, a, [_module("Invoices"), _module("Works"), _module("Places"),
                        _module("Twins")])
    _mirror_org(ns, b, [_module("Invoices")])
    for org in (a, b):
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


async def test_one_group_field_continues_the_answer_of_one_page(invoices, app_engine) -> None:
    """Second fix round, B: page 1 of one field is the answer of before, with
    ``next_group_page``. ``group_page=2`` and 3 continue it, and together
    the three pages hold each group once."""
    a = invoices.org_a
    one = {k: v for k, v in _TWO.items() if k not in ("group_field2", "bucket")}
    first = await _ok(invoices, a, **one)
    assert "group_count" not in first and first["omitted"] == 71
    assert first["next_group_page"] == 2
    page1 = await _ok(invoices, a, **one, group_page=1)
    assert page1 == first  # page 1 is the answer of before
    rest = [await _ok(invoices, a, **one, group_page=p) for p in (2, 3)]
    assert all(r["group_count"] == 121 and r["total"] == first["total"] for r in rest)
    groups = [*first["groups"], *rest[0]["groups"], *rest[1]["groups"]]
    assert len(groups) == 121 == len({g["key"] for g in groups})
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

#: The answer of a one-field call, recorded on the base code: origin/main
#: cc9c0a584 on 2026-11-18, with the base aggregate.py and field_sql.py put
#: back in place. Proved again on origin/main ecb879c69 on 2026-11-19, the
#: same way. This slice must not change one byte of it.
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
    # Fix round item 1: off the list the route ignores the page parameters,
    # page 2 and a huge page too. The answer is the one of no page.
    plain = await _get(invoices, a, entity="module:Invoices", group_field="Site")
    assert "next_group_page" not in plain.json()
    for page in (1, 2, 10**20):
        res = await _get(invoices, a, entity="module:Invoices", group_field="Site",
                         group_page=page, group_page_size=50)
        assert res.status_code == 200 and res.text == plain.text, (page, res.text[:200])


async def test_a_page_past_the_bound_is_a_422(invoices, app_engine) -> None:
    """Fix round item 2: ``page * size`` would overflow a bigint, so the
    route refuses a page past 1,000,000 before any statement."""
    a = invoices.org_a
    for page in (1_000_001, 10**20):
        res = await _get(invoices, a, **_TWO, group_page=page)
        assert res.status_code == 422, (page, res.text)
        assert "1 to 1,000,000" in res.json()["detail"]
    last = await _ok(invoices, a, **_TWO, group_page=1_000_000, group_page_size=200)
    assert last["groups"] == [] and last["group_page"] == 1_000_000


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
                        lambda *args: slow(*args)
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


# ── 9. A text field that holds numbers (CRM-Q34-11) ─────────────────────────

_WORK_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Invoice_Date", "date", None, False, False),
    ("Fiber", "text", None, False, False),
    ("Notes", "text", None, False, False),
]


@pytest.fixture
def works(invoices):
    """Module Works of org A: the dirty Fiber values of CRM-Q34-11, over two
    sites. The number of each value comes from that suite, written by hand."""
    from tests.unit.test_crm_text_numbers_r8 import _INVOICES, _MISSING

    a = invoices.org_a
    _defs(invoices, a, "module:Works", _WORK_DEFS)
    with invoices.admin_engine.begin() as c:
        for i, (ext, fiber, day, more) in enumerate(_INVOICES):
            fields = {"Site": {"id": f"w{i % 2}", "name": f"Works {i % 2}"},
                      "Invoice_Date": day, **more}
            if fiber is not _MISSING:
                fields["Fiber"] = fiber
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Works', 'zoho', :e, :e, "
                "CAST(:f AS jsonb))"), {"o": a, "e": ext, "f": json.dumps(fields)})
    return invoices


def _work_truth(p: Any, org: str) -> dict[tuple[str, str], float]:
    """The sum of Fiber for each site and month, by a join on the numbers
    written by hand, in SQL that shares no code with the route."""
    from tests.unit.test_crm_text_numbers_r8 import _INVOICE_NUMBERS

    values = ", ".join(f"(:r{i}, CAST(:n{i} AS numeric))" for i in range(len(_INVOICE_NUMBERS)))
    args: dict[str, Any] = {"o": org}
    for i, (raw, number) in enumerate(_INVOICE_NUMBERS.items()):
        args[f"r{i}"], args[f"n{i}"] = raw, str(number)
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT r.fields -> 'Site' ->> 'id' AS k1, left(r.fields ->> 'Invoice_Date', 7) "
            f"AS k2, sum(x.n) AS v FROM crm_module_records r JOIN (VALUES {values}) "
            "x(raw, n) ON r.fields ->> 'Fiber' = x.raw WHERE r.organization_id = "
            "CAST(:o AS uuid) AND r.module = 'Works' GROUP BY 1, 2"), args).fetchall()
    return {(r.k1, r.k2): float(r.v) for r in rows}


async def test_a_text_field_of_numbers_sums_in_two_groups(works, app_engine) -> None:
    a = works.org_a
    query = {"entity": "module:Works", "measure": "sum", "field": "Fiber",
             "group_field": "Site", "group_field2": "Invoice_Date", "bucket": "month"}
    two = await _ok(works, a, **query, group_page_size=200)
    one = await _ok(works, a, entity="module:Works", measure="sum", field="Fiber",
                    group_field="Invoice_Date", bucket="month")
    truth = _work_truth(works, a)
    got = {(g.get("key"), g.get("key2")): g["value"] for g in two["groups"]}
    # A pair with no number in Fiber sums to 0, as a group of one field does.
    assert set(truth) <= set(got)
    assert got == {k: round(truth.get(k, 0.0), 2) for k in got}
    # The same three counts, the same total, the same records as one field.
    assert two["text_numbers"] == one["text_numbers"]
    assert set(two["text_numbers"]["Fiber"]) >= {"numbers", "no_value", "not_numbers"}
    assert (two["total"], two["values"], two["records"]) == (
        one["total"], one["values"], one["records"])
    assert two["total"] == round(sum(truth.values()), 2)
    # Text equality stays text equality: "12" in Notes is one record.
    eq = await _ok(works, a, **query, **{"field.Notes": "12"})
    assert eq["records"] == 1 and [(g["key"], g["key2"]) for g in eq["groups"]] == [
        ("w0", "2026-11")]


# ── 10. Pages through the chat tool, with tied values ───────────────────────


async def _tool_text(p: Any, org: str, monkeypatch: Any, **args: Any) -> str:
    """``crm_aggregate`` with its gateway calls sent to the real router, so
    each page carries exactly the parameters that the tool builds."""
    import acb_skills.crm_tools as tools

    async def _forward(path: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        res = await _call(p, _admin(org), "GET", f"{path}?{query}" if query else path)
        if path == "/crm/aggregate":
            assert res.status_code == 200, res.text
        return res.json() if res.status_code == 200 else {}

    monkeypatch.setattr(tools, "_get", _forward)
    return await tools.crm_aggregate(**args)


_PLACE_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Kind", "select", [{"label": "Rent", "value": "R"}, {"label": "Power", "value": "P"}],
     False, False),
    ("Qty", "number", None, False, False),
    ("Tax", "number", None, False, False),
    ("Visit_Date", "date", None, False, False),
]


@pytest.fixture
def places(invoices):
    """Module Places of org A: 121 sites, and a record with no site. Site n
    holds 2 records when n is a multiple of 3, else 1, so most counts tie.
    The id of site n is ``p<n>`` and its name ``Z<200 - n>``, so the names
    sort the other way round from the ids. An order by id in place of the
    name puts other groups on page 2."""
    a = invoices.org_a
    _defs(invoices, a, "module:Places", _PLACE_DEFS)
    rows = []
    for n in range(121):
        for copy in range(2 if n % 3 == 0 else 1):
            rows.append((f"x{n:03d}{copy}", {
                "Site": {"id": f"p{n:03d}", "name": f"Z{200 - n:03d}"},
                "Kind": "R" if n % 2 else "P", "Qty": n % 7, "Tax": n % 5,
                "Visit_Date": f"2026-{7 + n % 4:02d}-0{1 + copy}"}))
    rows.append(("x-none", {"Kind": "R", "Qty": 1, "Tax": 1, "Visit_Date": "2026-07-09"}))
    with invoices.admin_engine.begin() as c:
        for ext, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Places', 'zoho', :e, "
                ":n, CAST(:f AS jsonb))"),
                {"o": a, "e": ext, "n": "Fiber", "f": json.dumps(fields)})
    return invoices


def _lines(out: str) -> list[tuple[str, float]]:
    import re

    return [(m[0], float(m[1].replace(",", "")))
            for m in re.findall(r"^- (.+?): ([0-9,.]+)$", out, flags=re.M)]


async def test_the_tool_walks_one_field_with_ties_by_the_name(
        places, app_engine, monkeypatch) -> None:
    """Second fix round, E4: page 1 through the tool is the answer of before
    (no page parameter), and pages 2 and 3 come from the paged read. Each of
    the 122 groups shows once, and page 2 starts right after page 1 in the
    order of page 1: the count down, then the NAME. The names sort the other
    way from the ids, so an order by the id goes red here."""
    a = places.org_a
    pages = [_lines(await _tool_text(places, a, monkeypatch, entity="module:Places",
                                     group_field="Site", page=page)) for page in (1, 2, 3)]
    assert [len(p) for p in pages] == [50, 50, 22]
    seen = [label for page in pages for label, _v in page]
    assert len(seen) == len(set(seen)) == 122
    walk = [item for page in pages for item in page]
    assert walk == sorted(walk, key=lambda g: (-g[1], g[0]))
    last, first = pages[0][-1], pages[1][0]
    assert (-last[1], last[0]) < (-first[1], first[0])
    page1 = await _tool_text(places, a, monkeypatch, entity="module:Places",
                             group_field="Site")
    assert "Ask for page 2 for the next groups." in page1
    assert "Narrow the filter" not in page1


async def _sent_and_text(p: Any, org: str, monkeypatch: Any,
                         **args: Any) -> tuple[list[tuple[dict, dict]], str]:
    """The tool text, and each aggregate call of the tool with its answer."""
    import acb_skills.crm_tools as tools

    calls: list[tuple[dict, dict]] = []

    async def _forward(path: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        sent = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(sent)
        res = await _call(p, _admin(org), "GET", f"{path}?{query}" if query else path)
        body = res.json() if res.status_code == 200 else {}
        if path == "/crm/aggregate":
            assert res.status_code == 200, res.text
            calls.append((sent, body))
        return body

    monkeypatch.setattr(tools, "_get", _forward)
    return calls, await tools.crm_aggregate(**args)


async def test_a_split_by_a_group_answers_on_and_off_the_list(
        places, app_engine, monkeypatch) -> None:
    """Second fix round, E1: a split by a group through the tool is the
    same answer on the list and off it, never a 422."""
    args = {"entity": "module:Places", "measure": "sum", "fields": ["Qty", "Tax"],
            "group_field": "Kind"}
    on_calls, on = await _sent_and_text(places, places.org_a, monkeypatch, **args)
    _set(monkeypatch, "")
    off_calls, off = await _sent_and_text(places, places.org_a, monkeypatch, **args)
    assert on == off and on_calls[-1][1] == off_calls[-1][1]
    assert "group_page" not in on_calls[-1][0] and len(on_calls[-1][1]["split"]) == 2


async def test_q_and_a_group_keep_the_near_pass(places, app_engine, monkeypatch) -> None:
    """Second fix round, E2: ``q`` with a near spelling and a group, on the
    list. Page 1 of the tool is the route's answer with no page, near pass
    and all."""
    a = places.org_a
    calls, _out = await _sent_and_text(places, a, monkeypatch, entity="module:Places",
                                       group_field="Kind", q="Fibre")
    sent, body = calls[-1]
    assert "group_page" not in sent and "group_count" not in body
    route = await _ok(places, a, entity="module:Places", group_field="Kind", q="Fibre")
    assert body == route
    assert body.get("near") or body.get("nearest"), body


async def test_a_date_group_stays_in_time_order(places, app_engine, monkeypatch) -> None:
    """Second fix round, E3: a month group of one field through the tool is
    the answer of before, in time order, on page 1. Page 2 of it is the
    same answer too, as off the list, since a date group cannot continue."""
    a = places.org_a
    args = {"entity": "module:Places", "group_field": "Visit_Date", "bucket": "month"}
    calls, _out = await _sent_and_text(places, a, monkeypatch, **args)
    body = calls[-1][1]
    keys = [g["key"] for g in body["groups"]]
    assert keys == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert body == await _ok(places, a, entity="module:Places", group_field="Visit_Date",
                             bucket="month")
    later = await _ok(places, a, **args, group_page=2)
    assert later == body and "group_count" not in later


def _ordered(groups: list[dict]) -> list[dict]:
    """The order of the route, in Python: value down, then key, then key2,
    each None last. The keys are ASCII letters and digits."""
    return sorted(groups, key=lambda g: (
        g["value"] is None, -(g["value"] or 0),
        g.get("key") is None, g.get("key") or "",
        g.get("key2") is None, g.get("key2") or ""))


async def test_the_order_among_ties_is_the_keys(invoices, app_engine) -> None:
    """Fix round item 7: a count by site and month ties on most values, and
    many ties share the site, so only ``key2`` orders them. The pages give
    the order of the keys, and the same page twice is the same page."""
    a = invoices.org_a
    query = {**_TWO, "measure": "count"}
    query.pop("field")
    groups, _first = await _every_page(invoices, a, 50, **query)
    assert groups == _ordered(groups)
    ties = {}
    for g in groups:
        ties.setdefault((g["value"], g.get("key")), []).append(g.get("key2"))
    assert any(len(k2) > 1 for k2 in ties.values())  # key2 decides somewhere
    again = await _ok(invoices, a, **query, group_page=2)
    assert again["groups"] == groups[50:100]


# ── 11. Ties of value AND label at the border of page 1 (round 3) ────────────

_TWIN_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Set", "select", [{"label": "A", "value": "A"}, {"label": "B", "value": "B"}],
     False, False),
]


@pytest.fixture
def twins(invoices):
    """Module Twins of org A, in two sets. Each set has 49 sites of 3
    records (ranks 1 to 49), then two groups of 2 records that tie on the
    value AND the label (ranks 50 and 51), then 5 sites of 1 record.

    * Set A: two lookups named "Acme Ltd", ids ``k-b`` and ``ka``. By code
      point ``k-b`` comes first, by the en_US collation of the database
      ``ka`` does. So an order of the key by the database collation, or by
      the order of the rows, splits the tie the other way from page 1.
    * Set B: a record with no site (key NULL) and one whose site is "" (key
      ""). Both are named "(none)"."""
    a = invoices.org_a
    _defs(invoices, a, "module:Twins", _TWIN_DEFS)
    rows: list[tuple[str, dict]] = []
    for kind in ("A", "B"):
        for n in range(49):
            for copy in range(3):
                rows.append((f"{kind}{n:02d}{copy}", {
                    "Set": kind, "Site": {"id": f"{kind}s{n:02d}", "name": f"B{n:02d}"}}))
        for n in range(5):
            rows.append((f"{kind}t{n}", {
                "Set": kind, "Site": {"id": f"{kind}t{n}", "name": f"C{n}"}}))
        for copy in range(2):
            if kind == "A":
                rows.append((f"Aka{copy}", {"Set": "A",
                                             "Site": {"id": "ka", "name": "Acme Ltd"}}))
                rows.append((f"Akb{copy}", {"Set": "A",
                                             "Site": {"id": "k-b", "name": "Acme Ltd"}}))
            else:
                rows.append((f"Bnull{copy}", {"Set": "B"}))
                rows.append((f"Bblank{copy}", {"Set": "B", "Site": ""}))
    with invoices.admin_engine.begin() as c:
        for ext, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Twins', 'zoho', :e, "
                ":e, CAST(:f AS jsonb))"), {"o": a, "e": ext, "f": json.dumps(fields)})
    return invoices


@pytest.mark.parametrize(("kind", "pair"), [
    ("A", ["k-b", "ka"]),
    ("B", ["", None]),
])
async def test_a_tie_at_the_border_shows_each_group_once(
        twins, app_engine, monkeypatch, kind: str, pair: list) -> None:
    """Round 3: page 1 (the answer of before, through the tool) and page 2
    (the paged read) split the tie of ranks 50 and 51 the same way: by the
    key, by code point, None last. Each group shows once."""
    a = twins.org_a
    keys: list[Any] = []
    for page in (1, 2):
        calls, _out = await _sent_and_text(
            twins, a, monkeypatch, entity="module:Twins", group_field="Site",
            field_filters={"Set": kind}, page=page)
        keys.append([g.get("key") for g in calls[-1][1]["groups"]])
    assert len(keys[0]) == 50 and len(keys[1]) == 6
    assert keys[0][-1] == pair[0] and keys[1][0] == pair[1]
    every = [*keys[0], *keys[1]]
    assert len(every) == 56 and len({repr(k) for k in every}) == 56
