"""The loaders of the synthetic set, the check that it is whole, and the
recomputation of each expected value (SEM-6).

``fixtures/synthetic.json`` holds the rows of two orgs, 20 questions and 5 cases.
Each question names a query and the value that the route must answer. The
file never trusts that value. :func:`recompute` derives the same value from the
rows with plain Python, and :func:`check_fixture` fails when the two differ, so
the fixture cannot drift from its own rows.

The recomputation is a second, small model of the filter rules of
``field_sql.py``: a number that is a JSON number or a plain decimal string is a
value, any other text is none, a range takes both ends, a lookup matches its
stored id or its exact name, and the Metorite id of a core contact matches the
records that hold the Zoho id of that contact in the same org only.

This module opens no connection and holds no model call.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.json"

QUESTIONS = 20
CASES = 5
#: The four refusal classes of 2026-11-17 and the silent zero, one case each.
CLASSES = (
    "number_range",
    "field_on_another_module",
    "guessed_field",
    "status_filter",
    "core_lookup_silent_zero",
)
ORGS = ("a", "b")
#: Field types whose stored value is a number (``field_sql.MEASURE_TYPES``).
NUMBER_TYPES = frozenset({"number", "currency", "decimal", "float", "integer", "percent"})
DATE_TYPES = frozenset({"date", "datetime"})
LOOKUP = "lookup"
#: A plain decimal string, the guard of ``field_sql._number``.
_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")

#: A domain that no real person owns. Every address of the fixture ends in one of these.
SAFE_TLDS = (".test", ".example", ".invalid", ".localhost")
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)
#: A phone number: a plus sign, a bracketed group, a 3-3-4 group, or 10 digits in a row.
_PHONE = re.compile(
    r"\+\d[\d ()-]{7,}\d|\(\d{3}\)\s?\d{3}[ -]?\d{4}|\b\d{3}[ .-]\d{3}[ .-]\d{4}\b|\b\d{10,}\b"
)


@dataclass(frozen=True)
class Query:
    """One module total: what the route is asked."""

    org: str
    module: str
    measure: str = "count"
    field: str | None = None
    filters: tuple[tuple[str, Any], ...] = ()
    group_field: str | None = None
    bucket: str | None = None
    group_by: str | None = None


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    kind: str
    query: Query
    expect: dict[str, Any]


@dataclass(frozen=True)
class Step:
    """One call of a case, and what the answer must be."""

    query: Query
    status: int
    expect: dict[str, Any]


@dataclass(frozen=True)
class Case:
    id: str
    cls: str
    text: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Fixture:
    raw: dict[str, Any]
    questions: tuple[Question, ...]
    cases: tuple[Case, ...]

    @property
    def records(self) -> list[dict[str, Any]]:
        return self.raw["records"]


def _query(d: dict[str, Any]) -> Query:
    return Query(
        org=d["org"],
        module=d["module"],
        measure=d.get("measure", "count"),
        field=d.get("field"),
        filters=tuple(d.get("filters", {}).items()),
        group_field=d.get("group_field"),
        bucket=d.get("bucket"),
        group_by=d.get("group_by"),
    )


def parse(raw: dict[str, Any]) -> Fixture:
    questions = tuple(
        Question(q["id"], q["text"], q["kind"], _query(q["query"]), dict(q["expect"]))
        for q in raw["questions"]
    )
    cases = tuple(
        Case(
            c["id"],
            c["class"],
            c["text"],
            tuple(
                Step(
                    _query(s["query"]),
                    int(s["status"]),
                    {k: v for k, v in s.items() if k not in ("query", "status")},
                )
                for s in c["steps"]
            ),
        )
        for c in raw["cases"]
    )
    return Fixture(raw, questions, cases)


def load_fixture(path: Path = FIXTURE) -> Fixture:
    """Read the set, refuse a row that looks real, and check that it is whole."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    fx = parse(raw)
    check_fixture(fx)
    return fx


# ── Nothing real ────────────────────────────────────────────────────────────


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_strings(str(k)), *_strings(v))]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _strings(v)]
    return []


def looks_real(text: str) -> str | None:
    """The reason that *text* looks like real customer data, or ``None``.
    An address must end in a reserved domain. A phone number is always refused."""
    for match in _EMAIL.finditer(text):
        domain = match.group(1).lower()
        if not domain.endswith(SAFE_TLDS):
            return f"an email at a real domain ({domain})"
    stripped = _ISO.sub(" ", text)
    if _PHONE.search(stripped):
        return "a phone number"
    return None


def refuse_real(raw: dict[str, Any]) -> list[str]:
    """One line for each string of the fixture that looks real."""
    out: list[str] = []
    for text in _strings(raw):
        why = looks_real(text)
        if why:
            out.append(f"{why}: {text[:40]!r}")
    return out


# ── Ids ─────────────────────────────────────────────────────────────────────


def core_id(org: str, key: str) -> str:
    """The Metorite id of the core contact *key* in *org* (a uuid). The seed gives
    the contact this id, so the fixture knows it before the seed runs."""
    return str(uuid.uuid5(uuid.UUID(org), f"contact:{key}"))


def org_map(a: str, b: str) -> dict[str, str]:
    return {"a": a, "b": b}


# ── The recomputation ───────────────────────────────────────────────────────


def _field_types(fx: Fixture) -> dict[str, dict[str, dict[str, Any]]]:
    return {m: {f["key"]: f for f in fields} for m, fields in fx.raw["fields"].items()}


def plain_number(value: Any) -> float | None:
    """The number of a stored value, or ``None`` when it is no value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str) and _PLAIN.match(value):
        return float(value)
    return None


def _contact(fx: Fixture, org: str, key: str) -> dict[str, Any] | None:
    return next((c for c in fx.raw["contacts"] if c["org"] == org and c["key"] == key), None)


def _resolve(value: Any, q: Query) -> Any:
    """A filter value of the fixture: a literal, or the Metorite id of a core contact."""
    if isinstance(value, dict):
        return ("core", value.get("org", q.org), value["core"])
    return value


def _matches(
    fx: Fixture,
    types: dict[str, dict[str, Any]],
    q: Query,
    row: dict[str, Any],
    name: str,
    value: Any,
) -> bool:
    base, end = name, None
    for suffix in (".from", ".to"):
        if name.endswith(suffix):
            base, end = name[: -len(suffix)], suffix
    ftype = types[base]["type"]
    stored = row["fields"].get(base)
    want = _resolve(value, q)
    if end is not None and ftype in NUMBER_TYPES:
        got, bound = plain_number(stored), plain_number(want)
        if got is None or bound is None:
            return False
        return got >= bound if end == ".from" else got <= bound
    if end is not None:
        if ftype not in DATE_TYPES:
            raise ValueError(f"{name}: a range takes a date or a number field")
        if not isinstance(stored, str):
            return False
        return stored[:10] >= str(want) if end == ".from" else stored[:10] <= str(want)
    if ftype == LOOKUP:
        if not isinstance(stored, dict):
            return False
        if isinstance(want, tuple):
            _, org, key = want
            contact = _contact(fx, org, key)
            # An id of another org is no contact of this org: it matches none.
            return bool(
                contact
                and org == q.org
                and contact["zoho_id"]
                and stored.get("id") == contact["zoho_id"]
            )
        return want in (stored.get("id"), stored.get("name"))
    if stored is None:
        return False
    return str(stored).casefold() == str(want).casefold()


def recompute(fx: Fixture, q: Query) -> dict[str, Any]:
    """The answer of *q* from the rows alone: ``{"total": n}`` or ``{"groups": {...}}``."""
    types = _field_types(fx)[q.module]
    rows = [
        r
        for r in fx.records
        if r["org"] == q.org
        and r["module"] == q.module
        and all(_matches(fx, types, q, r, n, v) for n, v in q.filters)
    ]

    def measure(part: list[dict[str, Any]]) -> float:
        if q.measure == "count":
            return float(len(part))
        got = [plain_number(r["fields"].get(q.field)) for r in part]
        return float(sum(v for v in got if v is not None))

    if q.group_field is None:
        return {"total": measure(rows)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        stored = r["fields"].get(q.group_field)
        key = str(stored)[:7] if q.bucket == "month" and stored else str(stored)
        groups.setdefault(key, []).append(r)
    return {"groups": {k: measure(v) for k, v in groups.items()}}


def same(want: dict[str, Any], got: dict[str, Any]) -> bool:
    """The expected value of the fixture against a recomputed one."""
    if "total" in want:
        return "total" in got and math.isclose(float(want["total"]), got["total"], abs_tol=1e-9)
    if "groups" in want and "groups" in got:
        a, b = want["groups"], got["groups"]
        return a.keys() == b.keys() and all(
            math.isclose(float(a[k]), b[k], abs_tol=1e-9) for k in a
        )
    return False


# ── The set is whole ────────────────────────────────────────────────────────


def _check_rows(fx: Fixture) -> list[str]:
    problems = [f"a row looks real, {p}" for p in refuse_real(fx.raw)]
    types = _field_types(fx)
    seen: set[tuple[str, str, str]] = set()
    for r in fx.records:
        if r["org"] not in ORGS or r["module"] not in fx.raw["modules"][r["org"]]:
            problems.append(f"record {r['ext_id']}: org or module is not in the set")
        unknown = set(r["fields"]) - set(types.get(r["module"], {}))
        if unknown:
            problems.append(f"record {r['ext_id']} holds unknown fields {sorted(unknown)}")
        key = (r["org"], r["module"], r["ext_id"])
        if key in seen:
            problems.append(f"record {key} twice")
        seen.add(key)
    return problems


def _check_expected(fx: Fixture) -> list[str]:
    """Each 200 expectation of a question and of a case step recomputes from the rows."""
    wanted = [(q.id, q.query, q.expect) for q in fx.questions]
    wanted += [
        (f"{c.id} step {i}", s.query, s.expect)
        for c in fx.cases
        for i, s in enumerate(c.steps, start=1)
        if s.status == 200
    ]
    problems = []
    for name, query, expect in wanted:
        try:
            got = recompute(fx, query)
        except (KeyError, ValueError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        if not same(expect, got):
            problems.append(f"{name}: expected {expect}, the rows give {got}")
    for c in fx.cases:
        for i, s in enumerate(c.steps, start=1):
            if s.status != 200 and not (s.expect.get("has") and s.expect.get("code")):
                problems.append(f"{c.id} step {i}: a refusal names its code and its words")
    return problems


def check_fixture(fx: Fixture) -> None:
    """Raise ``ValueError`` when the set is not whole. Needs no database."""
    problems = _check_rows(fx)
    ids = [q.id for q in fx.questions]
    if len(fx.questions) != QUESTIONS or len(set(ids)) != QUESTIONS:
        problems.append(f"the set needs {QUESTIONS} questions with distinct ids, found {len(ids)}")
    if [c.cls for c in fx.cases] != list(CLASSES):
        problems.append(f"the cases must be the classes {list(CLASSES)}, in this order")
    kinds = {q.kind for q in fx.questions}
    problems += [
        f"no question of kind {need!r}"
        for need in (
            "count",
            "sum",
            "month",
            "range",
            "lookup_name",
            "lookup_core",
            "status",
            "zero",
            "isolation",
        )
        if need not in kinds
    ]
    problems += _check_expected(fx)
    if problems:
        raise ValueError("; ".join(problems))


# ── The query as the route reads it ─────────────────────────────────────────


def params_of(q: Query, orgs: dict[str, str]) -> dict[str, str]:
    """The query string of ``GET /crm/aggregate`` for *q*. *orgs* maps ``a`` and
    ``b`` to the org ids of the run. A core value becomes the Metorite id that
    the seed gave the contact."""
    params = {"entity": f"module:{q.module}", "measure": q.measure}
    for key, value in (
        ("field", q.field),
        ("group_by", q.group_by),
        ("group_field", q.group_field),
        ("bucket", q.bucket),
    ):
        if value:
            params[key] = value
    for name, value in q.filters:
        if isinstance(value, dict):
            value = core_id(orgs[value.get("org", q.org)], value["core"])
        params[f"field.{name}"] = str(value)
    return params
