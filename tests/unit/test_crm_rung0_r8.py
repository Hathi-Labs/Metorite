"""R8 — CRM Rung 0 (CRM-Q34-1 and CRM-Q34-2, tasks 3693 and 3694): a number
range, a lookup by the id of a core record, and refusals that name a step
that works.

Spec: ``project-docs/specs/crm_platform.md`` §18.17.
Production log of 2026-11-17: 10 of 61 ``crm_aggregate`` calls of a member
got 422, although the fields existed.

Against a REAL Postgres, through the real ``/crm`` router, as the app role.

1. ``field.<number>.from`` / ``.to`` read the guarded number. A dirty value
   (``"Rs. 0.00"``, ``"NA"``, ``"-"``, ``""``, an object, an array,
   ``"1e999"``, ``"12,50"``) is NO value, so no end of a range takes it, and
   no value gives a 500. A JSON number and a plain decimal string are values.
2. A lookup filter that carries the Metorite id of a core contact counts the
   records whose lookup holds the contact's Zoho id. The id of an org B
   contact, and a uuid that is no contact, count none.
3. The 422 of a field that is not on the module names the close fields and
   the other modules that hold it, and never a label of another org.
4. ``q`` on a module total is a 422 that names the fields to use.
   ``group_by=status`` reads ``@status``, or names the select fields.
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
    keyed,
    mods,
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
    ("Contact", "lookup", {"lookup_module": "Contacts"}, False, False),
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
        # CRM-Q34-1: a core contact of each org with the SAME Zoho id, and a
        # Native contact with none. Three bills point at Zoho contacts.
        ns.contact_a = _contact(c, a, "z-c1")
        ns.contact_b = _contact(c, b, "z-c1")
        ns.native_a = _contact(c, a, None)
        ravi = {"id": "z-c1", "name": "Ravi Kumar"}
        _named_record(c, a, "Clients", "l1", "Ravi bill 1", {"Contact": ravi})
        _named_record(c, a, "Clients", "l2", "Ravi bill 2", {"Contact": ravi})
        _named_record(c, a, "Clients", "l3", "Other bill",
                      {"Contact": {"id": "z-c9", "name": "Someone"}})
        _named_record(c, b, "Clients", "l1", "B bill", {"Contact": ravi})
    return ns


def _contact(c: Any, org: str, ext_id: str | None) -> str:
    return str(c.execute(text(
        "INSERT INTO crm_contacts (organization_id, first_name, ext_source, ext_id) "
        "VALUES (CAST(:o AS uuid), 'Ravi', :s, :e) RETURNING id"),
        {"o": org, "s": "zoho" if ext_id else None, "e": ext_id}).scalar())


async def _total(p: Any, org: str, **query: Any) -> dict:
    res = await _call(p, _admin(org), "GET", _path(entity="module:Clients", **query))
    assert res.status_code == 200, (query, res.text)
    return res.json()


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
        # The hint stands before the list of keys: the tool cuts at 800 characters.
        assert message.index("is on:") < message.index("Its fields are"), message


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


# ── 4. A lookup by the Metorite id of a core contact (CRM-Q34-1) ─────────────


async def _contacts(p: Any, org: str, value: str) -> int:
    body = await _total(p, org, **{"field.Contact": value})
    return body["total"]


async def test_a_lookup_by_a_core_contact_id_counts_its_records(rung, app_engine) -> None:
    """Before CRM-Q34-1 the id read only the module records, and the total
    was a silent zero."""
    a = rung.org_a
    assert await _contacts(rung, a, rung.contact_a) == 2
    assert await _contacts(rung, a, rung.contact_a.upper()) == 2
    # The Zoho id and the exact name answer as before.
    assert await _contacts(rung, a, "z-c1") == 2
    assert await _contacts(rung, a, "Ravi Kumar") == 2
    # Org B reads its own contact, and its own bill.
    assert await _contacts(rung, rung.org_b, rung.contact_b) == 1


async def test_a_contact_id_of_another_org_or_no_contact_counts_none(rung, app_engine) -> None:
    a = rung.org_a
    assert await _contacts(rung, a, rung.contact_b) == 0
    assert await _contacts(rung, rung.org_b, rung.contact_a) == 0
    assert await _contacts(rung, a, "77309599-0000-4000-8000-00000000dead") == 0
    # A Native contact has no Zoho id, so it matches no stored lookup.
    assert await _contacts(rung, a, rung.native_a) == 0


@pytest.mark.parametrize("value", [
    "x'; DROP TABLE crm_contacts; --", "' OR '1'='1", "z-c1' OR 1=1 --",
])
async def test_a_hostile_lookup_value_matches_nothing(rung, app_engine, value: str) -> None:
    res = await _call(rung, _admin(rung.org_a), "GET",
                      _path(entity="module:Clients", **{"field.Contact": value}))
    assert res.status_code in (200, 422), res.text
    if res.status_code == 200:
        assert res.json()["total"] == 0
    with rung.admin_engine.begin() as c:
        left = c.execute(text("SELECT count(*) FROM crm_contacts WHERE organization_id = "
                              "CAST(:o AS uuid)"), {"o": rung.org_a}).scalar()
    assert left == 2


# ── 5. q on a module, and group_by=status (CRM-Q34-2) ────────────────────────


async def test_q_on_a_module_names_the_lookup_and_text_fields(rung, app_engine) -> None:
    for query in ({"q": "Ravi"}, {"q": "Ravi", "group_by": "status"}, {"q": ""}):
        res = await _call(rung, _admin(rung.org_a), "GET",
                          _path(entity="module:Clients", **query))
        assert res.status_code == 422, (query, res.text)
        message = res.json()["detail"]["message"]
        assert message.startswith("A module total takes no q."), message
        assert "field.<lookup>=<the exact name>" in message
        assert "The lookup fields of module:Clients are: 'Contact' (to 'Contacts')." in message
        assert "The text fields of module:Clients are: Note." in message


async def test_group_by_status_with_no_status_field_names_the_selects(rung, app_engine) -> None:
    res = await _call(rung, _admin(rung.org_a), "GET",
                      _path(entity="module:Clients", group_by="status"))
    assert res.status_code == 422, res.text
    assert ("Give group_field=<key> with one of its select fields: Category."
            in res.json()["detail"]["message"])


async def test_group_by_status_reads_the_status_key_field(keyed, app_engine) -> None:
    a = keyed.org_a
    by_status = await _total_of(keyed, a, entity="module:Invoices", group_by="status")
    by_field = await _total_of(keyed, a, entity="module:Invoices", group_field="Status")
    assert [(g["key"], g["value"]) for g in by_status["groups"]] == [
        (g["key"], g["value"]) for g in by_field["groups"]] == [("Paid", 3)]
    assert by_status["keys"] == {"@status": "Status"}


async def _total_of(p: Any, org: str, **query: Any) -> dict:
    res = await _call(p, _admin(org), "GET", _path(**query))
    assert res.status_code == 200, (query, res.text)
    return res.json()
