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
                          {"label": "Due", "value": "Due"},
                          {"label": "Void", "value": "Void"}], False, False),
    ("Notes", "text", None, False, False),
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
    "'Status', CASE WHEN g % 3 = 0 THEN 'Paid' WHEN g % 7 = 0 THEN 'Void' ELSE 'Due' END, "
    "'Notes', CASE WHEN g % 5 = 0 THEN 'box X1' ELSE 'plain' END) "
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
                        _module("Twins"), _module("Halves"), _module("Bits"),
                        _module("Bavg"), _module("Mv"), _module("B50"),
                        _module("Months")])
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
#: 9bb67296a on 2026-11-20, with its aggregate.py, field_sql.py, core.py and
#: crm_tools.py put in place (first recorded on cc9c0a584, 2026-11-18). This
#: slice must not change one byte of it.
_BASE_ONE_FIELD = [
    (
        '{"entity":"module:Invoices","measure":"sum","group_by":null,"group_field":"Statu'
        's","field":"Amount","field_type":"currency","field_label":"Amount","group_field_'
        'label":"Status","keys":{},"group_field_type":"select","bucket":null,"groups":[{"'
        'key":"Due","label":"Due","value":20703.0},{"key":"Paid","label":"Paid","value":1'
        '2013.0},{"key":"Void","label":"Void","value":3461.0}],"total":36177.0,"other":0.'
        '0,"omitted":0,"zone":"UTC","values":720,"records":720}'
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


# ── 12. A half cent at the border of page 1 (round 4) ───────────────────────

_HALF_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Amt", "number", None, False, False),
]

#: case → (measure, the two groups at the border: (id, name, values), and
#: their keys at ranks 50 and 51). Python's ``round(float, 2)`` and Postgres
#: ``round(numeric, 2)`` disagree on each half cent, so the old cut put the
#: two groups in the other order: a tie, broken by the name.
_HALVES = {
    "half_sum": ("sum", [("ka", "Z", [0.125]), ("kb", "A", [0.12])], ("ka", "kb")),
    "avg_2675": ("avg", [("ka", "Z", [2.67, 2.68]), ("kb", "A", [2.67, 2.67])],
                 ("ka", "kb")),
    "neg_sum": ("sum", [("ka", "A", [-0.125]), ("kb", "Z", [-0.12])], ("kb", "ka")),
}


@pytest.mark.parametrize("case", list(_HALVES))
async def test_a_half_cent_at_the_border_shows_each_group_once(
        invoices, app_engine, monkeypatch, case: str) -> None:
    """Round 4 (the verifier's three cases): 49 large groups, two groups at
    ranks 50 and 51 that differ by a half cent, and 5 small ones. Page 1
    (the answer of before) and page 2 (the paged read), through the tool,
    show each of the 56 groups once."""
    measure, border, ranks = _HALVES[case]
    a = invoices.org_a
    _defs(invoices, a, "module:Halves", _HALF_DEFS)
    big = 100.0 if measure == "sum" else 50.0
    rows = [(f"b{n}", {"Site": {"id": f"s{n:02d}", "name": f"B{n:02d}"}, "Amt": big})
            for n in range(49)]
    for site, name, values in border:
        rows.extend((f"{site}{i}", {"Site": {"id": site, "name": name}, "Amt": value})
                    for i, value in enumerate(values))
    rows.extend((f"t{n}", {"Site": {"id": f"t{n}", "name": f"C{n}"}, "Amt": -100.0})
                for n in range(5))
    with invoices.admin_engine.begin() as c:
        for ext, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Halves', 'zoho', :e, "
                ":e, CAST(:f AS jsonb))"), {"o": a, "e": ext, "f": json.dumps(fields)})
    keys: list[list[Any]] = []
    for page in (1, 2):
        calls, _out = await _sent_and_text(invoices, a, monkeypatch, entity="module:Halves",
                                           measure=measure, field="Amt", group_field="Site",
                                           page=page)
        keys.append([g.get("key") for g in calls[-1][1]["groups"]])
    assert (keys[0][-1], keys[1][0]) == ranks, (keys[0][-2:], keys[1][:2])
    every = [*keys[0], *keys[1]]
    assert len(every) == 56 and len(set(every)) == 56


# ── 13. Long digits at the border of page 1 (round 5) ───────────────────────

#: case → (measure, the two groups: (id, name, Amt texts), keys at ranks 50
#: and 51). Each Amt goes in as JSON number TEXT, so jsonb keeps every
#: digit. Postgres sums 10.0 and 0.12499999999999999 to 10.12499999999999999,
#: which rounds to 10.12, a tie with 10.12 that the name breaks. A float of
#: it is 10.125, which rounds to 10.13: the old cut put the groups the other
#: way round, and page 2 repeated one group and lost the other.
_LONG = {
    "sum_long": ("sum", [("ka", "Z", ["10.0", "0.12499999999999999"]),
                         ("kb", "A", ["10.12"])], ("kb", "ka")),
    "sum_neg_long": ("sum", [("ka", "A", ["-10.0", "-0.12499999999999999"]),
                             ("kb", "Z", ["-10.12"])], ("ka", "kb")),
    # AVG keeps 17 places here: 10.12499999999999999, which rounds to 10.12.
    "avg_long": ("avg", [("ka", "Z", ["10.0", "10.24999999999999998"]),
                         ("kb", "A", ["10.12"])], ("kb", "ka")),
}


@pytest.mark.parametrize("case", list(_LONG))
async def test_long_digits_at_the_border_show_each_group_once(
        invoices, app_engine, monkeypatch, case: str) -> None:
    """Round 5 (the verifier's two cases and an avg): page 1 rounds the
    exact numeric of each group, as page 2 does, so each of the 56 groups
    shows once, and the tie at ranks 50 and 51 breaks by the name."""
    measure, border, ranks = _LONG[case]
    a = invoices.org_a
    _defs(invoices, a, "module:Halves", _HALF_DEFS)
    big = "100.0" if measure == "sum" else "50.0"

    def row(site: str, name: str, amt: str) -> str:
        return (f'{{"Site": {{"id": "{site}", "name": "{name}"}}, "Amt": {amt}}}')

    rows = [(f"b{n}", row(f"s{n:02d}", f"B{n:02d}", big)) for n in range(49)]
    for site, name, amts in border:
        rows.extend((f"{site}{i}", row(site, name, amt)) for i, amt in enumerate(amts))
    rows.extend((f"t{n}", row(f"t{n}", f"C{n}", "-100.0")) for n in range(5))
    with invoices.admin_engine.begin() as c:
        for ext, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Halves', 'zoho', :e, "
                ":e, CAST(:f AS jsonb))"), {"o": a, "e": ext, "f": fields})
        kept = c.execute(text(
            "SELECT fields ->> 'Amt' FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Halves' AND ext_id = 'ka1'"), {"o": a}).scalar()
    assert kept in ("0.12499999999999999", "-0.12499999999999999", "10.24999999999999998")
    keys: list[list[Any]] = []
    for page in (1, 2):
        calls, _out = await _sent_and_text(invoices, a, monkeypatch, entity="module:Halves",
                                           measure=measure, field="Amt", group_field="Site",
                                           page=page)
        keys.append([g.get("key") for g in calls[-1][1]["groups"]])
    assert (keys[0][-1], keys[1][0]) == ranks, (keys[0][-2:], keys[1][:2])
    every = [*keys[0], *keys[1]]
    assert len(every) == 56 and len(set(every)) == 56


# ── 14. The filter words of CRM-Q34-5 on a paged answer (merge round) ───────


async def test_filter_words_count_the_same_on_every_page(invoices, app_engine) -> None:
    """A paged answer of two fields takes the filter words (``.in`` and
    ``.has``) through the one module filter helper. Every group and the
    count of records equal an independent SQL count, and the module list
    with the same filters shows the same count."""
    a = invoices.org_a
    words = {"field.Status.in": "Paid,Void", "field.Notes.has": "x"}
    query = {**{k: v for k, v in _TWO.items() if k != "field"}, "measure": "count", **words}
    groups, first = await _every_page(invoices, a, 200, **query)
    with invoices.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT fields -> 'Site' ->> 'id' AS k1, "
            "to_char(CAST(fields ->> 'Invoice_Date' AS date), 'YYYY-MM') AS k2, "
            "count(*) AS n FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Invoices' AND fields ->> 'Status' "
            "IN ('Paid', 'Void') AND fields ->> 'Notes' ILIKE '%x%' GROUP BY 1, 2"),
            {"o": a}).fetchall()
    truth = {(r.k1, r.k2): float(r.n) for r in rows}
    assert truth and {(g.get("key"), g.get("key2")): g["value"] for g in groups} == truth
    assert first["group_count"] == len(truth)
    assert first["records"] == sum(int(v) for v in truth.values())
    assert first.get("filter_words")
    listed = await _call(invoices, _admin(a), "GET",
                         "/crm/modules/Invoices/records?" + urlencode({**words, "page_size": 1}))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == first["records"]


# ── 15. Off the list, page 1 keeps the order of before (verifier round 6) ───

#: The answers of the base code, recorded with origin/main's aggregate.py,
#: field_sql.py, core.py and crm_tools.py put in place (9bb67296a,
#: 2026-11-20). Name → the response text.
_BASE_CUT: dict[str, str] = {
    "avg": (
        '{"entity":"module:Bavg","measure":"avg","group_by":null,"group_field":"Site","fi'
        'eld":"Amt","field_type":"number","field_label":"Amt","group_field_label":"Site",'
        '"keys":{},"group_field_type":"lookup","bucket":null,"groups":[{"key":"kb","label'
        '":"Bbb","value":31.01},{"key":"ka","label":"Aaa","value":31.0},{"key":"kc","labe'
        'l":"Ccc","value":20.0}],"total":28.25,"other":0.0,"omitted":0,"zone":"UTC","valu'
        'es":4,"records":4}'
    ),
    "deals": (
        '{"entity":"deals","measure":"sum","group_by":null,"group_field":"region","field"'
        ':"fee","field_type":"number","field_label":"fee","group_field_label":"region","k'
        'eys":{},"group_field_type":"select","bucket":null,"groups":[{"key":"r00","label"'
        ':"r00","value":50.0},{"key":"r01","label":"r01","value":50.0},{"key":"r02","labe'
        'l":"r02","value":50.0},{"key":"r03","label":"r03","value":50.0},{"key":"r04","la'
        'bel":"r04","value":50.0},{"key":"r05","label":"r05","value":50.0},{"key":"r06","'
        'label":"r06","value":50.0},{"key":"r07","label":"r07","value":50.0},{"key":"r08"'
        ',"label":"r08","value":50.0},{"key":"r09","label":"r09","value":50.0},{"key":"r1'
        '0","label":"r10","value":50.0},{"key":"r11","label":"r11","value":50.0},{"key":"'
        'r12","label":"r12","value":50.0},{"key":"r13","label":"r13","value":50.0},{"key"'
        ':"r14","label":"r14","value":50.0},{"key":"r15","label":"r15","value":50.0},{"ke'
        'y":"r16","label":"r16","value":50.0},{"key":"r17","label":"r17","value":50.0},{"'
        'key":"r18","label":"r18","value":50.0},{"key":"r19","label":"r19","value":50.0},'
        '{"key":"r20","label":"r20","value":50.0},{"key":"r21","label":"r21","value":50.0'
        '},{"key":"r22","label":"r22","value":50.0},{"key":"r23","label":"r23","value":50'
        '.0},{"key":"r24","label":"r24","value":50.0},{"key":"r25","label":"r25","value":'
        '50.0},{"key":"r26","label":"r26","value":50.0},{"key":"r27","label":"r27","value'
        '":50.0},{"key":"r28","label":"r28","value":50.0},{"key":"r29","label":"r29","val'
        'ue":50.0},{"key":"r30","label":"r30","value":50.0},{"key":"r31","label":"r31","v'
        'alue":50.0},{"key":"r32","label":"r32","value":50.0},{"key":"r33","label":"r33",'
        '"value":50.0},{"key":"r34","label":"r34","value":50.0},{"key":"r35","label":"r35'
        '","value":50.0},{"key":"r36","label":"r36","value":50.0},{"key":"r37","label":"r'
        '37","value":50.0},{"key":"r38","label":"r38","value":50.0},{"key":"r39","label":'
        '"r39","value":50.0},{"key":"r40","label":"r40","value":50.0},{"key":"r41","label'
        '":"r41","value":50.0},{"key":"r42","label":"r42","value":50.0},{"key":"r43","lab'
        'el":"r43","value":50.0},{"key":"r44","label":"r44","value":50.0},{"key":"r45","l'
        'abel":"r45","value":50.0},{"key":"r46","label":"r46","value":50.0},{"key":"r47",'
        '"label":"r47","value":50.0},{"key":"r48","label":"r48","value":50.0},{"key":"Bbb'
        '","label":"Bbb","value":31.01}],"total":2512.01,"other":31.0,"omitted":1,"zone":'
        '"UTC","values":51,"records":51}'
    ),
    "sum": (
        '{"entity":"module:Bits","measure":"sum","group_by":null,"group_field":"Site","fi'
        'eld":"Amt","field_type":"number","field_label":"Amt","group_field_label":"Site",'
        '"keys":{},"group_field_type":"lookup","bucket":null,"groups":[{"key":"s00","labe'
        'l":"B00","value":50.0},{"key":"s01","label":"B01","value":50.0},{"key":"s02","la'
        'bel":"B02","value":50.0},{"key":"s03","label":"B03","value":50.0},{"key":"s04","'
        'label":"B04","value":50.0},{"key":"s05","label":"B05","value":50.0},{"key":"s06"'
        ',"label":"B06","value":50.0},{"key":"s07","label":"B07","value":50.0},{"key":"s0'
        '8","label":"B08","value":50.0},{"key":"s09","label":"B09","value":50.0},{"key":"'
        's10","label":"B10","value":50.0},{"key":"s11","label":"B11","value":50.0},{"key"'
        ':"s12","label":"B12","value":50.0},{"key":"s13","label":"B13","value":50.0},{"ke'
        'y":"s14","label":"B14","value":50.0},{"key":"s15","label":"B15","value":50.0},{"'
        'key":"s16","label":"B16","value":50.0},{"key":"s17","label":"B17","value":50.0},'
        '{"key":"s18","label":"B18","value":50.0},{"key":"s19","label":"B19","value":50.0'
        '},{"key":"s20","label":"B20","value":50.0},{"key":"s21","label":"B21","value":50'
        '.0},{"key":"s22","label":"B22","value":50.0},{"key":"s23","label":"B23","value":'
        '50.0},{"key":"s24","label":"B24","value":50.0},{"key":"s25","label":"B25","value'
        '":50.0},{"key":"s26","label":"B26","value":50.0},{"key":"s27","label":"B27","val'
        'ue":50.0},{"key":"s28","label":"B28","value":50.0},{"key":"s29","label":"B29","v'
        'alue":50.0},{"key":"s30","label":"B30","value":50.0},{"key":"s31","label":"B31",'
        '"value":50.0},{"key":"s32","label":"B32","value":50.0},{"key":"s33","label":"B33'
        '","value":50.0},{"key":"s34","label":"B34","value":50.0},{"key":"s35","label":"B'
        '35","value":50.0},{"key":"s36","label":"B36","value":50.0},{"key":"s37","label":'
        '"B37","value":50.0},{"key":"s38","label":"B38","value":50.0},{"key":"s39","label'
        '":"B39","value":50.0},{"key":"s40","label":"B40","value":50.0},{"key":"s41","lab'
        'el":"B41","value":50.0},{"key":"s42","label":"B42","value":50.0},{"key":"s43","l'
        'abel":"B43","value":50.0},{"key":"s44","label":"B44","value":50.0},{"key":"s45",'
        '"label":"B45","value":50.0},{"key":"s46","label":"B46","value":50.0},{"key":"s47'
        '","label":"B47","value":50.0},{"key":"s48","label":"B48","value":50.0},{"key":"k'
        'b","label":"Bbb","value":31.01}],"total":2512.01,"other":31.0,"omitted":1,"zone"'
        ':"UTC","values":51,"records":51}'
    ),
}


def _bits(p: Any, org: str, module: str, rows: list[tuple[str, str, str, str]]) -> None:
    """``(ext_id, site id, site name, Amount text)`` rows of *module*."""
    _defs(p, org, f"module:{module}", _HALF_DEFS)
    with p.admin_engine.begin() as c:
        for ext, site, name, amt in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :e, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"{module}-{ext}",
                 "f": f'{{"Site": {{"id": "{site}", "name": "{name}"}}, "Amt": {amt}}}'})


@pytest.fixture
def bits(invoices):
    """Bits: 49 sites of 50, "Aaa" of 31.005 and "Bbb" of 31.01. Bavg: the
    avg of "Aaa" is 31.005 exactly (31.0 and 31.01), "Bbb" 31.01, "Ccc" 20."""
    a = invoices.org_a
    _bits(invoices, a, "Bits", [
        *((f"b{n}", f"s{n:02d}", f"B{n:02d}", "50") for n in range(49)),
        ("xa1", "ka", "Aaa", "31.005"), ("xb1", "kb", "Bbb", "31.01")])
    _bits(invoices, a, "Bavg", [
        ("a1", "ka", "Aaa", "31.0"), ("a2", "ka", "Aaa", "31.01"),
        ("b1", "kb", "Bbb", "31.01"), ("c1", "kc", "Ccc", "20")])
    return invoices


async def test_off_the_list_page_1_keeps_the_cut_of_before(bits, app_engine, monkeypatch) -> None:
    """Off the list the answer of one page is the answer of the base code,
    byte for byte: the larger "Bbb" (31.01) stays in the 50, and the
    smaller "Aaa" (shown 31.0) folds into ``other``. An avg is in value
    order. The exact cut of the list must not reach this answer."""
    a = bits.org_a
    _set(monkeypatch, "")
    total = await _get(bits, a, entity="module:Bits", measure="sum", field="Amt",
                       group_field="Site")
    avg = await _get(bits, a, entity="module:Bavg", measure="avg", field="Amt",
                     group_field="Site")
    body = total.json()
    assert body["groups"][-1]["label"] == "Bbb" and body["other"] == 31.0
    values = [g["value"] for g in avg.json()["groups"]]
    assert values == sorted(values, reverse=True) == [31.01, 31.0, 20.0]
    assert {"sum": total.text, "avg": avg.text} == {k: _BASE_CUT[k] for k in ("sum", "avg")}


async def test_on_the_list_the_cut_continues_on_page_2(bits, app_engine) -> None:
    """On the list, page 1 takes the exact cut, so page 2 holds the one
    group past it and no group shows twice."""
    a = bits.org_a
    first = await _ok(bits, a, entity="module:Bits", measure="sum", field="Amt",
                      group_field="Site")
    second = await _ok(bits, a, entity="module:Bits", measure="sum", field="Amt",
                       group_field="Site", group_page=2)
    keys = [g["key"] for g in first["groups"]] + [g["key"] for g in second["groups"]]
    assert len(keys) == len(set(keys)) == 51 and first["next_group_page"] == 2
    # Round 7, the boundary of 51: the exact cut keeps "Aaa" (31.005, which
    # rounds to 31.01 as SQL rounds it, and sorts first by its name), and
    # page 2 holds "Bbb" alone.
    assert first["groups"][-1]["key"] == "ka" and [g["key"] for g in second["groups"]] == ["kb"]


@pytest.fixture
async def deals(promoted, monkeypatch):
    """Org A in Native mode: deals with a select ``region`` and a number
    ``fee`` of 49 regions of 50, "Aaa" of 31.005 and "Bbb" of 31.01."""
    from tests.unit._crm_fakes import crm_switch_on
    from tests.unit.test_crm_aggregate_r8 import _TABLES as DEAL_TABLES
    from tests.unit.test_crm_aggregate_r8 import _deal

    crm_switch_on(monkeypatch, "*")
    a = promoted.org_a
    res = await _call(promoted, _admin(a), "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    with promoted.admin_engine.begin() as c:
        for position, (key, ftype) in enumerate((("region", "select"), ("fee", "number"))):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
                "source, provider_api_name, position) VALUES (CAST(:o AS uuid), 'deal', "
                ":k, :k, :t, 'native', :k, :p)"), {"o": a, "k": key, "t": ftype, "p": position})
        for n in range(49):
            _deal(c, a, f"D{n}", "Proposal", 1, owner="x@q10.test", clock_days=0,
                  custom={"region": f"r{n:02d}", "fee": 50})
        _deal(c, a, "DA", "Proposal", 1, owner="x@q10.test", clock_days=0,
              custom={"region": "Aaa", "fee": 31.005})
        _deal(c, a, "DB", "Proposal", 1, owner="x@q10.test", clock_days=0,
              custom={"region": "Bbb", "fee": 31.01})
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in DEAL_TABLES:
                c.execute(text(f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                          {"o": org})


@pytest.mark.parametrize("flag", ["", "*"])
async def test_a_core_entity_keeps_the_cut_of_before(deals, app_engine, monkeypatch,
                                                     flag: str) -> None:
    """A core entity never pages, so its answer of one page keeps the cut of
    the base code on and off the list."""
    _set(monkeypatch, flag)
    res = await _get(deals, deals.org_a, entity="deals", measure="sum", field="fee",
                     group_field="region")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["groups"][-1]["key"] == "Bbb" and body["other"] == 31.0
    assert res.text == _BASE_CUT["deals"]


# ── 16. The query of SEM-1b reaches the paged read (verifier round 6) ───────

_QUERY = json.dumps([{"field": "Amount", "op": "gte", "value": 50},
                     {"field": "Notes", "op": "eq", "value": "plain"}])


def _query_truth(p: Any, org: str, *, by_month: bool) -> dict[Any, float]:
    month = (", to_char(CAST(fields ->> 'Invoice_Date' AS date), 'YYYY-MM')"
             if by_month else "")
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            f"SELECT fields -> 'Site' ->> 'id'{month}, count(*) FROM crm_module_records "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'Invoices' "
            "AND CAST(fields ->> 'Amount' AS numeric) >= 50 AND fields ->> 'Notes' = 'plain' "
            f"GROUP BY 1{', 2' if by_month else ''}"), {"o": org}).fetchall()
    return {(tuple(r[:-1]) if by_month else r[0]): float(r[-1]) for r in rows}


async def test_the_query_filters_every_paged_read(invoices, app_engine, monkeypatch) -> None:
    """A two-field page and page 2 of one field both take the ``query``
    leaves (a range and a text leaf): each group equals an independent
    GROUP BY. A paged read that drops the query counts other records."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "semantic_model_orgs", "*", raising=False)
    a = invoices.org_a
    two = {k: v for k, v in _TWO.items() if k != "field"}
    two.update(measure="count", query=_QUERY)
    groups, first = await _every_page(invoices, a, 200, **two)
    truth = _query_truth(invoices, a, by_month=True)
    assert {(g.get("key"), g.get("key2")): g["value"] for g in groups} == truth
    assert first["group_count"] == len(truth)
    one = {"entity": "module:Invoices", "measure": "count", "group_field": "Site",
           "query": _QUERY}
    page1 = await _ok(invoices, a, **one)
    later = [await _ok(invoices, a, **one, group_page=p) for p in (2, 3)]
    assert later[0]["groups"], later[0]
    walk = [*page1["groups"], *later[0]["groups"], *later[1]["groups"]]
    assert {g.get("key"): g["value"] for g in walk} == _query_truth(invoices, a,
                                                                    by_month=False)
    assert len(walk) == len({g.get("key") for g in walk})


# ── 17. 50 groups or fewer keep the order of before, flag on (round 7) ───────

_NOTE_DEFS = [*_HALF_DEFS, ("Notes", "text", None, False, False),
              ("Visit_Date", "date", None, False, False)]


def _note_rows(p: Any, org: str, module: str, rows: list[tuple[str, dict]]) -> None:
    _defs(p, org, f"module:{module}", _NOTE_DEFS)
    with p.admin_engine.begin() as c:
        for ext, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, 'Fiber', "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"{module}-{ext}", "f": json.dumps(fields)})


def _site(site: str, name: str, amt: Any, notes: str = "plain") -> dict:
    return {"Site": {"id": site, "name": name}, "Amt": amt, "Notes": notes}


@pytest.fixture
def few(invoices):
    """Mv: 36 sites of 50, "Aaa" 31.005 and "Bbb" 31.01, all with Notes
    "plain", and 40 more sites with Notes "other", so the filter leaves 38
    groups. B50: 48 sites of 50, "Aaa" and "Bbb": exactly 50 groups.
    Months: 60 records, one in each month from 2020-01."""
    a = invoices.org_a
    _note_rows(invoices, a, "Mv", [
        *((f"s{n}", _site(f"s{n:02d}", f"B{n:02d}", 50)) for n in range(36)),
        *((f"o{n}", _site(f"o{n:02d}", f"O{n:02d}", 70, "other")) for n in range(40)),
        ("xa", _site("ka", "Aaa", 31.005)), ("xb", _site("kb", "Bbb", 31.01))])
    _note_rows(invoices, a, "B50", [
        *((f"s{n}", _site(f"s{n:02d}", f"B{n:02d}", 50)) for n in range(48)),
        ("xa", _site("ka", "Aaa", 31.005)), ("xb", _site("kb", "Bbb", 31.01))])
    _note_rows(invoices, a, "Months", [
        (f"m{n}", {**_site(f"s{n:02d}", f"B{n:02d}", 1),
                   "Visit_Date": f"{2020 + n // 12}-{1 + n % 12:02d}-15"})
        for n in range(60)])
    return invoices


async def test_fifty_groups_or_fewer_keep_the_order_of_before(few, app_engine) -> None:
    """Round 7: on the list, an answer of 50 groups or fewer has no page 2,
    so it keeps main's order byte for byte: "Bbb" (31.01) before "Aaa"
    (shown 31.0), and no ``next_group_page``. The verifier's Mv input (38
    groups after the filter) and the boundary of exactly 50 groups."""
    a = few.org_a
    mv = await _get(few, a, entity="module:Mv", measure="sum", field="Amt",
                    group_field="Site", **{"field.Notes": "plain"})
    b50 = await _get(few, a, entity="module:B50", measure="sum", field="Amt",
                     group_field="Site")
    print("BASECUT", json.dumps({"mv": mv.text, "b50": b50.text}))
    for res, count in ((mv, 38), (b50, 50)):
        body = res.json()
        assert len(body["groups"]) == count and "next_group_page" not in body
        assert [g["key"] for g in body["groups"][-2:]] == ["kb", "ka"]
    assert {"mv": mv.text, "b50": b50.text} == {k: _BASE_CUT[k] for k in ("mv", "b50")}


async def test_q_or_a_date_group_never_offers_a_page_2(few, places, app_engine) -> None:
    """Round 7: ``q`` (its near pass) and a date bucket cannot continue in
    the order of the value. With more than 50 groups and the flag on, such
    an answer carries no ``next_group_page``, and page 2 is page 1 again."""
    a = few.org_a
    for query in ({"entity": "module:Places", "group_field": "Site", "q": "Fiber"},
                  {"entity": "module:Months", "group_field": "Visit_Date",
                   "bucket": "month"}):
        first = await _ok(few, a, **query)
        assert first["omitted"] > 0 and "next_group_page" not in first, query
        assert await _ok(few, a, **query, group_page=2) == first, query
