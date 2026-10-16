"""The executor of the mapping plan, with no database (WS-53 CRM-Q19).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, "CRM-Q19, the missing
items". ``tests/unit/test_crm_map_preview_r8.py`` proves the rest against a
real Postgres.

What this pins, by item of the narrowed contract:

1. ``copy`` and ``cast`` are pure functions in ``crm_sync/executor.py``, and
   both call ``mapping.coerce_target_value``;
2. the value of a rule has one path: ``mapping.apply_field_rules`` and
   ``field_rules.backfill_batch`` call the executor, and a source fence finds
   no other call of the coercion in the gateway;
3. the executor holds only ``copy`` and ``cast``. The five other transforms
   are CRM-Q20-1;
6. the preview of one stored record (``executor.preview_record``) gives the
   value of the fixed map back, applies the draft, and names the columns
   that did not map, never a value;
7. the run counts carry ``unmapped`` for each entity, and Sync health passes
   it through ``last_run``.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from gateway.crm_sources.zoho import mapping
from gateway.crm_sources.zoho.mapping import map_record, rule_target
from gateway.crm_sync import executor
from gateway.crm_sync.executor import (
    CAST,
    COPY,
    TRANSFORMS,
    Outcome,
    apply_rules,
    blank,
    cast,
    copy,
    preview_record,
    run,
    transform_of,
)

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps" / "services" / "gateway" / "gateway"
EXECUTOR = GATEWAY / "crm_sync" / "executor.py"
MAPPING = GATEWAY / "crm_sources" / "zoho" / "mapping.py"
FIELD_RULES = GATEWAY / "crm_sync" / "field_rules.py"

#: The names of the one coercion of a rule value.
COERCE_NAMES = frozenset({"coerce_target_value", "coerce_rule_value"})


def _target(entity: str, column: str) -> Any:
    target = rule_target(entity, column)
    assert target is not None, (entity, column)
    return target


# ── 1. copy and cast ─────────────────────────────────────────────────────────


def test_1_copy_puts_the_text_into_a_text_column() -> None:
    title = _target("contact", "title")
    assert copy(title, "  Head of Sales ") == Outcome("Head of Sales", unmapped=False)
    assert copy(title, 42) == Outcome("42", unmapped=False)


def test_1_copy_refuses_a_column_that_is_not_text() -> None:
    with pytest.raises(ValueError, match="TEXT"):
        copy(_target("lead", "no_of_employees"), "40")


def test_1_a_filled_value_that_does_not_map_is_unmapped_and_a_blank_is_not() -> None:
    title = _target("contact", "title")
    # A lookup object has no text, so it does not map.
    assert copy(title, {"id": "9", "name": "Ann"}) == Outcome(None, unmapped=True)
    for nothing in (None, "", "   ", [], {}):
        assert copy(title, nothing) == Outcome(None, unmapped=False), nothing


def test_1_cast_turns_the_value_into_the_type_of_the_column() -> None:
    assert cast(_target("lead", "no_of_employees"), "40") == Outcome(40)
    assert cast(_target("lead", "annual_revenue"), "1250.5") == Outcome(1250.5)
    assert cast(_target("deal", "expected_close_date"), "2026-11-02T10:00:00") == (
        Outcome(date(2026, 11, 2)))
    assert cast(_target("deal", "probability"), 60) == Outcome(60)


def test_1_cast_gives_null_and_unmapped_for_a_value_that_does_not_coerce() -> None:
    assert cast(_target("lead", "no_of_employees"), "lots") == Outcome(None, unmapped=True)
    assert cast(_target("deal", "expected_close_date"), "soon") == (
        Outcome(None, unmapped=True))
    # Outside the limits of the target: a percent above 100.
    assert cast(_target("deal", "probability"), 150) == Outcome(None, unmapped=True)
    # A blank is not a value that failed.
    assert cast(_target("lead", "no_of_employees"), "") == Outcome(None, unmapped=False)


def test_1_copy_and_cast_call_the_one_coercion(monkeypatch) -> None:
    """Both transforms call ``mapping.coerce_target_value``, so no second rule
    of coercion exists."""
    calls: list[tuple[str, Any]] = []

    def spy(target: Any, value: Any) -> Any:
        calls.append((target.column, value))
        return "from-the-mapping"

    monkeypatch.setattr(mapping, "coerce_target_value", spy)
    assert copy(_target("contact", "title"), "x").value == "from-the-mapping"
    assert cast(_target("lead", "no_of_employees"), "y").value == "from-the-mapping"
    assert calls == [("title", "x"), ("no_of_employees", "y")]


def test_1_blank_agrees_with_the_rule_of_filled() -> None:
    """``fill.FILLED_SQL``: NULL, empty, blank, ``[]``, ``{}`` and ``false``
    are empty. The number 0 is filled."""
    for nothing in (None, "", "  ", [], {}, False):
        assert blank(nothing), nothing
    for something in (0, 0.0, "0", "x", [1], {"a": 1}, True):
        assert not blank(something), something


def test_1_the_executor_is_pure() -> None:
    """It reads no database, calls no provider and calls no model."""
    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    banned = ("sqlalchemy", "httpx", "acb_llm", "acb_common", "gateway.routes",
              "gateway.db", "gateway.crm_sync.engine", "asyncio")
    found = {m for m in imported for b in banned if m == b or m.startswith(b + ".")}
    assert not found, found
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]


# ── 2. One path of a rule value ──────────────────────────────────────────────


def test_2_a_field_rule_runs_copy_for_text_and_cast_for_each_other_type() -> None:
    assert transform_of(_target("contact", "title")) == COPY
    for entity, column in (("lead", "no_of_employees"), ("lead", "annual_revenue"),
                           ("deal", "expected_close_date"), ("deal", "probability")):
        assert transform_of(_target(entity, column)) == CAST
    assert run(_target("lead", "no_of_employees"), "7") == Outcome(7)
    assert run(_target("contact", "title"), "CTO") == Outcome("CTO")
    with pytest.raises(ValueError, match="Unknown transform"):
        run(_target("contact", "title"), "CTO", "value_map")


def test_2_apply_rules_fills_only_where_the_fixed_map_gave_null() -> None:
    values: dict[str, Any] = {"industry": "Fixed", "no_of_employees": None}
    missed = apply_rules("lead", {"Sector": "Robotics", "Head": "lots", "X": "1"}, values, {
        "industry": "Sector", "no_of_employees": "Head", "lead_name": "X",
    })
    assert values["industry"] == "Fixed", "the value of the fixed map wins"
    assert values["no_of_employees"] is None
    assert "lead_name" not in values, "a column that is not a target is skipped"
    assert missed == ("no_of_employees",)


def test_2_map_record_names_the_columns_that_did_not_map() -> None:
    mapped = map_record("lead", {
        "id": "l1", "Last_Name": "One", "Head": "lots", "Why": "Price",
    }, modified_at=None, owners={}, rules={"no_of_employees": "Head", "lost_note": "Why"})
    assert mapped.values["lost_note"] == "Price"
    assert mapped.values["no_of_employees"] is None
    assert mapped.unmapped == ("no_of_employees",)
    assert mapped.values["custom"]["Head"] == "lots", "the value stays in custom"
    plain = map_record("lead", {"id": "l2", "Last_Name": "Two"},
                       modified_at=None, owners={})
    assert plain.unmapped == ()


def test_2_apply_field_rules_runs_the_executor(monkeypatch) -> None:
    seen: list[Any] = []

    def spy(entity: str, fields: Any, values: dict[str, Any], rules: Any) -> tuple[str, ...]:
        seen.append((entity, dict(rules)))
        values["lost_note"] = "by-the-executor"
        return ("lost_note",)

    monkeypatch.setattr(executor, "apply_rules", spy)
    out: list[str] = []
    values: dict[str, Any] = {}
    mapping.apply_field_rules("lead", {"Why": "x"}, values, {"lost_note": "Why"}, unmapped=out)
    assert seen == [("lead", {"lost_note": "Why"})]
    assert values["lost_note"] == "by-the-executor" and out == ["lost_note"]


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _Db:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return _Rows(self.rows)


def test_2_the_backfill_runs_the_executor_and_counts_what_did_not_map(monkeypatch) -> None:
    from types import SimpleNamespace

    from gateway.crm_sync import field_rules

    written: list[Any] = []

    async def door(db: Any, table: str, column: str, src: str, pairs: Any, **kw: Any) -> int:
        written.append((table, column, list(pairs)))
        return len(pairs)

    monkeypatch.setattr(field_rules, "apply_field_rule_from_source", door)
    rows = [SimpleNamespace(id="r1", value="40"), SimpleNamespace(id="r2", value="lots"),
            SimpleNamespace(id="r3", value="")]
    rule = field_rules.Rule("id-1", "lead", "Head", "no_of_employees")
    batch = asyncio.run(field_rules.backfill_batch(
        _Db(rows), mapping, rule, "zoho", None, organization_id="org"))
    assert batch == field_rules.Batch("r3", 3, 3, 1)
    assert written == [("crm_leads", "no_of_employees",
                        [("r1", 40), ("r2", None), ("r3", None)])]


def _coerce_uses(path: Path) -> list[tuple[str, int]]:
    """Each reference to the coercion in *path*: ``(enclosing def, line)``."""
    return _coerce_uses_in(ast.parse(path.read_text(encoding="utf-8")))


def _coerce_uses_in(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = child.name
            name = (
                child.id if isinstance(child, ast.Name)
                else child.attr if isinstance(child, ast.Attribute)
                else (child.asname or child.name) if isinstance(child, ast.alias)
                else None
            )
            if name in COERCE_NAMES:
                found.append((scope, getattr(child, "lineno", 0)))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def test_2_the_fence_finds_a_planted_coercion() -> None:
    """The scan is not blind: each form of a second path is found."""
    for planted in (
        "def f(m, t, v):\n    return m.coerce_target_value(t, v)\n",
        "from gateway.crm_sources.zoho.mapping import coerce_rule_value\n",
        "def f(t, v):\n    return coerce_rule_value(t.column_type, v)\n",
        "import x\nf = x.coerce_target_value\n",
    ):
        assert _coerce_uses_in(ast.parse(planted)), planted


def test_2_no_other_code_coerces_a_rule_value() -> None:
    """The source fence of item 2. Only the executor calls the coercion, and
    ``mapping.coerce_target_value`` calls ``coerce_rule_value`` inside the
    mapping module. Each other file of the gateway names neither."""
    allowed = {
        EXECUTOR: None,  # the one path: any use
        MAPPING: {"coerce_target_value"},  # its own body calls coerce_rule_value
    }
    offenders: list[str] = []
    for path in sorted(GATEWAY.rglob("*.py")):
        uses = _coerce_uses(path)
        if not uses:
            continue
        scopes = allowed.get(path, set())
        if scopes is None:
            continue
        bad = [(s, n) for s, n in uses if s not in scopes]
        offenders += [f"{path.relative_to(REPO)}:{n} in {s}" for s, n in bad]
    assert offenders == []
    assert _coerce_uses(EXECUTOR), "the executor no longer calls the coercion"


def _calls_in(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    (node,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function]
    return {
        ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)
    }


def test_2_the_sync_and_the_backfill_call_the_executor() -> None:
    assert "executor.apply_rules" in _calls_in(MAPPING, "apply_field_rules")
    assert "executor.run" in _calls_in(FIELD_RULES, "backfill_batch")


# ── 3. Only copy and cast ────────────────────────────────────────────────────


def test_3_the_executor_holds_only_copy_and_cast() -> None:
    """``value_map``, ``normalize``, ``coalesce``, ``link`` and ``owner`` are
    CRM-Q20-1. It adds each one to ``TRANSFORMS``."""
    assert dict(TRANSFORMS) == {COPY: copy, CAST: cast}
    with pytest.raises(TypeError):
        TRANSFORMS["value_map"] = copy  # type: ignore[index]


# ── 6. The preview of one stored record ──────────────────────────────────────


def test_6_a_free_column_goes_back_to_null_when_the_draft_drops_its_rule() -> None:
    values = {"lost_note": "Price", "industry": None}
    missed = preview_record("lead", values, {"Why": "Price"},
                            live={"lost_note": "Why"}, draft={})
    assert values["lost_note"] is None and missed == ()


def test_6_the_value_of_the_fixed_map_wins_over_a_draft_rule() -> None:
    """``industry`` has no live rule, so the stored value is the fixed map's."""
    values = {"industry": "Standard"}
    preview_record("lead", values, {"Sector": "Drones"}, live={},
                   draft={"industry": "Sector"})
    assert values["industry"] == "Standard"
    empty = {"industry": None}
    preview_record("lead", empty, {"Sector": "Drones"}, live={},
                   draft={"industry": "Sector"})
    assert empty["industry"] == "Drones"


def test_6_a_fixed_column_that_a_live_rule_fills_takes_the_draft_value() -> None:
    values = {"industry": "Robotics"}
    preview_record("lead", values, {"Sector": "Robotics", "Other": "Space"},
                   live={"industry": "Sector"}, draft={"industry": "Other"})
    assert values["industry"] == "Space"


def test_6_the_preview_names_the_columns_that_did_not_map_never_a_value() -> None:
    values: dict[str, Any] = {"no_of_employees": None, "lost_note": None}
    missed = preview_record("lead", values, {"Head": "lots", "Why": "Price"}, live={},
                            draft={"no_of_employees": "Head", "lost_note": "Why"})
    assert missed == ("no_of_employees",)
    assert values == {"no_of_employees": None, "lost_note": "Price"}


# ── 7. The run counts and Sync health ────────────────────────────────────────


def test_7_each_entity_of_the_run_counts_unmapped() -> None:
    from gateway.crm_sync import engine

    blank_counts = engine._blank_counts()
    assert all(blank_counts[slug]["unmapped"] == 0 for slug in engine.SLUG_ORDER)
    # A resumed run keeps the count it had.
    merged = engine._merge_counts({"leads": {"unmapped": 3, "seen": 9}})
    assert merged["leads"]["unmapped"] == 3


def test_7_the_engine_counts_a_record_that_did_not_map() -> None:
    """``_write_record`` bumps ``unmapped`` from ``Mapped.unmapped``, and the
    rules step bumps it from each backfill batch."""
    engine_src = (GATEWAY / "crm_sync" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src)
    bumps: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bumps[node.name] = [
                ast.unparse(c) for c in ast.walk(node)
                if isinstance(c, ast.Call) and ast.unparse(c.func) == "ctx.bump"
                and "'unmapped'" in ast.unparse(c)
            ]
    assert bumps["_write_record"] == ["ctx.bump(entity, 'unmapped', len(mapped.unmapped))"]
    assert bumps["_field_rules"] == ["ctx.bump(rule.entity, 'unmapped', batch.unmapped)"]


def test_7_sync_health_passes_unmapped_through_last_run() -> None:
    from gateway.routes.crm.sync import _last_run_counts

    assert _last_run_counts({"seen": 4, "unmapped": 2})["unmapped"] == 2
