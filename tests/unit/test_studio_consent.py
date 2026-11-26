"""WS-STU J6: the install consent from the manifest (task 1426).

Code: ``packages/acb_common/acb_common/studio_consent.py``. No database. The
screen draws from :func:`consent`, so the first tests walk the real op list and
the source fence says the module reads only the manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args, get_type_hints

from acb_common.studio_consent import (
    FIELD_PARTS,
    RUNTIME_ORDER,
    TOP_LEVEL_PARTS,
    USES_SOURCES,
    consent,
    widens,
)
from acb_common.studio_manifest import (
    OBJECT_OP_CLASSES,
    OBJECT_OPS,
    RECORD_OPS,
    Manifest,
    UsesRef,
    parse_uses,
    validate_manifest,
)
from pydantic import BaseModel

_SRC = (
    Path(__file__).resolve().parents[2] / "packages/acb_common/acb_common/studio_consent.py"
).read_text(encoding="utf-8")
WF = "11111111-1111-1111-1111-111111111111"


def _m(**over: object) -> dict:
    base: dict = {
        "studio": 1,
        "id": "shop",
        "name": "Shop",
        "purpose": "Orders.",
        "connections": ["zoho", "mcp:tally"],
        "roles": ["clerk"],
        "objects": [
            {
                "name": "order",
                "fields": [{"key": "name", "type": "text"}],
                "access": [{"role": "clerk", "read": "all"}],
            },
            {"name": "note", "fields": [{"key": "t", "type": "text"}]},
        ],
        "actions": [
            {"id": "order_list", "uses": "object:order.list"},
            {"id": "order_create", "uses": "object:order.create"},
            {"id": "order_delete", "uses": "object:order.delete"},
            {"id": "note_tree", "uses": "object:note.tree"},
            {"id": "mail", "uses": "integration:zoho.deal"},
            {"id": "wf", "uses": f"workflow:{WF}"},
            {"id": "lg", "uses": "logic:stock_check"},
            {"id": "door", "uses": "crm.create_deal"},
        ],
        "views": [],
        "workflows": [],
        "runtime": {"kind": "browser"},
    }
    base.update(over)
    return base


def test_consent_groups_reads_writes_sends_viewers_runtime() -> None:
    c = consent(_m(), run_as="sam@x.test")
    assert c["reads"] == [{"object": "note", "ops": ["tree"]}, {"object": "order", "ops": ["list"]}]
    assert c["writes"] == [{"object": "order", "ops": ["create", "delete"]}]
    assert c["sends"] == {
        "integrations": ["zoho.deal"],
        "mcp_tools": [],
        "workflows": [WF],
        "logic": ["stock_check"],
        "door_ops": ["crm.create_deal"],
        "unknown": [],
        "app_workflows": [],
    }
    assert c["connections"] == {
        "providers": ["zoho"],
        "mcp_servers": ["mcp:tally"],
        "needs": [
            {"name": "mcp:tally", "as": "org", "scopes": None},
            {"name": "zoho", "as": "org", "scopes": None},
        ],
        "unknown": [],
    }
    assert c["viewers"] == {
        "roles": ["clerk"],
        "restricted": ["order"],
        "open": ["note"],
        "access": {"order": '[{"read":"all","role":"clerk"}]'},
    }
    assert c["links"] == [] and c["knowledge"] == [] and c["unclassified"] == {}
    assert c["runtime"] == {"kind": "browser", "packages": {}}
    assert c["run_as"] == "sam@x.test" and c["sensitive_fields"] == []


def test_every_object_op_lands_in_reads_or_writes_by_its_class() -> None:
    """A new op in OBJECT_OPS reaches the screen with no edit here."""
    actions = [{"id": f"x_{op}", "uses": f"object:x.{op}"} for op in OBJECT_OPS]
    c = consent(_m(actions=actions))
    got = {op for r in c["reads"] for op in r["ops"]} | {op for r in c["writes"] for op in r["ops"]}
    assert got == set(OBJECT_OPS)
    assert {op for r in c["reads"] for op in r["ops"]} == {
        op for op, k in OBJECT_OP_CLASSES.items() if k == "A"
    }


def test_the_example_manifest_is_valid_and_junk_gives_an_empty_consent() -> None:
    assert validate_manifest(_m(connections=[], actions=_m()["actions"][:4])) == []
    for junk in (None, 5, [], {"actions": "x"}, {"actions": [None, {"uses": 5}, {"uses": "zz"}]}):
        c = consent(junk)
        assert c["reads"] == [] and c["writes"] == [] and c["runtime"]["kind"] == "none"


def test_a_same_manifest_and_a_narrower_one_ask_for_nothing() -> None:
    assert widens(_m(), _m()) == []
    narrow = _m(
        actions=[{"id": "order_list", "uses": "object:order.list"}],
        connections=[],
        runtime={"kind": "none"},
    )
    assert widens(_m(), narrow) == []


def test_each_widening_is_named() -> None:
    old = _m(
        actions=[{"id": "l", "uses": "object:order.list"}],
        connections=[],
        runtime={"kind": "none"},
        roles=[],
    )
    new = _m(
        actions=[
            {"id": "l", "uses": "object:order.list"},
            {"id": "g", "uses": "object:order.get"},
            {"id": "u", "uses": "object:order.update"},
            {"id": "i", "uses": "integration:zoho"},
            {"id": "d", "uses": "crm.create_deal"},
        ],
        connections=["zoho", "mcp:tally"],
        runtime={"kind": "packages", "packages": {"pandas": "2.2.2"}},
        roles=["clerk"],
    )
    lines = widens(old, new)
    text = "\n".join(lines)
    for needle in (
        "Reads `order`: get",
        "Writes `order`: update",
        "Sends through `zoho`",
        "Calls the app action `crm.create_deal`",
        "connection `zoho`",
        "MCP server `mcp:tally`",
        "role `clerk`",
        "`none` to `packages`",
        "package `pandas` to 2.2.2",
    ):
        assert needle in text, needle


def test_opening_a_restricted_object_widens_and_a_new_sensitive_field_widens() -> None:
    opened = _m(
        objects=[
            {"name": "order", "fields": [{"key": "name", "type": "text"}]},
            {"name": "note", "fields": []},
        ]
    )
    assert any("Opens `order`" in x for x in widens(_m(), opened))
    flagged = _m(
        objects=[
            {
                "name": "order",
                "access": [{"role": "clerk", "read": "all"}],
                "fields": [{"key": "pay", "type": "money", "sensitive": True}],
            },
            {"name": "note", "fields": []},
        ]
    )
    assert consent(flagged)["sensitive_fields"] == [{"object": "order", "field": "pay"}]
    assert any("sensitive field `order.pay`" in x for x in widens(_m(), flagged))


def test_runtime_order_and_the_lines_hold_no_value() -> None:
    assert RUNTIME_ORDER == ("none", "browser", "sandbox", "packages")
    assert set(RECORD_OPS) <= set(OBJECT_OPS)
    body = _SRC.split('"""', 2)[2]
    for banned in ("import os", "httpx", "execute(", "get_db", "open("):
        assert banned not in body, banned


def test_an_action_past_any_list_cap_still_counts() -> None:
    """A version must not widen where a cut hides it (review of PR 2361)."""
    many = [{"id": f"g{i}", "uses": "object:t.get"} for i in range(300)]
    old = {"actions": many}
    new = {"actions": [*many, {"id": "z", "uses": "object:t.delete"}]}
    assert consent(new)["writes"] == [{"object": "t", "ops": ["delete"]}]
    assert widens(old, new) == ["Writes `t`: delete."]


# --- the forms that main added after the first review (fix round 2) ---------


def test_a_connection_need_and_an_mcp_tool_widen() -> None:
    """The case of the verifier: STU-I9a needs and STU-B1b-1 MCP actions."""
    new = _m(
        connections=["zoho", "mcp:tally", {"name": "mcp:acme"}],
        actions=[*_m()["actions"], {"id": "zap", "uses": "mcp:acme.delete_everything"}],
    )
    assert validate_manifest(new) == []
    assert consent(new)["sends"]["mcp_tools"] == ["mcp:acme.delete_everything"]
    lines = widens(_m(), new)
    assert "Calls the MCP tool `mcp:acme.delete_everything`." in lines
    assert "Needs the MCP server `mcp:acme` with the org account." in lines


def test_a_wider_scope_and_a_new_account_widen() -> None:
    plain = _m(connections=["zoho", "google_calendar"])
    same = _m(connections=[{"name": "zoho"}, {"name": "google_calendar", "as": "member"}])
    assert validate_manifest(same) == []
    assert widens(plain, same) == []  # a plain name and its default are equal
    scoped = _m(connections=[
        {"name": "zoho", "scopes": ["books.read"]}, {"name": "google_calendar", "as": "org"},
    ])
    assert validate_manifest(scoped) == []
    lines = widens(plain, scoped)
    assert "Asks `zoho` for the scopes books.read." in lines
    assert "Uses the org account for `google_calendar`." in lines
    wider = _m(connections=[
        {"name": "zoho", "scopes": ["books.read", "books.write"]}, {"name": "google_calendar", "as": "org"},
    ])
    assert widens(scoped, wider) == ["Asks `zoho` for the scopes books.write."]
    assert widens(wider, scoped) == []  # fewer scopes is a narrowing
    assert "Drops the scope list of `zoho`, so it can ask for any scope." in widens(
        scoped, _m(connections=["zoho", {"name": "google_calendar", "as": "org"}])
    )
    back = widens(scoped, _m(connections=[{"name": "zoho", "scopes": ["books.read"]}, "google_calendar"]))
    assert back == ["Uses the member account for `google_calendar`."]


def test_a_connection_item_that_cannot_be_read_widens() -> None:
    for junk in ({"name": 5}, {"name": "x", "as": "world"}, {"name": "x", "extra": 1},
                 {"name": "x", "scopes": "all"}, 7, None):
        lines = widens(_m(), _m(connections=["zoho", "mcp:tally", junk]))
        assert any("connection item that the consent cannot read" in x for x in lines), junk


def test_a_uses_that_no_form_reads_widens() -> None:
    for uses in ("zz", "rest:https://x.test", 5, None):
        new = _m(actions=[*_m()["actions"], {"id": "odd", "uses": uses}])
        assert any("cannot classify" in x for x in widens(_m(), new)), uses


def test_each_uses_source_has_a_case_that_is_not_unknown() -> None:
    """A new source of the A2 grammar needs a row here and a branch in consent."""
    cases = {
        "object": "object:order.get",
        "manifest": "crm.create_deal",
        "integration": "integration:zoho.deal",
        "workflow": f"workflow:{WF}",
        "logic": "logic:stock_check",
        "mcp": "mcp:acme.find",
    }
    assert set(cases) == USES_SOURCES == set(get_args(get_type_hints(UsesRef)["source"]))
    for source, uses in cases.items():
        assert parse_uses(uses).source == source
        assert consent({"actions": [{"id": "a", "uses": uses}]})["sends"]["unknown"] == [], uses


def test_a_skill_a_view_a_shell_and_a_trigger_that_name_an_object_count() -> None:
    old = _m(actions=[{"id": "l", "uses": "object:order.list"}])
    new = _m(
        actions=[{"id": "l", "uses": "object:order.list"}],
        skill={"description": "d", "actions": ["l", "object:order.delete"],
               "examples": [{"prompt": "p", "action": "object:note.update"}]},
        views=[{"id": "v", "block": "table", "object": "note"}],
        shell={"search": ["note"], "needs": [{"object": "order"}]},
        workflows=[{"id": "w", "trigger": {"record": "note", "schedule": "0 9 * * *"}}],
    )
    assert validate_manifest(new) == []
    lines = widens(old, new)
    for needle in (
        "Writes `order`: delete.",
        "Reads `note`: list.",
        "Writes `note`: update.",
        "Adds or changes the app workflow `w`.",
    ):
        assert needle in lines, needle
    moved = _m(**{**{k: new[k] for k in ("actions", "skill", "views", "shell")},
                  "workflows": [{"id": "w", "trigger": {"record": "note", "schedule": "* * * * *"}}]})
    assert widens(new, moved) == ["Adds or changes the app workflow `w`."]


def test_a_link_a_grant_change_knowledge_and_a_hook_widen() -> None:
    linked = _m(objects=[
        {"name": "order", "access": [{"role": "clerk", "read": "all"}],
         "fields": [{"key": "deal", "type": "relation", "to": "crm.deal"}]},
        {"name": "note", "fields": [{"key": "t", "type": "text"}]},
    ])
    assert validate_manifest(linked) == []
    assert widens(_m(), linked) == ["Links to the records of `crm.deal`."]
    regrant = _m(objects=[
        {"name": "order", "fields": [{"key": "name", "type": "text"}],
         "access": [{"role": "clerk", "read": "all", "write": "all"}]},
        {"name": "note", "fields": [{"key": "t", "type": "text"}]},
    ])
    assert widens(_m(), regrant) == ["Changes who can see `order`."]
    known = _m(skill={"description": "d", "knowledge": ["handbook"]})
    assert widens(_m(), known) == ["The skill reads the knowledge `handbook`."]
    hooked = _m(extends="crm", contributes={"jobs": [{"verb": "x"}]}, overrides=[{"a": 1}])
    assert widens(_m(), hooked) == [
        "Extends the package `crm`.",
        "Adds or changes an item of `contributes`.",
        "Adds or changes an item of `overrides`.",
    ]
    assert widens(hooked, _m()) == []


def test_a_top_level_key_that_consent_does_not_classify_widens() -> None:
    assert widens(_m(), _m(hosts=["evil.test"])) == [
        "Changes `hosts`, which the consent does not classify."
    ]


def _models(t: object, seen: dict[str, type[BaseModel]]) -> None:
    if isinstance(t, type) and issubclass(t, BaseModel):
        if t.__name__ not in seen:
            seen[t.__name__] = t
            for f in t.model_fields.values():
                _models(f.annotation, seen)
        return
    for arg in get_args(t):
        _models(arg, seen)


def test_every_field_of_the_manifest_schema_has_a_consent_part() -> None:
    """The guard: a new form of the manifest fails here until FIELD_PARTS names
    its consent part, and consent() reads it."""
    seen: dict[str, type[BaseModel]] = {}
    _models(Manifest, seen)
    assert set(seen) == set(FIELD_PARTS)
    parts = {"none", "reads", "writes", "sends", "connections", "viewers", "links",
             "sensitive_fields", "knowledge", "hooks", "runtime"}
    for name, model in seen.items():
        assert set(model.model_fields) == set(FIELD_PARTS[name]), name
        for field, part in FIELD_PARTS[name].items():
            assert set(part.split()) <= parts, (name, field)
    assert set(Manifest.model_fields) == TOP_LEVEL_PARTS
