"""Unified watchdog policy for the executor's run loops.

Before this module the timeout knobs lived as inline ``os.environ.get(...)``
reads scattered across three executor call sites (native stream idle, native
tiered selection, Tier-1.5 per-tool). That made the policy impossible to
inspect or test and easy to drift. This centralises the VALUES and the
tier-selection RULE in one place (core_module_map A1/A2 "unified watchdog
policy").

Two distinct mechanisms remain — they are genuinely different and are NOT
merged (that would be over-generalisation):

* **Idle watchdog** (native MAF Tier 1): bounds the gap BETWEEN stream updates.
  Tiered by run state — a parked HITL question or an in-flight tool legitimately
  produces no updates for a while, so each gets a longer budget than a bare
  idle stream. See :meth:`WatchdogPolicy.idle_timeout`.
* **Per-tool timeout** (Tier-1.5 batch shim): bounds a SINGLE async tool call so
  a hung tool surfaces as an error instead of blocking the stream until the
  HTTP abort. HITL / delegation tools are exempt (they park by design). See
  :attr:`WatchdogPolicy.tool_execution`.

All values are read from the environment ONCE at policy construction, so a
single ``default_watchdog()`` instance gives every loop the same numbers.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    """Parse an env var as float, falling back on missing/invalid input."""
    try:
        raw = os.environ.get(name)
        return float(raw) if raw is not None and raw.strip() else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class WatchdogPolicy:
    """Timeout budgets + the native tier-selection rule, in one place."""

    # ── Idle watchdog (native stream, seconds between updates) ──────────────
    idle: float = 120.0            # bare idle stream — nothing happening
    tool_open: float = 600.0       # >=1 tool in flight (legitimately quiet)
    hitl_pending: float = 3600.0   # parked on a human answer
    # ── Per-tool execution (Tier-1.5 batch shim, one tool call) ─────────────
    tool_execution: float = 300.0

    def idle_timeout(self, *, hitl_pending: bool, tools_open: int) -> float:
        """Select the idle budget for the current native-stream run state.

        Priority: a pending HITL question wins (longest wait), then an in-flight
        tool, else the bare idle budget. This is the exact rule the native loop
        applied inline; centralised so both the value and the ordering are
        testable and can't drift.
        """
        if hitl_pending:
            return self.hitl_pending
        if tools_open > 0:
            return self.tool_open
        return self.idle


class LoopDetector:
    """Trip when the SAME tool call (name + args) repeats too many times.

    A model can wedge itself re-issuing an identical call (bad plan, ignored
    error), burning tokens until the idle/HTTP timeout. This counts completed
    calls by ``name(args)`` signature and reports a trip once any signature hits
    ``max_repeats``. Distinct-arg repeats never trip — only genuine loops.
    """

    def __init__(self, max_repeats: int = 5) -> None:
        self.max_repeats = max_repeats
        self._counts: dict[str, int] = {}

    def record(self, name: str, args: str) -> bool:
        """Record one completed call; return True if the loop threshold is hit."""
        sig = f"{name or 'tool'}({args or ''})"
        self._counts[sig] = self._counts.get(sig, 0) + 1
        return self._counts[sig] >= self.max_repeats


# ── The per-turn budget of one read tool (HRD-5, chat_failure_modes.md B5) ──
#
# ``LoopDetector`` stops only identical calls. A model can also call one read
# tool again and again with new arguments, and each answer is empty or refused.
# Production, 14 days: 60 turns called one tool 3 times or more. After
# ``TOOL_TURN_LIMIT`` empty or refused answers from one tool in one turn, the
# next call to that tool gets :func:`stop_text` and does not run. The state is
# one :class:`ToolTurnBudget` for each turn (``orchestrator.tool_turn_budget``
# makes it in ``before_run``). Nothing here is module state.

#: Empty or refused answers from one tool in one turn before the stop.
TOOL_TURN_LIMIT = 3
#: The most tools that one budget tracks, so a run cannot grow it without end.
_MAX_TOOLS = 64
#: Each listed argument form is cut to this many characters.
_TRIED_CHARS = 40
#: The most argument forms that the stop text lists.
_TRIED_LISTED = 3

# The line of a search that found nothing (``company_search_tools.NOTHING``).
_NOTHING_LINE = "(no matching records)"
# The first words of an empty answer: "No task matches ...", "Nothing found".
_EMPTY_START = re.compile(
    r"^(?:no (?:\w+ ){0,3}(?:match|matches|found|results?|records?)\b|nothing\b)",
    re.IGNORECASE,
)
# The first words of a refusal or a call that the tool did not take.
_REFUSED_START = re.compile(
    r"^(?:refused\b|not run\b|error:|give (?:at least|\d+ to \d+) )",
    re.IGNORECASE,
)
#: The tools that the budget covers: the read tools whose empty and refused
#: texts are pinned (as copies, with a source check) by ``tests/unit/test_tool_turn_budget.py``. An allow-list,
#: not "every read tool", because a tool such as ``get_errors`` answers "No
#: errors found." on SUCCESS, and a clean answer must never end a turn.
BUDGET_TOOLS: frozenset[str] = frozenset({"search_company", "find_tasks"})

#: A success phrase is never an empty answer, whatever a tool adds later.
_SUCCESS_START = re.compile(
    r"^no (?:\w+ ){0,2}(?:errors?|issues?|problems?|warnings?|changes?|conflicts?)\b"
    r"|^nothing to (?:do|change|fix)\b",
    re.IGNORECASE,
)

#: A real answer is long. An empty or refused one is short, so a long text
#: never counts, whatever its first words say.
_MAX_SHORT_ANSWER = 1500


def is_empty_or_refused(text: object) -> bool:
    """True when a READ tool's answer *text* is empty or refused.

    It is narrow, but not exact: it reads the first words of a short text. A
    miss leaves today's behaviour. A success phrase ("No errors found.") never
    counts, and the budget covers only the :data:`BUDGET_TOOLS`, so a false hit
    cannot stop a tool outside that list. It reads the shapes that the chat
    tools return now: the zero line of ``search_company``, "No task matches
    ...", the short-query refusal of ``find_tasks``, and the texts that start
    with "Refused", "Not run" or "Error:". Pinned by
    ``tests/unit/test_tool_turn_budget.py`` against the real tool texts.
    """
    if not isinstance(text, str):
        return False
    body = text.strip()
    if not body or len(body) > _MAX_SHORT_ANSWER:
        return False
    if _SUCCESS_START.match(body):
        return False
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if _NOTHING_LINE in lines[:4]:
        return True
    return bool(_EMPTY_START.match(body) or _REFUSED_START.match(body))


# Every C0 and C1 control character, and DEL. ESC and NUL must not reach the model.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _short(value: object) -> str:
    """One argument value on one line, with no quote and no control character."""
    text = _CONTROL.sub(" ", str(value)).replace('"', "'")
    return " ".join(text.split())


def tried_form(arguments: object) -> str:
    """The bounded, short form of a call's arguments, for the stop text."""
    items: list[tuple[str, object]] = []
    if isinstance(arguments, Mapping):
        items = [(str(k), v) for k, v in arguments.items()]
    elif hasattr(arguments, "model_dump"):
        try:
            dumped = arguments.model_dump()
            items = [(str(k), v) for k, v in dumped.items()]
        except Exception:
            items = []
    shown = [f"{_short(k)}={_short(v)}" for k, v in items if v not in (None, "")]
    return " ".join(shown)[:_TRIED_CHARS]


def stop_text(tried: Sequence[str]) -> str:
    """The answer of a stopped call. Simplified Technical English."""
    listed = ", ".join(f'"{t}"' for t in tried[:_TRIED_LISTED] if t) or "nothing that helped"
    return (
        f"Stop. You tried: {listed}. "
        "Tell the member what you tried and ask one question."
    )


class ToolTurnBudget:
    """Counts the empty or refused answers of each read tool in ONE turn.

    One instance serves one turn of one run, so a counter is never shared
    across runs, members or organizations. A call that is not a read, and a
    call with a real answer, never counts. A real answer does not reset the
    count: the spec says "3 empty or refused answers from one tool in a turn".
    """

    def __init__(self, limit: int = TOOL_TURN_LIMIT) -> None:
        self.limit = limit
        self._tried: dict[str, list[str]] = {}

    def stop_for(self, name: str) -> str | None:
        """The stop text when *name* has used its budget, else None."""
        tried = self._tried.get(name or "tool")
        if tried is not None and len(tried) >= self.limit:
            return stop_text(tried)
        return None

    def record(self, name: str, arguments: object, answer: object) -> bool:
        """Note one answer. Returns True when it counted as empty or refused."""
        if not is_empty_or_refused(answer):
            return False
        key = name or "tool"
        if key not in self._tried and len(self._tried) >= _MAX_TOOLS:
            return False
        tried = self._tried.setdefault(key, [])
        if len(tried) < self.limit:
            tried.append(tried_form(arguments))
        return True


def default_watchdog() -> WatchdogPolicy:
    """Build the process watchdog policy from the environment (read once).

    Env knobs (unchanged names, for backward compat):
      NATIVE_STREAM_IDLE_TIMEOUT_SECONDS   → idle          (default 120)
      NATIVE_TOOL_IDLE_TIMEOUT_SECONDS     → tool_open     (default 600)
      HITL_IDLE_TIMEOUT_SECONDS            → hitl_pending  (default 3600)
      COPILOT_TOOL_TIMEOUT_SECONDS         → tool_execution(default 300)
    """
    return WatchdogPolicy(
        idle=_env_float("NATIVE_STREAM_IDLE_TIMEOUT_SECONDS", 120.0),
        tool_open=_env_float("NATIVE_TOOL_IDLE_TIMEOUT_SECONDS", 600.0),
        hitl_pending=_env_float("HITL_IDLE_TIMEOUT_SECONDS", 3600.0),
        tool_execution=_env_float("COPILOT_TOOL_TIMEOUT_SECONDS", 300.0),
    )
