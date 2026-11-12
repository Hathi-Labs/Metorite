"""WS-STU STU-C18: the hierarchy rule, the pure half.

Spec: ``project-docs/specs/ai_studio.md`` section 14, row C18. Code: the rule
kind ``hierarchy`` in ``acb_common/studio_rules.py`` (the grammar, the fit check,
``tree_moves`` and ``check_tree``). ``test_studio_hierarchy_r8.py`` runs the
walk and the tree read of ``objects_store.py`` on a real Postgres.

**The fences (R7).** Each test names the rule that it holds.

* The grammar takes ``{name, kind, field, message?}`` and no other key.
* An object holds one hierarchy.
* The parent field is one hot relation field that points to the same object.
* A hierarchy reads no key, so the store queues no key check for it.
* ``tree_moves`` names a new parent only. A kept parent and a root name none.
* ``check_tree`` refuses a cycle and a tree of more than 20 levels.
"""

from __future__ import annotations

from typing import Any

import pytest
from acb_common import studio_rules as sr
from acb_common.studio_fields import FieldSpec
from acb_common.studio_rules import ConstraintError, TreeViolation

PARENT = "11111111-2222-4333-8444-555555555555"
OTHER = "66666666-7777-4888-9999-aaaaaaaaaaaa"

_FIELDS = (
    FieldSpec(key="name", type="text"),
    FieldSpec(key="parent", type="relation", to="site", hot=True),
)


def _tree(**more: Any) -> dict[str, Any]:
    return {"name": "tree", "kind": "hierarchy", "field": "parent", **more}


def test_a_hierarchy_parses_and_stores_as_it_came() -> None:
    rule = sr.parse_rule(_tree(message="Pick another parent."))
    assert rule.kind == sr.HIERARCHY and rule.field == "parent"
    assert rule.stored() == _tree(message="Pick another parent.")
    assert sr.stored_rules([rule.stored()]) == (rule,)


@pytest.mark.parametrize("spec", [
    _tree(field=None), _tree(field="Parent"), _tree(field=3), _tree(depth=10),
    _tree(max_depth=20), _tree(to="site"),
])
def test_a_bad_hierarchy_is_refused(spec: dict[str, Any]) -> None:
    with pytest.raises(ConstraintError):
        sr.parse_rule(spec)


def test_a_stored_hierarchy_that_does_not_parse_fails_closed() -> None:
    (rule,) = sr.stored_rules([{"name": "tree", "kind": "hierarchy", "field": 3}])
    assert rule.kind == sr.MALFORMED
    with pytest.raises(sr.ConstraintViolation):
        sr.check_kind_known(rule, "site")


def test_an_object_holds_one_hierarchy() -> None:
    with pytest.raises(ConstraintError, match="one hierarchy"):
        sr.parse_rules([_tree(), _tree(name="tree2")])


def test_a_hierarchy_fits_one_hot_relation_to_its_own_object() -> None:
    sr.check_rules_fit(sr.parse_rules([_tree()]), _FIELDS, object_name="site")


@pytest.mark.parametrize(("fields", "said"), [
    ((FieldSpec(key="name", type="text"),), "not a field"),
    ((FieldSpec(key="parent", type="text", hot=True),), "one relation"),
    ((FieldSpec(key="parent", type="relation", to="site", many=True),), "one relation"),
    ((FieldSpec(key="parent", type="relation", to="building", hot=True),), "point to `site`"),
    ((FieldSpec(key="parent", type="relation", to="site"),), "must be hot"),
])
def test_a_hierarchy_that_does_not_fit_is_refused(fields: tuple[FieldSpec, ...], said: str) -> None:
    with pytest.raises(ConstraintError, match=said):
        sr.check_rules_fit(sr.parse_rules([_tree()]), fields, object_name="site")


def test_a_hierarchy_needs_the_name_of_its_object() -> None:
    with pytest.raises(ConstraintError, match="name of its object"):
        sr.check_rules_fit(sr.parse_rules([_tree()]), _FIELDS)


def test_a_hierarchy_reads_no_key_and_keeps_its_field_hot() -> None:
    rule = sr.parse_rule(_tree())
    assert sr.touched_keys(rule, None, {"parent": {"id": PARENT}}) == []
    assert rule.hot_keys() == ("parent",)
    assert sr.rule_reads(rule) == frozenset({"parent"})
    with pytest.raises(sr.ConstraintViolation, match="not hot"):
        sr.check_rule_is_hot(rule, "site", (FieldSpec(key="parent", type="relation", to="site"),))


@pytest.mark.parametrize(("before", "after", "moved"), [
    (None, {"name": "root"}, None),
    (None, {"parent": {"id": PARENT}}, PARENT),
    (None, {"parent": {"id": PARENT.upper()}}, PARENT),
    ({"parent": {"id": PARENT}}, {"parent": {"id": PARENT}}, None),
    ({"parent": {"id": PARENT}}, {"parent": {"id": PARENT.upper()}}, None),
    ({"parent": {"id": PARENT}}, {"parent": {"id": OTHER}}, OTHER),
    ({"name": "root"}, {"parent": {"id": OTHER}}, OTHER),
    ({"parent": {"id": PARENT}}, {"name": "root"}, None),
    ({"parent": {"id": PARENT}}, None, None),
])
def test_tree_moves_names_a_new_parent_only(before: Any, after: Any, moved: str | None) -> None:
    assert sr.tree_moves(sr.parse_rule(_tree()), before, after) == moved


def test_check_tree_refuses_a_cycle() -> None:
    with pytest.raises(TreeViolation) as caught:
        sr.check_tree(sr.parse_rule(_tree()), "site", cycle=True, depth=3)
    assert caught.value.code == sr.TREE_CYCLE and caught.value.field == "parent"
    assert "under itself" in caught.value.message


def test_check_tree_holds_the_depth_cap_of_twenty() -> None:
    rule = sr.parse_rule(_tree())
    assert sr.MAX_TREE_DEPTH == 20
    sr.check_tree(rule, "site", cycle=False, depth=20)
    with pytest.raises(TreeViolation) as caught:
        sr.check_tree(rule, "site", cycle=False, depth=21)
    assert caught.value.code == sr.TREE_TOO_DEEP and "20 levels" in caught.value.message


def test_check_tree_says_the_message_of_the_rule() -> None:
    rule = sr.parse_rule(_tree(message="A floor sits in one building."))
    with pytest.raises(TreeViolation, match=r"A floor sits in one building\."):
        sr.check_tree(rule, "site", cycle=True, depth=1)
