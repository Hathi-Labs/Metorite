"""The Studio manifest validator refuses what ai_studio.md section 3.7 lists (STU-A2).

Each refusal has one test. It asserts a JSON path and one sentence of at most
25 words. ``studio_manifest.py`` is the fence's owner (R7).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from acb_common.studio_manifest import (
    Manifest,
    ManifestError,
    validate_manifest,
)

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "packages/acb_common/acb_common/studio_manifest.schema.json"
)

EXAMPLE: dict = {
    "studio": 1,
    "id": "field-visits",
    "name": "Field visits",
    "purpose": "Plan, run and close customer site visits.",
    "team": "service",
    "connections": ["whatsapp"],
    "objects": [
        {
            "name": "visit",
            "fields": [
                {"key": "customer", "type": "relation", "to": "crm.account", "required": True},
                {"key": "engineer", "type": "person"},
                {"key": "when", "type": "datetime"},
                {"key": "status", "type": "select", "options": ["planned", "on site", "done"]},
                {"key": "site", "type": "geo"},
                {"key": "photos", "type": "file", "many": True},
            ],
        }
    ],
    "actions": [
        {"id": "close_visit", "uses": "object:visit.update", "set": {"status": "done"}},
        {"id": "tell_customer", "uses": "integration:whatsapp.send_template"},
    ],
    "views": [
        {
            "id": "board",
            "block": "board",
            "source": {"object": "visit"},
            "group_by": "status",
            "card": {"title": "customer", "meta": ["engineer", "when"]},
        },
        {"id": "week", "block": "calendar", "source": {"object": "visit"}, "start": "when"},
        {
            "id": "check_in",
            "block": "form",
            "object": "visit",
            "fields": ["status", "photos"],
            "submit": "close_visit",
        },
    ],
    "workflows": [
        {
            "id": "after_visit",
            "trigger": {"record": "visit", "when": {"status": "done"}},
            "graph": "workflows/after_visit.json",
        }
    ],
    "skill": {
        "description": "Use for site visits: plan one, find one, check in, close one.",
        "instructions": "Ask which customer first. Show the board card. Close a visit only on the member's card.",
        "actions": ["object:visit.create", "close_visit"],
        "cards": ["board", "check_in"],
    },
    "shell": {
        "jobs": [{"verb": "Check in to a visit", "view": "check_in"}],
        "needs": [{"object": "visit", "where": {"engineer": "$me", "status": "planned"}}],
        "search": ["visit"],
    },
}

CATALOG = {"integration:whatsapp.send_template": "B"}


def doc(**patch) -> dict:
    d = copy.deepcopy(EXAMPLE)
    d.update(patch)
    return d


def one(d: dict, path: str, *, catalog=None, contains: str = "") -> ManifestError:
    """Assert exactly one error at ``path``, a sentence of at most 25 words."""
    errs = [e for e in validate_manifest(d, catalog=catalog) if e.path == path]
    assert len(errs) == 1, validate_manifest(d, catalog=catalog)
    msg = errs[0].message
    assert len(msg.split()) <= 25, msg
    assert contains in msg, msg
    return errs[0]


def test_example_is_valid_without_a_catalog():
    assert validate_manifest(EXAMPLE) == []


def test_example_is_valid_with_a_catalog():
    assert validate_manifest(EXAMPLE, catalog=CATALOG) == []


def test_not_an_object():
    one_err = validate_manifest([])
    assert [e.path for e in one_err] == ["$"]


def test_unknown_block():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["block"] = "carousel"
    one(d, "$.views[0].block", contains="carousel")


def test_code_block_is_reserved_for_l1():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["block"] = "code"
    e = one(d, "$.views[0].block", contains="L1")
    assert "v1 does not accept it" in e.message


@pytest.mark.parametrize(
    "key,slice_id",
    [("rules", "C6"), ("state_machine", "C6"), ("states", "C6"), ("formula", "C7"), ("code", "L1")],
)
def test_reserved_key(key, slice_id):
    d = doc()
    d[key] = []
    e = one(d, f"$.{key}", contains=slice_id)
    assert f"`{key}` is reserved for slice {slice_id} and v1 does not accept it" in e.message


@pytest.mark.parametrize("ftype", ["formula", "rollup", "roll-up"])
def test_formula_field_type_is_reserved_for_c7(ftype):
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][2]["type"] = ftype
    one(d, "$.objects[0].fields[2].type", contains="C7")


def test_unknown_top_level_key():
    d = doc(color_scheme="x")
    one(d, "$.color_scheme", contains="not a v1 manifest key")


def test_wrong_studio_version():
    errs = validate_manifest(doc(studio=2))
    assert [e.path for e in errs] == ["$.studio"]


def test_action_not_in_catalog():
    one(EXAMPLE, "$.actions[1].uses", catalog={"other.read": "A"}, contains="not in the catalog")


def test_unknown_action_not_checked_without_catalog():
    assert validate_manifest(EXAMPLE, catalog=None) == []


def test_class_x_action():
    e = one(
        EXAMPLE,
        "$.actions[1].uses",
        catalog={"integration:whatsapp.send_template": "X"},
        contains="class X",
    )
    assert "integration:whatsapp.send_template" in e.message


def test_object_action_is_never_looked_up():
    assert validate_manifest(EXAMPLE, catalog=CATALOG) == []


def test_object_action_names_an_undeclared_object():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][0]["uses"] = "object:ghost.update"
    one(d, "$.actions[0].uses", contains="ghost")


def test_bad_uses_grammar():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["uses"] = "Send Mail"
    one(d, "$.actions[1].uses", contains="not an action id")


@pytest.mark.parametrize(
    "uses", ["crm.create_account", "workflow:123e4567-e89b-12d3-a456-426614174000"]
)
def test_other_uses_forms_are_valid(uses):
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["uses"] = uses
    assert validate_manifest(d) == []
    assert [e.path for e in validate_manifest(d, catalog={})] == ["$.actions[1].uses"]


@pytest.mark.parametrize("value", ["#ff0000", "#fff", "rgb(1, 2, 3)", "hsl(10 20% 30%)"])
def test_colour_literal(value):
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["title"] = value
    one(d, "$.views[0].title", contains="colour")


def test_named_colour_in_a_style_key():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["card"]["style"] = "red"
    one(d, "$.views[0].card.style", contains="colour")


@pytest.mark.parametrize("key", ["color", "font", "fontSize"])
def test_colour_font_keys(key):
    d = copy.deepcopy(EXAMPLE)
    d["views"][0][key] = "x"
    one(d, f"$.views[0].{key}", contains="style")


def test_pixel_size():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["title"] = "Board 320px wide"
    one(d, "$.views[0].title", contains="pixel")


@pytest.mark.parametrize(
    "value",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "xoxb-1234567890-abcdef",
        "eyJhbGciOiJI.eyJzdWIiOiIx.abcdefghij",
        "-----BEGIN RSA PRIVATE KEY-----",
        "Bearer abcdefghijklmnopqrstuv",
        "password: hunter22",
        "api_key=abcdef123",
    ],
)
def test_credential_shaped_string(value):
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["instructions"] = value
    one(d, "$.skill.instructions", contains="credential")


def test_view_names_a_field_the_object_lacks():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["group_by"] = "stage"
    one(d, "$.views[0].group_by", contains="stage")


def test_view_names_an_undeclared_object():
    d = copy.deepcopy(EXAMPLE)
    d["views"][1]["source"] = {"object": "ghost"}
    one(d, "$.views[1].source.object", contains="ghost")


def test_form_submit_names_an_undeclared_action():
    d = copy.deepcopy(EXAMPLE)
    d["views"][2]["submit"] = "nope"
    one(d, "$.views[2].submit", contains="nope")


def test_skill_action_undeclared():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["actions"].append("nope")
    one(d, "$.skill.actions[2]", contains="nope")


def test_skill_card_undeclared():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["cards"].append("nope")
    one(d, "$.skill.cards[2]", contains="nope")


def test_relation_to_is_bad():
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][0]["to"] = "Account"
    one(d, "$.objects[0].fields[0].to", contains="Account")


def test_relation_to_a_declared_object_is_valid():
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][0]["to"] = "visit"
    assert validate_manifest(d) == []


def test_relation_without_to():
    d = copy.deepcopy(EXAMPLE)
    del d["objects"][0]["fields"][0]["to"]
    one(d, "$.objects[0].fields[0].to", contains="needs")


def test_duplicate_action_id():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["id"] = "close_visit"
    one(d, "$.actions[1]", contains="used twice")


def test_duplicate_view_id():
    d = copy.deepcopy(EXAMPLE)
    d["views"][1]["id"] = "board"
    one(d, "$.views[1]", contains="used twice")


def test_duplicate_object_and_field_key():
    d = copy.deepcopy(EXAMPLE)
    d["objects"].append(copy.deepcopy(d["objects"][0]))
    d["objects"][0]["fields"].append({"key": "when", "type": "date"})
    paths = {e.path for e in validate_manifest(d)}
    assert {"$.objects[1]", "$.objects[0].fields[6]"} <= paths


def test_select_with_no_options():
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][3]["options"] = []
    one(d, "$.objects[0].fields[3].options", contains="at least one option")


def test_missing_required_key():
    d = copy.deepcopy(EXAMPLE)
    del d["purpose"]
    one(d, "$.purpose", contains="required")


def test_data_subtree_may_hold_a_reserved_word():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][0]["set"] = {"code": "A1"}
    assert validate_manifest(d) == []


def test_every_error_is_short():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["block"] = "carousel"
    for e in validate_manifest(d):
        assert len(e.message.split()) <= 25


def test_committed_schema_equals_the_live_export():
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(Manifest.model_json_schema())), (
        "studio_manifest.schema.json is stale. Regenerate it from Manifest.model_json_schema()."
    )
