"""The field studio rules that need no database (WS-53 CRM-C4a).

Spec: ``project-docs/specs/crm_platform.md`` §20.7. The key rule (item 2), the
system fields (item 3), the option rule (item 6), the value checks (items 1,
7 and 8), the type matrix (item 9) and the type words of the mirror (item 1).
``test_crm_field_studio_r8.py`` proves the routes against a real Postgres.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from fastapi import HTTPException
from gateway.crm_sources.zoho import mapping
from gateway.routes.crm import core, field_studio
from gateway.routes.crm.field_studio import (
    FIELD_TYPES,
    ROUTE_WORDS,
    TYPE_CHANGES,
    CustomField,
    OptionIn,
    apply_options,
    check_value,
    key_problem,
    make_key,
    option_value,
    validate_key,
)

# ── The key rule (item 2) ───────────────────────────────────────────────────


@pytest.mark.parametrize("word", sorted(ROUTE_WORDS))
def test_the_key_validator_refuses_the_route_words(word: str) -> None:
    assert {"order", "layout", "restore"} == ROUTE_WORDS
    with pytest.raises(HTTPException) as exc:
        validate_key("deals", word)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "field_key"


@pytest.mark.parametrize("slug", sorted(core.ENTITIES))
def test_a_key_never_equals_a_fixed_column(slug: str) -> None:
    for column in core.ENTITIES[slug].model.model_fields:
        assert key_problem(slug, column) is not None, column
    assert key_problem(slug, "custom") is not None
    assert key_problem(slug, "organization_id") is not None


def test_the_key_comes_from_the_label() -> None:
    assert make_key("deals", "Contract Value (INR)", set()) == "contract_value_inr"
    assert make_key("deals", "  Région  ", set()) == "r_gion"
    assert make_key("deals", "2nd owner", set()) == "f_2nd_owner"
    assert make_key("deals", "!!!", set()) == "field"
    long = make_key("deals", "x" * 90, set())
    assert len(long) == 40 and long == "x" * 40


def test_a_clash_takes_a_number() -> None:
    assert make_key("deals", "Tier", {"tier"}) == "tier_2"
    assert make_key("deals", "Tier", {"tier", "tier_2"}) == "tier_3"
    # A clash in another case is a clash: a Zoho key keeps its own case.
    assert make_key("deals", "Tier", {"Tier"}) == "tier_2"
    long = make_key("deals", "y" * 60, {"y" * 40})
    assert long == "y" * 38 + "_2"


def test_a_label_that_names_a_route_word_or_a_column_takes_a_number() -> None:
    assert make_key("deals", "Order", set()) == "order_2"
    assert make_key("deals", "Layout", set()) == "layout_2"
    assert make_key("deals", "Restore", set()) == "restore_2"
    assert make_key("deals", "Name", set()) == "name_2"
    assert make_key("leads", "Email", set()) == "email_2"
    for slug in core.ENTITIES:
        assert key_problem(slug, make_key(slug, "Amount", set())) is None


# ── System fields (item 3) ──────────────────────────────────────────────────


@pytest.mark.parametrize("slug", sorted(core.ENTITIES))
def test_each_system_field_is_a_writable_column_of_its_entity(slug: str) -> None:
    entity = core.ENTITIES[slug]
    keys = [f.key for f in core.SYSTEM_FIELDS[slug]]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(entity.payload.model_fields) - {"custom", "source"}
    assert set(entity.required) <= set(keys), "a required column is a system field"
    for field in core.SYSTEM_FIELDS[slug]:
        assert field.type in FIELD_TYPES, field
        assert (field.type == "lookup") == (field.lookup_target is not None), field
        assert field.lookup_target in (None, *core.ENTITIES), field


def test_the_four_entities_have_system_fields() -> None:
    assert set(core.SYSTEM_FIELDS) == set(core.ENTITIES)
    assert core.system_field("deals", "name").label == "Deal name"
    assert core.system_field("deals", "custom") is None


# ── Options (item 6) ────────────────────────────────────────────────────────


def test_a_new_option_takes_a_slug_and_a_clash_takes_a_number() -> None:
    made = apply_options([], [OptionIn(label="Gold Tier"), OptionIn(label="gold tier!")])
    assert made == [
        {"label": "Gold Tier", "value": "gold-tier"},
        {"label": "gold tier!", "value": "gold-tier-2"},
    ]
    assert option_value("Über", set()) == "ber"
    assert option_value("???", set()) == "option"


def test_a_rename_keeps_the_value_and_a_retire_marks_it() -> None:
    current = [{"label": "Gold", "value": "gold"}, {"label": "Silver", "value": "silver"}]
    after = apply_options(current, [
        OptionIn(label="Silver", value="silver", retired=True),
        OptionIn(label="Gold+", value="gold"),
        OptionIn(label="Gold"),
    ])
    assert after == [
        {"label": "Silver", "value": "silver", "retired": True},
        {"label": "Gold+", "value": "gold"},
        {"label": "Gold", "value": "gold-2"},
    ]


@pytest.mark.parametrize("given, code", [
    ([OptionIn(label="Gold", value="gold")], "option_removed"),
    ([OptionIn(label="X", value="nope"), OptionIn(label="S", value="silver"),
      OptionIn(label="G", value="gold")], "option_unknown"),
    ([OptionIn(label="G", value="gold"), OptionIn(label="G", value="gold"),
      OptionIn(label="S", value="silver")], "option_twice"),
])
def test_an_option_leaves_only_by_a_retire(given: list[OptionIn], code: str) -> None:
    current = [{"label": "Gold", "value": "gold"}, {"label": "Silver", "value": "silver"}]
    with pytest.raises(HTTPException) as exc:
        apply_options(current, given)
    assert exc.value.detail["code"] == code


# ── The type matrix (item 9) ────────────────────────────────────────────────


def test_the_type_matrix_is_the_spec_table() -> None:
    assert {
        "text": {"textarea"},
        "number": {"decimal", "currency", "percent"},
        "decimal": {"percent", "currency"},
        "percent": {"decimal", "currency"},
        "currency": {"decimal", "percent"},
        "select": {"multiselect"},
        "email": {"text"},
        "phone": {"text"},
        # Review round 1: a URL can be longer than a text field holds.
        "url": {"textarea"},
    } == TYPE_CHANGES
    assert set(TYPE_CHANGES) <= set(FIELD_TYPES)
    assert all(t <= set(FIELD_TYPES) for t in TYPE_CHANGES.values())
    for frozen in ("textarea", "date", "datetime", "boolean", "multiselect", "lookup"):
        assert frozen not in TYPE_CHANGES


# ── Values (items 1, 7 and 8) ───────────────────────────────────────────────


def _field(kind: str, **over: Any) -> CustomField:
    base: dict[str, Any] = {
        "key": "f", "label": "Contract value", "type": kind, "options": (),
        "required": False, "default_value": None, "lookup_target": None,
        "retired": False,
    }
    return CustomField(**{**base, **over})


def _check(field: CustomField, value: Any, kept: Any = None) -> Any:
    return asyncio.run(check_value(None, field, value, kept))


@pytest.mark.parametrize("kind, good, stored", [
    ("text", "Hello", "Hello"),
    ("textarea", "a\nb", "a\nb"),
    ("number", 12, 12),
    ("number", 12.0, 12),
    ("decimal", 12.5, 12.5),
    ("percent", 7, 7),
    ("currency", 1000.25, 1000.25),
    ("date", "2026-10-13", "2026-10-13"),
    ("datetime", "2026-10-13T10:00:00+05:30", "2026-10-13T10:00:00+05:30"),
    ("datetime", "2026-10-13T10:00Z", "2026-10-13T10:00:00+00:00"),
    ("number", 10**15 - 1, 10**15 - 1),
    ("decimal", -999_999_999_999_999.0, -999_999_999_999_999.0),
    ("boolean", False, False),
    ("email", " a@b.co ", "a@b.co"),
    ("phone", "+91 98450 12345", "+91 98450 12345"),
    ("url", "https://fracktal.in", "https://fracktal.in"),
])
def test_a_good_value_of_each_type(kind: str, good: Any, stored: Any) -> None:
    assert _check(_field(kind), good) == stored


@pytest.mark.parametrize("kind, bad", [
    ("text", 5), ("text", "x" * 256), ("number", 1.5), ("number", True),
    ("number", "12"), ("decimal", "1.5"), ("decimal", float("nan")),
    ("date", "13/10/2026"), ("date", "2026-10-13T10:00"),
    ("datetime", "2026-10-13T10:00:00"), ("boolean", "true"),
    ("email", "not-mail"), ("phone", "12"), ("phone", "call me"),
    ("url", "javascript:alert(1)"), ("url", "ftp://x"),
    # Review round 1: a number of 10^15 or more, and one too big for a float.
    ("number", 10**15), ("number", -(10**15)), ("number", 10**400),
    ("decimal", 1e15), ("currency", 10**400), ("percent", -(10**400)),
    # Review round 1: only the plain ISO forms.
    ("date", "2026-W42-1"), ("date", "20261013"), ("date", "2026-10-13\n"),
    ("date", "2026-287"), ("datetime", "2026-10-13 10:00:00+05:30"),
    ("datetime", "2026-W42-1T10:00:00+05:30"), ("datetime", "20261013T100000Z"),
])
def test_a_bad_value_gives_422_that_names_the_label(kind: str, bad: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        _check(_field(kind), bad)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "field_value"
    assert exc.value.detail["message"].startswith("Contract value:")


def test_a_picklist_takes_an_active_option_and_keeps_a_retired_one() -> None:
    options = ({"label": "Gold", "value": "gold"},
               {"label": "Old", "value": "old", "retired": True})
    select = _field("select", options=options)
    assert _check(select, "gold") == "gold"
    with pytest.raises(HTTPException):
        _check(select, "old")
    assert _check(select, "old", kept="old") == "old", "a record keeps a retired value"
    multi = _field("multiselect", options=options)
    assert _check(multi, ["gold", "gold"]) == ["gold"]
    assert _check(multi, ["old", "gold"], kept=["old"]) == ["old", "gold"]
    with pytest.raises(HTTPException):
        _check(multi, ["old"])
    with pytest.raises(HTTPException):
        _check(multi, "gold")


def test_a_lookup_has_no_default_and_needs_a_native_target() -> None:
    lookup = _field("lookup", lookup_target="organizations")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(check_value(None, lookup, {"id": "x"}, allow_lookup=False))
    assert "no default" in exc.value.detail["message"]
    for target in (None, "module:Products"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(check_value(None, _field("lookup", lookup_target=target), {"id": "x"}))
        assert exc.value.status_code == 422


@pytest.mark.parametrize("target", ["module:Products", "projects", None])
def test_a_native_lookup_target_is_one_of_the_four_entities(target: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        field_studio._lookup_target("lookup", target)
    assert exc.value.status_code == 422
    assert field_studio._lookup_target("lookup", "deals") == "deals"
    assert field_studio._lookup_target("text", None) is None


def test_empty_is_null_blank_or_an_empty_list() -> None:
    assert all(field_studio.is_empty(v) for v in (None, "", "  ", [], {}))
    assert not any(field_studio.is_empty(v) for v in (0, False, "x", ["a"]))


# ── The type words of the mirror (item 1) ───────────────────────────────────


def test_a_zoho_double_and_percent_take_the_new_words() -> None:
    assert mapping._FIELD_TYPES["double"] == "decimal"
    assert mapping._FIELD_TYPES["percent"] == "percent"
    assert mapping._FIELD_TYPES["integer"] == "number"


def test_the_new_words_fill_the_columns_of_a_number() -> None:
    """Without this, an approved CRM-Q7a rule goes ``type_mismatch``."""
    number = mapping._RULE_COLUMN_TYPES["number"]
    assert mapping._RULE_COLUMN_TYPES["decimal"] == number
    assert mapping._RULE_COLUMN_TYPES["percent"] == number
    for column_type in number:
        assert mapping.types_agree("decimal", column_type)
        assert mapping.types_agree("percent", column_type)
    # Every word that the sync writes is a word of the studio.
    assert set(mapping._FIELD_TYPES.values()) <= set(FIELD_TYPES)


# ── Review round 1 ──────────────────────────────────────────────────────────


def test_a_url_fits_the_textarea_it_may_become() -> None:
    """A URL holds up to 2048 characters, so the matrix sends it to a
    textarea, never to a text field of 255."""
    url = "https://fracktal.in/" + "a" * (field_studio.URL_MAX - 20)
    stored = _check(_field("url"), url)
    assert _check(_field("textarea"), stored) == url
    with pytest.raises(HTTPException):
        _check(_field("text"), stored)
    assert "text" not in TYPE_CHANGES["url"]


def test_a_huge_integer_is_a_422_and_never_an_overflow() -> None:
    with pytest.raises(HTTPException) as exc:
        _check(_field("number"), 10**400)
    assert exc.value.status_code == 422


def test_the_export_writes_the_option_label() -> None:
    from gateway.routes.crm.export import custom_cell

    labels = {"gold-tier": "Gold Tier", "silver": "Silver"}
    assert custom_cell("gold-tier", labels) == "Gold Tier"
    assert custom_cell(["silver", "gold-tier"], labels) == "Silver; Gold Tier"
    assert custom_cell("unknown", labels) == "unknown"
    assert custom_cell({"id": "x", "name": "Acme"}) == "Acme"
    assert custom_cell(5, labels) == 5


# ── The RLS of migration 260 (review round 1, an R7 gap) ────────────────────
#
# The R8 fixture applies the generated policies after the ladder, so the
# database alone cannot see a migration that lost its own RLS block. This
# fence reads the file.

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_TENANT = r"organization_id\s*=\s*current_setting\('app\.tenant_id',\s*true\)::uuid"


def _studio_migration() -> str:
    """The migration that creates the table, found by content (R1)."""
    found = [
        p for p in _MIGRATIONS.glob("*.sql")
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_field_sections" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0].read_text(encoding="utf-8")


def rls_problems(sql: str) -> list[str]:
    """What the migration lacks of the tenant shape of crm_field_sections."""
    body = re.sub(r"--[^\n]*", "", sql)
    wanted = {
        "ENABLE": r"ALTER\s+TABLE\s+crm_field_sections\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
        "FORCE": r"ALTER\s+TABLE\s+crm_field_sections\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
        "POLICY": (
            r"CREATE\s+POLICY\s+\w+\s+ON\s+crm_field_sections\s+"
            rf"USING\s*\(\s*{_TENANT}\s*\)\s*WITH\s+CHECK\s*\(\s*{_TENANT}\s*\)"
        ),
    }
    return [name for name, pattern in wanted.items() if not re.search(pattern, body, re.I)]


def test_migration_260_forces_rls_with_a_tenant_policy() -> None:
    assert rls_problems(_studio_migration()) == []


def test_the_rls_fence_sees_a_missing_block() -> None:
    sql = _studio_migration()
    start = sql.index("DO $rls$")
    end = sql.index("$rls$;", start) + len("$rls$;")
    assert rls_problems(sql[:start] + sql[end:]) == ["ENABLE", "FORCE", "POLICY"]
    no_check = re.sub(r"WITH CHECK \([^\n]*\);", ";", sql)
    assert rls_problems(no_check) == ["POLICY"]
    no_force = sql.replace("FORCE  ROW LEVEL SECURITY", "NO FORCE ROW LEVEL SECURITY")
    assert rls_problems(no_force) == ["FORCE"]
