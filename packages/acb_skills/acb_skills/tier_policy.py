"""The AI tier policy — which Router tier serves each step. WS-45, D90.

Spec: ``project-docs/specs/ai_tier_routing.md``.

S1 built two parts of this module:

* :func:`tier_routing_on`, the ONE reader of ``AI_TIER_ROUTING`` (§9). It
  names the agents that the policy covers. It fails closed.
* The System-1 tier and its threshold per effort (§5, Q7).

S2 adds the rest:

* The table of §4.1 (:data:`KIND_TIERS`), the tool hints of §4.4
  (:data:`TOOL_HINTS`) and the effort mapping of §5, in :func:`choose`.
* The turn-kind question of §4.3 (:func:`turn_kind`). It asks the System-1
  agent once, on ``tier-fast``, and waits at most 1.5 s.
* :class:`TierPolicyMiddleware`, which sets ``options["model"]`` on each
  model request of ONE run, and :class:`TierPolicyProvider`, which adds it
  to the run's own view of the agent (§4.5). The executor attaches it, the
  same way it attaches ``EgressGuardProvider``.

🔴 **One home for the policy.** §4.1 puts the table here, and nothing else
holds a second copy. Add a rung here, never at a call site.

🔴 **The policy never touches a tool.** It sets the model of a request and
nothing else. So no tool's egress class changes, and the H-236 pins
(``COVERED_PROJECTS_TOOLS``) stay as they are.

🔴 **No tenant text in a log line or an event.** ``ai_route.chosen`` and the
``ai.route`` event hold the agent, the run, the tier, the kind and the
reason. Nothing here logs the member's message or a tool's output.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, ContextProvider, FunctionMiddleware

from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.tier_policy")

# ── The tiers ────────────────────────────────────────────────────────────────

TIER_FAST = "tier-fast"
TIER_BALANCED = "tier-balanced"
TIER_POWERFUL = "tier-powerful"

#: The only tiers the policy chooses among, from lowest to highest (§4.2
#: rule 3). Code goes to ``tier-powerful``, not ``tier-code`` (§13 Q2).
LADDER: tuple[str, ...] = (TIER_FAST, TIER_BALANCED, TIER_POWERFUL)

#: The tier that serves every System-1 request, in every effort mode (§5).
#: No setting moves it. The owner put System 1 on the fast tier.
SYSTEM_ONE_TIER = TIER_FAST

#: The tier of every main request under Max (§5).
MAX_TIER = TIER_POWERFUL

#: The agent's own default when it declares none (D-AI-4).
DEFAULT_TIER = TIER_BALANCED

# ── The effort modes (§5, Q3: three labels) ─────────────────────────────────

EFFORTS: tuple[str, ...] = ("auto", "thinking", "max")

#: The confidence under which System 1 hands a decision back to the main
#: model (§5, Q7). A higher effort hands back more decisions. That is how
#: effort reaches a decision, because the tier of a decision never changes.
SYSTEM_ONE_THRESHOLDS: dict[str, float] = {
    "auto": 0.70,
    "thinking": 0.80,
    "max": 0.90,
}

# ── The table of record (§4.1) ──────────────────────────────────────────────

#: The kinds of work of a turn (§4.3). ``chat`` is the default kind.
TURN_KINDS: tuple[str, ...] = ("chat", "code", "plan", "analysis")

#: Kind of work → tier (§4.1). ``None`` means the agent's own default
#: (D-AI-4), so a ``chat`` turn never moves (§4.2 rule 2).
KIND_TIERS: dict[str, str | None] = {
    "chat": None,
    "code": TIER_POWERFUL,
    "plan": TIER_POWERFUL,
    "analysis": TIER_POWERFUL,
}

#: Tool name → kind (§4.4). After the model calls one of these, the NEXT
#: model request of the turn goes to the kind's tier, because that request
#: reads the tool's output and writes the answer. Each name is a real tool:
#: ``test_tier_policy.py`` fails on a name that no tool registry holds.
TOOL_HINTS: dict[str, str] = {
    # Code: the sandbox shell, and the host code tools (D85 withholds those
    # from a shared agent, so they act for a personal agent only).
    "run_command": "code",
    "code_task": "code",
    "run_script": "code",
    # A plan of many steps (skill-projects).
    "propose_plan": "plan",
    "rebalance": "plan",
    # Hard analysis (skill-projects): the on-the-fly table, the forecast and
    # the conflict read with many parts.
    "task_dataset": "analysis",
    "analytics_outlook": "analysis",
    "find_conflicts": "analysis",
}

#: Why a request got its tier. ``ai_route.chosen`` carries one of these.
REASONS: tuple[str, ...] = ("default", "turn_kind", "tool_hint", "effort")

# ── The turn-kind question (§4.3) ───────────────────────────────────────────

#: The longest the executor waits for the turn kind. A slower answer is
#: ``chat``, so the turn stays on the default.
TURN_KIND_TIMEOUT_S = 1.5

#: A message with fewer words skips the question and reads as ``chat``.
SHORT_MESSAGE_WORDS = 12

#: The most characters of the member's message that the question sends.
TURN_MESSAGE_MAX = 4000

#: The most tool names that the question sends.
TURN_TOOLS_MAX = 200

TURN_QUESTION = (
    "What kind of work does the member's message ask for? "
    "chat: answer, read, look up or summarise. "
    "code: write, run or fix code. "
    "plan: plan many steps, schedule or rebalance work. "
    "analysis: a hard analysis, a forecast or a conflict read with many parts."
)

#: The ``ai.route`` AG-UI custom event (§7.2). S3 folds these into a label.
ROUTE_EVENT = "ai.route"

#: The context provider's source id.
SOURCE_ID = "metorite-tier-policy"

# ── The flag (§9) ────────────────────────────────────────────────────────────

#: The value of ``AI_TIER_ROUTING`` that covers every agent.
ALL_AGENTS = "*"


def covered_agents() -> frozenset[str]:
    """The agent names that ``AI_TIER_ROUTING`` lists. Empty means OFF.

    A broken settings read reads as empty, so it covers no agent.
    """
    try:
        from acb_common import get_settings

        raw = str(get_settings().ai_tier_routing or "")
    except Exception:  # a broken settings read must not arm the policy
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def tier_routing_on(agent: str | None) -> bool:
    """True when the tier policy covers *agent*. ONE reader, and it fails closed.

    The value is a comma list of agent names, or ``*`` for every agent. An
    empty value, an empty agent name and any error read as OFF. The idiom is
    ``executor._native_sessions_enabled``.
    """
    name = str(agent or "").strip()
    if not name:
        return False
    try:
        names = covered_agents()
        return ALL_AGENTS in names or name in names
    except Exception:  # never arm the policy on a fault
        _log.warning("tier_policy.flag_read_failed")
        return False


def normalise_effort(effort: str | None) -> str:
    """``auto``, ``thinking`` or ``max``. Anything else reads as ``auto``."""
    key = str(effort or "").strip().lower()
    return key if key in EFFORTS else "auto"


def system_one_threshold(effort: str | None) -> float:
    """The System-1 threshold for the run's effort mode (§5).

    *effort* is the run's ``think_mode``: ``auto``, ``thinking`` or ``max``.
    An unknown or absent value reads as ``auto``, as the gateway reads it.
    """
    return SYSTEM_ONE_THRESHOLDS[normalise_effort(effort)]


# ── The policy ───────────────────────────────────────────────────────────────


def _rank(tier: str) -> int | None:
    return LADDER.index(tier) if tier in LADDER else None


def _at_least(floor: str, tier: str) -> str:
    """*tier*, but never below *floor* (§4.2 rule 2).

    A *floor* off the ladder (for example ``tier-code`` or a ``provider/model``
    that an admin set) cannot be compared, so a raise to *tier* wins.
    """
    low, high = _rank(floor), _rank(tier)
    if low is None or high is None:
        return tier
    return LADDER[max(low, high)]


def rung_up(tier: str) -> str:
    """The rung above *tier*. The top rung, and a tier off the ladder, stay."""
    rank = _rank(tier)
    if rank is None:
        return tier
    return LADDER[min(rank + 1, len(LADDER) - 1)]


def tier_for_kind(kind: str, default: str) -> str:
    """The tier of a *kind* of work, never below the agent's *default*."""
    target = KIND_TIERS.get(kind)
    return default if target is None else _at_least(default, target)


def hint_kind(names: Iterable[str]) -> str | None:
    """The kind of the strongest hint among the tools *names*, else ``None``."""
    best: str | None = None
    best_rank = -1
    for name in names:
        kind = TOOL_HINTS.get(str(name or ""))
        if kind is None:
            continue
        rank = _rank(KIND_TIERS.get(kind) or "") or 0
        if rank > best_rank:
            best, best_rank = kind, rank
    return best


@dataclass(frozen=True)
class Choice:
    """The tier of one model request, with the kind and the reason."""

    tier: str
    kind: str
    reason: str


def choose(
    *, default: str, kind: str, effort: str | None, hint: str | None = None,
) -> Choice:
    """The tier of one main model request (§4.1, §4.4 and §5).

    * Max: every main request goes to ``tier-powerful`` (reason ``effort``).
    * Otherwise the turn's *kind* sets the tier, never below *default*.
    * *hint* is the kind of a hinted tool that the model called just before
      this request. It raises THIS request only.
    * Thinking: in a ``chat`` turn, a request after a hinted tool goes at
      least one rung above the turn's tier (reason ``effort``).

    A System-1 request never comes here. Its tier is fixed (§5).
    """
    kind = kind if kind in TURN_KINDS else "chat"
    mode = normalise_effort(effort)
    if mode == "max":
        return Choice(MAX_TIER, kind, "effort")
    base = tier_for_kind(kind, default)
    reason = "default" if base == default else "turn_kind"
    if hint is None:
        return Choice(base, kind, reason)
    raised = tier_for_kind(hint, base)
    if mode == "thinking" and kind == "chat":
        stepped = _at_least(raised, rung_up(base))
        if stepped != raised:
            return Choice(stepped, kind, "effort")
    if raised != base:
        return Choice(raised, kind, "tool_hint")
    return Choice(base, kind, reason)


# ── The turn kind (§4.3) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnKind:
    """The kind of one turn, and how it was found.

    ``source`` is ``system_one``, ``short``, ``max``, ``unsure``,
    ``timeout``, ``unavailable`` or ``error``. ``latency_ms`` is the time of
    the System-1 question, or ``None`` when no question went out.
    """

    kind: str
    source: str
    latency_ms: int | None = None


def _turn_context(message: str, tools: Iterable[str]) -> str:
    names = sorted({str(n) for n in tools if n})[:TURN_TOOLS_MAX]
    return (
        "Member message:\n" + str(message or "")[:TURN_MESSAGE_MAX]
        + "\n\nTools the agent holds: " + (", ".join(names) or "none")
    )


async def turn_kind(message: str, tools: Iterable[str], effort: str | None) -> TurnKind:
    """Ask System 1 the kind of this turn. ONE ``tier-fast`` request, or none.

    The question sends the member's last message and the names of the tools
    the agent holds, and no earlier history (§4.3). It never raises.

    * Max reads every main request as ``tier-powerful``, so the answer would
      change nothing. No question goes out.
    * A message under :data:`SHORT_MESSAGE_WORDS` words is ``chat``, with no
      question.
    * A confidence under the threshold of the effort, a timeout of
      :data:`TURN_KIND_TIMEOUT_S` and a System-1 failure are ``chat``.
    """
    mode = normalise_effort(effort)
    if mode == "max":
        return TurnKind("chat", "max")
    if len(str(message or "").split()) < SHORT_MESSAGE_WORDS:
        return TurnKind("chat", "short")
    from acb_skills import system_one

    item = system_one.Item(
        id="turn", question=TURN_QUESTION, kind="choice", options=TURN_KINDS,
    )
    # So the 1.5 s counts the exchange, not an import. An import fault reads
    # as no answer, below.
    with contextlib.suppress(Exception):
        system_one.warm()
    started = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        answers = await asyncio.wait_for(
            system_one.ask(
                _turn_context(message, tools), [item],
                timeout_s=TURN_KIND_TIMEOUT_S,
            ),
            timeout=TURN_KIND_TIMEOUT_S,
        )
    except TimeoutError:
        return TurnKind("chat", "timeout", _ms())
    except system_one.SystemOneUnavailable:
        return TurnKind("chat", "unavailable", _ms())
    except Exception as exc:  # the question never breaks a turn
        _log.warning("tier_policy.turn_kind_failed", error_type=type(exc).__name__)
        return TurnKind("chat", "error", _ms())
    answer = answers[0] if answers else None
    if (
        answer is None
        or answer.choice not in TURN_KINDS
        or answer.confidence is None
        or answer.confidence < system_one_threshold(mode)
    ):
        return TurnKind("chat", "unsure", _ms())
    return TurnKind(answer.choice, "system_one", _ms())


# ── The per-run policy and its middleware (§4.5) ────────────────────────────

#: The callback that carries one ``ai.route`` event to the run's stream.
Emit = Callable[[dict[str, Any]], None]


def route_event(choice: Choice, request: int) -> dict[str, Any]:
    """The ``ai.route`` AG-UI custom event of one model request (§7.2).

    It holds a tier slug, never a model (D32.7), and no tenant text.
    """
    return {
        "type": "CUSTOM",
        "name": ROUTE_EVENT,
        "value": {
            "tier": choice.tier, "kind": choice.kind,
            "reason": choice.reason, "request": request,
        },
    }


@dataclass
class RunTierPolicy:
    """The tier policy of ONE run. The executor builds one per run.

    It holds the agent's *default* tier, the turn's *kind* and the run's
    *effort*. :meth:`next_choice` gives the tier of the next main request,
    and :meth:`note_tool` records a tool that the model called.
    """

    agent: str
    run_id: str
    default: str
    kind: str
    effort: str
    emit: Emit | None = None
    _called: list[str] = field(default_factory=list)
    _requests: int = 0

    def run_choice(self) -> Choice:
        """The choice for the whole run, with no hint."""
        return choose(default=self.default, kind=self.kind, effort=self.effort)

    def run_tier(self) -> str:
        """The tier of the whole run, with no hint (§4.5).

        The executor sets it once, as the run's model. A native agent's
        middleware then sets each request. A Copilot SDK agent cannot switch
        per request, so it keeps this tier for the whole run.
        """
        return self.run_choice().tier

    def announce_run(self) -> dict[str, Any]:
        """Log the whole-run choice once, and give its ``ai.route`` event.

        For a Copilot SDK agent, which makes its requests out of reach of a
        MAF middleware. The executor yields the event on the run's stream.
        """
        choice = self.run_choice()
        self.record(choice)
        return route_event(choice, self._requests)

    def note_tool(self, name: str) -> None:
        """Record a tool call. The next request reads it, once."""
        self._called.append(str(name or ""))

    def next_choice(self) -> Choice:
        """The tier of the next main request. It takes the recorded calls."""
        hint = hint_kind(self._called)
        self._called.clear()
        return choose(default=self.default, kind=self.kind, effort=self.effort, hint=hint)

    def record(self, choice: Choice) -> None:
        """Log ``ai_route.chosen`` and emit ``ai.route`` for one request."""
        self._requests += 1
        _log.info(
            "ai_route.chosen",
            agent=self.agent, run_id=self.run_id, tier=choice.tier,
            kind=choice.kind, reason=choice.reason, request=self._requests,
        )
        if self.emit is None:
            return
        with contextlib.suppress(Exception):  # the label never breaks a request
            self.emit(route_event(choice, self._requests))


class TierPolicyMiddleware(ChatMiddleware):
    """Sets ``options["model"]`` on each main model request of ONE run.

    It changes a copy of the request's options, never an agent object.
    """

    def __init__(self, policy: RunTierPolicy) -> None:
        self._policy = policy

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        choice = self._policy.next_choice()
        options = context.options if isinstance(context.options, dict) else {}
        context.options = {**options, "model": choice.tier}
        self._policy.record(choice)
        await call_next()


class ToolHintRecorder(FunctionMiddleware):
    """Records the name of each tool the model calls, for the next request."""

    def __init__(self, policy: RunTierPolicy) -> None:
        self._policy = policy

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        function = getattr(context, "function", None)
        if function is not None:
            self._policy.note_tool(tool_name(function))
        await call_next()


class TierPolicyProvider(ContextProvider):
    """Adds the two middlewares above to ONE run of a native MAF agent.

    The executor attaches it to the run's own view of the agent
    (``agent_with_providers``), never to a shared agent object.
    """

    def __init__(self, policy: RunTierPolicy) -> None:
        super().__init__(SOURCE_ID)
        self.policy = policy

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(
            SOURCE_ID,
            [TierPolicyMiddleware(self.policy), ToolHintRecorder(self.policy)],
        )
