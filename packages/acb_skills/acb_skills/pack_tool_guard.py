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

**The failure log (TOF-2c).** The guard is the capture site C1 (the
``except`` branch) and C2 (the ``take_bad`` branch) of
``tool_failure_log.md`` §4.2. Each hands ONE record to
``acb_common.failure_log.record_tool_failure``, which is off unless
``TOOL_FAILURE_LOG_ORGS`` lists the org. :func:`chat_failure_code` holds the
C1 skip rule. The capture never changes the answer, and it never raises.

Fences: ``tests/unit/test_every_pack_tool_answers_text.py``,
``tests/unit/test_tool_argument_shapes.py`` and
``tests/unit/test_failure_capture_sites.py``.
"""

from __future__ import annotations

import collections.abc
import contextvars
import functools
import json
import os
import types
import typing
from collections import deque
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import urlsplit

from acb_common import failure_log
from acb_common._log import get_logger
from acb_common.tool_refusals import FAILED, REFUSED, GatewayRefusal

# TOF-2b: the set of built-in names is in the failure log before C1 or C2 runs
# (tool_failure_log.md §3.2 rule 1).
from acb_skills import builtin_tools as _builtin_tools  # noqa: F401

_log = get_logger("acb_skills.pack_tool_guard")

__all__ = [
    "GUARDED_ATTR",
    "REFUSAL_STATUSES",
    "STRING_PARAMS",
    "arg_shapes_enabled",
    "bad_shape_text",
    "chat_failure_code",
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


# ── The capture of a failure (TOF-2c, C1 and C2) ───────────────────────────
#
# Spec: ``tool_failure_log.md`` §4.1 and §4.2. Fence:
# ``tests/unit/test_failure_capture_sites.py``. The capture never changes
# what the guard answers, and it never raises into the tool call.

#: The statuses of §4.1: each is an answer, not a failure.
REFUSAL_STATUSES = frozenset({400, 403, 404, 409, 412, 422, 429, 503})
_WALK_MAX = 64


def _chain(exc: BaseException) -> list[BaseException]:
    """*exc*, then its groups, causes and contexts, once each, outer first.

    It follows ``__context__`` too, as ``mcp_tools._causes`` does. So a
    ``KeyError`` that a handler of a gateway 500 raised still shows the 500."""
    seen: list[BaseException] = []
    queue: deque[BaseException | None] = deque([exc])
    while queue and len(seen) < _WALK_MAX:
        cur = queue.popleft()
        if cur is None or any(cur is s for s in seen):
            continue
        seen.append(cur)
        if isinstance(cur, BaseExceptionGroup):
            queue.extend(cur.exceptions)
        queue.extend((cur.__cause__, cur.__context__))
    return seen


def _gateway_host() -> str:
    """The host of ``GATEWAY_URL``, with the default of the pack transports."""
    url = os.environ.get("GATEWAY_URL", "http://localhost:8080")
    return (urlsplit(url).hostname or "").lower()


def _host_of(exc: BaseException) -> str | None:
    """The host of the request of an httpx error, or None. It reads no text."""
    try:
        return str(exc.request.url.host or "").lower()  # type: ignore[attr-defined]
    except Exception:  # an error with no request
        return None


def _code_for_status(status: int) -> str | None:
    """The code of a status of the gateway (§4.2, the C1 skip rule)."""
    if status in REFUSAL_STATUSES or status >= 500:
        # A refusal is an answer. D1 or D2 writes a 5xx at the gateway.
        return None
    if status == 401:
        return "auth_401"
    return "exception"


def chat_failure_code(exc: BaseException) -> str | None:
    """The ``error_code`` of C1 for *exc*, or None to write nothing.

    The walk reads one number from the chain: classes, numbers and hosts,
    never a message. The first item that names a status or a transport
    error decides.

    * ``GatewayRefusal.status`` is the gateway's. With no status, it is a
      refusal of our own client, an answer, so it writes nothing.
    * ``httpx.HTTPStatusError`` is the gateway's only when its host is the
      host of ``GATEWAY_URL``. Any other host answered, so it writes nothing.
    * ``httpx.TransportError`` gives ``gateway_unreachable`` for the gateway
      host and ``outside_unreachable`` for any other host.
    * With none of these in the chain, the code is ``exception``.
    """
    import httpx

    gateway = None
    for item in _chain(exc):
        if isinstance(item, GatewayRefusal):
            status = item.status
            return _code_for_status(status) if isinstance(status, int) else None
        if isinstance(item, httpx.HTTPStatusError):
            gateway = _gateway_host() if gateway is None else gateway
            if _host_of(item) != gateway:
                return None
            try:
                status = int(item.response.status_code)
            except Exception:
                return "exception"
            return _code_for_status(status)
        if isinstance(item, httpx.TransportError):
            gateway = _gateway_host() if gateway is None else gateway
            host = _host_of(item)
            return "gateway_unreachable" if host == gateway else "outside_unreachable"
    return "exception"


def _record(exc: BaseException | None, tool: str) -> None:
    """Hand one failure to the one writer. Never raises.

    With *exc*, the code is :func:`chat_failure_code` (C1). With no
    exception, the code is ``bad_arguments`` (C2)."""
    try:
        code = chat_failure_code(exc) if exc is not None else "bad_arguments"
        if code is None:
            return
        failure_log.record_tool_failure(
            exc, source="chat_tool", error_code=code, tool=tool, **failure_log.run_fields())
    except Exception as err:  # the class only: the tool answers as before
        _log.debug("pack_tool_guard.capture_failed", error_type=type(err).__name__)


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
    propagate, so a stopped run stops, and they write no failure record.
    Each branch that answers a failure records it once (C1, C2)."""
    read_only = _read_only(fn)

    @functools.wraps(fn)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        bad = take_bad(name)
        if bad:
            _record(None, name)  # C2 (TOF-2c)
            return bad_shape_text(*bad[0])
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            _log.warning("pack_tool_guard.failed", tool=name, error_type=type(exc).__name__)
            _record(exc, name)  # C1 (TOF-2c), inside the except: the traceback is there
            return failure_text(exc, read_only=read_only)

    setattr(guarded, GUARDED_ATTR, True)
    if shapes:
        install_annotations(guarded, name)
    return guarded
