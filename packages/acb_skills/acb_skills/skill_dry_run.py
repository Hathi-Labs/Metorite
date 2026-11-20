"""The dry-run door of a skill test: hold every write, record every call (WS-STU F4a).

Spec: ``project-docs/specs/ai_studio_rows/F4.md`` and ``ai_studio.md`` §7.
A skill test runs the skill on a real turn, but no write may leave. This
module holds the three parts of that door, and it attaches none of them.
F4b wires them into one run, and it owns the flag.

* :func:`dry_run_scope` marks the code in a block as a dry run. It is the ONE
  setter of the mark, and a server run boundary calls it. Never a tool, and
  never a request field. :func:`current_dry_run` reads it.
* :class:`DryRun` is the record of one run. It holds plain data: the forced
  pack, the calls, and the state of the caps.
* :class:`HoldAndRecord` is the function middleware. A tool runs for real
  only when :func:`runs_for_real` says yes. Every other call is recorded
  with its arguments, and the model reads one fixed text. ``call_next`` is
  never called for it, so the body of a held write never runs.

**What runs for real.** A tool that the platform registered as
``read_only=True`` and ``open_world=False``, less the tools that ask the
member, add model calls, delegate, or load a pack. A tool with no entry in
the registry is held, so a new tool fails closed.

**The pass rule reads this record, never the answer text.** Each call is
recorded as :class:`RecordedCall`. ``held`` is True for a call that did not
run. ``skill_tests.evaluate`` reads the list and nothing else.

Fence: ``tests/unit/test_skill_dry_run.py``.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from agent_framework import ContextProvider, FunctionMiddleware

from acb_skills.egress import DELEGATION_TOOLS
from acb_skills.subagent_contract import EXTRA_CALL_TOOLS, MEMBER_TOOLS
from acb_skills.tool_annotations import get_annotations
from acb_skills.tool_guard import tool_name

__all__ = [
    "HELD_TEXT", "LOADER_TOOLS", "DryRun", "DryRunProvider", "HoldAndRecord",
    "RecordedCall", "current_dry_run", "dry_run_scope", "runs_for_real",
]

#: The tools that load a pack in the middle of a turn. A dry run holds them,
#: because the forced pack is loaded by the harness, never by the model.
LOADER_TOOLS = frozenset({"load_skill_tools"})

#: What the model reads for a held call. It is fixed, so no argument and no
#: secret reaches it.
HELD_TEXT = "Held. This run is a skill test. The call is recorded and nothing ran."


@dataclass(frozen=True)
class RecordedCall:
    """One tool call of a dry run.

    *action* is ``tool:<name>``, the form of an ``expect`` entry. *held* is
    True when the door held the call, and False when the tool ran for real.
    """

    tool: str
    action: str
    args: Mapping[str, Any]
    held: bool


@dataclass
class DryRun:
    """The record of one dry run. Plain data: no tenant, no member, no text.

    *pack* is the slug of the forced skill pack. *armed* says that the door
    is on, and an unarmed door holds every call. *loaded* says that the
    forced pack reached the run. *call_cap* is the most calls that the run
    may make, and *stopped* names the cap that ended it, if one did.
    """

    pack: str
    calls: list[RecordedCall] = field(default_factory=list)
    armed: bool = False
    loaded: bool = False
    call_cap: int | None = None
    credit_cap: Decimal | None = None
    stopped: str | None = None


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


def runs_for_real(name: str) -> bool:
    """True when a dry run lets the tool *name* run. It must be registered
    as read-only and closed-world, and it must not ask the member, add model
    calls, delegate or load a pack."""
    if name in MEMBER_TOOLS or name in EXTRA_CALL_TOOLS:
        return False
    if name in DELEGATION_TOOLS or name in LOADER_TOOLS:
        return False
    hints = get_annotations(name)
    return bool(hints and hints.get("read_only") is True and hints.get("open_world") is False)


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
        name = tool_name(getattr(context, "function", None))
        over = (record is not None and record.call_cap is not None
                and len(record.calls) >= record.call_cap)
        run = (record is not None and record.armed and not over
               and runs_for_real(name))
        if record is not None:
            if over and record.stopped is None:
                record.stopped = "call_cap"
            record.calls.append(RecordedCall(
                tool=name, action=f"tool:{name}",
                args=_args(getattr(context, "arguments", None)), held=not run))
        if run:
            await call_next()
            return
        context.result = HELD_TEXT


class DryRunProvider(ContextProvider):
    """Adds :class:`HoldAndRecord` to ONE run of a native MAF agent."""

    SOURCE_ID = "skill_dry_run"

    def __init__(self, record: DryRun) -> None:
        super().__init__(self.SOURCE_ID)
        self._record = record

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(self.SOURCE_ID, [HoldAndRecord(self._record)])
