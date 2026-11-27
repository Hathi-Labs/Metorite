"""The flag of a quantity value (STU-C24a).

The flag is advice, computed on read from the ranges of the field spec. It is
never stored. The exact edges are in ``studio_field_cases.json``, and the
TypeScript twin runs them (``fieldCheck.test.ts``). This file holds the spec
rules, the exactness and the source fence.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf

MODULE = Path(sf.__file__)


def spec(**kw: Any) -> dict[str, Any]:
    return {"key": "glu", "type": "quantity", "units": ["mmol/L", "mg/dL"], **kw}


def field(**kw: Any) -> sf.FieldSpec:
    return sf.parse_field_spec(spec(**kw))


def flag(f: sf.FieldSpec, value: str, unit: str = "mmol/L") -> str:
    return sf.quantity_flag(f, {"value": value, "unit": unit})


GOOD = {
    "normal": {"low": "3.9", "high": "5.6"},
    "critical": {"low": "2.8", "high": "10"},
    "conversions": [{"from": "mg/dL", "to": "mmol/L", "factor": "0.0555"}],
}


@pytest.mark.parametrize(("value", "want"), [
    ("3.9", "normal"), ("5.6", "normal"), ("3.899999", "low"), ("5.600001", "high"),
    ("2.8", "low"), ("2.799999", "critical_low"), ("10", "high"), ("10.000001", "critical_high"),
    ("-5", "critical_low"), ("999999999999999", "critical_high"),
])
def test_each_bound_is_inclusive(value: str, want: str) -> None:
    assert flag(field(**GOOD), value) == want


def test_a_converted_value_on_the_edge() -> None:
    f = field(**GOOD, units=["mmol/L", "mg/dL"])
    assert flag(f, "70", "mg/dL") == "low"  # 3.885
    assert flag(f, "70.27", "mg/dL") == "low"  # 3.899985, just under 3.9


def test_the_exact_edge_after_a_conversion() -> None:
    f = field(units=["kg", "g"], normal={"low": "1", "high": "2"},
              conversions=[{"from": "g", "to": "kg", "factor": "0.001"}])
    assert flag(f, "1000", "g") == "normal"
    assert flag(f, "999.999", "g") == "low"
    assert flag(f, "2000", "g") == "normal"
    assert flag(f, "2000.001", "g") == "high"


def test_a_six_decimal_factor_and_a_fifteen_digit_value_are_exact() -> None:
    conv = [{"from": "b", "to": "a", "factor": "0.123457"}]
    edge = "123456999999999.876543"
    f = field(units=["a", "b"], normal={"high": edge}, conversions=conv)
    assert flag(f, "999999999999999", "b") == "normal"
    f2 = field(units=["a", "b"], normal={"high": "123456999999999.876542"}, conversions=conv)
    assert flag(f2, "999999999999999", "b") == "high"


def test_no_range_gives_none_and_no_conversion_gives_unchecked() -> None:
    assert flag(field(), "5") == "none"
    assert flag(field(normal={"low": None, "high": None}), "5") == "none"
    assert flag(field(**{k: v for k, v in GOOD.items() if k != "conversions"}), "5", "mg/dL") == "unchecked"
    assert flag(field(normal={"low": "1"}), "5", "mg/dL") == "unchecked"
    # No range wins over a missing conversion.
    assert flag(field(), "5", "mg/dL") == "none"


def test_only_one_bound() -> None:
    assert flag(field(normal={"low": "1"}), "0.999999") == "low"
    assert flag(field(normal={"low": "1"}), "999999999999999") == "normal"
    assert flag(field(normal={"high": "1"}), "1.000001") == "high"
    assert flag(field(normal={"high": "1"}), "-999999999999999") == "normal"
    assert flag(field(critical={"high": "9"}), "9.000001") == "critical_high"


def test_a_bad_value_gives_none() -> None:
    f = field(**GOOD)
    assert sf.quantity_flag(f, {"value": "1e3", "unit": "mmol/L"}) == "none"
    assert sf.quantity_flag(f, {"unit": "mmol/L"}) == "none"


def test_the_stored_spec_round_trips_and_the_value_stays_value_and_unit() -> None:
    f = field(**GOOD)
    stored = f.stored_spec()
    assert stored["normal"] == {"low": "3.9", "high": "5.6"}
    assert stored["conversions"] == GOOD["conversions"]
    assert sf.stored_field("glu", "quantity", stored, False) == f
    assert sf.check_value(f, {"value": "5", "unit": "mmol/L"}) == {"value": "5", "unit": "mmol/L"}


# --- the refused specs ------------------------------------------------------


def problems(**kw: Any) -> list[sf.SpecProblem]:
    return sf.validate_field_spec(spec(**kw))


def paths(**kw: Any) -> set[str]:
    return {p.path for p in problems(**kw)}


def test_a_good_spec_has_no_problem() -> None:
    assert problems(**GOOD) == []


def test_critical_inside_normal_is_refused() -> None:
    assert "critical.low" in paths(normal={"low": "3", "high": "5"}, critical={"low": "4"})
    assert "critical.high" in paths(normal={"low": "3", "high": "5"}, critical={"high": "4"})
    assert problems(normal={"low": "3", "high": "5"}, critical={"low": "3", "high": "5"}) == []


def test_a_range_with_low_above_high_is_refused() -> None:
    assert "normal.high" in paths(normal={"low": "5", "high": "3"})


@pytest.mark.parametrize("bad", ["1e3", "1,5", 5, "", "1.1234567", "x"])
def test_a_bound_that_is_not_decimal_text_is_refused(bad: Any) -> None:
    assert "normal.low" in paths(normal={"low": bad})


def test_a_range_of_the_wrong_shape_is_refused() -> None:
    assert "normal" in paths(normal=["3", "5"])
    assert "critical" in paths(critical={"low": "1", "mid": "2"})


def test_conversion_rules() -> None:
    ok = {"from": "mg/dL", "to": "mmol/L", "factor": "0.0555"}
    assert problems(conversions=[ok]) == []
    assert any("twice" in p.message for p in problems(conversions=[ok, dict(ok)]))
    assert "conversions[0].from" in paths(conversions=[{**ok, "from": "lb"}])
    assert "conversions[0].to" in paths(conversions=[{**ok, "to": "lb"}])
    # `to` is the base unit, so a chain is refused.
    chain = {"units": ["a", "b", "c"], "conversions": [
        {"from": "c", "to": "b", "factor": "2"}]}
    assert "conversions[0].to" in paths(**chain)
    # A pair and its inverse.
    inv = {"units": ["a", "b"], "conversions": [
        {"from": "b", "to": "a", "factor": "2"}, {"from": "a", "to": "b", "factor": "0.5"}]}
    assert any("inverse" in p.message for p in problems(**inv))
    assert problems(conversions=[{**ok, "from": "mmol/L"}]) != []


@pytest.mark.parametrize("factor", ["0", "0.000000", "-1", "1e2", "1.1234567", 2, "", "x"])
def test_a_bad_factor_is_refused(factor: Any) -> None:
    c = {"from": "mg/dL", "to": "mmol/L", "factor": factor}
    assert "conversions[0].factor" in paths(conversions=[c])


def test_at_most_thirty_conversions() -> None:
    units = ["base"] + [f"u{i}" for i in range(29)]
    many = [{"from": u, "to": "base", "factor": "2"} for u in units[1:]]
    assert sf.validate_field_spec(spec(units=units, conversions=many)) == []
    assert "conversions" in {p.path for p in sf.validate_field_spec(
        spec(units=units, conversions=many + many[:2]))}


def test_only_a_quantity_takes_the_keys() -> None:
    for key, val in (("normal", {"low": "1"}), ("critical", {"low": "1"}), ("conversions", [])):
        got = sf.validate_field_spec({"key": "n", "type": "number", key: val})
        assert [p.path for p in got] == [key]


# --- the fences ---------------------------------------------------------------


def test_the_module_uses_no_float() -> None:
    """Mutation: write ``float(factor)`` in ``quantity_flag``. This test turns red."""
    text = MODULE.read_text(encoding="utf-8")
    body = text[text.index("def quantity_flag"):text.index("def _percent")]
    assert "float(" not in body
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not re.search(r"\bfloat\(", "\n".join(code)), "studio_fields.py must not call float("


def test_an_unchanged_manifest_is_no_change_for_the_sync() -> None:
    """The sync compares each attribute of ``_FIELD_ATTRS`` of the stored field with
    that of the manifest. A field with ranges and conversions must compare equal
    after the store round trip, or a second sync would refuse it as ``field_changed``."""
    from gateway.studio.objects_store import _FIELD_ATTRS

    f = field(units=["mmol/L", "mg/dL"], normal={"low": "3.9", "high": "5.6"},
              critical={"low": "2.8", "high": "10"},
              conversions=[{"from": "mg/dL", "to": "mmol/L", "factor": "0.0555"}])
    again = sf.stored_field("glu", "quantity", f.stored_spec(), False)
    assert [a for a in _FIELD_ATTRS if getattr(f, a) != getattr(again, a)] == []
    assert f.normal and f.critical and f.conversions, "the keys are in the compare"
