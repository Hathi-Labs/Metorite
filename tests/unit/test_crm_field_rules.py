"""The rules of the Field map that need no database (WS-53 CRM-Q7a).

Spec: ``project-docs/specs/crm_platform.md`` §18.7, "CRM-Q7a: the rule table
and the Field map". ``tests/unit/test_crm_field_rules_r8.py`` proves the rest
against a real Postgres.

What this pins:

- ``FIELD_RULE_TARGETS`` holds exactly the two tables of §18.7, no more;
- the type agreement table;
- a fixed column opens only when its standard field is off every active
  layout, and an unknown layout keeps it closed;
- ``map_record`` applies a rule after the fixed rules, only where the fixed
  map gives NULL, and the value stays in ``custom``;
- a value that does not coerce gives NULL;
- the three stale cases, and that a section that was not read never makes a
  rule stale;
- the profile gains ``layout_fields`` with no new provider call;
- the door refuses a column that is not a target, and a clear of a fixed
  column.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from gateway.crm_sources.zoho import mapping
from gateway.crm_sources.zoho.mapping import (
    FIELD_RULE_TARGETS,
    apply_field_rules,
    build_profile,
    coerce_rule_value,
    discovered_field_types,
    entity_field_types,
    entity_layout_fields,
    layout_fields,
    map_record,
    rule_problem,
    rule_stale_reason,
    types_agree,
)

#: §18.7 "The legal targets", copied by hand: (column type, standard field).
EXPECTED: dict[str, dict[str, tuple[str, str | None]]] = {
    "company": {
        "linkedin_url": ("TEXT", None),
        "website": ("TEXT", "Website"), "industry": ("TEXT", "Industry"),
        "phone": ("TEXT", "Phone"), "email": ("TEXT", "Email"),
        "description": ("TEXT", "Description"),
        "no_of_employees": ("INT", "Employees"),
        "annual_revenue": ("NUMERIC", "Annual_Revenue"),
    },
    "contact": {
        "linkedin_url": ("TEXT", None),
        "email": ("TEXT", "Email"), "phone": ("TEXT", "Phone"),
        "mobile": ("TEXT", "Mobile"), "title": ("TEXT", "Title"),
        "description": ("TEXT", "Description"),
    },
    "lead": {
        "lost_note": ("TEXT", None),
        "website": ("TEXT", "Website"), "industry": ("TEXT", "Industry"),
        "email": ("TEXT", "Email"), "phone": ("TEXT", "Phone"),
        "mobile": ("TEXT", "Mobile"), "lead_source": ("TEXT", "Lead_Source"),
        "organization_name": ("TEXT", "Company"),
        "description": ("TEXT", "Description"),
        "no_of_employees": ("INT", "No_of_Employees"),
        "annual_revenue": ("NUMERIC", "Annual_Revenue"),
    },
    "deal": {
        "lost_note": ("TEXT", None),
        "next_step": ("TEXT", "Next_Step"), "description": ("TEXT", "Description"),
        "expected_close_date": ("DATE", "Closing_Date"),
        "probability": ("SMALLINT", "Probability"),
    },
}


def test_the_targets_are_exactly_the_two_tables_of_the_spec() -> None:
    got = {
        entity: {c: (t.column_type, t.standard_field) for c, t in targets.items()}
        for entity, targets in FIELD_RULE_TARGETS.items()
    }
    assert got == EXPECTED


def test_each_fixed_target_reads_its_standard_field_in_the_fixed_map() -> None:
    """A fixed column is one the fixed map fills from its standard field, so
    the standard field must be a field the fixed map consumes."""
    for entity, targets in FIELD_RULE_TARGETS.items():
        consumed = mapping.custom_field_keys(entity)
        for target in targets.values():
            if target.standard_field is not None:
                assert target.standard_field in consumed, (entity, target.column)
            else:
                # A free column: the fixed map never writes it.
                assert target.column not in map_record(
                    entity, {"id": "x"}, modified_at=None, owners={},
                ).values, (entity, target.column)


@pytest.mark.parametrize(("word", "columns"), [
    ("text", {"TEXT"}), ("textarea", {"TEXT"}), ("email", {"TEXT"}),
    ("phone", {"TEXT"}), ("url", {"TEXT"}), ("select", {"TEXT"}),
    ("number", {"NUMERIC", "INT", "SMALLINT"}),
    ("currency", {"NUMERIC", "INT", "SMALLINT"}),
    ("date", {"DATE"}), ("datetime", {"TIMESTAMPTZ"}),
    ("multiselect", set()), ("lookup", set()), ("boolean", set()), (None, set()),
])
def test_the_type_agreement(word: str | None, columns: set[str]) -> None:
    every = {"TEXT", "NUMERIC", "INT", "SMALLINT", "DATE", "TIMESTAMPTZ"}
    assert {c for c in every if types_agree(word, c)} == columns


def test_a_fixed_column_opens_only_with_its_standard_field_off_every_layout() -> None:
    on = frozenset({"Industry", "Email"})
    off = frozenset({"Email"})
    assert rule_problem("lead", "select", "industry", on) == "standard_field_on_layout"
    assert rule_problem("lead", "select", "industry", off) is None
    assert rule_problem("lead", "select", "industry", None) == "layout_unknown"
    # A free column needs no layout.
    assert rule_problem("lead", "text", "lost_note", None) is None
    assert rule_problem("contact", "url", "linkedin_url", None) is None


def test_an_illegal_rule_names_its_reason() -> None:
    off = frozenset()
    assert rule_problem("lead", "date", "industry", off) == "type_mismatch"
    assert rule_problem("lead", "multiselect", "lost_note", off) == "type_mismatch"
    assert rule_problem("lead", "lookup", "industry", off) == "type_mismatch"
    assert rule_problem("deal", "currency", "amount", off) == "unknown_target"
    assert rule_problem("deal", "text", "name", off) == "unknown_target"
    assert rule_problem("lead", "text", "owner_email", off) == "unknown_target"
    assert rule_problem("note", "text", "body", off) == "unknown_target"
    assert rule_problem("deal", "number", "probability", off) is None
    assert rule_problem("deal", "date", "expected_close_date", off) is None


LAYOUTS = {
    "Leads": [
        {"id": "1", "status": "active", "sections": [
            {"fields": [{"api_name": "Last_Name"}, {"api_name": "Email"}]},
            {"fields": [{"api_name": "Custom_Industry"}]},
        ]},
        # An inactive layout does not count.
        {"id": "2", "status": "inactive", "sections": [
            {"fields": [{"api_name": "Industry"}]},
        ]},
    ],
    # No status: active.
    "Accounts": [{"id": "3", "sections": [{"fields": [{"api_name": "Website"}]}]}],
    "Tasks": [{"id": "4", "sections": [{"fields": [{"api_name": "Subject"}]}]}],
}


def test_layout_fields_lists_the_fields_on_an_active_layout() -> None:
    assert layout_fields(LAYOUTS) == {
        "company": ["Website"],
        "lead": ["Custom_Industry", "Email", "Last_Name"],
    }


def test_the_profile_gains_layout_fields_and_keeps_them_on_a_failed_read() -> None:
    raw = {"layouts": LAYOUTS, "failed": []}
    profile = build_profile(raw)
    assert profile["layout_fields"]["lead"] == ["Custom_Industry", "Email", "Last_Name"]
    assert entity_layout_fields(profile, "lead") == frozenset(
        {"Custom_Industry", "Email", "Last_Name"})
    assert entity_layout_fields(profile, "deal") is None, "not read is not 'empty'"
    assert entity_layout_fields({}, "lead") is None
    failed = build_profile({"layouts": {}, "failed": ["layouts"]}, previous=profile)
    assert failed["layout_fields"] == profile["layout_fields"]


def test_map_record_applies_a_rule_only_where_the_fixed_map_gives_null() -> None:
    rules = {"industry": "Custom_Industry", "lost_note": "Why_Lost"}
    blank = map_record("lead", {
        "id": "l1", "Custom_Industry": "Robotics", "Why_Lost": "Price",
    }, modified_at=None, owners={}, rules=rules)
    assert blank.values["industry"] == "Robotics"
    assert blank.values["lost_note"] == "Price"
    # The value also stays in custom, so a reject loses nothing.
    assert blank.values["custom"]["Custom_Industry"] == "Robotics"
    kept = map_record("lead", {
        "id": "l2", "Industry": "Standard", "Custom_Industry": "Robotics",
    }, modified_at=None, owners={}, rules=rules)
    assert kept.values["industry"] == "Standard"
    # With no rule the free column is not written at all.
    plain = map_record("lead", {"id": "l3", "Why_Lost": "Price"}, modified_at=None, owners={})
    assert "lost_note" not in plain.values and plain.values["industry"] is None


def test_a_value_that_does_not_coerce_gives_null() -> None:
    values: dict[str, Any] = {"no_of_employees": None, "expected_close_date": None}
    apply_field_rules("lead", {"Head": "lots"}, values, {"no_of_employees": "Head"})
    assert values["no_of_employees"] is None
    deal: dict[str, Any] = {"expected_close_date": None, "probability": None}
    apply_field_rules("deal", {"When": "soon", "P": 10**6}, deal,
                      {"expected_close_date": "When", "probability": "P"})
    assert deal == {"expected_close_date": None, "probability": None}
    # A column that is not a target is skipped, never written.
    other: dict[str, Any] = {}
    apply_field_rules("deal", {"X": "1"}, other, {"amount": "X", "name": "X"})
    assert other == {}


@pytest.mark.parametrize(("kind", "value", "expected"), [
    ("TEXT", "  Robotics ", "Robotics"), ("TEXT", "", None), ("TEXT", ["a"], None),
    ("TEXT", 12, "12"),
    ("NUMERIC", "12.5", 12.5), ("NUMERIC", "x", None), ("NUMERIC", 10**13, None),
    ("INT", "41.9", 41), ("INT", 2**31, None),
    ("SMALLINT", 55, 55), ("SMALLINT", 2**15, None),
    ("DATE", "2026-10-12", date(2026, 10, 12)), ("DATE", "soon", None),
    ("TIMESTAMPTZ", "nope", None), ("BLOB", "x", None),
])
def test_the_coercion_uses_the_mapping_helpers(kind: str, value: Any, expected: Any) -> None:
    assert coerce_rule_value(kind, value) == expected


def test_the_three_stale_cases() -> None:
    types = {"Custom_Industry": "select", "Head": "number"}
    off = frozenset({"Email"})
    assert rule_stale_reason("lead", "Custom_Industry", "industry", types, off) is None
    assert rule_stale_reason("lead", "Gone", "industry", types, off) == "field_gone"
    assert rule_stale_reason(
        "lead", "Custom_Industry", "industry", {"Custom_Industry": "multiselect"}, off,
    ) == "type_mismatch"
    assert rule_stale_reason(
        "lead", "Custom_Industry", "industry", types, frozenset({"Industry"}),
    ) == "standard_field_on_layout"
    # A free column has no standard field to come back.
    assert rule_stale_reason(
        "lead", "Custom_Industry", "lost_note", types, frozenset({"Industry"}),
    ) is None
    # A section that was not read never makes a rule stale.
    assert rule_stale_reason("lead", "Gone", "industry", None, None) is None


def test_the_discovered_field_types_use_the_field_def_words() -> None:
    raw = {"fields": {
        "Leads": [{"api_name": "Custom_Industry", "data_type": "picklist"},
                  {"api_name": "Tags", "data_type": "multiselectpicklist"},
                  {"api_name": "Site", "data_type": "website"},
                  # A standard field has a column, so it does not go to custom.
                  {"api_name": "Industry", "data_type": "picklist"},
                  {"api_name": "$approved", "data_type": "boolean"}],
        "Tasks": [{"api_name": "Subject", "data_type": "text"}],
    }}
    assert discovered_field_types(raw) == {
        "lead": {"Custom_Industry": "select", "Tags": "multiselect", "Site": "url"},
    }
    profile = build_profile({**raw, "failed": []})
    assert entity_field_types(profile, "lead") == {
        "Custom_Industry": "select", "Site": "url", "Tags": "multiselect"}
    assert entity_field_types(profile, "deal") is None
    kept = build_profile({"fields": {}, "failed": ["fields"]}, previous=profile)
    assert kept["custom_field_types"] == profile["custom_field_types"]


class _FakeDb:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.sql.append(str(statement))

        class _Result:
            rowcount = 1

        return _Result()


def _door(*args: Any) -> Any:
    from gateway.routes.crm.core import apply_field_rule_from_source

    db = _FakeDb()
    return db, asyncio.run(apply_field_rule_from_source(db, *args))


def test_the_door_refuses_a_column_that_is_not_a_target() -> None:
    with pytest.raises(ValueError):
        _door("crm_deals", "amount", "zoho", [("00000000-0000-0000-0000-000000000001", 1)])
    with pytest.raises(ValueError):
        _door("crm_activities", "subject", "zoho", [])
    with pytest.raises(ValueError):
        _door("crm_leads", "industry; DROP TABLE x", "zoho", [])
    with pytest.raises(ValueError):
        _door("crm_leads", "industry", "", [])


def test_the_door_clears_only_a_free_column() -> None:
    with pytest.raises(ValueError):
        _door("crm_leads", "industry", "zoho", None)
    db, written = _door("crm_leads", "lost_note", "zoho", None)
    assert written == 1
    assert "SET lost_note = NULL" in db.sql[0] and "LIMIT 500" in db.sql[0]


def test_the_door_fills_only_a_null_column_of_a_mirrored_row() -> None:
    db, written = _door("crm_leads", "industry", "zoho", [
        ("00000000-0000-0000-0000-000000000001", "Robotics"),
        ("00000000-0000-0000-0000-000000000002", None),
    ])
    assert written == 1, "a value that did not coerce writes nothing"
    assert "industry IS NULL" in db.sql[0] and "ext_source = :src" in db.sql[0]
