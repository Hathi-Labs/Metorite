"""The dry-run door of a skill test: hold each write, record each call (WS-STU F4).

Spec: ``project-docs/specs/ai_studio_rows/F4.md`` and ``ai_studio.md`` §7.
A skill test runs the skill on a real turn, but no write may leave. This
module holds the parts of that door. ``orchestrator._tool_injection`` calls
:func:`attach`, and ``gateway.studio.skill_tests.run_suite`` sets the mark.

* :func:`dry_run_scope` marks the code in a block as a dry run. It is the ONE
  setter of the mark, and a server run boundary calls it. Never a tool, and
  never a request field. :func:`current_dry_run` reads it.
* :class:`DryRun` is the record of one run: the forced pack, the calls, the
  tool objects that may run, and the state of the caps.
* :class:`HoldAndRecord` is the function middleware. It records each call.
  A held call never reaches ``call_next``, so the body of a held write never
  runs, and the model reads one fixed text.
* :class:`CapGuard` is the chat middleware. It reads the cost before each
  model call, and it raises :class:`DryRunStopped` before ``call_next`` when a
  cap stops the run. A stop is sticky, so a retry cannot start a call.
* :func:`attach` puts both at index 0 of ``agent.middleware`` of a per-run
  view. MAF runs that list before each provider middleware, so the hold sees
  each call first, an MCP call too. It arms the record last. An agent that
  cannot take the hold raises :class:`DryRunRefused`, so the run fails closed.

**What runs for real (F4b).** A tool runs for real only when all four hold.
The door is armed and under its call cap. The tool is an action of the skill
under test. :func:`attach` captured the tool object from the run's own tool
list. The identity of the object is on :data:`REAL_READS`. The identity is
the ``module.qualname`` of the owner's function, never the bare name. An MCP
tool never runs. The door holds each other call.

**The door checks a held call against the schema of the tool.** The record
keeps ``valid`` and the names of the bad keys, so a call that the real tool
refuses never passes a test.

Fence: ``tests/unit/test_skill_dry_run.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from agent_framework import ChatMiddleware, FunctionMiddleware
from pydantic import BaseModel, ValidationError

from acb_skills.egress import _from_mcp, _risk_of
from acb_skills.tool_guard import tool_name

__all__ = [
    "HELD_TEXT", "METER_TIMEOUT_S", "MODEL_CALL_CAP", "REAL_READS", "TOOL_CALL_CAP", "CapGuard",
    "DryRun", "DryRunRefused", "DryRunStopped", "HoldAndRecord", "RecordedCall",
    "attach", "check_args", "current_dry_run", "dry_run_scope",
    "model_call_refusal", "tool_identity",
]

#: What the model reads for a held call. It is fixed, so no argument and no
#: secret reaches it.
HELD_TEXT = "Held. This run is a skill test. The call is recorded and nothing ran."

#: The most model calls of one test. The meter lags one call under streaming,
#: so this cap is the one that always holds (``call_cap``).
MODEL_CALL_CAP = 8
#: The most tool calls of one test (``call_cap``).
TOOL_CALL_CAP = 20
#: The most seconds of one read of the meter. A slower read is a fault.
METER_TIMEOUT_S = 10.0

#: THE frozen allowlist of the tools that a dry run may run for real. The key
#: is the identity of the owner's function (:func:`tool_identity`), and the
#: value is the one tool name that it may carry. Only a GET read of a
#: first-party store may join: no model call, no embedding call, no member
#: ask, no card, no page move and no network write. A new entry needs a
#: reason in ``tests/unit/test_skill_dry_run.py::REASONS``. A tool that is no
#: entry is held, so a new tool fails closed.
REAL_READS: Mapping[str, str] = MappingProxyType({
    "skill_projects.reads.list_tasks": "list_tasks",
    "skill_projects.reads.task_detail": "task_detail",
    "skill_projects.reads.my_work": "my_work",
    "acb_skills.knowledge_tools.read_page": "read_page",
    "acb_skills.knowledge_tools.list_spaces": "list_spaces",
})


class DryRunRefused(RuntimeError):
    """A dry run cannot start: an agent of the run cannot take the hold."""


class DryRunStopped(RuntimeError):
    """A cap stopped the dry run before a model call. ``reason`` is the code."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"The skill test stopped: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class RecordedCall:
    """One tool call of a dry run.

    *action* is ``tool:<name>``. *held* is True when the door held the call,
    and False when the tool ran for real. *write* is False only for a tool
    object that its annotation calls read-only and no MCP server gave. *valid* is
    False when the arguments fail the schema of the tool. *invalid_keys* names
    the bad keys, never a value. *args* stays in memory for
    ``skill_tests.evaluate``. No caller stores it, and no answer holds it.
    """

    tool: str
    action: str
    args: Mapping[str, Any]
    held: bool
    write: bool = True
    valid: bool = True
    invalid_keys: tuple[str, ...] = ()


@dataclass
class DryRun:
    """The record of one dry run. It holds no tenant, no member and no text.

    *pack* is the forced skill pack (``skill_packs.SkillPack``), or a slug in
    a test. *armed* says that :func:`attach` put the door in place. An
    unarmed door holds every call, and :class:`CapGuard` refuses each model
    call. *loaded* says that the forced pack reached the run. *real* holds the
    tool objects that :func:`attach` captured. *spend* reads the credits of
    the suite so far, and *credit_cap* bounds them. *stopped* names the cap
    that ended the run, if one did.
    """

    pack: Any
    calls: list[RecordedCall] = field(default_factory=list)
    armed: bool = False
    loaded: bool = False
    call_cap: int | None = None
    model_call_cap: int = MODEL_CALL_CAP
    model_calls: int = 0
    credit_cap: Decimal | None = None
    spend: Callable[[], Awaitable[Any]] | None = None
    real: tuple[Any, ...] = ()
    stopped: str | None = None

    @property
    def slug(self) -> str:
        """The slug of the forced pack."""
        return str(getattr(self.pack, "slug", self.pack) or "")

    @property
    def tools(self) -> frozenset[str]:
        """The tool names of the actions of the forced pack."""
        return frozenset(str(t) for t in (getattr(self.pack, "tools", ()) or ()))


_DRY_RUN: ContextVar[DryRun | None] = ContextVar("skill_dry_run", default=None)


def current_dry_run() -> DryRun | None:
    """The record of the dry run that the code runs in, or None."""
    return _DRY_RUN.get()


@contextlib.contextmanager
def dry_run_scope(record: DryRun) -> Iterator[DryRun]:
    """Mark the code in the block as the dry run *record*. The old value
    comes back as the block ends. ⚠️ The ONE setter. Only a server run
    boundary calls it, never request input and never a tool."""
    token = _DRY_RUN.set(record)
    try:
        yield record
    finally:
        _DRY_RUN.reset(token)


def tool_identity(item: Any) -> str | None:
    """The identity of a tool: ``module.qualname`` of the owner's function.

    It reads ``item.func``, or *item*, and follows ``__wrapped__`` to the
    function that the owner wrote. A function with no module or qualname, a
    ``functools.partial`` and a loop of wrappers give None."""
    fn = getattr(item, "func", item)
    if fn is None or not callable(fn):
        return None
    try:
        base = inspect.unwrap(fn)
    except ValueError:
        return None
    module = getattr(base, "__module__", None)
    qualname = getattr(base, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return None
    return f"{module}.{qualname}"


def _args(raw: Any) -> dict[str, Any]:
    """The arguments of a call as a plain dict, in each shape that MAF holds."""
    if isinstance(raw, Mapping):
        return dict(raw)
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            out = dump()
        except Exception:
            return {}
        return dict(out) if isinstance(out, Mapping) else {}
    return {}


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
    "array": (list, tuple), "object": (Mapping,), "null": (type(None),),
}


def _json_type_ok(value: Any, kind: str) -> bool:
    """True when *value* has the JSON schema type *kind*. A bool is no number."""
    want = _JSON_TYPES.get(kind)
    if want is None:
        return True
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, want)


def _kinds(spec: Mapping[str, Any]) -> list[str]:
    kind = spec.get("type")
    if isinstance(kind, str):
        return [kind]
    if isinstance(kind, list):
        return [k for k in kind if isinstance(k, str)]
    return []


def _schema_problems(args: Mapping[str, Any], schema: Mapping[str, Any]) -> set[str]:
    """The keys that break the top-level schema checks of MAF: a missing
    required key, a key that ``additionalProperties: false`` refuses, a value
    outside an ``enum``, and a type that differs."""
    bad: set[str] = set()
    required = schema.get("required")
    for key in required if isinstance(required, list) else []:
        if isinstance(key, str) and key not in args:
            bad.add(key)
    props = schema.get("properties")
    props = props if isinstance(props, Mapping) else {}
    if schema.get("additionalProperties") is False:
        bad.update(str(k) for k in args if k not in props)
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, Mapping):
            continue
        enum = spec.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            bad.add(str(key))
        kinds = _kinds(spec)
        if kinds and not any(_json_type_ok(value, k) for k in kinds):
            bad.add(str(key))
    return bad


def check_args(function: Any, args: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Check *args* against the schema of the tool *function*, as MAF checks a
    call before the tool runs. Answers ``(valid, bad key names)``.

    First the input model of the tool (pydantic), then the top-level checks
    of its JSON schema. A tool with no schema to read fails closed, and the
    answer is ``(False, ())``. The names come from the error locations only,
    so no value reaches them."""
    model = getattr(function, "input_model", None)
    params = getattr(function, "parameters", None)
    if model is None and not callable(params):
        return False, ()
    parsed: Mapping[str, Any] = dict(args)
    if isinstance(model, type) and issubclass(model, BaseModel):
        try:
            parsed = model.model_validate(dict(args)).model_dump(exclude_unset=True)
        except ValidationError as exc:
            names = {str(e["loc"][0]) if e.get("loc") else "(arguments)"
                     for e in exc.errors(include_url=False, include_context=False,
                                         include_input=False)}
            return False, tuple(sorted(names))
        except Exception:
            return False, ()
    try:
        schema = params() if callable(params) else None
    except Exception:
        return False, ()
    bad = _schema_problems(parsed, schema) if isinstance(schema, Mapping) else set()
    return (not bad), tuple(sorted(bad))


def _writes(function: Any, name: str) -> bool:
    """False only for a tool OBJECT whose own annotation says read-only, and
    that no MCP server gave. ``egress._risk_of`` is the ONE reader, and it
    fails closed: an unknown tool, and a borrowed name, is a write."""
    if function is None or _from_mcp(function):
        return True
    hints = _risk_of(function, name)
    return not (isinstance(hints, Mapping) and hints.get("read_only") is True)


def _captured(record: DryRun, function: Any) -> bool:
    """True when *function*, or its ``func``, is an object that :func:`attach`
    captured. A test of the object, never of a name."""
    inner = getattr(function, "func", None)
    return any(item is function or (inner is not None and item is inner)
               for item in record.real)


def _may_run(record: DryRun, function: Any, name: str) -> bool:
    """The allowlist rule of one call (the module docstring)."""
    if not name or name not in record.tools or _from_mcp(function):
        return False
    if not _captured(record, function):
        return False
    return REAL_READS.get(tool_identity(function) or "") == name


class HoldAndRecord(FunctionMiddleware):
    """Records each tool call of one run, and holds each one that may write.

    It takes the record from *record*, or from the mark when none is given.
    With no record, or with an unarmed one, it holds every call, so a door
    that was never switched on cannot let a write through.
    """

    def __init__(self, record: DryRun | None = None) -> None:
        self._record = record

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        record = self._record or current_dry_run()
        function = getattr(context, "function", None)
        name = tool_name(function)
        over = (record is not None and record.call_cap is not None
                and len(record.calls) >= record.call_cap)
        run = (record is not None and record.armed and not over
               and record.stopped is None and _may_run(record, function, name))
        if record is not None:
            if over and record.stopped is None:
                record.stopped = "call_cap"
            args = _args(getattr(context, "arguments", None))
            valid, bad = (True, ()) if run else check_args(function, args)
            record.calls.append(RecordedCall(
                tool=name, action=f"tool:{name}", args=args, held=not run,
                write=_writes(function, name), valid=valid, invalid_keys=bad))
        if run:
            await call_next()
            return
        context.result = HELD_TEXT


def _finite_positive(value: Any) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


async def model_call_refusal(record: DryRun | None) -> str | None:
    """The code that refuses the next model call of *record*, or None.

    In order: ``hold_unarmed`` (no record, or an unarmed one), the code of an
    earlier stop, ``call_cap`` (the model call cap), ``cap_unset`` (no credit
    cap), then ``cost_read_failed`` (no meter, a fault, a slow read or no
    number) and ``cap_reached``. A fault never reads as 0."""
    if record is None or not record.armed:
        return "hold_unarmed"
    if record.stopped is not None:
        return record.stopped
    if record.model_calls >= record.model_call_cap:
        return "call_cap"
    cap = record.credit_cap
    if cap is None or not _finite_positive(cap):
        return "cap_unset"
    if record.spend is None:
        return "cost_read_failed"
    try:
        spent = await asyncio.wait_for(record.spend(), METER_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        return "cost_read_failed"
    if not isinstance(spent, Decimal) or not spent.is_finite() or spent < 0:
        return "cost_read_failed"
    if spent >= cap:
        return "cap_reached"
    return None


class CapGuard(ChatMiddleware):
    """Refuses a model call of a dry run that a cap stops (:func:`model_call_refusal`).

    It raises :class:`DryRunStopped` BEFORE ``call_next``, so no request goes
    out. The record keeps the first code in ``stopped``, and each later call
    reads it, so a retry starts no call."""

    def __init__(self, record: DryRun | None = None) -> None:
        self._record = record

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        record = self._record or current_dry_run()
        reason = await model_call_refusal(record)
        if reason is not None or record is None:
            code = reason or "hold_unarmed"
            if record is not None and record.stopped is None:
                record.stopped = code
            raise DryRunStopped(code)
        record.model_calls += 1
        await call_next()


def _middleware_list(agent: Any) -> list[Any]:
    found = getattr(agent, "middleware", None)
    if found is None:
        return []
    if isinstance(found, list | tuple):
        return list(found)
    return [found]


def attach(agents: list[Any], record: DryRun, *, native: type,
           tools_of: Callable[[Any], Iterable[Any]]) -> None:
    """Put the door on each agent of *agents* for ONE run, then arm *record*.

    Each agent becomes a shallow copy whose ``middleware`` list starts with
    :class:`HoldAndRecord` and :class:`CapGuard`. The agent object that
    another run holds never changes. *native* is the MAF ``Agent`` class.
    *tools_of* gives the run's own tools of an agent. This step captures each
    allowlisted tool of the forced pack from them.

    It raises :class:`DryRunRefused`, and arms nothing, for a forced skill
    that did not load, for no agent, an agent that is no native MAF agent, a
    Copilot agent, or an agent whose chat client holds a middleware of its
    own. Such a middleware would run before the hold. So a run that cannot
    test the skill starts no model call."""
    if not record.loaded:
        raise DryRunRefused("The skill under test did not load in this run.")
    if not agents:
        raise DryRunRefused("A skill test found no agent to hold.")
    views: list[Any] = []
    real: list[Any] = []
    for agent in agents:
        if not isinstance(agent, native) or hasattr(agent, "_permission_handler"):
            raise DryRunRefused("A skill test runs only on a native agent.")
        client = getattr(agent, "client", None)
        if getattr(client, "function_middleware", None) or getattr(client, "chat_middleware", None):
            raise DryRunRefused("A skill test cannot put its hold first on this agent.")
        view = copy.copy(agent)
        view.middleware = [HoldAndRecord(record), CapGuard(record), *_middleware_list(agent)]
        views.append(view)
        for item in tools_of(agent):
            name = tool_name(item)
            if (name in record.tools and not _from_mcp(item)
                    and REAL_READS.get(tool_identity(item) or "") == name):
                real.append(item)
                inner = getattr(item, "func", None)
                if inner is not None:
                    real.append(inner)
    agents[:] = views
    record.real = tuple(real)
    record.armed = True
