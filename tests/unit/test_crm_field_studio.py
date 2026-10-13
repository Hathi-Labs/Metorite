"""The field studio rules that need no database (WS-53 CRM-C4a).

Spec: ``project-docs/specs/crm_platform.md`` §20.7. The key rule (item 2), the
system fields (item 3), the option rule (item 6), the value checks (items 1,
7 and 8), the type matrix (item 9) and the type words of the mirror (item 1).
``test_crm_field_studio_r8.py`` proves the routes against a real Postgres.
"""

from __future__ import annotations

import asyncio
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
        "url": {"text"},
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
