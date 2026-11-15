"""The one guard of every pack tool (HRD-3, task 2982).

Spec: ``project-docs/specs/chat_failure_modes.md`` §4.2 B3 and B4, and §7
rules 2 and 3.

``orchestrator._tool_injection._pack_call`` builds ONE wrapper for each pack
tool, and it calls this module twice. This module holds the logic, so the
orchestrator keeps a small hook-up only.

**B4: a failure is text with a next step.** :func:`guard` turns any
``Exception`` that a pack tool raises into text that the model can act on::

    The tool failed (<kind>). Nothing changed. Next: ...

Before this guard, only a ``GatewayRefusal`` became text
(``acb_common.tool_refusals.refusals_as_text``, Projects, CRM and email).
Any other exception reached MAF, which told the model "Error: Function
failed." The guard sits OUTSIDE ``refusals_as_text``, so a tool that is
already wrapped keeps its refusal text. ``functools.wraps`` keeps
``__wrapped__``, the signature and ``__tool_risk__`` (the egress rule H-236
reads it).

"Nothing changed" must be true. A read tool (``read_only`` in its
``__tool_risk__``) cannot change anything, so it says so. Every other tool
may fail after a write, so it says: "It may have changed part of it. Check
before you retry." A tool with no risk annotation counts as a write.

The log line holds the tool name and the exception class, and nothing else.
A message of an exception can hold what a member typed.

**B3: an argument has the shape that its schema declares.** A model sends a
JSON object as a string, and MAF refuses it before the tool body runs
("Invalid arguments for 'studio_patch'"). It also sends a native list for a
parameter that the tool declares as ``str``. Behind the flag
``TOOL_ARG_SHAPES`` (default off, :func:`arg_shapes_enabled`):

* a string that starts with ``{`` or ``[`` becomes the object or list that
  the parameter declares (:func:`install_annotations`, for a plain function,
  and :func:`install_tool_shapes`, for a MAF ``FunctionTool``);
* a native list or object for a ``str`` parameter that the tool parses as
  JSON becomes that JSON (:data:`STRING_PARAMS`);
* a string that does not parse, or that parses to the other container,
  gets a refusal that names the parameter and its shape, never "Invalid
  arguments" (:func:`take_bad`).

A real string argument never changes: only a parameter whose declared type
is an object or a list is read, and only a string that starts with ``{`` or
``[``. The coercion runs BEFORE the pydantic validation of MAF. For a plain
function it is a validator on the annotation, so the JSON schema that the
model reads does not change.

Fences: ``tests/unit/test_every_pack_tool_answers_text.py`` and
``tests/unit/test_tool_argument_shapes.py``.
"""

from __future__ import annotations

import collections.abc
import contextvars
import functools
import json
import types
import typing
from collections.abc import Mapping
from typing import Annotated, Any

from acb_common._log import get_logger
from acb_common.tool_refusals import FAILED, REFUSED

_log = get_logger("acb_skills.pack_tool_guard")

__all__ = [
    "GUARDED_ATTR",
    "STRING_PARAMS",
    "arg_shapes_enabled",
    "bad_shape_text",
    "coerce_arguments",
    "failure_text",
    "guard",
    "install_annotations",
    "install_tool_shapes",
    "shape_of",
    "take_bad",
]

#: Marks the wrapper that :func:`guard` made. The fence reads it.
GUARDED_ATTR = "__pack_tool_guarded__"

#: The tools whose ``str`` parameter holds a batch that the tool parses as
#: JSON (``json``), or as one title on each line (``lines``). A model that
#: sends a native list gets it joined here. The Projects batch tools are the
#: set of HRD-3. The fence reads each body to prove the claim.
STRING_PARAMS: dict[str, dict[str, str]] = {
    "create_tasks": {"tasks": "json"},
    "update_tasks": {"changes": "json"},
    "link_tasks_batch": {"links": "json"},
    "create_tags": {"tags": "json"},
    "create_types": {"types": "json"},
    "file_tasks": {"moves": "json"},
    "add_subtasks": {"titles": "lines"},
}

_NEXT_READ = (
    "Next: tell the member that this step failed, or use a different tool. "
    "Do not call this tool again with the same arguments."
)
_NEXT_WRITE = (
    "Next: read the object again with a read tool, and tell the member what "
    "you find. Do not call this tool again with the same arguments before you check."
)


def arg_shapes_enabled() -> bool:
    """THE one reader of ``TOOL_ARG_SHAPES``. Off when settings fail."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "tool_arg_shapes", False))
    except Exception:
        return False


def _read_only(fn: Any) -> bool:
    """True when *fn* says in its ``__tool_risk__`` that it only reads."""
    for obj in (fn, getattr(fn, "func", None)):
        hints = getattr(obj, "__tool_risk__", None)
        if isinstance(hints, Mapping):
            return hints.get("read_only") is True
    return False


def failure_text(exc: BaseException, *, read_only: bool) -> str:
    """The text the model reads for a tool that raised *exc*.

    Only the class of the exception shows. Its message can hold what a
    member typed, a host or a query."""
    kind = type(exc).__name__
    if read_only:
        return f"{FAILED}{kind}). Nothing changed. {_NEXT_READ}"
    return (
        f"{FAILED}{kind}). It may have changed part of it. Check before you "
        f"retry. {_NEXT_WRITE}"
    )


# ── The shapes ──────────────────────────────────────────────────────────────


class _Unreadable:
    """What :func:`_read_string` gives for a string that does not read."""

    __slots__ = ()


_UNREADABLE = _Unreadable()

#: The bad arguments of the call that runs now, as ``(tool, param, shape)``.
#: The check of MAF copies a value, so a marker in the value would not survive
#: it. The check runs in the task of the call (MAF makes one task for each
#: tool call), so the guard of the same task reads what the check wrote.
_BAD: contextvars.ContextVar[tuple[tuple[str, str, str], ...]] = contextvars.ContextVar(
    "pack_tool_guard_bad", default=())


def _note_bad(tool: str, param: str, shape: str) -> None:
    _BAD.set((*_BAD.get(), (tool, param, shape)))


def take_bad(tool: str) -> tuple[tuple[str, str], ...]:
    """The ``(param, shape)`` pairs that the check of this call refused for
    *tool*. It clears them."""
    mine = tuple((p, s) for t, p, s in _BAD.get() if t == tool)
    if _BAD.get():
        _BAD.set(tuple(row for row in _BAD.get() if row[0] != tool))
    return mine


def _empty(shape: str) -> Any:
    return {} if shape == "object" else []


def bad_shape_text(param: str, shape: str) -> str:
    """The refusal for a *param* that does not read as *shape*."""
    what = "a JSON object" if shape == "object" else "a JSON list"
    return (
        f"{REFUSED} {param} must be {what}, and the text that you sent does not read "
        f"as {what}.\nNext: send {param} as {what}, and call the tool once more."
    )


_ARRAYS = (list, tuple, set, frozenset)
_ARRAY_ABCS = (collections.abc.Sequence, collections.abc.Set)
_OBJECT_ABCS = (collections.abc.Mapping,)


def _strip(hint: Any) -> Any:
    while typing.get_origin(hint) is Annotated:
        hint = typing.get_args(hint)[0]
    return hint


def shape_of(hint: Any) -> str | None:
    """``"object"`` or ``"array"`` for a type hint that declares one, else None.

    ``X | None`` counts as X. A union of two kinds, ``str``, ``Any`` and every
    scalar give None, so such a parameter is never touched."""
    hint = _strip(hint)
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        kept = [a for a in typing.get_args(hint) if a is not type(None)]
        return shape_of(kept[0]) if len(kept) == 1 else None
    base = origin or hint
    if not isinstance(base, type) or base is str or base is bytes:
        return None
    if issubclass(base, _OBJECT_ABCS):
        return "object"
    if issubclass(base, _ARRAYS + _ARRAY_ABCS):
        return "array"
    return None


def _read_string(param: str, text: str, shape: str) -> Any:
    """The object or list in *text*, or the marker, or *text* itself.

    Only a string that starts with ``{`` or ``[`` is read. Any other string
    comes back as it is, so MAF refuses it as before."""
    body = text.strip()
    if not body.startswith(("{", "[")):
        return text
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return _UNREADABLE
    want = dict if shape == "object" else list
    return parsed if isinstance(parsed, want) else _UNREADABLE


def _join(value: Any, mode: str) -> Any:
    """A native list or object as the string of a ``str`` parameter, or
    *value* itself when it is no list or object."""
    if not isinstance(value, (list, tuple, dict)):
        return value
    if mode == "lines":
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return "\n".join(v.strip() for v in value)
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return value


def coerce_arguments(
    arguments: Any, shapes: Mapping[str, str], strings: Mapping[str, str] | None = None,
) -> Any:
    """*arguments* with each parameter in *shapes* read as its shape, and each
    parameter in *strings* joined. Other arguments stay as they are.

    A string that cannot be read stays an ``_UNREADABLE`` sentinel, and the
    caller decides what to do with it."""
    if not isinstance(arguments, Mapping):
        return arguments
    out = dict(arguments)
    for name, shape in shapes.items():
        if isinstance(out.get(name), str):
            out[name] = _read_string(name, out[name], shape)
    for name, mode in (strings or {}).items():
        if name in out:
            out[name] = _join(out[name], mode)
    return out


def _validator(tool: str, param: str, shape: str) -> Any:
    from pydantic import WrapValidator

    def read(value: Any, handler: Any) -> Any:
        if isinstance(value, str):
            value = _read_string(param, value, shape)
            if value is _UNREADABLE:
                # The guard answers with text. The tool body never runs.
                _note_bad(tool, param, shape)
                return _empty(shape)
        return handler(value)

    return WrapValidator(read)


def _described(hint: Any) -> tuple[Any, list[Any]]:
    """The base type of *hint* and its extras. MAF turns a string extra into
    a field description, so it is turned here, before a validator joins it."""
    from pydantic import Field

    if typing.get_origin(hint) is not Annotated:
        return hint, []
    base, *extras = typing.get_args(hint)
    return base, [Field(description=e) if isinstance(e, str) else e for e in extras]


def install_annotations(wrapper: Any, tool: str) -> int:
    """Put a reading validator on each object or list parameter of *wrapper*.

    MAF builds its input model from ``typing.get_type_hints(wrapper)``, which
    reads the ``__annotations__`` of the wrapper. A ``WrapValidator`` in
    ``Annotated`` runs inside the pydantic check, and it leaves the JSON
    schema as it was. Returns the number of parameters it changed. A hint that
    does not resolve changes nothing."""
    try:
        hints = typing.get_type_hints(wrapper, include_extras=True)
    except Exception as exc:
        _log.info("pack_tool_guard.hints_unread", tool=getattr(wrapper, "__name__", ""),
                  error_type=type(exc).__name__)
        return 0
    notes = dict(getattr(wrapper, "__annotations__", {}))
    changed = 0
    for param, hint in hints.items():
        shape = None if param == "return" else shape_of(hint)
        if shape is None:
            continue
        base, extras = _described(hint)
        notes[param] = Annotated[(base, *extras, _validator(tool, param, shape))]  # type: ignore[misc]
        changed += 1
    if changed:
        wrapper.__annotations__ = notes
    return changed


def install_tool_shapes(tool: Any) -> int:
    """Read the arguments of a MAF ``FunctionTool`` before MAF checks them.

    *tool* must be a copy that this caller owns. The method
    ``_prepare_arguments`` is the one place where MAF validates, so the
    instance gets a version that reads first. A string that does not read is
    noted (:func:`take_bad`) and replaced by an empty value, so the check
    passes. The guard then refuses the call before the body runs. Returns the
    number of parameters it watches."""
    model = getattr(tool, "input_model", None)
    fields = getattr(model, "model_fields", None)
    if not fields:
        return 0
    shapes: dict[str, str] = {}
    for name, info in fields.items():
        shape = shape_of(info.annotation)
        if shape is not None:
            shapes[name] = shape
    strings = {
        p: mode for p, mode in STRING_PARAMS.get(str(getattr(tool, "name", "")), {}).items()
        if p in fields and _strip(fields[p].annotation) is str
    }
    if not shapes and not strings:
        return 0
    original = tool._prepare_arguments

    label = str(getattr(tool, "name", ""))

    def prepare(arguments: Any) -> Any:
        take_bad(label)  # a failed check of an earlier call leaves nothing behind
        given = coerce_arguments(arguments, shapes, strings)
        if not isinstance(given, Mapping):
            return original(arguments)
        clean = dict(given)
        for key, value in given.items():
            if value is _UNREADABLE:
                _note_bad(label, key, shapes[key])
                clean[key] = _empty(shapes[key])
        return original(clean)

    tool._prepare_arguments = prepare
    return len(shapes) + len(strings)


# ── The wrapper ─────────────────────────────────────────────────────────────


def guard(fn: Any, *, name: str, shapes: bool = False) -> Any:
    """*fn* (an async pack tool), answering a failure as text.

    The wrapper keeps the name, the docstring, the signature,
    ``__tool_risk__`` and ``__wrapped__`` (``functools.wraps``), so the
    wrapper below it stays reachable. *shapes* installs the reading
    validators (:func:`install_annotations`) and so is ``arg_shapes_enabled()``
    of the caller. A call with a string that did not read (:func:`take_bad`)
    gets the refusal of :func:`bad_shape_text` and runs nothing.

    ``asyncio.CancelledError`` and the other ``BaseException`` kinds still
    propagate, so a stopped run stops."""
    read_only = _read_only(fn)

    @functools.wraps(fn)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        bad = take_bad(name)
        if bad:
            return bad_shape_text(*bad[0])
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            _log.warning("pack_tool_guard.failed", tool=name, error_type=type(exc).__name__)
            return failure_text(exc, read_only=read_only)

    setattr(guarded, GUARDED_ATTR, True)
    if shapes:
        install_annotations(guarded, name)
    return guarded
