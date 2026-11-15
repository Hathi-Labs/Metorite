"""WS-STU J6: the install consent from the manifest (task 1426).

Code: ``packages/acb_common/acb_common/studio_consent.py``. No database. The
screen draws from :func:`consent`, so the first tests walk the real op list and
the source fence says the module reads only the manifest.
"""

from __future__ import annotations

from pathlib import Path

from acb_common.studio_consent import RUNTIME_ORDER, consent, widens
from acb_common.studio_manifest import OBJECT_OP_CLASSES, OBJECT_OPS, RECORD_OPS, validate_manifest

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
        "workflows": [WF],
        "logic": ["stock_check"],
        "door_ops": ["crm.create_deal"],
    }
    assert c["connections"] == {"providers": ["zoho"], "mcp_servers": ["mcp:tally"]}
    assert c["viewers"] == {"roles": ["clerk"], "restricted": ["order"], "open": ["note"]}
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
