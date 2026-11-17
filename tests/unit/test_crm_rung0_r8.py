"""R8 — CRM Rung 0 (task 3690): a number range, ``q`` on a module, and the
hint of a field that is not on the module.

Spec: ``project-docs/specs/crm_platform.md`` §18, "CRM Rung 0 (task 3690)".
Production log of 2026-11-17: 10 of 61 ``crm_aggregate`` calls of a member
got 422, although the fields existed.

Against a REAL Postgres, through the real ``/crm`` router, as the app role.

1. ``field.<number>.from`` / ``.to`` read the guarded number. A dirty value
   (``"Rs. 0.00"``, ``"NA"``, ``"-"``, ``""``, an object, an array,
   ``"1e999"``, ``"12,50"``) is NO value, so no end of a range takes it, and
   no value gives a 500. A JSON number and a plain decimal string are values.
2. ``q`` on the module aggregate and on the module list: one matcher, so the
   count of the aggregate equals the total of the list.
3. The 422 of a field that is not on the module names the close fields and
   the other modules that hold it, and never a label of another org.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
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
    _path,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: Each record of org A: its name and its stored ``Fee``. ``_ABSENT`` keeps
#: the key out of the record.
_ABSENT = object()
_CLIENTS: list[tuple[str, Any]] = [
    ("Acme Corp", 5),
    ("Acme Labs", 0),
    ("Beta 50% Off", -3),
    ("Gamma_Co", 12.5),
    ("O'Brien and Sons", "7"),
    ("Café Crème", "Rs. 0.00"),
    ("ACME INDIA", "NA"),
    ("delta", "-"),
    ("Epsilon", ""),
    ("Zeta", None),
    ("Eta", {"x": 1}),
    ("Theta", [1]),
    ("Iota", "1e999"),
    ("Kappa", "12,50"),
    ("Lambda", _ABSENT),
]

#: The guard of ``field_sql._number``: a JSON number, or a string that is a
#: plain decimal. Every other stored form is no value.
_VALUES = {"Acme Corp": 5, "Acme Labs": 0, "Beta 50% Off": -3, "Gamma_Co": 12.5,
           "O'Brien and Sons": 7}

_CLIENT_DEFS = [
    ("Fee", "currency", None, False, False),
    ("Start", "date", None, False, False),
    ("Category", "select", None, False, False),
    ("Note", "text", None, False, False),
    ("Flag", "boolean", None, False, False),
]


def _named_record(c: Any, org: str, module: str, ext_id: str, name: str,
                  fields: dict[str, Any]) -> None:
    c.execute(text(
        "INSERT INTO crm_module_records (organization_id, module, ext_source, "
        "ext_id, name, owner_email, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', "
        ":e, :n, 'a@rung0.test', CAST(:f AS jsonb))"),
        {"o": org, "m": module, "e": ext_id, "n": name, "f": json.dumps(fields)})


@pytest.fixture
def rung(ns):
    """Org A mirrors Clients and Invoices. Org B mirrors Clients and Secrets,
    and holds one client named Acme of its own."""
    a, b = ns.org_a, ns.org_b
    _mirror_org(ns, a, [_module("Clients"), _module("Invoices")])
    _mirror_org(ns, b, [_module("Clients"), _module("Secrets")])
    _defs(ns, a, "module:Clients", _CLIENT_DEFS)
    _defs(ns, a, "module:Invoices", [("Grand_Total", "currency", None, False, False),
                                     ("Invoice_Date", "date", None, False, False)])
    _defs(ns, b, "module:Clients", _CLIENT_DEFS)
    _defs(ns, b, "module:Secrets", [("Hidden_B", "currency", None, False, False)])
    with ns.admin_engine.begin() as c:
        for i, (name, fee) in enumerate(_CLIENTS):
            fields: dict[str, Any] = {"Start": f"2026-0{1 + i % 9}-10"}
            if fee is not _ABSENT:
                fields["Fee"] = fee
            _named_record(c, a, "Clients", f"c{i}", name, fields)
        _named_record(c, b, "Clients", "b1", "Acme B Only", {"Fee": 100})
    return ns


async def _total(p: Any, org: str, **query: Any) -> dict:
    res = await _call(p, _admin(org), "GET", _path(entity="module:Clients", **query))
    assert res.status_code == 200, (query, res.text)
    return res.json()


async def _list(p: Any, org: str, **query: Any) -> Any:
    path = "/crm/modules/Clients/records?" + urlencode({"page_size": 100, **query})
    return await _call(p, _admin(org), "GET", path)


async def _names(p: Any, org: str, **query: Any) -> list[str]:
    res = await _list(p, org, **query)
    assert res.status_code == 200, (query, res.text)
    body = res.json()
    assert body["total"] == len(body["rows"])
    return sorted(r["name"] for r in body["rows"])


def _expect(test: Any) -> list[str]:
    return sorted(n for n, v in _VALUES.items() if test(v))


# ── 1. A number range over dirty values ──────────────────────────────────────


@pytest.mark.parametrize(("ends", "test"), [
    ({"from": "1"}, lambda v: v >= 1),
    ({"to": "0"}, lambda v: v <= 0),
    ({"from": "0", "to": "7"}, lambda v: 0 <= v <= 7),
    ({"from": "-3", "to": "-3"}, lambda v: v == -3),
    ({"from": "12.5"}, lambda v: v >= 12.5),
    ({"from": "10", "to": "1"}, lambda v: False),
])
async def test_a_number_range_takes_real_values_only(rung, app_engine, ends: dict,
                                                     test: Any) -> None:
    query = {f"field.Fee.{end}": value for end, value in ends.items()}
    wanted = _expect(test)
    body = await _total(rung, rung.org_a, **query)
    assert (body["total"], body["records"]) == (len(wanted), len(wanted)), body
    total = await _total(rung, rung.org_a, measure="sum", field="Fee", **query)
    assert total["values"] == len(wanted)
    if wanted:
        assert total["total"] == float(sum(_VALUES[n] for n in wanted))
    assert await _names(rung, rung.org_a, **query) == wanted


async def test_from_one_is_the_records_with_a_value_of_at_least_one(rung, app_engine) -> None:
    """The prod question: the records where the number has a value. "Rs. 0.00",
    "NA", "-", "", null, an object, an array, "1e999" and "12,50" are no value."""
    body = await _total(rung, rung.org_a, measure="sum", field="Fee",
                        **{"field.Fee.from": "1"})
    assert (body["records"], body["values"], body["total"]) == (3, 3, 24.5)


async def test_a_range_past_the_other_end_is_empty_as_a_date_range_is(rung, app_engine) -> None:
    dates = await _total(rung, rung.org_a, **{"field.Start.from": "2026-09-01",
                                              "field.Start.to": "2026-01-01"})
    numbers = await _total(rung, rung.org_a, **{"field.Fee.from": "10", "field.Fee.to": "1"})
    assert dates["total"] == numbers["total"] == 0


@pytest.mark.parametrize("query", [
    {"field.Fee.from": "abc"},
    {"field.Fee.to": "1e1000000"},
    {"field.Fee.from": ""},
    {"field.Fee.from": "1" * 61},
    {"field.Note.from": "1"},
    {"field.Flag.to": "1"},
    {"field.Category.from": "1"},
])
async def test_an_unfit_number_range_is_a_422(rung, app_engine, query: dict) -> None:
    res = await _call(rung, _admin(rung.org_a), "GET",
                      _path(entity="module:Clients", **query))
    assert res.status_code == 422, (query, res.text)
    lst = await _list(rung, rung.org_a, **query)
    assert lst.status_code == 422, (query, lst.text)


async def test_a_range_on_a_text_field_names_the_date_and_number_fields(rung, app_engine) -> None:
    res = await _call(rung, _admin(rung.org_a), "GET",
                      _path(entity="module:Clients", **{"field.Note.from": "1"}))
    message = res.json()["detail"]["message"]
    assert "A range takes a date or a number field." in message
    assert "The date and number fields of module:Clients are: Fee, Start." in message


async def test_the_equality_filter_and_a_date_range_answer_as_before(rung, app_engine) -> None:
    """No flag: a query that worked before gives the same answer."""
    assert (await _total(rung, rung.org_a, **{"field.Fee": "5"}))["total"] == 1
    assert (await _total(rung, rung.org_a, **{"field.Fee": "7"}))["total"] == 1
    assert (await _total(rung, rung.org_a, **{"field.Fee": "0"}))["total"] == 1
    ranged = await _total(rung, rung.org_a, **{"field.Start.from": "2026-02-01",
                                               "field.Start.to": "2026-02-28"})
    assert ranged["total"] == 2


# ── 2. q on the module aggregate and the module list ─────────────────────────


@pytest.mark.parametrize(("q", "wanted"), [
    ("acme", ["ACME INDIA", "Acme Corp", "Acme Labs"]),
    ("ACME corp", ["Acme Corp"]),
    ("O'Brien", ["O'Brien and Sons"]),
    ("café", ["Café Crème"]),
    ("Gamma_Co", ["Gamma_Co"]),
    ("50%", ["Beta 50% Off"]),
    ("zzzz", []),
])
async def test_q_matches_the_name_on_the_total_and_the_list(rung, app_engine, q: str,
                                                            wanted: list[str]) -> None:
    body = await _total(rung, rung.org_a, q=q)
    assert body["total"] == len(wanted), (q, body)
    assert await _names(rung, rung.org_a, q=q) == wanted


async def test_a_wildcard_q_is_one_answer_on_both(rung, app_engine) -> None:
    """``%`` and ``_`` stay ILIKE wildcards, as on every CRM list. The list
    and the total still agree."""
    for q in ("_", "%", "a_m", "CAFÉ"):
        body = await _total(rung, rung.org_a, q=q)
        assert body["total"] == len(await _names(rung, rung.org_a, q=q)), q


async def test_q_with_a_sum_and_a_range(rung, app_engine) -> None:
    acme = await _total(rung, rung.org_a, q="acme", measure="sum", field="Fee")
    assert (acme["records"], acme["values"], acme["total"]) == (3, 2, 5.0)
    both = await _total(rung, rung.org_a, q="acme", **{"field.Fee.from": "1"})
    assert both["total"] == 1
    assert await _names(rung, rung.org_a, q="acme", **{"field.Fee.from": "1"}) == ["Acme Corp"]


async def test_an_empty_q_is_an_absent_one(rung, app_engine) -> None:
    for q in ("", "   "):
        assert (await _total(rung, rung.org_a, q=q))["total"] == len(_CLIENTS)
        assert len(await _names(rung, rung.org_a, q=q)) == len(_CLIENTS)


@pytest.mark.parametrize("q", ["x" * 10_000, "a" * 201, "acme\x00"])
async def test_a_long_q_or_a_nul_is_a_422_on_both(rung, app_engine, q: str) -> None:
    res = await _call(rung, _admin(rung.org_a), "GET", _path(entity="module:Clients", q=q))
    assert res.status_code == 422, res.text
    assert (await _list(rung, rung.org_a, q=q)).status_code == 422


@pytest.mark.parametrize("query", [
    {},
    {"q": "acme"},
    {"field.Fee.from": "1"},
    {"field.Fee.to": "0"},
    {"field.Fee.from": "0", "field.Fee.to": "12.5", "q": "a"},
    {"field.Start.from": "2026-03-01", "q": "e"},
])
async def test_the_total_equals_the_list(rung, app_engine, query: dict) -> None:
    """One filter for both reads: the count of the aggregate is the total of
    the module list with the same query."""
    body = await _total(rung, rung.org_a, **query)
    res = await _list(rung, rung.org_a, **query)
    assert res.status_code == 200, res.text
    assert body["total"] == res.json()["total"], query


async def test_org_b_never_sees_org_a_through_q(rung, app_engine) -> None:
    b = await _total(rung, rung.org_b, q="acme", measure="sum", field="Fee")
    assert (b["records"], b["total"]) == (1, 100.0)
    assert await _names(rung, rung.org_b, q="acme") == ["Acme B Only"]
    assert "Acme B Only" not in await _names(rung, rung.org_a, q="acme")
    assert (await _total(rung, rung.org_a, q="B Only"))["total"] == 0


# ── 3. The hint of a field that is not on the module ─────────────────────────


async def _refusal(p: Any, org: str, **query: Any) -> str:
    res = await _call(p, _admin(org), "GET", _path(entity="module:Clients", **query))
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "unknown_field", detail
    return detail["message"]


async def test_a_field_of_another_module_names_that_module(rung, app_engine) -> None:
    for query in ({"field.Grand_Total.from": "1"}, {"field.grand total": "5"},
                  {"measure": "sum", "field": "Grand_Total"}):
        message = await _refusal(rung, rung.org_a, **query)
        assert "is on: 'module:Invoices'. Give that module as the entity." in message, message
    listed = await _list(rung, rung.org_a, **{"field.Grand_Total": "1"})
    assert listed.status_code == 422
    assert "'module:Invoices'" in listed.json()["detail"]["message"]


async def test_a_guessed_name_names_the_close_fields(rung, app_engine) -> None:
    message = await _refusal(rung, rung.org_a, **{"field.Fees.from": "1"})
    assert "The closest fields of module:Clients are: 'Fee'" in message
    assert "is on:" not in message


async def test_a_field_that_no_module_holds_names_no_module(rung, app_engine) -> None:
    message = await _refusal(rung, rung.org_a, **{"field.Xyzzy_Plugh": "1"})
    assert "is on:" not in message and "closest" not in message


async def test_no_label_of_another_org_reaches_the_hint(rung, app_engine) -> None:
    for name in ("Hidden_B", "hidden b"):
        message = await _refusal(rung, rung.org_a, **{f"field.{name}": "1"})
        assert "Secrets" not in message and "is on:" not in message, message
    # Org B asks the same name on its own Clients, and its own module answers.
    message = await _refusal(rung, rung.org_b, **{"field.Hidden_B": "1"})
    assert "is on: 'module:Secrets'" in message
    assert "Invoices" not in message
