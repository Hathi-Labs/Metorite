"""The TypeScript field check equals the Python field check (STU-D2a).

``studio_fields.py`` is the one source. This test builds a list of cases from
it: for each of the 16 field types, a valid value, an invalid value and an
edge value, each with the normalized output. It fails when the committed
file ``studio_field_cases.json`` differs from the list built now.
``fieldCheck.test.ts`` reads the same file and holds the TypeScript check to
it. A second test holds the six limits of ``fieldCheck.ts`` equal to the
Python constants.

To write the file after a change of ``studio_fields.py``::

    STUDIO_FIELD_CASES_WRITE=1 uv run pytest tests/unit/test_studio_field_cases_parity.py

A long string is a ``{"$repeat": "a", "count": 2000}`` marker in the file.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common.studio_fields import FieldError, FieldSpec, RecordError

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "packages/acb_common/acb_common/studio_field_cases.json"
TS = ROOT / "workbench/control_plane/src/lib/studio/fieldCheck.ts"


class Rep:
    """A long string, written as a marker so the case file stays small."""

    def __init__(self, ch: str, count: int, prefix: str = "") -> None:
        self.ch, self.count, self.prefix = ch, count, prefix


def real(x: Any) -> Any:
    if isinstance(x, Rep):
        return x.prefix + x.ch * x.count
    if isinstance(x, dict):
        return {k: real(v) for k, v in x.items()}
    if isinstance(x, list):
        return [real(v) for v in x]
    return x


def wire(x: Any) -> Any:
    if isinstance(x, Rep):
        out = {"$repeat": x.ch, "count": x.count}
        return {**out, "prefix": x.prefix} if x.prefix else out
    if isinstance(x, dict):
        return {k: wire(v) for k, v in x.items()}
    if isinstance(x, list):
        return [wire(v) for v in x]
    return x


def _spec(kind: str, **kw: Any) -> dict[str, Any]:
    return {"key": "f", "type": kind, **kw}


def _field(spec: dict[str, Any]) -> FieldSpec:
    opts = spec.get("options")
    return FieldSpec(
        key=spec["key"], type=spec["type"], required=spec.get("required", False),
        many=spec.get("many", False), options=tuple(opts) if opts else None)


OPEN_CLOSED = ["Open", "Closed"]
UUID_L = "123e4567-e89b-12d3-a456-426614174000"

# (type, spec extras, [values]) in the order: valid, invalid, edge.
VALUES: dict[str, tuple[dict[str, Any], list[Any]]] = {
    "text": ({}, [
        "Hello", 5, None, "a\u0000b", "", "\ud800", Rep("a", 2000), Rep("a", 2001),
        Rep("\U0001f600", 2000), Rep("\U0001f600", 2001),
    ]),
    "longText": ({}, [
        "A long note.\nTwo lines.", 5, Rep("a", 20000), Rep("a", 20001), "a\u0000b",
    ]),
    "number": ({}, [
        42, -3.5, 0, 0.0001, 999999999999999, -999999999999999, 0.00001, 1.5e-07,
        10**15, 1e15, True, "5", None, [1],
    ]),
    "money": ({}, [
        {"minor": 1999, "currency": "INR"}, {"minor": -5, "currency": "USD"},
        {"minor": 10**15 - 1, "currency": "INR"}, {"minor": 10**15, "currency": "INR"},
        {"minor": 1.5, "currency": "INR"}, {"minor": True, "currency": "INR"},
        {"minor": 100, "currency": "inr"}, {"minor": 100, "currency": "INRX"},
        {"minor": 100}, {"minor": 100, "currency": "INR", "extra": 1}, 100, "INR 100",
    ]),
    "date": ({}, [
        "2024-02-29", "2023-02-29", "2024-13-01", "2024-00-10", "2024-02-30",
        "2024-2-9", "0000-01-01", "0001-01-01", "9999-12-31", "2024-02-29 ",
        "٢٠٢٤-٠١-٠١", 20240229, None,
    ]),
    "datetime": ({}, [
        "1999-12-31T23:59:00+05:30", "2024-01-01T00:00:00Z", "2024-01-01t00:00:00z",
        "2024-01-01T00:00:00.5Z", "2024-01-01T00:00:00.123456Z", "2024-01-01T00:00:00.1234567Z",
        "2024-01-01T00:00:00", "2024-01-01", "2024-01-01T24:00:00Z",
        "2024-01-01T00:60:00Z", "2024-01-01T00:00:60Z", "2024-02-30T00:00:00Z",
        "2024-01-01T00:00:00+23:59", "2024-01-01T00:00:00+24:00",
        "2024-01-01T00:00:00+05:60", "2024-01-01T00:00:00+23:60", "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00Z", "2024-03-01T00:30:00+01:00",
        20240101,
    ]),
    "checkbox": ({}, [True, False, 1, 0, "true", None]),
    "select": ({"options": OPEN_CLOSED}, ["Open", "open", "Other", "", 5, None, ["Open"]]),
    "multiSelect": ({"options": OPEN_CLOSED}, [
        ["Open"], ["Open", "Closed"], [], ["Open", "Open"], ["Other"], "Open", ["open"],
        [5], None,
    ]),
    "person": ({}, [
        "ann@example.com", "Ann@Example.com", "ann", "ann@example", "a b@example.com",
        5, None, "x" * 242 + "@example.com", "x" * 243 + "@example.com",
    ]),
    "relation": ({}, [
        {"id": "abc-1"}, {"id": "rec:1.2_x"}, {"id": ""}, {"id": "-x"}, {"id": "a" * 64},
        {"id": "a" * 65}, {"id": "a b"}, {"id": 5}, {"id": "a", "extra": 1}, {}, "abc", None,
    ]),
    "file": ({}, [
        {"id": UUID_L}, {"id": UUID_L.upper()}, {"id": UUID_L.replace("-", "")},
        {"id": "not-a-uuid"}, {"id": UUID_L[:-1]}, {"id": UUID_L, "extra": 1}, {}, UUID_L, None,
    ]),
    "url": ({}, [
        "https://example.com/a?b=1#c", "http://localhost:8080", "HTTPS://EXAMPLE.COM",
        "https://example.com:65535", "https://example.com:", "ftp://example.com",
        "javascript:alert(1)", "https://user:pw@example.com", "https://@example.com",
        "https://example.com:99999", "https://example.com:abc", "https://a b.com",
        "https://", "http:example.com", "example.com", "", 5,
        Rep("a", 2048 - 14, "https://x.com/"), Rep("a", 2049 - 14, "https://x.com/"),
        "https://example.com/\u0000",
    ]),
    "email": ({}, [
        "ann@example.com", "Ann@Example.COM", "ann@example", "ann@", "@example.com",
        "a b@example.com", "a@-example.com", "a@example..com", 5, None,
        "x" * 242 + "@example.com", "x" * 243 + "@example.com",
    ]),
    "phone": ({}, [
        "+919876543210", "+12345678", "+123456789012345", "+1234567", "+1234567890123456",
        "919876543210", "+0123456789", "+91 9876543210", "+", 919876543210, None,
    ]),
    "geo": ({}, [
        {"lat": 12.97, "lng": 77.59}, {"lat": 90, "lng": 180}, {"lat": -90, "lng": -180},
        {"lat": 91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": 0}, {"lat": 1e-07, "lng": 0},
        {"lat": "1", "lng": 2}, {"lat": True, "lng": 2}, {"lat": 1, "lng": 2, "extra": 3},
        "12,77", None,
    ]),
}

MANY_CASES: list[tuple[str, dict[str, Any], Any]] = [
    ("text", {}, ["a", "b"]),
    ("text", {}, []),
    ("text", {}, "a"),
    ("text", {}, ["a", None]),
    ("text", {}, ["a", 5]),
    ("text", {}, [Rep("a", 2001)]),
    ("number", {}, [1, 2.5]),
    ("email", {}, ["A@B.CO", "c@d.io"]),
    ("date", {}, ["2024-01-01", "2024-13-01"]),
    ("select", {"options": OPEN_CLOSED}, ["Open", "Closed", "Open"]),
    ("text", {}, ["a"] * 500),
    ("text", {}, ["a"] * 501),
]

RECORD_FIELDS = [
    {"key": "title", "type": "text", "required": True},
    {"key": "qty", "type": "number"},
    {"key": "status", "type": "select", "options": OPEN_CLOSED},
    {"key": "tags", "type": "text", "many": True},
    {"key": "due", "type": "datetime"},
    {"key": "owner", "type": "email"},
    {"key": "done", "type": "checkbox"},
]

RECORDS: list[Any] = [
    {"title": "Ship it"},
    {"title": "Ship it", "qty": 3, "status": "Open", "tags": ["a", "b"],
     "due": "2024-03-01T05:30:00+05:30", "owner": "Ann@Example.com", "done": False},
    {"title": "Ship it", "qty": None, "status": None},
    {"title": "Ship it", "qty": "", "status": ""},
    {"title": "Ship it", "tags": []},
    {"title": "Ship it", "tags": ["a", None]},
    {"title": "  "},
    {"title": ""},
    {"title": None},
    {},
    {"title": "Ship it", "nope": 1},
    {"title": "Ship it", "Bad Key": 1},
    {"title": "Ship it", "qty": "x", "status": "Other"},
    {"title": 5, "qty": "x"},
    {"title": "Ship it", "done": None},
    {"title": "Ship it", "status": "  "},
    [], "x", None,
]


def build_cases() -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    for kind, (extra, items) in VALUES.items():
        spec = _spec(kind, **extra)
        for i, v in enumerate(items):
            values.append(_value_case(f"{kind}-{i}", spec, v))
    for i, (kind, extra, v) in enumerate(MANY_CASES):
        values.append(_value_case(f"{kind}-many-{i}", _spec(kind, many=True, **extra), v))
    records: list[dict[str, Any]] = []
    fields = [_field(s) for s in RECORD_FIELDS]
    for i, data in enumerate(RECORDS):
        case: dict[str, Any] = {"id": f"record-{i}", "fields": RECORD_FIELDS, "data": wire(data)}
        try:
            case["ok"], case["out"] = True, sf.validate_record(fields, real(data))
        except FieldError as e:
            case["ok"], case["error_field"] = False, e.field
        except RecordError:
            case["ok"], case["record_error"] = False, True
        records.append(case)
    return {
        "note": "Written by tests/unit/test_studio_field_cases_parity.py. Do not edit by hand.",
        "values": values,
        "records": records,
    }


def _value_case(case_id: str, spec: dict[str, Any], v: Any) -> dict[str, Any]:
    case: dict[str, Any] = {"id": case_id, "field": spec, "value": wire(v)}
    try:
        out = sf.check_value(_field(spec), real(v))
    except FieldError:
        case["ok"] = False
        return case
    case["ok"] = True
    if isinstance(v, Rep) or (isinstance(v, list) and any(isinstance(x, Rep) for x in v)):
        case["out_same"] = out == real(v)
        assert case["out_same"], "a long value normalizes; write it out in full"
    else:
        case["out"] = out
    return case


def _text() -> str:
    return json.dumps(build_cases(), indent=1, ensure_ascii=True) + "\n"


def test_golden_file_equals_the_python_module():
    if os.environ.get("STUDIO_FIELD_CASES_WRITE") == "1":
        CASES.write_text(_text(), encoding="utf-8", newline="\n")
    committed = CASES.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == _text(), (
        "studio_field_cases.json differs from studio_fields.py. "
        "Run with STUDIO_FIELD_CASES_WRITE=1, then update fieldCheck.ts to match."
    )


def test_each_type_has_a_valid_an_invalid_and_an_edge_case():
    cases = json.loads(CASES.read_text(encoding="utf-8"))["values"]
    for kind in sf.FIELD_TYPES:
        mine = [c for c in cases if c["field"]["type"] == kind and not c["field"].get("many")]
        assert len(mine) >= 3, kind
        assert any(c["ok"] for c in mine), f"{kind} has no valid case"
        assert any(not c["ok"] for c in mine), f"{kind} has no invalid case"


def test_ts_limits_equal_the_python_constants():
    ts = TS.read_text(encoding="utf-8")
    found = {
        name: int(num.replace("_", ""))
        for name, num in re.findall(r"^export const (MAX_[A-Z_]+) = ([\d_]+);", ts, re.M)
    }
    want = {
        "MAX_TEXT_CHARS": sf.MAX_TEXT_CHARS,
        "MAX_LONG_TEXT_CHARS": sf.MAX_LONG_TEXT_CHARS,
        "MAX_ABS_NUMBER": sf.MAX_ABS_NUMBER,
        "MAX_MANY_ITEMS": sf.MAX_MANY_ITEMS,
        "MAX_URL_CHARS": sf.MAX_URL_CHARS,
        "MAX_EMAIL_CHARS": sf.MAX_EMAIL_CHARS,
    }
    assert found == want


@pytest.mark.parametrize("kind", sf.FIELD_TYPES)
def test_field_types_list_is_in_the_manifest_twin(kind):
    # `manifest.ts` holds the FieldType union, and test_studio_manifest_ts_parity
    # fences it. The field check reads that union, so its table needs every type.
    assert f'"{kind}"' in (ROOT / "workbench/control_plane/src/lib/studio/manifest.ts").read_text(
        encoding="utf-8")
