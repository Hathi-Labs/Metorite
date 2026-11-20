"""R8 — the filter words of a module filter (WS-53 CRM-Q34-5).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-5.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser. A module ``Invoices`` holds a lookup ``Site``, a text ``Notes``
with hostile values, a currency ``Amount`` with dirty values, a select
``Status``, a boolean ``Paid`` and a date ``Due``.

1. Each word: the total, the list and an INDEPENDENT SQL count of the test
   hold the same records, and the names are the ones written here by hand.
2. ``%``, ``_``, ``\\``, quotes, 5 KB, RTL and zero-width text are literal.
   ``.has`` reads its own field only, and a lookup reads its name only.
3. ``.not`` holds the records with no value. ``.empty`` holds them, for
   each type. A number field reads a value that is no plain number as empty.
4. ``.in`` with 51 values is a 422 on both. Org B never counts.
5. The answer of a sum with a word says how many records hold no plain
   number. The flag off: a word is the 422 of before, and an old call
   keeps its answer to the byte.
6. ``.has`` over 30,000 rows of 3 KB answers inside the 10 s timeout.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import _defs, _mirror_org, _module
from tests.unit.test_crm_module_find_r8 import (  # noqa: F401
    _BIG,
    TIMEOUT_S,
    _flag,
    _list,
    _timed,
    _total,
    big,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_DEFS = [
    ("Site", "lookup", {"lookup_module": "Sites"}, False, False),
    ("Notes", "text", None, False, False),
    ("Other", "text", None, False, False),
    ("Amount", "currency", None, False, False),
    ("Status", "select", None, False, False),
    ("Paid", "boolean", None, False, False),
    ("Due", "date", None, False, False),
    # Fix round: a text field that holds Zoho objects (an unknown Zoho type
    # such as a multi-user lookup maps to text), and a text field of numbers.
    ("Tags", "text", None, False, False),
    ("QtyT", "text", None, False, False),
]

#: Fix round: the Tags and QtyT value of some rows of org A. A Zoho id in
#: Tags is digits, so `.has=<digits>` would count an id if the JSON text
#: of an object were read.
_EXTRA: dict[str, dict[str, Any]] = {
    "R01": {"Tags": [{"id": "9001", "name": "Red"}], "QtyT": "5"},
    "R02": {"Tags": ["Blue", "Green"], "QtyT": "NA"},
    "R03": {"Tags": {"id": "9002", "name": "Red Rose"}, "QtyT": "7"},
    "R04": {"Tags": [[{"id": "9003", "name": "Red"}]], "QtyT": "-"},
    "R05": {"Tags": [{"id": "9004"}, 42], "QtyT": "  "},
    "R06": {"QtyT": "10"},
    # Round 3: hostile names. A name that is an object, a list, a boolean or
    # null holds no member text, so its JSON text never matches.
    "R07": {"Tags": [{"id": "t2", "name": {"id": "inner", "name": "Deep"}}]},
    "R08": {"Tags": {"id": "9005", "name": {"x": "idq"}}},
    "R09": {"Tags": [{"id": "9006", "name": None}]},
    "R10": {"Tags": [{"name": ["xq"]}]},
    "R11": {"Tags": [{"name": True}]},
    "R12": {"Tags": {"id": "9007", "name": ["idz"]}},
    "R13": {"Site": {"id": "s11", "name": {"id": "in2"}}},
}

_FIVE_KB = "x" * 5000 + " needle"

#: Org A: (name, fields). A missing key is a field with no value.
_ROWS_A: list[tuple[str, dict[str, Any]]] = [
    ("R01", {"Site": {"id": "s1", "name": "50% Off Ltd"}, "Notes": "has 100% literal",
             "Amount": 100, "Status": "Open", "Paid": True, "Due": "2026-01-05"}),
    ("R02", {"Site": {"id": "s2", "name": "Under_score Co"}, "Notes": "under_score here",
             "Amount": "200", "Status": "open ", "Paid": False, "Due": "2026-02-30"}),
    ("R03", {"Site": {"id": "s3", "name": "O'Brien & Sons"}, "Notes": "back\\slash path",
             "Amount": "NA", "Status": "Won", "Due": None}),
    ("R04", {"Site": {"id": "s4", "name": "Café Mōra"}, "Notes": "it's 'quoted'",
             "Amount": "abc", "Status": None, "Paid": True}),
    ("R05", {"Site": {"id": "s5", "name": "Acme"}, "Notes": _FIVE_KB, "Amount": None,
             "Status": "Lost"}),
    ("R06", {"Site": {"id": "s6", "name": "Acme"}, "Notes": "مرحبا بالعالم",
             "Amount": " 300 ", "Status": "Open"}),
    ("R07", {"Site": {"id": "s7", "name": "Acme West"}, "Notes": "zero​width",
             "Amount": "1e5", "Due": "2026-01-05"}),
    ("R08", {"Site": {"id": "s8", "name": "CAFÉ MŌRA"}, "Notes": "  NA  ", "Amount": 0}),
    ("R09", {"Notes": "", "Amount": "12,50"}),
    ("R10", {"Site": None, "Notes": None, "Amount": "-5", "Status": "", "Paid": None}),
    ("R11", {"Site": "s9", "Notes": ["a%b", "plain"], "Amount": 7.5, "Status": "Won"}),
    ("R12", {"Site": {"id": "s10", "name": "Other"},
             "Notes": {"id": "n1", "name": "Obj name 100%"}, "Amount": "   ",
             "Status": "Won", "Due": "2026-03-01"}),
    ("R13", {"Notes": "   ", "Other": "needle has% elsewhere"}),
    ("R14", {"Notes": [], "Amount": "100"}),
]
_ALL = sorted(name for name, _f in _ROWS_A)
_ROWS_B: list[tuple[str, dict[str, Any]]] = [
    ("B01", {"Site": {"id": "s1", "name": "Acme"}, "Notes": "has 100% literal needle",
             "Amount": 100, "Status": "Open", "Paid": True, "Due": "2026-01-05"}),
    ("B02", {"Notes": None, "Amount": "NA"}),
]


def _but(*names: str) -> list[str]:
    return [n for n in _ALL if n not in names]


def _seed(p: Any, org: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    with p.admin_engine.begin() as c:
        for i, (name, fields) in enumerate(rows):
            fields = {**fields, **_EXTRA.get(name, {})} if org == p.org_a else fields
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Invoices', 'zoho', "
                ":e, :n, CAST(:f AS jsonb))"),
                {"o": org, "e": f"e{i}", "n": name, "f": json.dumps(fields)})


@pytest.fixture
def wd(ns):
    a, b = ns.org_a, ns.org_b
    _mirror_org(ns, a, [_module("Invoices")])
    _mirror_org(ns, b, [_module("Invoices")])
    _defs(ns, a, "module:Invoices", _DEFS)
    _defs(ns, b, "module:Invoices", _DEFS)
    _seed(ns, a, _ROWS_A)
    _seed(ns, b, _ROWS_B)
    return ns


# ── The independent SQL: other functions than the builder, on purpose ────────

#: The text of Notes as the member reads it: a string, a number, the name of
#: an object, or each element of a list. Never the JSON text.
_NOTE_TEXTS = (
    "SELECT t FROM (SELECT CASE WHEN jsonb_typeof(fields -> 'Notes') IN ('string', 'number') "
    "THEN fields ->> 'Notes' WHEN jsonb_typeof(fields -> 'Notes') = 'object' "
    "THEN fields -> 'Notes' ->> 'name' END AS t UNION ALL SELECT jsonb_array_elements_text("
    "CASE WHEN jsonb_typeof(fields -> 'Notes') = 'array' THEN fields -> 'Notes' "
    "ELSE '[]'::jsonb END)) AS agf_t WHERE t IS NOT NULL"
)
#: The name of a Site object. A string lookup holds an id, so no name.
_SITE_NAME = ("CASE WHEN jsonb_typeof(fields -> 'Site') = 'object' "
              "THEN fields -> 'Site' ->> 'name' END")
#: A plain number of Amount, written with other functions: the shape check
#: by character classes, then the cast.
_AMOUNT = ("CASE WHEN jsonb_typeof(fields -> 'Amount') = 'number' THEN "
           "(fields ->> 'Amount')::numeric WHEN jsonb_typeof(fields -> 'Amount') = 'string' "
           "AND trim(fields ->> 'Amount') ~ '^-?[[:digit:]]+([.][[:digit:]]+)?$' "
           "THEN trim(fields ->> 'Amount')::numeric END")


def _no_value(key: str) -> str:
    """No value: missing, null, blank text or an empty list."""
    return (f"(NOT fields ? '{key}' OR fields -> '{key}' = 'null'::jsonb "
            f"OR fields -> '{key}' = '[]'::jsonb OR (jsonb_typeof(fields -> '{key}') = "
            f"'string' AND trim(fields ->> '{key}', E' \\t\\r\\n') = ''))")


#: Each case: the query words, the names written by hand, and the
#: independent predicate. ``:w`` is bound to the text of the case.
_CASES: list[tuple[dict[str, str], list[str], str, str]] = [
    ({"field.Notes.has": "%"}, ["R01", "R11", "R12"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "%"),
    ({"field.Notes.has": "_"}, ["R02"], f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "_"),
    ({"field.Notes.has": "\\"}, ["R03"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "\\"),
    ({"field.Notes.has": "NEEDLE"}, ["R05"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(lower(t), lower(:w)) > 0)", "NEEDLE"),
    ({"field.Notes.has": "'quoted'"}, ["R04"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "'quoted'"),
    ({"field.Notes.has": "مرحبا"}, ["R06"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "مرحبا"),
    ({"field.Notes.has": "zero​width"}, ["R07"],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "zero​width"),
    ({"field.Notes.has": "zerowidth"}, [], f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)",
     "zerowidth"),
    # `.has` reads Notes only: R13 holds the word in another field.
    ({"field.Notes.has": "elsewhere"}, [],
     f"EXISTS ({_NOTE_TEXTS} AND strpos(t, :w) > 0)", "elsewhere"),
    ({"field.Notes.starts": "UNDER"}, ["R02"],
     f"EXISTS ({_NOTE_TEXTS} AND left(lower(trim(t)), 5) = lower(:w))", "UNDER"),
    ({"field.Notes.starts": "a%"}, ["R11"],
     f"EXISTS ({_NOTE_TEXTS} AND left(trim(t), 2) = :w)", "a%"),
    ({"field.Site.has": "acme"}, ["R05", "R06", "R07"],
     f"strpos(lower({_SITE_NAME}), :w) > 0", "acme"),
    ({"field.Site.has": "café"}, ["R04", "R08"], f"strpos(lower({_SITE_NAME}), :w) > 0",
     "café"),
    ({"field.Site.has": "o'brien"}, ["R03"], f"strpos(lower({_SITE_NAME}), :w) > 0",
     "o'brien"),
    ({"field.Site.has": "50%"}, ["R01"], f"strpos(lower({_SITE_NAME}), :w) > 0", "50%"),
    # A lookup never matches its id, as an object or as a string.
    ({"field.Site.has": "s9"}, [], f"strpos(lower({_SITE_NAME}), :w) > 0", "s9"),
    ({"field.Site.has": "s1"}, [], f"strpos(lower({_SITE_NAME}), :w) > 0", "s1"),
    ({"field.Site.starts": "acme"}, ["R05", "R06", "R07"],
     f"lower({_SITE_NAME}) LIKE 'acme' || '%'", ""),
    ({"field.Site.in": "Acme,O'Brien & Sons"}, ["R03", "R05", "R06"],
     f"{_SITE_NAME} IN ('Acme', 'O''Brien & Sons')", ""),
    ({"field.Site.in": "s1, s10"}, ["R01", "R12"],
     "fields -> 'Site' ->> 'id' IN ('s1', 's10')", ""),
    ({"field.Site.not": "Acme"}, _but("R05", "R06"),
     f"{_SITE_NAME} IS DISTINCT FROM 'Acme'", ""),
    ({"field.Status.not": "Open"}, _but("R01", "R02", "R06"),
     "lower(trim(fields ->> 'Status')) IS DISTINCT FROM 'open'", ""),
    ({"field.Status.in": "Won,Lost"}, ["R03", "R05", "R11", "R12"],
     "fields ->> 'Status' IN ('Won', 'Lost')", ""),
    ({"field.Status.empty": "true"}, ["R04", "R07", "R08", "R09", "R10", "R13", "R14"],
     _no_value("Status"), ""),
    ({"field.Status.empty": "false"}, ["R01", "R02", "R03", "R05", "R06", "R11", "R12"],
     f"NOT {_no_value('Status')}", ""),
    # A plain text field reads "NA" as a value (CRM-Q34-11 is for numbers).
    ({"field.Notes.empty": "true"}, ["R09", "R10", "R13", "R14"], _no_value("Notes"), ""),
    ({"field.Amount.empty": "true"}, ["R03", "R04", "R05", "R07", "R09", "R12", "R13"],
     f"({_AMOUNT}) IS NULL", ""),
    ({"field.Amount.empty": "false"}, ["R01", "R02", "R06", "R08", "R10", "R11", "R14"],
     f"({_AMOUNT}) IS NOT NULL", ""),
    ({"field.Amount.not": "100"}, _but("R01", "R14"),
     f"({_AMOUNT}) IS DISTINCT FROM 100", ""),
    ({"field.Amount.in": "100, 300,-5"}, ["R01", "R06", "R10", "R14"],
     f"({_AMOUNT}) IN (100, 300, -5)", ""),
    ({"field.Paid.not": "true"}, _but("R01", "R04"),
     "fields -> 'Paid' IS DISTINCT FROM 'true'::jsonb", ""),
    ({"field.Paid.empty": "true"}, _but("R01", "R02", "R04"), _no_value("Paid"), ""),
    ({"field.Due.in": "2026-01-05,2026-03-01"}, ["R01", "R07", "R12"],
     "fields ->> 'Due' IN ('2026-01-05', '2026-03-01')", ""),
    ({"field.Due.not": "2026-01-05"}, _but("R01", "R07"),
     "fields ->> 'Due' IS DISTINCT FROM '2026-01-05'", ""),
    # A day that does not exist is a value: `.empty` reads the stored value.
    ({"field.Due.empty": "true"}, _but("R01", "R02", "R07", "R12"), _no_value("Due"), ""),
    # Two words together, and a word with an old filter.
    ({"field.Status.in": "Open,Won", "field.Amount.empty": "false"},
     ["R01", "R02", "R06", "R11"],
     f"lower(trim(fields ->> 'Status')) IN ('open', 'won') AND ({_AMOUNT}) IS NOT NULL", ""),
    ({"field.Site.has": "acme", "field.Amount.from": "300"}, ["R06"],
     f"strpos(lower({_SITE_NAME}), 'acme') > 0 AND ({_AMOUNT}) >= 300", ""),
]


async def _independent(p: Any, org: str, where: str, word: str) -> list[str]:
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT name FROM crm_module_records WHERE organization_id = CAST(:o AS uuid) "
            f"AND module = 'Invoices' AND ({where}) ORDER BY name"), {"o": org, "w": word})
        return [r[0] for r in rows]


async def _both(p: Any, org: str, query: dict[str, str]) -> tuple[dict, list[str]]:
    total = await _total(p, org, **query)
    page = await _list(p, org, **query)
    assert total.status_code == 200, (query, total.text)
    assert page.status_code == 200, (query, page.text)
    names = sorted(r["name"] for r in page.json()["rows"])
    assert total.json()["records"] == page.json()["total"] == len(names), (query, names)
    return total.json(), names


# ── 1 to 3. Each word, on the total, the list and the independent count ──────


@pytest.mark.parametrize(("query", "names", "where", "word"), _CASES,
                         ids=[" ".join(f"{k}={v}" for k, v in c[0].items()) for c in _CASES])
async def test_each_word_counts_the_records_of_the_independent_sql(
        wd, app_engine, query, names, where, word) -> None:
    _total_json, listed = await _both(wd, wd.org_a, query)
    assert listed == names
    assert await _independent(wd, wd.org_a, where, word) == names


async def test_not_and_eq_split_the_module(wd, app_engine) -> None:
    """``.not`` is the exact negation of ``=``: the two hold every record
    once, the records with no value too."""
    for key, value in (("Status", "Open"), ("Amount", "100"), ("Site", "Acme"),
                       ("Paid", "false"), ("Due", "2026-01-05"), ("Notes", "  NA  ")):
        _e, eq = await _both(wd, wd.org_a, {f"field.{key}": value})
        _n, no = await _both(wd, wd.org_a, {f"field.{key}.not": value})
        assert sorted(eq + no) == _ALL, key
        assert not set(eq) & set(no), key


# ── 4. The limits, and org B ─────────────────────────────────────────────────


async def test_in_with_51_values_is_a_422_on_both(wd, app_engine) -> None:
    many = ",".join(f"v{i}" for i in range(51))
    for call in (_total, _list):
        res = await call(wd, wd.org_a, **{"field.Status.in": many})
        assert res.status_code == 422 and "at most 50 values" in res.text
    _t, names = await _both(wd, wd.org_a, {"field.Status.in": many.rsplit(",", 1)[0]})
    assert names == []


@pytest.mark.parametrize(("query", "says"), [
    ({"field.Amount.has": "1"}, "field.Amount.not"),
    ({"field.Due.starts": "2026"}, "field.Due.in"),
    ({"field.Paid.in": "true,false"}, "field.Paid.empty"),
])
async def test_a_word_that_the_type_does_not_take_is_a_422_on_both(
        wd, app_engine, query, says) -> None:
    for call in (_total, _list):
        res = await call(wd, wd.org_a, **query)
        assert res.status_code == 422 and says in res.text, res.text


def _member_texts(value: Any) -> list[str]:
    """The member text of one stored value, by an independent definition in
    Python: a string, a number, or the name of an object when that name is a
    string or a number. In a list, each such element. Never JSON text, and
    never an element that is itself a list."""
    def plain(v: Any) -> str | None:
        if isinstance(v, str):
            return v
        if isinstance(v, int | float) and not isinstance(v, bool):
            return json.dumps(v)
        return None

    def text_of(v: Any) -> str | None:
        return plain(v.get("name")) if isinstance(v, dict) else plain(v)

    items = value if isinstance(value, list) else [value]
    if isinstance(value, list):
        return [t for t in (text_of(e) for e in items if not isinstance(e, list)) if t is not None]
    one = text_of(value)
    return [one] if one is not None else []


async def _oracle(p: Any, org: str, key: str, word: str, starts: bool) -> list[str]:
    """The names of the rows whose *key* holds *word*, by :func:`_member_texts`.
    The case folds as ``lower`` does, so the words of these tests are ASCII."""
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT name, fields FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Invoices' ORDER BY name"), {"o": org}).fetchall()
    out = []
    for name, fields in rows:
        if key not in fields:
            continue
        texts = [t.strip().lower() for t in _member_texts(fields[key])]
        hit = (any(t.startswith(word.lower()) for t in texts) if starts
               else any(word.lower() in t for t in texts))
        if hit:
            out.append(name)
    return out


@pytest.mark.parametrize(("query", "names"), [
    ({"field.Tags.has": "id"}, []),
    ({"field.Tags.has": '"'}, []),
    ({"field.Tags.has": ":"}, []),
    ({"field.Tags.has": "9001"}, []),
    ({"field.Tags.has": "900"}, []),
    ({"field.Tags.has": "{"}, []),
    ({"field.Tags.has": "["}, []),
    ({"field.Tags.has": "inner"}, []),
    ({"field.Tags.has": "deep"}, []),
    ({"field.Tags.has": "idq"}, []),
    ({"field.Tags.has": "idz"}, []),
    ({"field.Tags.has": "xq"}, []),
    ({"field.Tags.has": "true"}, []),
    ({"field.Tags.has": "null"}, []),
    ({"field.Tags.has": "red"}, ["R01", "R03"]),
    ({"field.Tags.has": "42"}, ["R05"]),
    ({"field.Tags.has": "green"}, ["R02"]),
    ({"field.Tags.starts": "Red"}, ["R01", "R03"]),
    ({"field.Tags.starts": "R"}, ["R01", "R03"]),
    ({"field.Tags.starts": "id"}, []),
    ({"field.Tags.starts": "{"}, []),
    ({"field.Tags.starts": "["}, []),
    ({"field.Site.has": "in2"}, []),
    ({"field.Site.has": "{"}, []),
])
async def test_has_on_a_list_of_objects_reads_names_and_never_json(
        wd, app_engine, query, names) -> None:
    _t, listed = await _both(wd, wd.org_a, query)
    assert listed == names
    (name, word), = query.items()
    key = name[len("field."):].rsplit(".", 1)[0]
    assert await _oracle(wd, wd.org_a, key, word, name.endswith(".starts")) == names


async def test_q_reads_no_json_text_of_a_name(wd, app_engine) -> None:
    """The module find (CRM-Q34-7) reads the same guarded name: ``q`` finds
    no row by the JSON text of a name that is an object or a list."""
    for q in ("idq", "idz", "in2"):
        total = await _total(wd, wd.org_a, q=q)
        page = await _list(wd, wd.org_a, q=q)
        assert total.json()["records"] == page.json()["total"] == 0, q
    # A string name still reads. `q` reads no list (CRM-Q34-7), so the list
    # of R01 stays out, and the object name "Red Rose" of R03 is found.
    total, page = await _total(wd, wd.org_a, q="Red"), await _list(wd, wd.org_a, q="Red")
    assert sorted(r["name"] for r in page.json()["rows"]) == ["R03"]
    assert total.json()["records"] == 1


async def test_empty_on_a_text_field_has_one_meaning(wd, app_engine) -> None:
    """QtyT holds numbers, so a sum of it reads them (CRM-Q34-11). Its
    `.empty` still counts only a blank or a missing value: the same records
    with a count, with a sum of QtyT and on the list."""
    query = {"field.QtyT.empty": "true"}
    _t, listed = await _both(wd, wd.org_a, query)
    summed = await _total(wd, wd.org_a, measure="sum", field="QtyT", **query)
    assert summed.status_code == 200, summed.text
    expected = ["R05", *(n for n in _ALL if n >= "R07")]
    assert listed == expected
    assert summed.json()["records"] == len(expected)
    assert await _independent(wd, wd.org_a, _no_value("QtyT"), "") == expected


async def test_the_answer_names_the_words_it_read(wd, app_engine) -> None:
    word = await _total(wd, wd.org_a, **{"field.Notes.has": "x", "field.Status": "Open"})
    assert word.json()["filter_words"] == ["Notes.has"]
    plain = await _total(wd, wd.org_a, **{"field.Status": "Open"})
    assert "filter_words" not in plain.json()


async def test_org_b_never_counts(wd, app_engine) -> None:
    _t, a = await _both(wd, wd.org_a, {"field.Notes.has": "needle"})
    _t, b = await _both(wd, wd.org_b, {"field.Notes.has": "needle"})
    assert a == ["R05"] and b == ["B01"]
    _t, a = await _both(wd, wd.org_a, {"field.Amount.empty": "true"})
    assert "B02" not in a


# ── 5. The answer of a sum, and the flag ─────────────────────────────────────


async def test_a_sum_with_a_word_says_how_many_records_hold_no_number(wd, app_engine) -> None:
    from acb_skills.crm_tools import no_number_line

    query = {"field.Status.in": "Open,Won"}
    res = await _total(wd, wd.org_a, measure="sum", field="Amount", **query)
    body = res.json()
    # Open: R01, R02, R06. Won: R03, R11, R12. Numbers: 100, 200, 300, 7.5.
    assert (body["records"], body["values"], body["total"]) == (6, 4, 607.5)
    assert no_number_line(query, body) == (
        "2 of 6 matching records hold no plain number in Amount, so the total leaves "
        "them out.")


#: Two old calls and their answers with the flag OFF, recorded on the base
#: commit 96dad67f3: this test run with the base field_sql.py, aggregate.py,
#: modules.py and core.py in place.
_OLD: list[tuple[dict[str, str], str]] = [
    ({"field.Status": "Open"},
     '{"entity":"module:Invoices","measure":"count","group_by":null,"group_field":null,'
     '"field":null,"field_type":null,"field_label":null,"group_field_label":null,'
     '"keys":{},"group_field_type":null,"bucket":null,"groups":[],"total":3.0,'
     '"other":0.0,"omitted":0,"zone":"UTC","values":null,"records":3,'
     '"matched":{"Status":["Open","open"]}}'),
    ({"measure": "sum", "field": "Amount", "field.Amount.from": "7"},
     '{"entity":"module:Invoices","measure":"sum","group_by":null,"group_field":null,'
     '"field":"Amount","field_type":"currency","field_label":"Amount",'
     '"group_field_label":null,"keys":{},"group_field_type":null,"bucket":null,'
     '"groups":[],"total":707.5,"other":0.0,"omitted":0,"zone":"UTC","values":5,'
     '"records":5}'),
]


async def test_the_flag_off_keeps_the_answers_of_before(wd, app_engine, monkeypatch) -> None:
    on = [(await _total(wd, wd.org_a, **q)).content for q, _r in _OLD]
    _flag(monkeypatch, "")
    refused = await _total(wd, wd.org_a, **{"field.Notes.has": "x"})
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["code"] == "unknown_field" and detail["key"] == "Notes.has"
    page = await _list(wd, wd.org_a, **{"field.Notes.has": "x"})
    assert page.status_code == 422 and "crm_aggregate" in page.text
    off = [(await _total(wd, wd.org_a, **q)).content for q, _r in _OLD]
    print("\nCRM-Q34-5 old answers:", [o.decode() for o in off])
    assert off == on
    for got, (_q, recorded) in zip(off, _OLD, strict=True):
        assert got.decode() == recorded


# ── 6. 30,000 rows of 3 KB ───────────────────────────────────────────────────


async def test_has_over_thirty_thousand_rows_answers_inside_the_timeout(big, app_engine) -> None:
    from gateway.routes.crm import field_sql as fs

    agg = "/crm/aggregate?entity=module:Ledger&"
    lst = "/crm/modules/Ledger/records?page_size=50&"
    paths = {
        "total_text": agg + urlencode({"field.Field_7.has": "zzz"}),
        "total_text_hit": agg + urlencode({"field.Field_7.has": "GH"}),
        "list_text_hit": lst + urlencode({"field.Field_7.has": "GH"}),
        "total_lookup": agg + urlencode({"field.Party.has": "party 42"}),
        "list_lookup": lst + urlencode({"field.Party.has": "party 42"}),
        "total_in": agg + urlencode({"field.Party.in": "Party 1,Party 2"}),
        "total_empty": agg + urlencode({"field.Amount.empty": "false"}),
    }
    timed = await _timed(big, big.org_a, paths)
    print("\nCRM-Q34-5 30,000 rows of 3 KB, seconds:",
          {name: round(t, 3) for name, (_s, _b, t) in timed.items()})
    for name, (status, body, seconds) in timed.items():
        assert status == 200, (name, body)
        assert seconds < TIMEOUT_S, (name, seconds)
    with big.admin_engine.begin() as c:
        hits = c.execute(text(
            "SELECT count(*) FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Ledger' AND "
            "strpos(lower(fields ->> 'Field_7'), 'gh') > 0"), {"o": big.org_a}).scalar()
    assert 0 < hits < _BIG
    assert timed["total_text"][1]["records"] == 0
    assert timed["total_text_hit"][1]["records"] == timed["list_text_hit"][1]["total"] == hits
    # "Party 42" and "Party 420" to "Party 429": 11 names of 60 rows each.
    assert timed["total_lookup"][1]["records"] == timed["list_lookup"][1]["total"] == 11 * 60
    assert timed["total_in"][1]["records"] == 2 * (_BIG // 500)
    assert timed["total_empty"][1]["records"] == _BIG
    # The plan of `.has` on a text field, as the superuser, with the builder's SQL.
    scope = fs.FieldScope(column="fields", owner="module:Ledger", fields={
        "Field_7": fs.FieldRef(key="Field_7", type="text", label="Field 7")})
    where = fs.module_where(scope, "Ledger", [("Field_7.has", "gh")], zone="UTC", words=True)
    with big.admin_engine.begin() as c:
        plan = [r[0] for r in c.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM crm_module_records"
            f"{where.source}{where.sql} AND organization_id = CAST(:o AS uuid)"),
            {"o": big.org_a, **where.params})]
    print("\n".join(plan))
    took = next(line for line in plan if line.startswith("Execution Time"))
    assert float(took.split()[2]) < TIMEOUT_S * 1000, took
