"""CRM-Q34-5: the filter words of a module filter. Hermetic half (R7).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-5. The
R8 half, against a real Postgres, is ``test_crm_filter_words_r8.py``.

1. ONE parser (``field_sql.filter_end``): a key of the scope wins over a
   word, and a word is parsed only with the flag (``words``).
2. The matrix of type and word: each cell builds, or is the 422 that names
   the words that the field takes.
3. The flag off: a word is the 422 ``unknown_field`` of before, and the SQL
   of each old call is the SQL of the base commit, to the byte (hashes
   proven on ``96dad67f3``). With the flag on, an old call keeps it too.
4. ``.has`` and ``.starts`` escape ``%``, ``_`` and ``\\``. ``.in`` takes
   1 to 50 values. A lowered word builds the clause of the word, through
   the ONE builder ``_filter``.
5. The tool words each filter, and names the records with no plain number
   only for a call with a word.
"""

from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from fastapi import HTTPException
from gateway.routes.crm import aggregate as agg
from gateway.routes.crm import field_sql as fs

R = fs.FieldRef

_FIELDS = {
    "Site": R("Site", "lookup", "Site", lookup_module="Sites"),
    "Notes": R("Notes", "text", "Notes"),
    "Amount": R("Amount", "currency", "Amount"),
    "Qty": R("Qty", "number", "Qty"),
    "Status": R("Status", "select", "Status"),
    "Paid": R("Paid", "boolean", "Paid"),
    "Due": R("Due", "date", "Due"),
    "When": R("When", "datetime", "When"),
    "Tags": R("Tags", "multiselect", "Tags"),
    # A key that ends in a word: it wins over the word.
    "Notes.has": R("Notes.has", "text", "Notes has"),
}
SCOPE = fs.FieldScope(column="fields", owner="module:Invoices", fields=_FIELDS)
#: The scope with no key that ends in a word: every other test reads it.
_PLAIN_FIELDS = {k: v for k, v in _FIELDS.items() if k != "Notes.has"}
PLAIN = fs.replace(SCOPE, fields=_PLAIN_FIELDS)


def _where(pairs, words=True, scope=PLAIN, **kw):
    return fs.module_where(scope, "Invoices", list(pairs), zone="UTC", words=words, **kw)


def _refused(pairs, words=True) -> dict:
    with pytest.raises(HTTPException) as caught:
        _where(pairs, words)
    assert caught.value.status_code == 422
    return caught.value.detail


# ── 1. The one parser ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "words", "parsed"), [
    ("Notes.has", True, ("Notes.has", None)),          # the key wins
    ("Notes.starts", True, ("Notes", ".starts")),
    ("Amount.not", True, ("Amount", ".not")),
    ("Status.in", True, ("Status", ".in")),
    ("Due.empty", True, ("Due", ".empty")),
    ("Due.from", True, ("Due", ".from")),
    ("Due.to", False, ("Due", ".to")),
    ("Notes.starts", False, ("Notes.starts", None)),   # no words: a key
    ("Amount.sideways", True, ("Amount.sideways", None)),
    ("Notes", True, ("Notes", None)),
])
def test_filter_end_is_the_one_parser(name, words, parsed) -> None:
    assert fs.filter_end(name, _FIELDS, words) == parsed


def test_a_key_that_ends_in_a_word_wins_over_the_word() -> None:
    built = _where([("Notes.has", "x")], scope=SCOPE)
    assert built.params["agf_k0"] == "Notes.has"
    assert "ILIKE" not in built.sql
    plain = _where([("Notes.has", "x")])
    assert plain.params["agf_k0"] == "Notes" and "ILIKE" in plain.sql
    assert fs.lower_pair("Notes.has", "x", _FIELDS, True).op == "eq"
    assert fs.lower_pair("Notes.has", "x", (), True) == fs.QueryLeaf(
        "Notes", "contains", "x", lowered=True)


@pytest.mark.parametrize(("name", "op"), [
    ("Notes.has", "contains"), ("Notes.starts", "starts"), ("Notes.not", "ne"),
    ("Notes.in", "in"), ("Notes.empty", "is_empty"), ("Notes.from", "gte"),
    ("Notes", "eq")])
def test_each_word_lowers_to_its_op(name, op) -> None:
    assert fs.lower_pair(name, "x", (), True).op == op
    # Without words, only the two ends of before lower.
    assert fs.lower_pair(name, "x").op == (op if op in ("gte", "eq") else "eq")


# ── 2. The matrix of type and word ──────────────────────────────────────────

_GOOD = {"Amount": "5", "Qty": "5", "Due": "2026-01-05", "When": "2026-01-05",
         "Paid": "true"}
_TWO = {"Amount": "5,6", "Qty": "5,6", "Due": "2026-01-05,2026-01-06",
        "When": "2026-01-05,2026-01-06", "Paid": "true,false"}
_ALL = ("", ".from", ".to", ".has", ".starts", ".not", ".in", ".empty")


def _value(key: str, word: str) -> str:
    """A value that the field takes for *word*, so only the word decides."""
    if word == ".empty":
        return "true"
    if word in (".has", ".starts"):
        return "x"
    if word == ".in":
        return _TWO.get(key, "x,y")
    return _GOOD.get(key, "x")


#: Each field → the words it takes. A text field takes no range (it holds
#: no numbers here), and a lookup, a select and a multiselect take none.
_MATRIX = {
    "Amount": ("", ".from", ".to", ".not", ".in", ".empty"),
    "Qty": ("", ".from", ".to", ".not", ".in", ".empty"),
    "Due": ("", ".from", ".to", ".not", ".in", ".empty"),
    "When": ("", ".from", ".to", ".not", ".in", ".empty"),
    "Paid": ("", ".not", ".empty"),
    "Site": ("", ".has", ".starts", ".not", ".in", ".empty"),
    "Notes": ("", ".has", ".starts", ".not", ".in", ".empty"),
    "Status": ("", ".has", ".starts", ".not", ".in", ".empty"),
    "Tags": ("", ".has", ".starts", ".not", ".in", ".empty"),
}


@pytest.mark.parametrize("key", sorted(_MATRIX))
@pytest.mark.parametrize("word", _ALL)
def test_the_matrix_of_type_and_word(key: str, word: str) -> None:
    pair = (f"{key}{word}", _value(key, word))
    if word in _MATRIX[key]:
        built = _where([pair])
        assert built.params["agf_v0"] is not None
        return
    detail = _refused([pair])
    if word in fs.FILTER_WORDS:
        # The 422 names each word that the field takes.
        for taken in _MATRIX[key][1:]:
            assert f"field.{key}{taken}" in detail["message"], detail
    else:
        assert "A range takes a date or a number field" in detail["message"]


def test_a_text_field_that_holds_numbers_takes_a_range_and_each_word() -> None:
    numbers = fs.replace(SCOPE, fields={"T": R("T", "text", "T", text_number=True)})
    for word in _ALL:
        fs.module_where(numbers, "M", [(f"T{word}", "true" if word == ".empty" else "5")],
                        zone="UTC", words=True)


# ── 3. The flag off, and the SQL of before ──────────────────────────────────

#: The SQL and the bound values of five old calls, as sha256, proven on the
#: base commit 96dad67f3 (field_sql.py before this slice).
_OLD = [
    ([("Notes", "Abc")], "00d0043d7d17a0a70cb203e414a489fdd00bb44bd8200ee24e2dd6777c3b1560"),
    ([("Amount", "5"), ("Amount.from", "1"), ("Amount.to", "9")],
     "0335614d439870b55914f693dc3804cd4f0c78ee6d90bc4e7580ba790e5a75bd"),
    ([("Site", "Acme"), ("Status", "Open"), ("Paid", "true"), ("Due", "2026-01-02")],
     "8d3feafdc480db5ba1a9f988f09635437ea13ce41f99e7ca2aa9591ad5b38519"),
    ([("Due.from", "2026-01-01"), ("Due.to", "2026-02-01")],
     "d82dbf9304607f16fda565d7c7de205da3d6e38d4fe0a14752840ca828503645"),
    ([("Notes.has", "x")], "359d321df5c849270f47b8af3033c3b740b977664b92d5c4c4e595728b26b895"),
]
_BASE_FIELDS = {k: v for k, v in _FIELDS.items() if k not in ("Qty", "When", "Tags")}


@pytest.mark.parametrize(("pairs", "digest"), _OLD)
@pytest.mark.parametrize("words", [False, True])
def test_an_old_call_builds_the_sql_of_before(pairs, digest, words) -> None:
    scope = fs.replace(SCOPE, fields=_BASE_FIELDS)
    w = fs.module_where(scope, "Invoices", pairs, zone="UTC",
                        matches={0: ["abc", "ABC"]}, ids={0: "z1"}, words=words)
    got = hashlib.sha256(json.dumps([w.sql, w.params, w.source], default=str,
                                    sort_keys=True).encode()).hexdigest()
    assert got == digest


@pytest.mark.parametrize("name", ["Notes.starts", "Amount.not", "Status.in", "Due.empty"])
def test_off_a_word_is_the_unknown_field_of_before(name) -> None:
    detail = _refused([(name, "x")], words=False)
    assert detail["code"] == "unknown_field" and detail["key"] == name
    assert detail["message"].startswith(f"'{name}' is not a field of module:Invoices.")


def test_an_unknown_suffix_stays_the_unknown_field() -> None:
    assert _refused([("Amount.sideways", "1")])["key"] == "Amount.sideways"


def test_the_probe_of_an_old_call_is_the_pairs() -> None:
    pairs = [("Notes", "a"), ("Amount.from", "1")]
    for words in (False, True):
        _probe, owners = fs.filter_probe(SCOPE, pairs, words)
        assert owners == [(0,), (1,)]
    assert fs.filter_probe(SCOPE, pairs, False)[0] == pairs
    assert fs.matched_values(pairs, [(0,), (1,)], {0: ["a", "A"]}) == {"Notes": ["a", "A"]}


def test_the_total_and_the_list_pass_the_flag_to_the_builder() -> None:
    read = inspect.getsource(agg._read)
    assert "filter_probe(scope, pairs, finding)" in read
    assert "words=finding" in read
    helper = inspect.getsource(fs.module_field_where)
    assert "module_find_allowed" in helper and "words=words" in helper


# ── 4. The escape, the .in limits, one builder ──────────────────────────────


def test_has_and_starts_escape_each_wildcard() -> None:
    built = _where([("Notes.has", " 50%_a\\b "), ("Notes.starts", "x_")])
    assert built.params["agf_v0"] == "%50\\%\\_a\\\\b%"
    assert built.params["agf_v1"] == "x\\_%"
    assert fs.like_pattern("a%") == "%a\\%%"  # the find of CRM-Q34-7 is unchanged


def test_has_on_a_lookup_reads_the_name_and_never_the_id() -> None:
    sql = _where([("Site.has", "acme")]).sql
    assert "->> 'name'" in sql and "->> 'id'" not in sql


@pytest.mark.parametrize(("value", "ok"), [
    (",".join(f"v{i}" for i in range(50)), True),
    (",".join(f"v{i}" for i in range(51)), False),
    (" , ,", False),
])
def test_in_takes_one_to_fifty_values(value, ok) -> None:
    if ok:
        assert len(_where([("Status.in", value)]).params["agf_v0"]) == 50
    else:
        _refused([("Status.in", value)])


def test_in_splits_with_the_grammar_of_the_custom_filter() -> None:
    from gateway.routes.crm.core import in_parts

    assert in_parts(" a , b,,c ") == ["a", "b", "c"]
    assert _where([("Amount.in", "1, 2.50 ,2.50")]).params["agf_v0"] == ["1", "2.50"]
    assert _where([("Due.in", "2026-01-05,2026-01-06")]).params["agf_v0"] == [
        "2026-01-05", "2026-01-06"]


@pytest.mark.parametrize(("pair", "words"), [
    (("Amount.in", "1,abc"), "must be a number"),
    (("Due.not", "soon"), "ISO date"),
    (("Notes.empty", "maybe"), "true or false"),
    (("Notes.has", "  "), "needs some text"),
    (("Notes.has", "x" * 201), "at most 200"),
])
def test_a_bad_value_is_a_422(pair, words) -> None:
    assert words in _refused([pair])["message"]


def test_not_is_the_negation_of_eq_with_no_value_too() -> None:
    eq = _where([("Amount", "5")])
    no = _where([("Amount.not", "5")])
    clause = eq.sql.split(" AND ", 1)[1]
    assert no.sql.split(" AND ", 1)[1] == f"NOT coalesce(({clause}), false)"


def test_empty_on_a_number_reads_the_guarded_number() -> None:
    built = _where([("Amount.empty", "true")])
    assert "IS NULL) = CAST(:agf_v0 AS boolean)" in built.sql
    assert built.params["agf_v0"] is True
    text = _where([("Notes.empty", "FALSE")])
    assert text.params["agf_v0"] is False and text.params["agf_v0_nv"] == [""]
    numbers = fs.replace(SCOPE, fields={"T": R("T", "text", "T", text_number=True)})
    built = fs.module_where(numbers, "M", [("T.empty", "true")], zone="UTC", words=True)
    assert built.params["agf_v0_nv"] == list(fs.NO_VALUE_WORDS)


@pytest.mark.parametrize("name", ["Notes.has", "Notes.not", "Amount.in", "Site.starts",
                                  "Due.empty", "Status.in"])
def test_a_lowered_word_builds_the_clause_of_the_word(name) -> None:
    """One builder: a leaf lowered from a word goes through ``_filter`` and
    builds the clause of the pair, with only its bound names changed."""
    value = {"Amount.in": "1,2", "Due.empty": "true"}.get(name, "x")
    pair = _where([(name, value)])
    leaf = fs.lower_pair(name, value, _PLAIN_FIELDS, True)
    assert leaf.op == fs.FILTER_WORDS[name[name.index("."):]]
    query = fs.query_sql(PLAIN, [(leaf,)], zone="UTC")
    renamed = query.sql.replace("agf_qk0_0", "agf_k0").replace("agf_qv0_0", "agf_v0")
    assert pair.sql == f" WHERE module = :agf_module AND {renamed}"


def test_no_native_query_takes_a_word() -> None:
    assert not (set(fs.FILTER_WORDS.values()) - {"in"}) & set(fs.QUERY_OPS)


def test_used_keys_reads_a_key_word_with_a_word() -> None:
    scope = fs.replace(SCOPE, fields={**_PLAIN_FIELDS, "@status": _FIELDS["Status"]})
    assert fs.used_keys(scope, ["@status.in"]) == {"@status": "Status"}


# ── 5. The tool text ─────────────────────────────────────────────────────────


def test_the_filter_line_words_each_word() -> None:
    from acb_skills.crm_tools import filters_line

    params = {"field.Notes.has": "50%", "field.Site.starts": "Ac", "field.Status.not": "Won",
              "field.Status.in": "Open, Lost", "field.Due.empty": "true",
              "field.Amount.empty": "false"}
    line = filters_line(params, {"matched": {"Status.in": ["Open", "OPEN", "Lost"]}})
    assert line == (
        "Filters: Notes contains '50%'; Site starts with 'Ac'; Status is not Won; "
        "Status is one of Open, Lost (counted Open, OPEN and Lost); Due has no value; "
        "Amount has a value.")


def test_the_no_number_line_needs_a_word() -> None:
    from acb_skills.crm_tools import no_number_line

    data = {"field": "Amount", "field_label": "Amount", "values": 3, "records": 5}
    assert no_number_line({"field.Status": "Open"}, data) == ""
    assert no_number_line({"field.Status.in": "Open"}, data) == (
        "2 of 5 matching records hold no plain number in Amount, so the total leaves "
        "them out.")
    assert no_number_line({"field.Status.in": "Open"}, {**data, "values": 5}) == ""
    assert no_number_line({"field.Status.in": "Open"},
                          {**data, "text_numbers": {"Amount": {}}}) == ""


def test_the_field_search_names_the_words() -> None:
    from acb_skills.crm_tools import FIELDS_SEARCH_HINT

    for word in fs.FILTER_WORDS:
        assert word in FIELDS_SEARCH_HINT
