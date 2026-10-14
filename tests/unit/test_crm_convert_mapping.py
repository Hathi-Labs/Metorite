"""The lead-conversion mapping, with no database (WS-53 CRM-C6a,
``crm_platform.md`` §20.9).

``routes/crm/convert_mapping.py`` holds the rules: which pair of types is
compatible, what a row means (auto, skip, explicit), which body a ``PUT``
refuses, and how a value takes the shape of its target. This file holds them,
and a static fence on the RLS block of the migration.

``tests/unit/test_crm_convert_mapping_r8.py`` runs the routes and the
conversion against a real Postgres.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.crm.convert_mapping import (
    TARGET_ENTITIES,
    Cell,
    ConversionMappingIn,
    MapRow,
    _Read,
    as_target_value,
    check_rows,
    choices,
    compatible,
    effective_map,
    inactive_options,
)
from gateway.routes.crm.field_studio import FIELD_TYPES, TYPE_CHANGES, CustomField


def _f(
    key: str, kind: str = "text", *, retired: bool = False, target: str | None = None,
    options: tuple[Any, ...] = (),
) -> CustomField:
    return CustomField(
        key=key, label=key.title(), type=kind, options=options, required=False,
        default_value=None, lookup_target=target, retired=retired,
    )


# ── Compatible pairs (item 5) ───────────────────────────────────────────────


@pytest.mark.parametrize("source", [t for t in FIELD_TYPES if t != "lookup"])
@pytest.mark.parametrize("target", [t for t in FIELD_TYPES if t != "lookup"])
def test_a_pair_is_compatible_by_the_type_change_matrix(source: str, target: str) -> None:
    want = source == target or target in TYPE_CHANGES.get(source, frozenset())
    assert compatible(_f("a", source), _f("b", target)) is want


def test_a_lookup_pairs_only_with_a_lookup_of_the_same_target() -> None:
    contacts = _f("a", "lookup", target="contacts")
    assert compatible(contacts, _f("b", "lookup", target="contacts"))
    assert not compatible(contacts, _f("b", "lookup", target="deals"))
    assert not compatible(contacts, _f("b", "text"))
    assert not compatible(_f("a", "text"), _f("b", "lookup", target="contacts"))


def test_a_text_never_fills_a_number_and_a_textarea_never_fills_a_text() -> None:
    assert not compatible(_f("a", "text"), _f("b", "number"))
    assert not compatible(_f("a", "textarea"), _f("b", "text"))
    assert compatible(_f("a", "select"), _f("b", "multiselect"))
    assert not compatible(_f("a", "multiselect"), _f("b", "select"))


# ── What a row means (items 1 to 7) ─────────────────────────────────────────


def _targets(**by_entity: dict[str, CustomField]) -> dict[str, dict[str, CustomField]]:
    return {e: by_entity.get(e, {}) for e in TARGET_ENTITIES}


def test_no_row_and_a_null_row_both_mean_auto() -> None:
    sources = {"region": _f("region")}
    targets = _targets(deal={"region": _f("region")})
    none = effective_map(sources, targets, [])
    null = effective_map(sources, targets, [MapRow("region", "deal", None, False)])
    assert none == null
    assert none[("region", "deal")] == Cell("region", "auto")
    assert none[("region", "contact")] == Cell(None, "auto")


def test_auto_takes_no_target_that_is_retired_or_incompatible() -> None:
    sources = {"region": _f("region"), "size": _f("size", "number")}
    targets = _targets(
        deal={"region": _f("region", retired=True), "size": _f("size", "text")},
    )
    cells = effective_map(sources, targets, [])
    assert cells[("region", "deal")] == Cell(None, "auto")
    assert cells[("size", "deal")] == Cell(None, "auto")


def test_skip_copies_nothing() -> None:
    sources = {"region": _f("region")}
    targets = _targets(deal={"region": _f("region")})
    cells = effective_map(sources, targets, [MapRow("region", "deal", None, True)])
    assert cells[("region", "deal")] == Cell(None, "skip")


def test_an_explicit_row_wins_over_auto_for_its_target() -> None:
    sources = {"region": _f("region"), "area": _f("area")}
    targets = _targets(deal={"region": _f("region")})
    cells = effective_map(sources, targets, [MapRow("area", "deal", "region", False)])
    assert cells[("area", "deal")] == Cell("region", "explicit")
    assert cells[("region", "deal")] == Cell(None, "auto"), "auto yields its target"


def test_an_explicit_row_names_the_problem_of_its_target() -> None:
    sources = {"a": _f("a"), "b": _f("b"), "c": _f("c")}
    targets = _targets(deal={"gone": _f("gone", retired=True), "num": _f("num", "number")})
    cells = effective_map(sources, targets, [
        MapRow("a", "deal", "missing", False),
        MapRow("b", "deal", "gone", False),
        MapRow("c", "deal", "num", False),
    ])
    assert cells[("a", "deal")].problem == "target_missing"
    assert cells[("b", "deal")].problem == "target_retired"
    assert cells[("c", "deal")].problem == "type_mismatch"


def test_a_retired_lead_field_is_not_in_the_map() -> None:
    sources = {"old": _f("old", retired=True)}
    targets = _targets(deal={"old": _f("old")})
    assert effective_map(sources, targets, []) == {}


def test_the_choices_are_the_live_compatible_targets() -> None:
    fields = {"t": _f("t"), "ta": _f("ta", "textarea"), "n": _f("n", "number"),
              "r": _f("r", retired=True)}
    assert choices(_f("s"), fields) == ["t", "ta"]


# ── The PUT body ────────────────────────────────────────────────────────────


def _read() -> _Read:
    return _Read(
        sources={"region": _f("region"), "budget": _f("budget", "number"),
                 "old": _f("old", retired=True)},
        targets=_targets(
            deal={"region": _f("region"), "amount_x": _f("amount_x", "decimal"),
                  "gone": _f("gone", retired=True), "note": _f("note")},
        ),
        rows=[],
    )


def _code(rows: list[dict[str, Any]]) -> str | None:
    try:
        check_rows(_read(), ConversionMappingIn(rows=rows).rows)
    except HTTPException as exc:
        assert exc.status_code == 422
        return exc.detail["code"]
    return None


@pytest.mark.parametrize(("rows", "code"), [
    ([{"source_key": "budget", "target_entity": "deal", "target_key": "amount_x"}], None),
    ([{"source_key": "region", "target_entity": "deal", "skip": True}], None),
    ([{"source_key": "region", "target_entity": "deal"}], None),
    ([{"source_key": "nope", "target_entity": "deal"}], "mapping_source"),
    ([{"source_key": "old", "target_entity": "deal"}], "mapping_source"),
    ([{"source_key": "region", "target_entity": "deal"},
      {"source_key": "region", "target_entity": "deal", "skip": True}], "mapping_twice"),
    ([{"source_key": "region", "target_entity": "deal", "target_key": "note",
       "skip": True}], "mapping_skip_target"),
    ([{"source_key": "region", "target_entity": "deal", "target_key": "nope"}],
     "mapping_target"),
    ([{"source_key": "region", "target_entity": "deal", "target_key": "gone"}],
     "mapping_target_retired"),
    ([{"source_key": "region", "target_entity": "deal", "target_key": "amount_x"}],
     "mapping_type"),
    ([{"source_key": "region", "target_entity": "deal", "target_key": "note"},
      {"source_key": "budget", "target_entity": "contact"},
      {"source_key": "region", "target_entity": "contact"},
      {"source_key": "budget", "target_entity": "deal", "target_key": "note"}],
     "mapping_type"),
])
def test_the_put_refuses_each_bad_row(rows: list[dict[str, Any]], code: str | None) -> None:
    assert _code(rows) == code


def test_two_explicit_rows_on_one_target_give_422() -> None:
    read = _read()
    read.sources["area"] = _f("area")
    body = ConversionMappingIn(rows=[
        {"source_key": "region", "target_entity": "deal", "target_key": "note"},
        {"source_key": "area", "target_entity": "deal", "target_key": "note"},
    ])
    with pytest.raises(HTTPException) as caught:
        check_rows(read, body.rows)
    assert caught.value.detail["code"] == "mapping_target_twice"


def test_a_body_names_no_tenant() -> None:
    """R5: the body carries no organization. An extra key gives 422."""
    with pytest.raises(ValueError):
        ConversionMappingIn(rows=[{"source_key": "a", "target_entity": "deal",
                                   "organization_id": "x"}])


# ── The value at a conversion ───────────────────────────────────────────────


def test_a_select_value_becomes_a_list_of_one_for_a_multiselect() -> None:
    assert as_target_value(_f("s", "select"), _f("m", "multiselect"), "gold") == ["gold"]
    assert as_target_value(_f("s", "select"), _f("t", "select"), "gold") == "gold"
    assert as_target_value(_f("n", "number"), _f("d", "decimal"), 5) == 5


def test_an_option_that_is_not_active_is_named() -> None:
    target = _f("t", "multiselect", options=(
        {"label": "Gold", "value": "gold"},
        {"label": "Old", "value": "old", "retired": True},
    ))
    assert inactive_options(target, ["gold"]) == []
    assert inactive_options(target, ["gold", "old", "new"]) == ["old", "new"]
    assert inactive_options(_f("t"), "anything") == []


# ── The RLS of the migration (R7) ───────────────────────────────────────────
#
# The R8 fixture applies the generated policies after the ladder, so the
# database alone cannot see a migration that lost its own RLS block. This
# fence reads the file, as test_crm_field_studio.py does for 260.

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_TENANT = r"organization_id\s*=\s*current_setting\('app\.tenant_id',\s*true\)::uuid"


def _mapping_migration() -> str:
    """The migration that creates the table, found by content (R1)."""
    found = [
        p for p in _MIGRATIONS.glob("*.sql")
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_convert_mappings" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0].read_text(encoding="utf-8")


def rls_problems(sql: str) -> list[str]:
    """What the migration lacks of the tenant shape of crm_convert_mappings."""
    body = re.sub(r"--[^\n]*", "", sql)
    wanted = {
        "ENABLE": r"ALTER\s+TABLE\s+crm_convert_mappings\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
        "FORCE": r"ALTER\s+TABLE\s+crm_convert_mappings\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
        "POLICY": (
            r"CREATE\s+POLICY\s+\w+\s+ON\s+crm_convert_mappings\s+"
            rf"USING\s*\(\s*{_TENANT}\s*\)\s*WITH\s+CHECK\s*\(\s*{_TENANT}\s*\)"
        ),
        "TENANT_KEY": (
            r"organization_id\s+UUID\s+NOT\s+NULL\s+REFERENCES\s+organization\s*\(\s*id\s*\)"
            r"\s+ON\s+DELETE\s+CASCADE"
        ),
        "UNIQUE": r"UNIQUE\s*\(\s*organization_id\s*,\s*source_key\s*,\s*target_entity\s*\)",
    }
    return [name for name, pattern in wanted.items() if not re.search(pattern, body, re.I)]


def test_the_mapping_migration_forces_rls_with_a_tenant_policy() -> None:
    assert rls_problems(_mapping_migration()) == []


def test_the_rls_fence_sees_a_missing_block() -> None:
    sql = _mapping_migration()
    start = sql.index("DO $rls$")
    end = sql.index("$rls$;", start) + len("$rls$;")
    assert rls_problems(sql[:start] + sql[end:]) == ["ENABLE", "FORCE", "POLICY"]
    no_check = re.sub(r"WITH CHECK \([^\n]*\);", ";", sql)
    assert rls_problems(no_check) == ["POLICY"]
    no_force = sql.replace("FORCE  ROW LEVEL SECURITY", "NO FORCE ROW LEVEL SECURITY")
    assert rls_problems(no_force) == ["FORCE"]
    no_unique = sql.replace("UNIQUE (organization_id, source_key, target_entity)", "")
    assert rls_problems(no_unique) == ["UNIQUE"]
