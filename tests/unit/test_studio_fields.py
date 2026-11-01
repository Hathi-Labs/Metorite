"""The field types of a Studio object, and the one check of each value (WS-STU C1).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1 row. Code:
``packages/acb_common/acb_common/studio_fields.py``. This file needs no
database. ``test_studio_objects_r8.py`` runs the store on a real Postgres.

**Acceptance (C1, the hermetic half).**

a. Each of the 16 types takes a good value and refuses a bad one. Each
   refusal is a ``FieldError`` whose ``field`` is the key and whose sentence
   names the key. No refusal echoes the value.
b. A bool is not a number, and NaN, an infinity, 10^15 and a float with an
   exponent are refused. A currency is three capital letters. A coordinate
   stays in range. A select value is one of the options.
c. ``person`` refuses an upper-case address, and ``email`` lower-cases one.
d. The spec rules: options, ``to``, ``many`` and ``hot``.
e. The fences (R7): one field-type module, one writer of the four tables,
   and the R8 suite is on the CI skip guard.

**The mutations (R7).** Commit before each, and restore with git after.

* M3. ``_plain_number``: drop the ``isinstance(value, bool)`` test. The
  ``number-True`` and ``number-False`` cases of
  ``test_each_type_refuses_a_bad_value`` go red.
* M7. ``_number``: write the sentence with no key. Each ``number`` case of
  ``test_each_type_refuses_a_bad_value`` goes red.
* M8. ``_person``: drop ``or v != v.lower()``. The ``person-upper`` case goes
  red.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_manifest
from acb_common.studio_fields import FieldError, FieldSpec, RecordError

_ROOT = Path(__file__).resolve().parents[2]

_UUID = str(uuid.uuid4())

#: The spec extras of each type, for the value tests.
_EXTRA: dict[str, dict[str, Any]] = {
    "select": {"options": ["planned", "on site", "done"]},
    "multiSelect": {"options": ["red", "green", "blue"]},
    "relation": {"to": "crm.account"},
}

#: One good value of each type, and the value the store keeps.
_GOOD: dict[str, tuple[Any, Any]] = {
    "text": ("Pump on site", "Pump on site"),
    "longText": ("x" * sf.MAX_LONG_TEXT_CHARS, "x" * sf.MAX_LONG_TEXT_CHARS),
    "number": (2.5, 2.5),
    "money": ({"minor": 125000, "currency": "INR"}, {"minor": 125000, "currency": "INR"}),
    "date": ("2026-11-01", "2026-11-01"),
    "datetime": ("2026-11-01T09:30:00+05:30", "2026-11-01T04:00:00Z"),
    "checkbox": (False, False),
    "select": ("on site", "on site"),
    "multiSelect": (["red", "blue"], ["red", "blue"]),
    "person": ("ana@fracktal.test", "ana@fracktal.test"),
    "relation": ({"id": "acct-42"}, {"id": "acct-42"}),
    "file": ({"id": _UUID.upper()}, {"id": _UUID}),
    "url": ("https://example.com/a?b=c", "https://example.com/a?b=c"),
    "email": ("Ana@Fracktal.TEST", "ana@fracktal.test"),
    "phone": ("+919876543210", "+919876543210"),
    "geo": ({"lat": 12.97, "lng": 77.59}, {"lat": 12.97, "lng": 77.59}),
}

#: Bad values of each type, by a name for the test id.
_BAD: list[tuple[str, str, Any]] = [
    ("text", "number", 5),
    ("text", "too-long", "x" * (sf.MAX_TEXT_CHARS + 1)),
    ("text", "nul", "a\x00b"),
    ("text", "lone-surrogate", "a\ud800b"),
    ("longText", "too-long", "x" * (sf.MAX_LONG_TEXT_CHARS + 1)),
    ("number", "True", True),
    ("number", "False", False),
    ("number", "nan", float("nan")),
    ("number", "inf", float("inf")),
    ("number", "text", "5"),
    ("number", "1e15", 10**15),
    ("number", "exponent", 1e-05),
    ("number", "big-float", 1e16),
    ("money", "no-currency", {"minor": 5}),
    ("money", "float-minor", {"minor": 5.5, "currency": "INR"}),
    ("money", "bool-minor", {"minor": True, "currency": "INR"}),
    ("money", "lower-currency", {"minor": 5, "currency": "inr"}),
    ("money", "short-currency", {"minor": 5, "currency": "IN"}),
    ("money", "extra-key", {"minor": 5, "currency": "INR", "rate": 1}),
    ("money", "number", 5),
    ("date", "not-real", "2026-02-30"),
    ("date", "other-digits", "٢٠٢٦-11-01"),
    ("date", "datetime", "2026-11-01T00:00:00Z"),
    ("datetime", "no-offset", "2026-11-01T09:30:00"),
    ("datetime", "space", "2026-11-01 09:30:00Z"),
    ("datetime", "bad-hour", "2026-11-01T25:30:00Z"),
    ("datetime", "date", "2026-11-01"),
    ("checkbox", "one", 1),
    ("checkbox", "text", "yes"),
    ("select", "not-an-option", "cancelled"),
    ("select", "list", ["done"]),
    ("multiSelect", "not-an-option", ["red", "pink"]),
    ("multiSelect", "twice", ["red", "red"]),
    ("multiSelect", "text", "red"),
    ("person", "upper", "Ana@fracktal.test"),
    ("person", "no-domain-dot", "ana@fracktal"),
    ("person", "name", "Ana"),
    ("relation", "bare-id", "acct-42"),
    ("relation", "number-id", {"id": 42}),
    ("relation", "long-id", {"id": "a" * (sf.MAX_ID_CHARS + 1)}),
    ("relation", "extra-key", {"id": "a", "name": "x"}),
    ("relation", "space-id", {"id": "a b"}),
    ("file", "not-uuid", {"id": "photo.png"}),
    ("file", "bare", _UUID),
    ("url", "ftp", "ftp://example.com/a"),
    ("url", "script", "javascript:alert(1)"),
    ("url", "no-host", "https://"),
    ("url", "space", "https://example.com/a b"),
    ("url", "bad-port", "https://example.com:99999/"),
    ("url", "too-long", "https://example.com/" + "a" * sf.MAX_URL_CHARS),
    ("url", "user-and-password", "https://ana:s3cret@example.com/a"),
    ("url", "user", "https://ana@example.com/a"),
    ("email", "no-at", "ana.fracktal.test"),
    ("email", "too-long", "a" * 250 + "@x.test"),
    ("phone", "no-plus", "919876543210"),
    ("phone", "short", "+1234567"),
    ("phone", "long", "+1234567890123456"),
    ("phone", "leading-zero", "+0123456789"),
    ("geo", "lat", {"lat": 90.5, "lng": 0}),
    ("geo", "lng", {"lat": 0, "lng": -180.5}),
    ("geo", "bool", {"lat": True, "lng": 0}),
    ("geo", "missing", {"lat": 1.5}),
    ("geo", "exponent", {"lat": 1e-05, "lng": 0}),
    ("geo", "list", [12.97, 77.59]),
]


def _field(kind: str, key: str = "f_key", **kw: Any) -> FieldSpec:
    return sf.parse_field_spec({"key": key, "type": kind, **_EXTRA.get(kind, {}), **kw})


def _words(message: str) -> int:
    return len(message.split())


# ── a. Each type takes a good value and refuses a bad one ────────────────────


def test_the_sixteen_types_are_the_spec_list() -> None:
    assert len(sf.FIELD_TYPES) == 16
    assert set(_GOOD) == set(sf.FIELD_TYPES)
    assert {kind for kind, _, _ in _BAD} == set(sf.FIELD_TYPES)
    assert set(sf.HOT_TYPES) <= set(sf.FIELD_TYPES)


@pytest.mark.parametrize("kind", sf.FIELD_TYPES)
def test_each_type_takes_a_good_value(kind: str) -> None:
    given, kept = _GOOD[kind]
    assert sf.check_value(_field(kind), given) == kept


@pytest.mark.parametrize(("kind", "value"), [(k, v) for k, _, v in _BAD],
                         ids=[f"{k}-{n}" for k, n, _ in _BAD])
def test_each_type_refuses_a_bad_value(kind: str, value: Any) -> None:
    """The refusal carries the key in `field` and in the sentence. It never
    echoes the value, because a record holds member data."""
    key = f"{kind.lower()}_value"
    with pytest.raises(FieldError) as refused:
        sf.check_value(_field(kind, key=key), value)
    err = refused.value
    assert err.field == key
    assert f"`{key}`" in err.message and str(err) == err.message
    assert _words(err.message) <= 25, err.message
    if isinstance(value, str) and len(value) > 3:
        assert value not in err.message


def test_numbers_at_the_edge() -> None:
    number = _field("number")
    for good in (0, -3, 0.0001, 123456789012345.6, -(10**15 - 1), 10**15 - 1):
        assert sf.check_value(number, good) == good
    for bad in (-(10**15), 1e15, float("-inf"), 2.5e20):
        with pytest.raises(FieldError):
            sf.check_value(number, bad)


def test_datetime_is_kept_in_utc() -> None:
    when = _field("datetime")
    assert sf.check_value(when, "2026-11-01T23:30:00.25-02:00") == "2026-11-02T01:30:00.250000Z"
    assert sf.check_value(when, "2026-11-01t09:30:00z") == "2026-11-01T09:30:00Z"
    assert sf.check_value(when, "0001-01-01T05:00:00+05:00") == "0001-01-01T00:00:00Z"
    with pytest.raises(FieldError):
        # One past the first moment that a Python datetime holds, in UTC.
        sf.check_value(when, "0001-01-01T00:00:00+05:00")


def test_a_person_must_be_lower_case_and_an_email_is_lowered() -> None:
    assert sf.check_value(_field("email"), "Ben@X.Test") == "ben@x.test"
    with pytest.raises(FieldError) as refused:
        sf.check_value(_field("person", key="engineer"), "Ben@x.test")
    assert refused.value.field == "engineer"
    assert sf.check_value(_field("person"), "ben@x.test") == "ben@x.test"


def test_a_many_field_takes_a_list() -> None:
    photos = _field("file", key="photos", many=True)
    assert sf.check_value(photos, [{"id": _UUID}]) == [{"id": _UUID}]
    assert sf.check_value(photos, []) == []
    for bad in ({"id": _UUID}, [None], [{"id": _UUID}] * (sf.MAX_MANY_ITEMS + 1)):
        with pytest.raises(FieldError) as refused:
            sf.check_value(photos, bad)
        assert refused.value.field == "photos"
    with pytest.raises(FieldError) as refused:
        sf.check_value(photos, [{"id": "nope"}])
    assert "`photos`" in refused.value.message


# ── The record ──────────────────────────────────────────────────────────────


def _visit() -> list[FieldSpec]:
    return [
        _field("relation", key="customer", required=True),
        _field("person", key="engineer"),
        _field("select", key="status", hot=True),
        _field("number", key="hours", hot=True),
        _field("text", key="notes"),
    ]


def test_a_record_is_checked_whole() -> None:
    data = {"notes": None, "status": "done", "customer": {"id": "c1"}, "hours": 3}
    # The order of the fields, and no key of a None.
    assert sf.validate_record(_visit(), data) == {
        "customer": {"id": "c1"}, "status": "done", "hours": 3}


@pytest.mark.parametrize(("data", "key"), [
    ({"status": "done"}, "customer"),
    ({"customer": None}, "customer"),
    ({"customer": {"id": "c1"}, "colour": "red"}, "colour"),
    ({"customer": {"id": "c1"}, "hours": True}, "hours"),
])
def test_a_record_refusal_names_the_field(data: dict[str, Any], key: str) -> None:
    with pytest.raises(FieldError) as refused:
        sf.validate_record(_visit(), data)
    assert refused.value.field == key and f"`{key}`" in refused.value.message


def test_an_unknown_key_of_a_bad_shape_is_not_echoed() -> None:
    with pytest.raises(FieldError) as refused:
        sf.validate_record(_visit(), {"customer": {"id": "c1"}, "Bad Key\x00": 1})
    assert refused.value.field == "?" and "\x00" not in refused.value.message


def test_a_record_must_be_an_object() -> None:
    for bad in ([], "x", None, 5):
        with pytest.raises(RecordError):
            sf.validate_record(_visit(), bad)


def test_record_json_holds_at_most_the_cap() -> None:
    fields = [_field("longText", key=f"t{i}") for i in range(4)]

    def of_size(size: int) -> dict[str, Any]:
        data = {f"t{i}": "" for i in range(4)}
        pad = size - len(sf.record_json(data).encode("utf-8"))
        for i in range(4):
            take = min(pad, sf.MAX_LONG_TEXT_CHARS)
            data[f"t{i}"] = "y" * take
            pad -= take
        return sf.validate_record(fields, data)

    exact = of_size(sf.MAX_RECORD_BYTES)
    assert len(sf.record_json(exact).encode("utf-8")) == sf.MAX_RECORD_BYTES
    with pytest.raises(RecordError):
        sf.record_json({**exact, "t3": exact["t3"] + "y"})


# ── d. The spec rules ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("spec", "path", "contains"), [
    ({"type": "select", "options": []}, "options", "at least one option"),
    ({"type": "select"}, "options", "at least one option"),
    ({"type": "select", "options": [f"o{i}" for i in range(101)]}, "options", "at most 100"),
    ({"type": "select", "options": ["a", "x" * 81]}, "options[1]", "1 to 80"),
    ({"type": "select", "options": ["a", " "]}, "options[1]", "1 to 80"),
    ({"type": "select", "options": ["a", "b\x00"]}, "options[1]", "NUL"),
    ({"type": "multiSelect", "options": ["a", "a"]}, "options[1]", "twice"),
    ({"type": "select", "options": ["Open", "open"]}, "options[1]", "only by case"),
    ({"type": "multiSelect", "options": ["Done", "x", "DONE"]}, "options[2]", "only by case"),
    ({"type": "text", "options": ["a"]}, "options", "Only a select"),
    ({"type": "text", "to": "crm.account"}, "to", "Only a relation"),
    ({"type": "relation"}, "to", "needs a `to`"),
    ({"type": "relation", "to": "Account"}, "to", "<app>.<object>"),
    ({"type": "longText", "hot": True}, "hot", "cannot be hot"),
    ({"type": "text", "hot": True, "many": True}, "hot", "`many` cannot be hot"),
    ({"type": "multiSelect", "options": ["a"], "many": True}, "many", "no `many`"),
    ({"type": "checkbox", "many": True}, "many", "no `many`"),
    ({"type": "text", "required": "yes"}, "required", "true or false"),
    ({"type": "text", "default": "x"}, "default", "not a key"),
    ({"type": "text", "label": "l" * 121}, "label", "at most 120"),
    ({"type": "user"}, "type", "unknown"),
], ids=lambda v: v if isinstance(v, str) else None)
def test_each_spec_rule_has_a_path_and_a_sentence(spec: dict[str, Any], path: str,
                                                  contains: str) -> None:
    problems = sf.validate_field_spec({"key": "f", **spec})
    assert [p.path for p in problems] == [path], problems
    assert contains in problems[0].message
    with pytest.raises(FieldError) as refused:
        sf.parse_field_spec({"key": "f", **spec})
    assert refused.value.field == "f" and "`f`" in refused.value.message


def test_a_bad_key_is_refused_and_not_echoed() -> None:
    for key in ("Status", "1st", "a" * 49, "a-b", "", None, "a\n"):
        problems = sf.validate_field_spec({"key": key, "type": "text"})
        assert [p.path for p in problems] == ["key"], key
        with pytest.raises(FieldError) as refused:
            sf.parse_field_spec({"key": key, "type": "text"})
        assert refused.value.field == "?"


def test_a_bare_to_is_checked_against_the_objects_when_given() -> None:
    spec = {"key": "site", "type": "relation", "to": "site"}
    assert sf.validate_field_spec(spec) == []
    assert sf.validate_field_spec(spec, objects={"site"}) == []
    [problem] = sf.validate_field_spec(spec, objects={"visit"})
    assert problem.path == "to" and problem.got == "site"


@pytest.mark.parametrize("kind", sf.HOT_TYPES)
def test_each_hot_type_may_be_hot(kind: str) -> None:
    assert _field(kind, hot=True).hot is True


def test_the_stored_spec_round_trips() -> None:
    field = _field("select", key="status", label="Status", required=True, hot=True)
    again = sf.stored_field("status", "select", field.stored_spec(), True)
    assert again == field
    assert "key" not in field.stored_spec() and "hot" not in field.stored_spec()


# ── The hot value ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("kind", "value", "column", "stored"), [
    ("text", "x" * 300, "t", "x" * sf.HOT_TEXT_CHARS),
    ("select", "done", "t", "done"),
    ("email", "ana@x.test", "t", "ana@x.test"),
    ("number", 2.5, "n", 2.5),
    ("money", {"minor": 125000, "currency": "INR"}, "n", 125000),
    ("date", "2026-11-01", "ts", "2026-11-01T00:00:00Z"),
    ("datetime", "2026-11-01T04:00:00Z", "ts", "2026-11-01T04:00:00Z"),
    ("checkbox", False, "b", False),
])
def test_hot_value_fills_one_column(kind: str, value: Any, column: str, stored: Any) -> None:
    row = sf.hot_value(_field(kind, key="k", hot=True), value)
    assert row is not None and row["k"] == "k" and row[column] == stored
    assert [c for c in ("t", "n", "ts", "b") if row[c] is not None] == [column]


def test_hot_value_is_none_for_a_cold_field_or_no_value() -> None:
    assert sf.hot_value(_field("number", key="k"), 5) is None
    assert sf.hot_value(_field("number", key="k", hot=True), None) is None


# ── e. The fences (R7) ──────────────────────────────────────────────────────


def _python_files() -> list[Path]:
    out: list[Path] = []
    for top in ("apps", "packages"):
        out += [p for p in (_ROOT / top).rglob("*.py")
                if "node_modules" not in p.parts and ".venv" not in p.parts]
    return out


def test_one_field_type_module() -> None:
    """A2 and the store import `FIELD_TYPES`, and no module holds a second
    sequence of the type names. A tuple, a list, a set or a `Literal[...]`
    that holds both `longText` and `multiSelect` is a copy."""
    assert studio_manifest.FIELD_TYPES is sf.FIELD_TYPES
    copies: list[str] = []
    for path in _python_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        if "longText" not in src or "multiSelect" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Tuple | ast.List | ast.Set):
                names = {e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if {"longText", "multiSelect"} <= names:
                    copies.append(f"{path.relative_to(_ROOT).as_posix()}:{node.lineno}")
    assert copies == ["packages/acb_common/acb_common/studio_fields.py:" + str(
        _line_of_field_types())], copies


def _line_of_field_types() -> int:
    src = (_ROOT / "packages/acb_common/acb_common/studio_fields.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "FIELD_TYPES":
            assert node.value is not None
            return node.value.lineno
    raise AssertionError("FIELD_TYPES is missing")


#: SQL that names one of the four tables.
_TABLE_SQL = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+studio_(?:objects|object_fields|records|record_hot)\b",
    re.IGNORECASE,
)


def test_one_writer_of_the_four_tables() -> None:
    """Only `gateway/studio/objects_store.py` runs SQL against the four tables.
    C2 adds its query module to this list in its own slice."""
    hits = sorted({p.relative_to(_ROOT).as_posix() for p in _python_files()
                   if _TABLE_SQL.search(p.read_text(encoding="utf-8", errors="replace"))})
    assert hits == ["apps/services/gateway/gateway/studio/objects_store.py"], hits


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    """A fence that never runs is no fence. The `--must-run` list names the
    pytest ARGUMENT, so a mention in a comment does not count."""
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_objects_r8\.py(?: \\)?$", block.group(1), re.M)


def test_the_store_imports_the_one_check() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/studio/objects_store.py").read_text(
        encoding="utf-8")
    assert "from acb_common.studio_fields import" in src
    assert "validate_record" in src and "parse_field_spec" in src
    for kind in ("longText", "multiSelect", "geo"):
        assert f'"{kind}"' not in src, kind


def test_the_record_json_has_the_jsonb_separators() -> None:
    """jsonb writes `{"a": 1, "b": [1, 2]}`, with a space after each `,` and
    `:`. `record_json` writes the same, so its byte count is the CHECK's."""
    assert sf.record_json({"a": 1, "b": [1, 2]}) == json.dumps({"a": 1, "b": [1, 2]})
    assert sf.record_json({"a": 1, "b": [1, 2]}) == '{"a": 1, "b": [1, 2]}'


# ── Fix round 1 ─────────────────────────────────────────────────────────────


def test_an_empty_options_list_on_another_type_counts_as_none() -> None:
    """A model often writes `"options": []`. On a type that is not a select it
    means no options. A list with an option is still refused."""
    assert sf.validate_field_spec({"key": "f", "type": "text", "options": []}) == []
    field = sf.parse_field_spec({"key": "f", "type": "text", "options": []})
    assert field.options is None and "options" not in field.stored_spec()
    [problem] = sf.validate_field_spec({"key": "f", "type": "text", "options": ["a"]})
    assert problem.path == "options"
    # A select still needs at least one option.
    [problem] = sf.validate_field_spec({"key": "f", "type": "select", "options": []})
    assert "at least one option" in problem.message


def test_a_stored_field_runs_no_authoring_check() -> None:
    """A spec that a later rule refuses still reads, so an older object works."""
    spec = {"options": ["Open", "open"], "required": False, "many": False}
    with pytest.raises(FieldError):
        sf.parse_field_spec({"key": "state", "type": "select", **spec})
    field = sf.stored_field("state", "select", spec, False)
    assert field.options == ("Open", "open")
    assert sf.check_value(field, "open") == "open"


def test_check_can_be_hot_holds_the_one_hot_rule() -> None:
    sf.check_can_be_hot(_field("number", key="n"))
    for field in (_field("longText", key="body"), _field("text", key="tags", many=True)):
        with pytest.raises(FieldError) as refused:
            sf.check_can_be_hot(field)
        assert refused.value.field == field.key and f"`{field.key}`" in refused.value.message


def test_a_url_with_no_credential_still_passes() -> None:
    url = _field("url")
    for good in ("https://example.com:8443/a?user=ana", "http://[::1]/x", "https://ex.com/@ana"):
        assert sf.check_value(url, good) == good
