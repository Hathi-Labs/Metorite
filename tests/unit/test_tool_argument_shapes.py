"""HRD-3 (task 2982): a pack tool reads an argument in the shape that its
schema declares (``chat_failure_modes.md`` §4.2 B3, §7 rule 2).

The guard is ``acb_skills.pack_tool_guard``. The hook is ``_pack_call`` in
``orchestrator._tool_injection``. The flag is ``TOOL_ARG_SHAPES`` (default off).

* Off: the tools, their schemas and their refusals are as before.
* On: for each object or list parameter of each pack tool, the JSON-string form
  passes the check of MAF, or gets a refusal that names the shape. It never
  gets "Invalid arguments". A native list for a ``str`` parameter that the
  tool parses (``STRING_PARAMS``) becomes that string. A real string never
  changes.

Each test names the mutation that turns it red. Run:
``uv run pytest tests/unit/test_tool_argument_shapes.py -q``.
"""
from __future__ import annotations

import inspect
import json
import types
import typing
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_skills import pack_tool_guard as ptg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from acb_skills.write_artifact import artifact_context
from agent_framework import FunctionTool
from agent_framework._tools import normalize_tools
from orchestrator import _tool_injection as ti

from tests.unit.test_pack_guards import _build, _run, _stub


@pytest.fixture(autouse=True)
def _agent_permission_off(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "tool_arg_shapes", on, raising=False)


def _owner_tools() -> list[tuple[str, str, Any]]:
    """Every real pack tool: ``(slug, owner, tool)``."""
    out = []
    for slug in sf.pack_slugs():
        owner = tp.pack(slug).owner
        module = ti._pack_owner_module(owner)
        assert module is not None, f"the owner {owner} of {slug} did not load"
        out += [(slug, owner, fn) for fn in ti._owner_pack_tools(slug, owner, module)
                if inspect.iscoroutinefunction(ti._pack_func(fn))]
    return out


def _sample(annotation: Any) -> Any:
    """A value that passes the check for *annotation* (required parameters)."""
    base = ptg._strip(annotation)
    origin = typing.get_origin(base)
    if origin is typing.Literal:
        return typing.get_args(base)[0]
    if origin in (typing.Union, types.UnionType):
        kept = [a for a in typing.get_args(base) if a is not type(None)]
        return _sample(kept[0]) if kept else None
    shape = ptg.shape_of(base)
    if shape == "object":
        return {}
    if shape == "array":
        return []
    if base is bool:
        return True
    if base is int:
        return 1
    if base is float:
        return 1.0
    return "x"


def _args_for(tool: FunctionTool, **given: Any) -> dict[str, Any]:
    args = {n: _sample(f.annotation) for n, f in tool.input_model.model_fields.items()
            if f.is_required()}
    args.update(given)
    return args


def _shaped(tool: FunctionTool) -> dict[str, str]:
    return {n: s for n, f in tool.input_model.model_fields.items()
            if (s := ptg.shape_of(f.annotation))}


def _wrap_all(monkeypatch, on: bool) -> list[tuple[str, FunctionTool]]:
    _flag(monkeypatch, on)
    made = []
    for slug, owner, fn in _owner_tools():
        wrapped = ti._pack_call(fn, owner, False, "key", slug)
        made.append((tool_name(fn), normalize_tools([wrapped])[0]))
    return made


def test_the_flag_is_off_by_default() -> None:
    """Mutation: default the setting to True → red."""
    from acb_common.settings import Settings

    assert Settings.model_fields["tool_arg_shapes"].default is False


def test_the_schema_of_every_pack_tool_is_the_same_on_and_off(monkeypatch) -> None:
    """The validator lives in the check, not in the schema. The model reads the
    same tool. Mutation: swap ``object`` for ``Any`` in an annotation, or add
    the validator as a ``Field`` → red."""
    off = {n: t.parameters() for n, t in _wrap_all(monkeypatch, False)}
    on = {n: t.parameters() for n, t in _wrap_all(monkeypatch, True)}
    assert off.keys() == on.keys() and len(on) > 200
    assert [n for n in on if on[n] != off[n]] == []


def test_a_json_string_passes_for_each_object_or_list_parameter(monkeypatch) -> None:
    """Mutation: skip ``install_annotations`` or ``install_tool_shapes`` → the
    JSON-string form is refused with "Invalid arguments" → red."""
    seen = 0
    for name, tool in _wrap_all(monkeypatch, True):
        for param, shape in _shaped(tool).items():
            text = "{}" if shape == "object" else "[]"
            try:
                got = tool._prepare_arguments(_args_for(tool, **{param: text}))
            except Exception as exc:
                pytest.fail(f"{name}.{param}: {exc}")
            assert got[param] == ({} if shape == "object" else []), f"{name}.{param}"
            seen += 1
    assert seen >= 40, "the sweep found too few object and list parameters"


def test_a_json_string_that_does_not_read_is_noted_for_a_refusal(monkeypatch) -> None:
    """The check passes with an empty value, and the guard refuses the call
    with text that names the shape. Never "Invalid arguments". Mutation: let a
    bad string through to the check of pydantic → "Invalid arguments" → red."""
    for name, tool in _wrap_all(monkeypatch, True):
        for param, shape in _shaped(tool).items():
            for bad in ('{"a": ', "[1, 2", "[]" if shape == "object" else "{}"):
                ptg.take_bad(name)
                try:
                    tool._prepare_arguments(_args_for(tool, **{param: bad}))
                except Exception as exc:
                    pytest.fail(f"{name}.{param} {bad}: {exc}")
                assert ptg.take_bad(name) == ((param, shape),), f"{name}.{param} {bad}"
                text = ptg.bad_shape_text(param, shape)
                assert text.startswith("Refused:") and param in text
                assert "Invalid arguments" not in text


def test_a_shape_param_is_an_object_or_array_in_the_schema(monkeypatch) -> None:
    """A field whose JSON schema says ``string`` must not count as a list.
    Mutation: count ``str`` as an array in ``shape_of`` → red."""
    for name, tool in _wrap_all(monkeypatch, False):
        props = tool.parameters().get("properties", {})
        for param, shape in _shaped(tool).items():
            kinds = {props[param].get("type")} | {
                a.get("type") for a in props[param].get("anyOf", [])}
            assert shape in kinds, f"{name}.{param}: {props[param]}"


async def _call(tool: FunctionTool, **args: Any) -> str:
    out = await tool.invoke(arguments=args)
    return "".join(getattr(c, "text", str(c)) for c in out)


def _stub_pack_tool() -> tuple[list[dict[str, Any]], Any]:
    seen: list[dict[str, Any]] = []

    async def tool(to: list[str] | None = None, subject: str = "",
                   manifest: dict[str, Any] | None = None) -> str:
        seen.append({"to": to, "subject": subject, "manifest": manifest})
        return "ok"

    tool.__name__ = "read_email"
    tool.__tool_risk__ = {"read_only": True, "destructive": False,  # type: ignore[attr-defined]
                          "idempotent": True, "open_world": False}
    return seen, tool


async def test_a_plain_function_reads_json_strings_and_keeps_real_strings(
    monkeypatch,
) -> None:
    """Through the whole pack seam: ``_pack_tools``, the input model of MAF,
    the call. A real string (``subject``) never changes, also when it starts
    with ``[``. Mutation: parse every string, not only a list or object
    parameter → ``subject`` becomes a list → red."""
    _flag(monkeypatch, True)
    seen, tool = _stub_pack_tool()
    with _run():
        build = await _build("email", _stub(tool))
        made = normalize_tools(list(build.tools))[0]
        assert await _call(made, to='["a@x.test", "b@x.test"]', subject="[urgent] hi",
                           manifest='{"k": 1}') == "ok"
        assert seen[-1] == {"to": ["a@x.test", "b@x.test"], "subject": "[urgent] hi",
                            "manifest": {"k": 1}}
        assert await _call(made, to=["c@x.test"]) == "ok"  # a native list still works
        assert seen[-1]["to"] == ["c@x.test"]
        before = len(seen)
        text = await _call(made, manifest='{"k": ')
        assert text.startswith("Refused:") and "manifest" in text and "JSON object" in text
        assert "Invalid arguments" not in text and len(seen) == before
        assert (await _call(made, to='{"a": 1}')).startswith("Refused:")  # the other container
        assert len(seen) == before, "the body must not run with an empty value"


async def test_with_the_flag_off_a_json_string_is_refused_as_before(monkeypatch) -> None:
    """Ship dark. Mutation: read the flag the wrong way round → red."""
    _flag(monkeypatch, False)
    seen, tool = _stub_pack_tool()
    with _run():
        build = await _build("email", _stub(tool))
        made = normalize_tools(list(build.tools))[0]
        with pytest.raises(Exception, match="Invalid arguments"):
            await made.invoke(arguments={"to": '["a@x.test"]'})
        assert seen == []


def _projects_tool(name: str, param: str, seen: list[Any]) -> FunctionTool:
    scope: dict[str, Any] = {}
    exec(f"async def {name}({param}: str) -> str:\n    seen.append({param})\n    return 'ok'",
         {"seen": seen}, scope)
    fn = scope[name]
    fn.__tool_risk__ = {"read_only": False, "destructive": False, "idempotent": False}
    return FunctionTool(name=name, description="d", func=fn)


@pytest.mark.parametrize(("name", "slug", "param", "value", "expect"), [
    ("create_tasks", "projects-bulk", "tasks", [{"title": "a"}], '[{"title": "a"}]'),
    ("update_tasks", "projects-bulk", "changes", [{"task_id": "t"}], '[{"task_id": "t"}]'),
    ("link_tasks_batch", "projects-plan", "links", [{"from_task_id": "a"}],
     '[{"from_task_id": "a"}]'),
    ("create_tags", "projects-structure", "tags", ["x", {"name": "y"}], '["x", {"name": "y"}]'),
    ("create_types", "projects-structure", "types", [{"name": "y"}], '[{"name": "y"}]'),
    ("file_tasks", "projects-bulk", "moves", [{"task_id": "a"}], '[{"task_id": "a"}]'),
    ("add_subtasks", "projects", "titles", ["one", "two"], "one\ntwo"),
])
async def test_a_native_list_becomes_the_string_of_a_str_batch_parameter(
    monkeypatch, name, slug, param, value, expect,
) -> None:
    """The Projects tools are ``FunctionTool`` objects with their own input
    model. Mutation: drop the tool from ``STRING_PARAMS``, or skip
    ``install_tool_shapes`` → "Invalid arguments" → red."""
    _flag(monkeypatch, True)
    seen: list[Any] = []
    tool = _projects_tool(name, param, seen)
    with _run():
        build = await ti._pack_tools(slug, artifact_context(), module=_stub(tool))
        made = build.tools[0]
        assert await _call(made, **{param: value}) == "ok"
        assert seen[-1] == expect
        assert await _call(made, **{param: "keep me"}) == "ok"  # a real string stays
        assert seen[-1] == "keep me"


def test_the_table_of_str_batch_params_tells_the_truth() -> None:
    """Each entry is a ``str`` parameter of a real Projects tool, and the tool
    reads JSON. Mutation: add an entry for a parameter that is no ``str`` or
    that no tool parses (a typo in a name) → red."""
    import skill_projects as sp

    for name, params in ptg.STRING_PARAMS.items():
        fn = inspect.unwrap(getattr(sp, name))
        hints = typing.get_type_hints(fn)
        for param, mode in params.items():
            assert hints[param] is str, f"{name}.{param}"
            assert mode in ("json", "lines"), f"{name}.{param}"
            if mode == "json":
                assert "JSON" in (fn.__doc__ or ""), f"{name}.{param} documents no JSON"


def test_coerce_never_changes_a_real_string_or_a_scalar() -> None:
    """Mutation: parse a string that starts with a letter → red."""
    shapes = {"items": "array", "obj": "object"}
    assert ptg.coerce_arguments({"items": "hello", "n": 1}, shapes) == {"items": "hello", "n": 1}
    assert ptg.coerce_arguments({"items": " [1] "}, shapes) == {"items": [1]}
    assert ptg.coerce_arguments({"obj": '{"a": 1}'}, shapes) == {"obj": {"a": 1}}
    assert json.loads(ptg.coerce_arguments({"t": [1]}, {}, {"t": "json"})["t"]) == [1]
    assert ptg.coerce_arguments({"t": 5}, {}, {"t": "json"}) == {"t": 5}
    assert ptg.coerce_arguments({"t": ["a", 1]}, {}, {"t": "lines"}) == {"t": ["a", 1]}


def test_shape_of_reads_only_a_declared_object_or_list() -> None:
    """A ``str``, a scalar and ``Any`` are never touched. Mutation: drop the
    ``str`` exclusion in ``shape_of`` (``str`` is a ``Sequence``) → red."""
    from collections.abc import Mapping, Sequence

    assert ptg.shape_of(list[str]) == "array"
    assert ptg.shape_of(list[str] | None) == "array"
    assert ptg.shape_of(Sequence[str]) == "array"
    assert ptg.shape_of(dict[str, Any]) == "object"
    assert ptg.shape_of(Mapping[str, Any] | None) == "object"
    assert ptg.shape_of(typing.Annotated[list[int], "a list"]) == "array"
    for plain in (str, str | None, bytes, int, bool, float, Any, list[str] | dict[str, Any]):
        assert ptg.shape_of(plain) is None, plain


@pytest.mark.parametrize(("tool_name_", "param", "shape", "string"), [
    # Production 2026-11-13 and 11-14: MAF said "Input should be a valid dictionary / list".
    ("studio_save", "manifest", "object", '{"name": "App", "objects": []}'),
    ("studio_patch", "ops", "array", '[{"op": "add", "path": "/x", "value": 1}]'),
])
def test_the_studio_calls_of_production_read_a_json_string(
    monkeypatch, tool_name_, param, shape, string,
) -> None:
    """``studio_save`` got ``manifest`` and ``studio_patch`` got ``ops`` as a
    JSON string. Mutation: drop the Studio pack from the shape install → the
    check says "Input should be a valid ..." → red."""
    tools = dict(_wrap_all(monkeypatch, True))
    assert tool_name_ in tools, f"{tool_name_} is no pack tool"
    tool = tools[tool_name_]
    assert _shaped(tool)[param] == shape
    got = tool._prepare_arguments(_args_for(tool, **{param: string}))
    assert got[param] == json.loads(string)
    ptg.take_bad(tool_name_)
    tool._prepare_arguments(_args_for(tool, **{param: '{"a": '}))  # must not raise
    assert ptg.take_bad(tool_name_) == ((param, shape),)
