"""The Studio manifest validator refuses what ai_studio.md section 3.7 lists (STU-A2).

Each refusal has one test. It asserts a JSON path and one sentence of at most
25 words. ``studio_manifest.py`` is the fence's owner (R7).
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

import pytest
from acb_common.studio_manifest import (
    BLOCKS,
    FIELD_TYPES,
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


_TOKENS = set(FIELD_TYPES) | set(BLOCKS)


def words(msg: str) -> int:
    """Count the words of a message. The echo tail and the allowed-token list do not count."""
    head = msg.partition(" Got `")[0]
    return len([w for w in head.split() if w.strip(",.") not in _TOKENS])


def one(d: dict, path: str, *, catalog=None, contains: str = "") -> ManifestError:
    """Assert exactly one error at ``path``, a sentence of at most 25 words."""
    errs = [e for e in validate_manifest(d, catalog=catalog) if e.path == path]
    assert len(errs) == 1, validate_manifest(d, catalog=catalog)
    msg = errs[0].message
    assert words(msg) <= 25, msg
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


@pytest.mark.parametrize("ftype", ["formula", "rollup"])
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
    one(d, "$.actions[1].uses", contains="Not an action id")


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
    d["actions"][1]["set"] = {"code": "A1"}
    assert validate_manifest(d) == []


def test_every_error_is_short():
    d = copy.deepcopy(EXAMPLE)
    d["views"][0]["block"] = "carousel"
    d["objects"][0]["fields"][2]["type"] = "user"
    errs = validate_manifest(d)
    assert len(errs) >= 2
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["uses"] = "Send the customer a WhatsApp message with the visit summary " * 3
    d["actions"][0]["set"] = {"stage": "x" * 90}
    errs += validate_manifest(d)
    assert len(errs) >= 4
    for e in errs:
        assert words(e.message) <= 25, e.message


def test_committed_schema_equals_the_live_export():
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(Manifest.model_json_schema())), (
        "studio_manifest.schema.json is stale. Regenerate it from Manifest.model_json_schema()."
    )


# --- fix round 1: hostile input, credentials, caps, strictness -----------------


def _set(d: dict, path: list, value) -> dict:
    node = d
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    return d


def _jpath(where: list, key: str = "") -> str:
    out = "$"
    for p in where:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out + (f".{key}" if key else "")


MALFORMED = [
    ["table", "chart"], ["person"], {"a": 1}, {}, [], 7, 1.5, True, False, None, "", "x" * 50,
    [[["deep"]]], {"a": {"b": [1, {"c": None}]}},
]  # fmt: skip

SLOTS = [
    ["studio"], ["id"], ["name"], ["team"], ["connections"], ["objects"], ["actions"], ["views"],
    ["workflows"], ["skill"], ["shell"], ["objects", 0, "name"], ["objects", 0, "fields"],
    ["objects", 0, "fields", 0, "type"], ["objects", 0, "fields", 0, "to"],
    ["objects", 0, "fields", 3, "options"], ["objects", 0, "fields", 0, "required"],
    ["objects", 0, "fields", 5, "many"], ["actions", 0, "uses"], ["actions", 0, "set"],
    ["actions", 0, "id"], ["views", 0, "block"], ["views", 0, "source"], ["views", 0, "card"],
    ["views", 0, "group_by"], ["views", 2, "fields"], ["views", 2, "submit"],
    ["workflows", 0, "trigger"], ["workflows", 0, "graph"], ["skill", "actions"],
    ["skill", "cards"], ["skill", "examples"], ["skill", "description"], ["shell", "jobs"],
    ["shell", "needs"], ["shell", "search"],
]  # fmt: skip


@pytest.mark.parametrize("slot", SLOTS, ids=lambda s: ".".join(map(str, s)))
def test_validate_never_raises_on_a_malformed_slot(slot):
    for bad in MALFORMED:
        d = _set(copy.deepcopy(EXAMPLE), slot, copy.deepcopy(bad))
        errs = validate_manifest(d, catalog=CATALOG)
        assert isinstance(errs, list)
        for e in errs:
            assert isinstance(e, ManifestError) and e.message


def test_a_list_for_block_and_type_is_an_error_at_that_path():
    d = _set(copy.deepcopy(EXAMPLE), ["views", 0, "block"], ["table", "chart"])
    one(d, "$.views[0].block", contains="Block is unknown")
    d = _set(copy.deepcopy(EXAMPLE), ["objects", 0, "fields", 1, "type"], ["person"])
    one(d, "$.objects[0].fields[1].type", contains="Field type is unknown")


def test_non_json_values_do_not_raise():
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 0, "set"], {"a": {1, 2}})
    assert validate_manifest(d)
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 0, "set"], {"a": b"x"})
    assert validate_manifest(d)


@pytest.mark.parametrize("depth", [25, 2000, 5000])
def test_deep_nesting_is_refused_not_raised(depth):
    inner: object = "x"
    for _ in range(depth):
        inner = {"a": inner}
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 0, "set"], inner)
    errs = validate_manifest(d)
    assert errs and "deeper than 20" in errs[0].message
    assert errs[0].path.startswith("$.actions[0].set")


def test_deep_nesting_in_a_list_is_refused():
    inner: object = "x"
    for _ in range(3000):
        inner = [inner]
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 0, "set"], {"a": inner})
    assert "deeper than 20" in validate_manifest(d)[0].message


def test_string_list_and_document_caps():
    d = _set(copy.deepcopy(EXAMPLE), ["skill", "instructions"], "a " * 10_001)
    e = validate_manifest(d)
    assert [x.path for x in e] == ["$.skill.instructions"] and "20000" in e[0].message
    d = _set(copy.deepcopy(EXAMPLE), ["shell", "search"], ["visit"] * 501)
    e = validate_manifest(d)
    assert [x.path for x in e] == ["$.shell.search"] and "500" in e[0].message
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["knowledge"] = ["k" * 19_000] * 15
    e = validate_manifest(d)
    assert [x.path for x in e] == ["$"] and "256 KiB" in e[0].message


@pytest.mark.parametrize(
    "text",
    ["eyJ-" * 5_000, "eyJ" + "a" * 250 + "." + "eyJ" * 6_000, ("eyJabcdefgh." * 1500)[:19_000]],
)
def test_adversarial_documents_validate_fast(text):
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["knowledge"] = [text] * 13  # about 256 KiB in all
    start = time.perf_counter()
    errs = validate_manifest(d)
    elapsed = time.perf_counter() - start
    assert isinstance(errs, list)
    assert elapsed < 0.5, elapsed


def test_one_adversarial_string_just_under_the_cap_is_fast():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["instructions"] = "eyJ-" * 4_999
    start = time.perf_counter()
    validate_manifest(d)
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize(
    "where,key",
    [
        (["actions", 1, "set"], "api_key"),
        (["actions", 1, "set"], "apiKey"),
        (["actions", 1, "set"], "api-key"),
        (["views", 0, "source", "args"], "password"),
        (["views", 0, "source", "args"], "Client_Secret"),
        (["workflows", 0, "trigger", "when"], "accessToken"),
        (["actions", 1, "set"], "passwd"),
        (["actions", 1, "set"], "my_credentials"),
        (["actions", 1, "set"], "private_key"),
    ],
)
def test_credential_in_a_secret_named_key(where, key):
    d = _set(copy.deepcopy(EXAMPLE), where, {key: "9f8e7d6c5b4a3210"})
    errs = validate_manifest(d)
    assert [e.path for e in errs] == [_jpath(where, key)]
    assert "credential" in errs[0].message


def test_secret_named_key_with_an_empty_value_is_allowed():
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 1, "set"], {"token": ""})
    assert validate_manifest(d) == []


def test_secret_named_key_covers_a_nested_value():
    d = _set(copy.deepcopy(EXAMPLE), ["actions", 1, "set"], {"secrets": {"a": ["v"]}})
    assert [e.path for e in validate_manifest(d)] == ["$.actions[1].set.secrets.a[0]"]


@pytest.mark.parametrize(
    "value",
    [
        "cc_live_ab12cd_9f8e7d6c5b4a32109f8e",
        "cc_depl_ab12cd_9f8e7d6c5b4a32109f8e",
        "AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "sk_live_51H8abcdefghijklmnop",
        "rk_test_51H8abcdefghijklmnop",
    ],
)
def test_more_credential_shapes(value):
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["instructions"] = "Key " + value
    one(d, "$.skill.instructions", contains="credential")


def test_a_password_in_prose_is_not_a_credential():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["instructions"] = "If they ask for a password, point them to Settings."
    assert validate_manifest(d) == []


def test_secret_shapes_are_bounded():
    from acb_common.secret_shapes import SECRET_SHAPES

    assert SECRET_SHAPES
    text = "eyJ-" * 5_000
    for _, rx in SECRET_SHAPES:
        start = time.perf_counter()
        rx.search(text)
        assert time.perf_counter() - start < 0.2, rx.pattern


def test_view_object_and_source_object_must_agree():
    d = copy.deepcopy(EXAMPLE)
    d["views"][2]["source"] = {"object": "other"}
    errs = validate_manifest(d)
    assert "$.views[2].source.object" in {e.path for e in errs}
    assert any("differ" in e.message for e in errs)


def test_source_object_is_checked_when_object_is_set():
    d = copy.deepcopy(EXAMPLE)
    d["views"][2]["source"] = {"object": "ghost"}
    paths = {e.path for e in validate_manifest(d)}
    assert "$.views[2].source.object" in paths


@pytest.mark.parametrize(
    "path,value",
    [
        (["actions", 0, "uses"], "object:visit.update\n"),
        (["actions", 1, "uses"], "crm.create_account\n"),
        (["objects", 0, "fields", 0, "to"], "crm.account\n"),
        (["objects", 0, "name"], "visit\n"),
        (["id"], "field-visits\n"),
    ],
)
def test_a_trailing_newline_is_refused(path, value):
    d = _set(copy.deepcopy(EXAMPLE), path, value)
    assert validate_manifest(d)


def test_a_newline_in_a_skill_action_is_refused():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["actions"].append("object:visit.delete\n")
    assert [e.path for e in validate_manifest(d)] == ["$.skill.actions[2]"]


@pytest.mark.parametrize(
    "path,value",
    [
        (["objects", 0, "fields", 0, "required"], "yes"),
        (["objects", 0, "fields", 5, "many"], "false"),
        (["studio"], True),
        (["studio"], 1.0),
        (["studio"], "1"),
        (["objects", 0, "fields", 0, "required"], 1),
    ],
)
def test_strict_types_do_not_coerce(path, value):
    d = _set(copy.deepcopy(EXAMPLE), path, value)
    assert validate_manifest(d)


def test_bad_type_message_lists_the_allowed_values():
    d = _set(copy.deepcopy(EXAMPLE), ["objects", 0, "fields", 1, "type"], "user")
    msg = one(d, "$.objects[0].fields[1].type", contains="Got `user`").message
    assert all(t in msg for t in FIELD_TYPES)
    assert msg.endswith("Got `user`.")


def test_long_uses_keeps_the_grammar_hint_and_a_short_echo():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["uses"] = (
        "Send the customer a WhatsApp message with the visit summary and photos"
    )
    msg = one(d, "$.actions[1].uses", contains="integration:<action>").message
    m = re.search(r"Got `(.*)`\.$", msg)
    assert m and len(m.group(1)) <= 40


def test_block_message_lists_the_blocks():
    d = _set(copy.deepcopy(EXAMPLE), ["views", 0, "block"], "carousel")
    msg = one(d, "$.views[0].block", contains="Got `carousel`").message
    assert all(b in msg for b in BLOCKS)


def test_camel_case_tokens_are_the_spelling():
    assert "longText" in FIELD_TYPES and "multiSelect" in FIELD_TYPES
    assert "mindMap" in BLOCKS and "processFlow" in BLOCKS
    assert not [t for t in (*FIELD_TYPES, *BLOCKS) if "-" in t]
    d = _set(copy.deepcopy(EXAMPLE), ["objects", 0, "fields", 1, "type"], "long-text")
    assert validate_manifest(d)


def test_skill_example_action_is_checked():
    d = copy.deepcopy(EXAMPLE)
    d["skill"]["examples"] = [{"prompt": "x", "action": "nope"}]
    one(d, "$.skill.examples[0].action", contains="nope")
    d["skill"]["examples"] = [{"prompt": "x", "action": "close_visit"}]
    assert validate_manifest(d) == []
    d["skill"]["examples"] = [{"prompt": "x", "action": "object:visit.create"}]
    assert validate_manifest(d) == []


def test_skill_example_action_of_class_x_is_refused_with_a_catalog():
    d = copy.deepcopy(EXAMPLE)
    d["actions"].append({"id": "purge", "uses": "crm.purge_all"})
    d["skill"]["examples"] = [{"prompt": "x", "action": "purge"}]
    cat = {**CATALOG, "crm.purge_all": "B", "purge": "X"}
    errs = [e for e in validate_manifest(d, catalog=cat) if e.path.startswith("$.skill")]
    assert [e.path for e in errs] == ["$.skill.examples[0].action"]


def test_set_key_must_be_a_field_of_the_object():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][0]["set"] = {"stage": "done"}
    one(d, "$.actions[0].set.stage", contains="stage")
    d["actions"][0]["set"] = {"status": "done", "engineer": "$me"}
    assert validate_manifest(d) == []


def test_set_key_is_not_checked_on_a_non_object_action():
    d = copy.deepcopy(EXAMPLE)
    d["actions"][1]["set"] = {"anything": "x"}
    assert validate_manifest(d) == []


# --- C1: the field rules come from the one field-type module ------------------


def test_the_field_types_are_the_one_list_of_studio_fields():
    """C1: `studio_fields.FIELD_TYPES` is the one list. A2 imports it, and the
    schema enum and the `Literal` of `FieldDef` are that list."""
    from acb_common import studio_fields

    assert FIELD_TYPES is studio_fields.FIELD_TYPES
    schema = Manifest.model_json_schema()
    assert schema["$defs"]["FieldDef"]["properties"]["type"]["enum"] == list(FIELD_TYPES)
    assert schema["$defs"]["FieldDef"]["properties"]["hot"] == {
        "default": False, "title": "Hot", "type": "boolean"}


def test_a_hot_field_of_a_hot_type_validates():
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][3]["hot"] = True  # status, a select
    d["objects"][0]["fields"][2]["hot"] = True  # when, a datetime
    assert validate_manifest(d) == []


@pytest.mark.parametrize(
    ("field", "patch", "path", "contains"),
    [
        (1, {"type": "text", "many": True, "hot": True}, ".hot", "`many` cannot be hot"),
        (5, {"hot": True}, ".hot", "cannot be hot"),  # photos: a file
        (4, {"hot": True}, ".hot", "cannot be hot"),  # site: a geo
        (3, {"options": [f"o{i}" for i in range(101)]}, ".options", "at most 100"),
        (3, {"options": ["planned", "planned"]}, ".options[1]", "twice"),
        (1, {"options": ["a"]}, ".options", "Only a select"),
        (1, {"to": "crm.account"}, ".to", "Only a relation"),
        (3, {"type": "multiSelect", "many": True}, ".many", "no `many`"),
    ],
)
def test_the_shared_spec_rules_refuse_with_a_path(field, patch, path, contains):
    """C1: `_check_objects` calls `studio_fields.validate_field_spec`, the check
    that the object store runs too, so a manifest that cannot install fails here."""
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"][field].update(patch)
    one(d, f"$.objects[0].fields[{field}]{path}", contains=contains)


def test_the_object_caps_of_c1_hold():
    from acb_common import studio_fields

    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["fields"] = [
        {"key": f"n{i}", "type": "number", "hot": i < studio_fields.MAX_HOT_PER_OBJECT + 1}
        for i in range(studio_fields.MAX_FIELDS_PER_OBJECT + 1)
    ]
    for view in d["views"]:
        view.update({"group_by": None, "card": None, "start": None, "fields": []})
    d["actions"][0]["set"] = {}
    paths = [e.path for e in validate_manifest(d)]
    assert paths.count("$.objects[0].fields") == 2, paths
    d = copy.deepcopy(EXAMPLE)
    d["objects"] = [{"name": f"o{i}"} for i in range(studio_fields.MAX_OBJECTS_PER_PACKAGE + 1)]
    d.update(actions=[], views=[], workflows=[], skill=None, shell=None)
    one(d, "$.objects", contains=f"at most {studio_fields.MAX_OBJECTS_PER_PACKAGE} objects")


def test_a_package_with_more_hot_fields_than_an_org_holds_is_refused():
    """C1 fix round 1: an org holds 24 hot fields in all, so a package with 28
    installs on no org. Each object holds 7, below the cap of an object."""
    from acb_common import studio_fields

    d = copy.deepcopy(EXAMPLE)
    d.update(actions=[], views=[], workflows=[], skill=None, shell=None)
    d["objects"] = [
        {"name": f"o{i}", "fields": [{"key": f"n{j}", "type": "number", "hot": True}
                                     for j in range(7)]}
        for i in range(4)
    ]
    one(d, "$.objects", contains=f"at most {studio_fields.MAX_HOT_PER_ORG} hot fields")
    d["objects"] = d["objects"][:3]  # 21 hot fields
    assert validate_manifest(d) == []


def test_an_object_label_holds_at_most_the_store_cap():
    from acb_common import studio_fields

    cap = studio_fields.MAX_LABEL_CHARS
    d = copy.deepcopy(EXAMPLE)
    d["objects"][0]["label"] = "v" * cap
    assert validate_manifest(d) == []
    d["objects"][0]["label"] = "v" * (cap + 1)
    one(d, "$.objects[0].label", contains=str(cap))
    schema = Manifest.model_json_schema()
    label = schema["$defs"]["ObjectDef"]["properties"]["label"]
    assert {"maxLength": cap, "type": "string"} in label["anyOf"]
