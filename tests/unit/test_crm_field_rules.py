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
  column;
- CRM-Q18-3: an empty fixed column opens, the ``opened_empty`` mark, a rule
  with the mark never goes stale for its layout, the value of the standard
  field still wins, and a put with a new column clears the mark.
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


ORG = "11111111-1111-1111-1111-111111111111"


class _FakeDb:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.sql.append(str(statement))
        self.params.append(params)

        class _Result:
            rowcount = 1

        return _Result()


def _door(*args: Any, organization_id: str = ORG) -> Any:
    from gateway.routes.crm.core import apply_field_rule_from_source

    db = _FakeDb()
    return db, asyncio.run(
        apply_field_rule_from_source(db, *args, organization_id=organization_id))


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


def test_the_door_names_the_organization_in_each_statement() -> None:
    """Review: like ``reset_crm_from_admin``, each statement names the org, so
    a session with row level security off still writes one organization."""
    db, _ = _door("crm_leads", "lost_note", "zoho", None)
    assert db.sql[0].count("organization_id = CAST(:org AS uuid)") == 2
    assert db.params[0]["org"] == ORG
    db, _ = _door("crm_leads", "industry", "zoho", [
        ("00000000-0000-0000-0000-000000000001", "Robotics")])
    assert "organization_id = CAST(:org AS uuid)" in db.sql[0]
    assert db.params[0]["org"] == ORG
    with pytest.raises(ValueError):
        _door("crm_leads", "lost_note", "zoho", None, organization_id="")


@pytest.mark.parametrize(("value", "expected"), [
    (55, 55), ("100", 100), (0, 0), (150, None), (101, None), (-5, None), ("x", None),
])
def test_a_rule_never_pushes_probability_past_100(value: Any, expected: Any) -> None:
    """Review: a custom score of 150 would forecast more than the amount."""
    deal: dict[str, Any] = {"probability": None}
    apply_field_rules("deal", {"Score": value}, deal, {"probability": "Score"})
    assert deal["probability"] == expected


# ── CRM-Q18-1 fix round: a deadlock with the sync is a 409, not a 500 ────────


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(("sqlstate", "code"), [
    ("40P01", "plan_busy"), ("40001", "plan_busy"), ("23505", "rule_taken"),
])
def test_a_deadlock_or_a_unique_violation_is_a_409(sqlstate: str, code: str) -> None:
    from gateway.routes.crm import field_rules
    from sqlalchemy.exc import DBAPIError

    conflict = field_rules._db_conflict(DBAPIError("SELECT 1", {}, _PgError(sqlstate)))
    assert conflict is not None
    assert (conflict.status_code, conflict.detail["code"]) == (409, code)
    if code == "plan_busy":
        assert conflict.headers == {"Retry-After": "1"}
    other = DBAPIError("SELECT 1", {}, _PgError("42P01"))
    assert field_rules._db_conflict(other) is None, "any other error stays a 500"


# ── CRM-Q18-3: a column that is empty opens ─────────────────────────────────

#: Email and Company are on the Leads layout. Phone is not.
ON_LAYOUT = frozenset({"Email", "Company"})


def test_q18_3_an_empty_fixed_column_opens_and_the_type_test_still_counts() -> None:
    """Items 1 and 4: ``rule_problem`` takes the empty keys as an argument."""
    assert rule_problem("lead", "text", "email", ON_LAYOUT) == "standard_field_on_layout"
    assert rule_problem("lead", "text", "email", ON_LAYOUT, {"email"}) is None
    assert rule_problem("lead", "text", "email", None) == "layout_unknown"
    assert rule_problem("lead", "text", "email", None, {"email"}) is None
    assert rule_problem("lead", "date", "email", ON_LAYOUT, {"email"}) == "type_mismatch"
    assert rule_problem(
        "lead", "text", "email", ON_LAYOUT, {"phone"},
    ) == "standard_field_on_layout", "another empty key opens no other column"
    assert rule_problem(
        "lead", "text", "lead_name", ON_LAYOUT, {"lead_name"},
    ) == "unknown_target", "an empty key opens only a legal target"
    assert "email" in mapping.legal_columns("lead", "text", ON_LAYOUT, {"email"})
    assert "email" not in mapping.legal_columns("lead", "text", ON_LAYOUT)


def test_q18_3_the_mark_names_a_column_that_only_its_fill_opened() -> None:
    """Item 5: the approve marks a rule only when item 1 opened its column."""
    opened = mapping.opened_by_empty
    assert opened("lead", "email", ON_LAYOUT, {"email"}) is True
    assert opened("lead", "email", None, {"email"}) is True
    assert opened("lead", "phone", ON_LAYOUT, {"phone"}) is False, "the layouts open it"
    assert opened("lead", "lost_note", ON_LAYOUT, {"lost_note"}) is False, "a free column"
    assert opened("lead", "email", ON_LAYOUT, ()) is False
    assert opened("lead", "lead_name", ON_LAYOUT, {"lead_name"}) is False


def test_q18_3_the_mapping_functions_stay_pure() -> None:
    """Item 4: the empty keys go in as an argument. ``mapping.py`` reads no
    fill and imports no route."""
    import inspect
    import re

    source = inspect.getsource(mapping)
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
    assert not [m for m in imports if "routes" in m or "fill" in m or "sqlalchemy" in m]
    for function in (rule_problem, mapping.legal_columns, mapping.opened_by_empty):
        assert "empty" in inspect.signature(function).parameters


def test_q18_3_an_opened_rule_never_goes_stale_for_its_layout() -> None:
    """Item 2: the other two reasons still apply."""
    types = {"Why_Lost": "textarea"}
    assert rule_stale_reason(
        "lead", "Why_Lost", "email", types, ON_LAYOUT,
    ) == "standard_field_on_layout"
    assert rule_stale_reason("lead", "Why_Lost", "email", types, ON_LAYOUT, opened=True) is None
    assert rule_stale_reason(
        "lead", "Gone", "email", types, ON_LAYOUT, opened=True,
    ) == "field_gone"
    assert rule_stale_reason(
        "lead", "Why_Lost", "email", {"Why_Lost": "date"}, ON_LAYOUT, opened=True,
    ) == "type_mismatch"


def test_q18_3_the_value_of_the_standard_field_still_wins_on_an_opened_column() -> None:
    """Item 8: ``apply_field_rules`` applies a rule only where the fixed map
    gives NULL, also on a column that an empty fill opened."""
    rules = {"email": "Why_Lost"}
    zoho = map_record("lead", {
        "id": "l1", "Email": "ann@lead.test", "Why_Lost": "Price",
    }, modified_at=None, owners={}, rules=rules)
    assert zoho.values["email"] == "ann@lead.test"
    blank = map_record("lead", {"id": "l2", "Why_Lost": "Price"},
                       modified_at=None, owners={}, rules=rules)
    assert blank.values["email"] == "Price"


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _LockDb:
    """Gives *rows* for each statement, and records each statement."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.sql: list[str] = []
        self.params: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.sql.append(str(statement))
        self.params.append(params)
        return _Rows(self.rows)


def test_q18_3_mark_stale_reads_the_mark_through_the_one_lock() -> None:
    """Item 6: ``FIELD_RULE_LOCK_SQL`` reads ``opened_empty``, and
    ``mark_stale`` passes it to ``rule_stale_reason``."""
    from types import SimpleNamespace as Row

    from gateway.crm_sync import field_rules as sync_rules
    from gateway.routes.crm.core import FIELD_RULE_LOCK_SQL

    assert "opened_empty" in FIELD_RULE_LOCK_SQL
    opened_id = "00000000-0000-0000-0000-000000000001"
    plain_id = "00000000-0000-0000-0000-000000000002"
    db = _LockDb([
        Row(id=opened_id, entity="lead", field_api_name="Why_Lost",
            target_column="email", status="approved", opened_empty=True),
        # NULL from a row of before CRM-Q18-3 means false.
        Row(id=plain_id, entity="lead", field_api_name="Note_X",
            target_column="phone", status="approved", opened_empty=None),
    ])
    profile = {
        "layout_fields": {"lead": ["Email", "Phone"]},
        "custom_field_types": {"lead": {"Why_Lost": "textarea", "Note_X": "text"}},
    }
    stale = asyncio.run(sync_rules.mark_stale(db, mapping, "zoho", profile))
    assert stale == [plain_id]
    assert db.sql[0] == FIELD_RULE_LOCK_SQL


def test_q18_3_the_approve_check_marks_and_skips_the_empty_test() -> None:
    """Items 4 and 7: ``_check_legal`` returns the mark, and an item that
    carries it skips the empty test. The type test still applies."""
    from fastapi import HTTPException
    from gateway.routes.crm import field_rules

    profile = {"layout_fields": {"lead": sorted(ON_LAYOUT)}}
    with pytest.raises(HTTPException) as closed:
        field_rules._check_legal("lead", "text", "email", profile)
    assert closed.value.detail["code"] == "standard_field_on_layout"
    assert field_rules._check_legal("lead", "text", "email", profile, {"email"}) is True
    assert field_rules._check_legal("lead", "text", "phone", profile, {"phone"}) is False
    assert field_rules._check_legal("lead", "text", "email", profile, opened=True) is True
    with pytest.raises(HTTPException) as mismatch:
        field_rules._check_legal("lead", "date", "email", profile, opened=True)
    assert mismatch.value.detail["code"] == "type_mismatch"

    targets = {t.column: t for t in field_rules._targets("lead", profile, {"email"})}
    assert (targets["email"].open, targets["email"].reason) == (True, None)
    assert targets["organization_name"].open is False


def test_q18_3_each_field_item_of_a_snapshot_carries_the_mark() -> None:
    """Item 7: through ``PlanRule.as_json`` and ``_rule_of``."""
    from gateway.routes.crm import mapping_plans as plans

    rule = plans.PlanRule("lead", "Why_Lost", "textarea", "email", 3, opened_empty=True)
    item = rule.as_json()
    assert item["opened_empty"] is True
    assert plans._rule_of(item) == rule
    before = {k: v for k, v in item.items() if k != "opened_empty"}
    assert plans._rule_of(before).opened_empty is False, "a snapshot from before"
    assert rule.identity == ("lead", "Why_Lost", "email"), "the mark is not identity"


def test_q18_3_a_put_with_a_new_column_clears_the_mark(monkeypatch) -> None:
    """Item 7: ``change_rule`` keeps the mark on the same column and clears
    it on a new one, so the empty test runs again."""
    from types import SimpleNamespace as Row

    from gateway.routes.crm import mapping_plans as plans

    seen: dict[str, Any] = {}

    async def commit(db: Any, *, fields: Any, **_kw: Any) -> str:
        seen["rules"] = {r.key: r for r in fields}
        return "plan"

    monkeypatch.setattr(plans, "commit", commit)
    opened = plans.PlanRule("lead", "Why_Lost", "textarea", "email", 3, opened_empty=True)
    approved = Row(rules=[opened.as_json()], version=4)
    key = ("lead", "Why_Lost")

    asyncio.run(plans.change_rule(None, approved, org=ORG, by="a", put=plans.PlanRule(
        "lead", "Why_Lost", "textarea", "description", 3, opened_empty=True)))
    assert seen["rules"][key].opened_empty is False, "a new column clears it"
    asyncio.run(plans.change_rule(None, approved, org=ORG, by="a", put=plans.PlanRule(
        "lead", "Why_Lost", "textarea", "email", 5)))
    assert seen["rules"][key].opened_empty is True, "the same column keeps it"
