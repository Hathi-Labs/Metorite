"""The tool argument contract — declaration vs. handler vs. gate.

``args_schema`` is now load-bearing in three places (the editor's form, the
publish gate, the runtime check), which means a wrong declaration is no longer
cosmetic: it can block a valid publish, or wave through a payload the
integration will reject. These tests hold the declaration to the handler.

The drift being hunted is specific and boring: someone adds
``args.get("space_id")`` to a handler and forgets the schema, so the editor
never offers the field, publish never demands it, and the node fails only when
a real run reaches ClickUp.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

import pytest
from gateway.routes.workflows import tools
from gateway.routes.workflows.engine.tool_args import (
    ARG_TYPES,
    check_args,
    parse_arg_spec,
    parse_args_schema,
)

ALL_SPECS = sorted(tools.list_tools(), key=lambda s: s.action)
ALL_ACTIONS = [s.action for s in ALL_SPECS]


def _spec(action: str) -> Any:
    return next(s for s in ALL_SPECS if s.action == action)


# ── The mini-language ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "type_name", "required", "description"),
    [
        ("string", "string", True, ""),
        ("string?", "string", False, ""),
        ("object?|Extra headers", "object", False, "Extra headers"),
        ("array|Ids to assign", "array", True, "Ids to assign"),
        ("  boolean?  ", "boolean", False, ""),
        ("", "string", True, ""),  # empty declaration is a required string
    ],
)
def test_declarations_parse(raw: str, type_name: str, required: bool, description: str) -> None:
    spec = parse_arg_spec("field", raw)
    assert (spec.type, spec.required, spec.description) == (type_name, required, description)


def test_an_unknown_type_degrades_to_string_rather_than_exploding() -> None:
    """A typo in one tool's declaration must not take the catalog down for
    every other tool — the palette is how makers discover what exists."""
    assert parse_arg_spec("field", "strnig").type == "string"


def test_required_arguments_are_ordered_first() -> None:
    """Field order in the inspector: what you must fill in, then what you may."""
    specs = parse_args_schema(
        {"c_opt": "string?", "a_req": "string", "b_opt": "string?", "d_req": "string"}
    )
    assert [s.name for s in specs] == ["a_req", "d_req", "b_opt", "c_opt"]


# ── Every registered tool's declaration is well-formed ──────────────────────


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_declared_types_are_in_the_closed_vocabulary(action: str) -> None:
    """Parsing silently degrades an unknown type; this catches it at the source
    so the degradation never has to happen for a tool we ship."""
    for name, raw in _spec(action).args_schema.items():
        type_part = str(raw).partition("|")[0].strip().rstrip("?").lower()
        assert type_part in ARG_TYPES, f"{action}.{name} declares '{type_part}'"


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_every_argument_carries_help_text(action: str) -> None:
    """The description is what the maker reads instead of guessing. A tool
    without one is a JSON box with extra steps."""
    for arg in parse_args_schema(_spec(action).args_schema):
        assert arg.description, f"{action}.{arg.name} has no description"


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_a_tool_declares_at_least_one_argument(action: str) -> None:
    assert _spec(action).args_schema, f"{action} declares no arguments"


# ── Declaration vs. handler: the drift test ─────────────────────────────────


def _args_read_by(handler: Any) -> set[str]:
    """Every literal key the handler reads out of its ``args`` mapping.

    Static, not behavioural: handlers reach real integrations, so calling them
    is not an option here. It reads ``args.get("x")`` / ``args["x"]`` out of the
    source, which is exactly the shape every handler uses.
    """
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):  # pragma: no cover - closures always resolve
        return set()
    tree = ast.parse(textwrap.dedent(source))
    found: set[str] = set()
    for node in ast.walk(tree):
        # args.get("x")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "args"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
        # args["x"]
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "args"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.add(node.slice.value)
    return found


#: Keys a handler may read without declaring: conventions the engine supplies,
#: not arguments the maker fills in.
UNDECLARED_OK = {"target"}


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_every_argument_a_handler_reads_is_declared(action: str) -> None:
    """The drift that matters: a handler grows a new input and the schema does
    not, so the editor never offers it and publish never demands it."""
    spec = _spec(action)
    assert spec.handler is not None, f"{action} has no handler"
    read = _args_read_by(spec.handler) - UNDECLARED_OK
    undeclared = read - set(spec.args_schema)
    assert not undeclared, f"{action} reads {sorted(undeclared)} but does not declare them"


def test_the_drift_detector_actually_detects_drift() -> None:
    """A contract test that cannot fail is worse than none — prove this one can
    see an undeclared read before trusting the parametrized runs above."""

    async def _handler(args: dict[str, Any], actor: str) -> dict[str, Any]:
        return {"a": args.get("declared"), "b": args["sneaky"]}

    assert _args_read_by(_handler) == {"declared", "sneaky"}


def test_every_broker_write_declares_its_target_field() -> None:
    """`_broker_write` addresses the proposal by one named argument; if that
    argument is not declared, the maker cannot supply it and every proposal
    would be filed against the action name instead of a real target.

    ⚠️ **Re-cut 2026-08-24 by D52 (board WS-39 S1).** This asserted the rule
    against `clickup.create_task`, the only `_broker_write` tool that ever
    existed, and it went `StopIteration` when D52 deleted it. Rather than pin a
    second example that does not exist, the check is now **structural**: it
    walks every registered spec, recovers `target_field` from the handler's
    closure, and holds each one to its own schema.

    **It is vacuous today, and it says so out loud below** rather than passing
    quietly — `_broker_write` currently has ZERO callers. The count assertion is
    what makes the vacuum visible: add a broker-write tool and this test starts
    doing real work on it automatically, and the reminder stops being true.
    """
    checked = 0
    for spec in ALL_SPECS:
        cells = getattr(spec.handler, "__closure__", None) or ()
        names = getattr(spec.handler, "__code__", None)
        free = getattr(names, "co_freevars", ()) if names else ()
        bound = dict(zip(free, (c.cell_contents for c in cells)))
        if "target_field" not in bound:
            continue
        checked += 1
        assert bound["target_field"] in spec.args_schema, (
            f"{spec.action}: _broker_write targets {bound['target_field']!r} "
            "but the args_schema does not declare it, so a maker cannot supply "
            "it and every proposal would be filed against the action name."
        )

    assert checked == 0, (
        f"{checked} _broker_write tool(s) are registered — good, the rule above "
        "now has real subjects. Delete this reminder assertion; it exists only "
        "to make the post-D52 vacuum visible instead of silently green."
    )


# ── The gate: what check_args accepts and rejects ───────────────────────────

SCHEMA = {
    "url": "string|Where to post",
    "count": "number?|How many",
    "headers": "object?|Extra headers",
    "tags": "array?|Labels",
}


def test_a_complete_argument_set_passes() -> None:
    assert check_args(SCHEMA, {"url": "https://x.io", "headers": {"A": "b"}}) == []


def test_a_missing_required_argument_is_named() -> None:
    problems = check_args(SCHEMA, {"headers": {}}, action="http.request")
    assert problems == ["'url' is required by http.request"]


def test_an_empty_string_does_not_satisfy_a_required_argument() -> None:
    """The failure mode this replaces: a blank field published happily and the
    integration rejected it hours later."""
    assert check_args(SCHEMA, {"url": "   "}) == ["'url' is required by this tool"]


def test_a_reference_satisfies_a_required_argument() -> None:
    """`{{trigger.url}}` resolves at run time — demanding a literal here would
    make the state bus unusable for exactly the fields that need it most."""
    assert check_args(SCHEMA, {"url": "{{trigger.url}}"}) == []


def test_a_reference_is_exempt_from_type_checking() -> None:
    """A reference can resolve to an object; it is a string until it does."""
    assert check_args(SCHEMA, {"url": "https://x.io", "headers": "{{prev.headers}}"}) == []


def test_an_unknown_argument_is_reported() -> None:
    """Catches a typo'd key that would otherwise be silently dropped by the
    handler — the maker sees a field they filled in do nothing."""
    problems = check_args(SCHEMA, {"url": "https://x.io", "headerz": {}})
    assert problems == ["'headerz' is not an argument of this tool"]


def test_a_container_in_a_scalar_field_is_reported() -> None:
    assert check_args(SCHEMA, {"url": {"nested": True}}) == ["'url' should be string"]


def test_a_scalar_in_a_structured_field_is_reported() -> None:
    assert check_args(SCHEMA, {"url": "https://x.io", "headers": "plain"}) == [
        "'headers' should be object"
    ]


def test_a_number_in_a_string_field_is_allowed() -> None:
    """Lenient by design: every handler coerces scalars, and crying wolf here
    trains makers to ignore the messages that matter."""
    assert check_args({"note": "string|x"}, {"note": 42}) == []


def test_all_problems_are_reported_at_once() -> None:
    """One publish attempt should surface the whole fix list, not the first."""
    problems = check_args(SCHEMA, {"headers": "plain", "nope": 1})
    assert len(problems) == 3


def test_non_object_arguments_are_rejected_clearly() -> None:
    assert check_args(SCHEMA, ["url"]) == ["arguments for this tool must be a JSON object"]


def test_absent_arguments_are_treated_as_empty() -> None:
    assert check_args(SCHEMA, None) == ["'url' is required by this tool"]


def test_a_tool_with_no_declared_arguments_accepts_nothing_extra() -> None:
    assert check_args({}, {"anything": 1}) == ["'anything' is not an argument of this tool"]
