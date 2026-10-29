"""The AI tier policy — which Router tier serves each step. WS-45, D90.

Spec: ``project-docs/specs/ai_tier_routing.md``.

S1 built two parts of this module:

* :func:`tier_routing_on`, the ONE reader of ``AI_TIER_ROUTING`` (§9). It
  names the agents that the policy covers. It fails closed.
* The System-1 tier and its threshold per effort (§5, Q7).

The amendment of 2026-10-07 (owner) adds one part:

* The member chooses no effort in the chat. The turn-kind question also asks
  whether the member explicitly asks for deep, careful or thorough work, in
  the SAME request. A yes at or above the threshold makes the run's effort
  ``thinking``, never ``max``. A short message makes no request, and a cheap
  keyword check (:func:`asks_for_depth`) reads it instead.

S2 adds the rest:

* The table of §4.1 (:data:`KIND_TIERS`), the tool hints of §4.4
  (:data:`TOOL_HINTS`) and the effort mapping of §5, in :func:`choose`.

The owner's amendment of 2026-10-09 (one model per turn, amends §4.4):

* A turn picks its tier ONCE, at the start, from the turn kind and the
  effort (Max, or Thinking from the member's words). Every main request of
  the turn keeps that tier, so the prompt cache of the vendor stays warm.
* A hinted tool no longer raises the NEXT request. :class:`ToolHintRecorder`
  logs ``ai_route.hint_ignored`` for tuning, and the tier stays.
* The turn-kind question of §4.3 (:func:`turn_kind`). It asks the System-1
  agent once, on ``tier-fast``, and waits at most :data:`TURN_KIND_TIMEOUT_S`.
* :class:`TierPolicyMiddleware`, which sets ``options["model"]`` on each
  model request of ONE run, and :class:`TierPolicyProvider`, which adds it
  to the run's own view of the agent (§4.5). The executor attaches it, the
  same way it attaches ``EgressGuardProvider``.

The parallel start (chat speed, 2026-10-30, §4.3), dark behind
``AI_TURN_KIND_PARALLEL`` (:func:`turn_kind_parallel_on`):

* The first request of a member's web chat turn starts as a provisional
  ``chat`` turn, and the turn-kind question runs beside it.
  :meth:`RunTierPolicy.settle` takes the answer. The request goes on when
  the answer moves nothing. When it moves the tier or the reasoning effort,
  and no output has left the run, the executor cancels the first request
  and starts the turn again, ONE time.
* :meth:`RunTierPolicy.seal` closes that window. The executor calls it when
  the first output leaves the run, and :class:`ToolHintRecorder` calls it
  when a tool starts. A cancelled request sent nothing to the member, so the
  turn still has one model (the box in §4.4).

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
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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

#: Tool name → kind (§4.4). Since the owner's amendment of 2026-10-09, a
#: hint does NOT move the tier of the turn. A call of one of these tools logs
#: ``ai_route.hint_ignored`` with the tier the hint would have chosen, so the
#: table stays the record for tuning the turn kind. Each name is a real tool:
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
    # WS-48 N1 (data_narrowing_pipeline.md §3.6): the READ step. The next
    # request reads the kept items in full and writes the answer.
    "narrow_and_read": "analysis",
}

#: Why a request got its tier. ``ai_route.chosen`` carries one of these.
#: ``tool_hint`` left on 2026-10-09: a hint no longer sets a tier.
REASONS: tuple[str, ...] = ("default", "turn_kind", "effort", "context")

#: The log line of a hinted tool that the turn did not follow (2026-10-09).
HINT_IGNORED = "ai_route.hint_ignored"

# ── The turn-kind question (§4.3) ───────────────────────────────────────────

#: The longest the executor waits for the turn kind. A slower answer is
#: ``chat``, so the turn stays on the default. 3.0 s since 2026-10-22:
#: production measured the answers at 1.1 to 1.5 s, so a 1.5 s wait timed out
#: on 41 of 80 turns (51%), and those turns could never go powerful. A short
#: message asks no question, so it waits for nothing.
TURN_KIND_TIMEOUT_S = 3.0

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
    "analysis: a hard analysis, a forecast or a conflict read with many parts, "
    "a comparison of options or offers with a choice, or a reply or a "
    "document that needs judgement."
)

# ── The size of the turn (owner, 2026-10-22) ─────────────────────────────────

#: A turn whose history and context hold at least this many tokens starts on
#: :data:`TIER_POWERFUL` (reason ``context``). The owner asked that a long
#: thread or a lot of context switch to the powerful tier, and that ordinary
#: turns stay on fast or balanced. The size is known before the first
#: request, so the turn still picks ONE model (2026-10-09).
CONTEXT_POWERFUL_TOKENS = 24_000

#: Characters for each token when the turn's size is counted. 3, not 4, so a
#: language that tokenizes worse than English still counts as long.
CONTEXT_CHARS_PER_TOKEN = 3


def turn_tokens(message: str | None, history: Iterable[Any] | None = (),
                extra: str | None = "") -> int:
    """About how many tokens the turn starts with: the member's message, the
    earlier messages of the chat and any added context. Never raises."""
    total = len(str(message or "")) + len(str(extra or ""))
    for item in history or ():
        content = item.get("content") if isinstance(item, dict) else item
        total += len(content) if isinstance(content, str) else len(str(content or ""))
    return total // CONTEXT_CHARS_PER_TOKEN

# ── The effort from the member's words (owner, 2026-10-07) ───────────────────

#: The effort that an explicit request for deep work gives. The existing
#: ``thinking`` state, NEVER ``max``: the member's words raise the effort one
#: step, and no words make a turn cost the Max rate.
TEXT_EFFORT = "thinking"

#: The second item of the turn-kind request. It goes in the SAME request as
#: :data:`TURN_QUESTION`, because one ``system_one.ask`` call is one request.
EFFORT_QUESTION = (
    "Does the member explicitly ask for deep, careful or thorough work, for "
    "example 'think hard', 'be thorough', 'take your time' or 'a detailed "
    "analysis'? Answer no when the message only asks for something, however "
    "large."
)

#: The explicit phrases that the keyword check finds in a SHORT message, with
#: no model. A short message sends no request, so this is all it gets. A bare
#: "thoroughly" does not count ("I thoroughly enjoyed it"). It counts after a
#: verb of work.
DEPTH_PHRASES = re.compile(
    r"\b(?:"
    r"think(?:ing)?\s+(?:hard|harder|deeply|deeper|carefully|it\s+through)"
    r"|be\s+(?:very\s+|extra\s+|really\s+)?(?:thorough|careful|meticulous)"
    r"|(?:check|review|read|test|analy[sz]e|examine|investigate|go\s+through)"
    r"\s+(?:\w+\s+){0,3}?thoroughly"
    r"|an?\s+(?:very\s+)?thorough"
    r"|take\s+your\s+time"
    r"|detailed\s+analysis"
    r"|in[-\s]depth"
    r"|deep\s+dive"
    r")\b",
    re.IGNORECASE,
)

#: The words before a phrase that turn it into its opposite ("no need to be
#: thorough", "do not think hard"). They count within two words of it.
_NEGATION = re.compile(
    r"\b(?:no\s+need\s+to|needn'?t|don'?t|do\s+not|not|never|without)"
    r"\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)


def asks_for_depth(message: str | None) -> bool:
    """True when *message* holds an explicit request for deep work.

    The cheap check, with no model, for a message too short for the turn-kind
    request. It reads the first :data:`TURN_MESSAGE_MAX` characters only. A
    phrase right after a negation does not count.
    """
    text = str(message or "")[:TURN_MESSAGE_MAX]
    for match in DEPTH_PHRASES.finditer(text):
        if not _NEGATION.search(text[max(0, match.start() - 40):match.start()]):
            return True
    return False

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


#: The ``reasoning_effort`` that turns a DeepSeek model's thinking off. litellm
#: sends it as ``thinking: {type: disabled}``. Any other value turns thinking
#: ON, so "low" is not a shorter think.
REASONING_OFF = "none"


def reasoning_off(agent: str | None, *, kind: str, effort: str | None) -> bool:
    """True when a turn of *agent* runs with the model's thinking off.

    Only an ``auto`` turn of kind ``chat``, for an agent that
    ``AI_REASONING_OFF_AGENTS`` names (a comma list, or ``*``). Thinking,
    Max, and a ``plan``, ``analysis`` or ``code`` turn keep thinking. A
    pinned turn reads as ``chat``, so do not list an agent whose default
    tier cannot move until it measures well. Any fault reads as off.
    """
    name = str(agent or "").strip()
    if not name or kind != "chat" or normalise_effort(effort) != "auto":
        return False
    try:
        from acb_common import get_settings

        raw = str(get_settings().ai_reasoning_off_agents or "")
    except Exception:  # never change a request on a fault
        return False
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return ALL_AGENTS in names or name in names


def turn_kind_parallel_on(agent: str | None) -> bool:
    """True when the first request of *agent*'s web chat turn starts before
    the turn-kind answer (§4.3, 2026-10-30). ONE reader of
    ``AI_TURN_KIND_PARALLEL``, and it fails closed.

    The value is a comma list of agent names, or ``*``. Empty, an empty
    agent name and any fault read as OFF. Only a run that
    :func:`tier_routing_on` covers asks a turn kind, so the executor reads
    this flag for such a run alone.
    """
    name = str(agent or "").strip()
    if not name:
        return False
    try:
        from acb_common import get_settings

        raw = str(get_settings().ai_turn_kind_parallel or "")
    except Exception:  # a broken settings read must not start a turn early
        return False
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return ALL_AGENTS in names or name in names


def tier_routing_on(agent: str | None) -> bool:
    """True when the tier policy covers *agent*. ONE reader, and it fails closed.

    The value is a comma list of agent names, or ``*`` for every agent. An
    empty value, an empty agent name and any error read as OFF. An internal
    agent reads as OFF too, whatever the value. The idiom is
    ``executor._native_sessions_enabled``.
    """
    name = str(agent or "").strip()
    if not name:
        return False
    try:
        # An internal agent (the spawn worker) never takes the policy, also
        # under ``*``. Its tier is the job tier (OA-9b hardening item 3).
        from acb_skills.subagent_contract import INTERNAL_AGENTS

        if name in INTERNAL_AGENTS:
            return False
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
    that an admin set) cannot be compared with a rung. The policy then keeps
    *floor*, so it never moves a request off the admin's choice.
    """
    low, high = _rank(floor), _rank(tier)
    if low is None:
        return floor
    if high is None:
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


def kind_can_move(default: str) -> bool:
    """True when the turn's kind can change the tier of an agent whose
    default is *default*.

    :func:`choose` never goes below *default*. So the top rung cannot move,
    and a tier off the ladder (an admin's ``provider/model``) cannot move
    either. For such an agent the System-1 question buys nothing.
    """
    rank = _rank(default)
    return rank is not None and rank < len(LADDER) - 1


@dataclass(frozen=True)
class Choice:
    """The tier of one model request, with the kind and the reason."""

    tier: str
    kind: str
    reason: str


def choose(*, default: str, kind: str, effort: str | None,
           context_tokens: int = 0) -> Choice:
    """The tier of a TURN: every main model request of it (§4.1 and §5).

    One model per turn (owner, 2026-10-09, amends §4.4). The tier is set
    once, at the start of the turn, and no tool call moves it:

    * Max: ``tier-powerful`` for the whole turn (reason ``effort``).
    * Otherwise the turn's *kind* sets the tier, never below *default*:
      ``plan``, ``analysis`` and ``code`` take the stronger tier, and
      ``chat`` keeps the agent's default.
    * Thinking (from the API, or from the member's words) starts the turn
      one rung above the agent's *default*, and the turn keeps it (owner,
      2026-10-09). A ``plan``, ``analysis`` or ``code`` turn may already
      start higher, so the turn takes the higher of the two. A tier off the
      ladder stays (§4.2 rule 2). Thinking also sets ``reasoning_effort`` and
      the System-1 threshold, as before.
    * Auto: the kind's tier, as above.

    A System-1 request never comes here. Its tier is fixed (§5).
    """
    kind = kind if kind in TURN_KINDS else "chat"
    mode = normalise_effort(effort)
    if mode == "max":
        return Choice(_at_least(default, MAX_TIER), kind, "effort")
    base = tier_for_kind(kind, default)
    # A long turn (owner, 2026-10-22): a long thread, many files or a long
    # chat. The powerful tier reads it whole and keeps it straight.
    if context_tokens >= CONTEXT_POWERFUL_TOKENS:
        long = _at_least(base, TIER_POWERFUL)
        if long != base:
            return Choice(long, kind, "context")
    if mode == "thinking":
        stepped = _at_least(base, rung_up(default))
        if stepped != base:
            return Choice(stepped, kind, "effort")
    return Choice(base, kind, "default" if base == default else "turn_kind")


# ── The turn kind (§4.3) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnKind:
    """The kind of one turn, and how it was found.

    ``source`` is ``system_one``, ``short``, ``max``, ``pinned``, ``unsure``,
    ``timeout``, ``unavailable`` or ``error``. ``latency_ms`` is the time of
    the System-1 question, or ``None`` when no question went out. ``pinned``
    means the agent's default tier cannot move (:func:`kind_can_move`).

    ``effort`` is :data:`TEXT_EFFORT` when the member's words explicitly ask
    for deep work, else ``None`` (the run keeps its effort). ``effort_source``
    is ``system_one`` or ``keyword``, or ``None`` with no effort.
    """

    kind: str
    source: str
    latency_ms: int | None = None
    effort: str | None = None
    effort_source: str | None = None
    #: OA-7f. The slugs of the tool packs that System 1 said this turn needs,
    #: at or above the threshold, in the order asked. Empty when the request
    #: held no pack item, and after a timeout or a failure.
    packs: tuple[str, ...] = ()


#: The id of a pack item is this prefix and the slug of the pack. The ids
#: ``turn`` and ``effort`` never start with it.
PACK_ITEM_PREFIX = "pack:"

PACK_QUESTION = (
    "Does the member's message need the {label} tools ({summary})? "
    "Answer yes only when the message asks to read, look up, change or send "
    "something with them. Answer no for a question that needs none of them."
)


def pack_items(slugs: Iterable[str]) -> list[Any]:
    """One ``yes_no`` System-1 item for each pack of *slugs* (OA-7f).

    A slug that is no pack, or twice named, gives no item. The caller keeps
    the room for the two items that the request holds already."""
    from acb_skills import system_one
    from acb_skills.skill_families import SKILL_FAMILIES, is_pack

    items: list[Any] = []
    for slug in dict.fromkeys(str(s) for s in slugs or ()):
        if not is_pack(slug):
            continue
        family = SKILL_FAMILIES[slug]
        items.append(system_one.Item(
            id=PACK_ITEM_PREFIX + slug,
            question=PACK_QUESTION.format(
                label=family.get("label") or slug, summary=family.get("summary") or slug),
            kind="yes_no", options=system_one.YES_NO,
        ))
    return items


def _turn_context(message: str, tools: Iterable[str]) -> str:
    names = sorted({str(n) for n in tools if n})[:TURN_TOOLS_MAX]
    return (
        "Member message:\n" + str(message or "")[:TURN_MESSAGE_MAX]
        + "\n\nTools the agent holds: " + (", ".join(names) or "none")
    )


async def turn_kind(
    message: str,
    tools: Iterable[str],
    effort: str | None,
    *,
    read_effort: bool = False,
    default: str | None = None,
    packs: Iterable[str] = (),
) -> TurnKind:
    """Ask System 1 the kind of this turn. ONE ``tier-fast`` request, or none.

    The question sends the member's last message and the names of the tools
    the agent holds, and no earlier history (§4.3). It never raises.

    * Max reads every main request as ``tier-powerful``, so the answer would
      change nothing. No question goes out.
    * A message under :data:`SHORT_MESSAGE_WORDS` words is ``chat``, with no
      question.
    * A confidence under the threshold of the effort, a timeout of
      :data:`TURN_KIND_TIMEOUT_S` and a System-1 failure are ``chat``.

    *read_effort* (owner, 2026-10-07): the executor passes it for the
    member's own turn. Under ``auto``, the same request then holds a second
    item, :data:`EFFORT_QUESTION`. A ``yes`` at or above the threshold sets
    ``effort`` to :data:`TEXT_EFFORT`. A short message sends no request, and
    :func:`asks_for_depth` reads it instead. A timeout or a failure reads as
    no effort.

    *default* is the agent's default tier. When the kind cannot move it
    (:func:`kind_can_move`) and no effort item is asked, no question goes
    out, and the source is ``pinned``. A sub-agent run of the email
    assistant (``tier-powerful``) asked one for 1.4 to 2.6 s in production
    and could not use the answer (chat speed review, 2026-10-25).

    *packs* (OA-7f) names the tool packs that the run can still load. The
    same request then holds one ``yes_no`` item for each (:func:`pack_items`).
    A ``yes`` at or above the threshold puts the slug in
    :attr:`TurnKind.packs`. The items ride in the request that goes out
    anyway: a short turn, a Max turn and a pinned turn send none, and a
    timeout, a failure or a garbled answer loads nothing. A pack item never
    changes the kind, the effort or the source. With no *packs* the request
    is the one of before.
    """
    mode = normalise_effort(effort)
    if mode == "max":
        return TurnKind("chat", "max")
    # The member's words can only raise ``auto``. Thinking is already there.
    wants_effort = read_effort and mode == "auto"
    if len(str(message or "").split()) < SHORT_MESSAGE_WORDS:
        if wants_effort and asks_for_depth(message):
            return TurnKind("chat", "short", effort=TEXT_EFFORT, effort_source="keyword")
        return TurnKind("chat", "short")
    if default is not None and not wants_effort and not kind_can_move(default):
        return TurnKind("chat", "pinned")
    from acb_skills import system_one

    item = system_one.Item(
        id="turn", question=TURN_QUESTION, kind="choice", options=TURN_KINDS,
    )
    # One request holds both items: ``ask`` sends a batch as ONE request.
    items = [item]
    if wants_effort:
        items.append(system_one.Item(
            id="effort", question=EFFORT_QUESTION, kind="yes_no",
            options=system_one.YES_NO,
        ))
    extra = pack_items(packs)[: system_one.MAX_ITEMS - len(items)] if packs else []
    items.extend(extra)
    # So the wait counts the exchange, not an import. An import fault reads
    # as no answer, below.
    with contextlib.suppress(Exception):
        system_one.warm()
    started = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        answers = await asyncio.wait_for(
            system_one.ask(
                _turn_context(message, tools), items,
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
    threshold = system_one_threshold(mode)
    by_id = {a.id: a for a in answers or []}
    answer = by_id.get("turn")
    deep = by_id.get("effort")
    # The items stand alone: an unsure kind keeps a sure effort.
    text_effort: dict[str, Any] = {}
    said = tuple(
        item.id[len(PACK_ITEM_PREFIX):] for item in extra
        if (a := by_id.get(item.id)) is not None and a.choice == "yes"
        and a.confidence is not None and a.confidence >= threshold
    )
    if said:
        text_effort["packs"] = said
    if (
        wants_effort and deep is not None and deep.choice == "yes"
        and deep.confidence is not None and deep.confidence >= threshold
    ):
        text_effort.update(effort=TEXT_EFFORT, effort_source="system_one")
    if (
        answer is None
        or answer.choice not in TURN_KINDS
        or answer.confidence is None
        or answer.confidence < threshold
    ):
        return TurnKind("chat", "unsure", _ms(), **text_effort)
    return TurnKind(answer.choice, "system_one", _ms(), **text_effort)


# ── The per-run policy and its middleware (§4.5) ────────────────────────────

#: The callback that carries one ``ai.route`` event to the run's stream.
Emit = Callable[[dict[str, Any]], None]

# The states of a parallel start (§4.3, 2026-10-30), in RunTierPolicy.start.
#: The turn runs as a provisional ``chat`` turn, and nothing has left the run.
START_OPEN = "open"
#: Output left the run, or a tool started. The tier of the turn stays.
START_SEALED = "sealed"
#: The answer came in time and moved the turn. The executor starts it again.
START_MOVED = "moved"
#: The answer came in time and moved nothing.
START_KEPT = "kept"

#: What :meth:`RunTierPolicy.settle` gives back.
SETTLE_MOVED = "moved"
SETTLE_KEPT = "kept"
SETTLE_LATE = "late"


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
    which is the turn's tier for every request (one model per turn, owner
    2026-10-09). :meth:`note_tool` logs a hinted tool that the turn ignores.
    """

    agent: str
    run_id: str
    default: str
    kind: str
    effort: str
    emit: Emit | None = None
    _requests: int = 0
    #: The size of the turn at its start (:func:`turn_tokens`).
    context_tokens: int = 0
    #: OA-7f. The packs that the turn-kind answer asked for (:attr:`TurnKind.packs`).
    #: The policy only carries them. It reads them nowhere, so they cannot
    #: move the tier.
    packs: tuple[str, ...] = ()
    #: The parallel start (§4.3, 2026-10-30). ``""`` for every other run: the
    #: tier was final before the first request. Else one of the ``START_*``
    #: values. :meth:`open_start`, :meth:`seal` and :meth:`settle` move it.
    start: str = ""
    _opened_at: float = 0.0

    def open_start(self) -> None:
        """Start the turn as a provisional ``chat`` turn (§4.3, 2026-10-30).

        The executor builds this policy with kind ``chat`` and the run's own
        effort, before the turn-kind answer. It logs
        ``ai_route.parallel_start`` with the provisional tier. No tenant text.
        """
        self.start = START_OPEN
        self._opened_at = time.monotonic()
        _log.info(
            "ai_route.parallel_start", agent=self.agent, run_id=self.run_id,
            tier=self.run_tier(),
        )

    def seal(self) -> bool:
        """Close the window of a restart. True when this call closed it.

        The executor calls it when the first output leaves the run, and
        :class:`ToolHintRecorder` calls it when a tool starts. It has no
        ``await``, so the event loop runs it whole, and it cannot interleave
        with :meth:`settle`. A run with no parallel start never changes.
        """
        if self.start != START_OPEN:
            return False
        self.start = START_SEALED
        return True

    def request_shape(self) -> tuple[str, str | None, str]:
        """What the turn kind can change in a main request of this turn.

        The tier, the ``reasoning_effort`` that :class:`TierPolicyMiddleware`
        sets, and the effort. The executor turns ``thinking`` into
        ``reasoning_effort: medium``, so an effort that moves changes the
        request too. A change of the reasoning alone is a move (2026-10-30),
        because the request options differ. Such a restart keeps the model,
        so the vendor's cache of the prompt stays warm for it.
        """
        effort = normalise_effort(self.effort)
        off = reasoning_off(self.agent, kind=self.kind, effort=effort)
        return self.run_tier(), (REASONING_OFF if off else None), effort

    def settle(self, final: RunTierPolicy | None, *, fresh: bool = False) -> str:
        """Take the turn-kind answer of a parallel start. ONE call for each run.

        *final* is the policy that the answer gives, or None when no answer
        came (a fault). *fresh* says that no request of the turn is running:
        the stream has not opened yet, or Tier 1 failed before any output
        and Tier 2 starts the turn again. A fresh turn takes the packs of a
        kept answer too, because no tool list changes in a running turn. It
        returns one of three values:

        * :data:`SETTLE_MOVED`. The window is open, and the answer moves the
          request (:meth:`request_shape`). This policy takes the kind, the
          effort and the packs of *final*, and counts its requests from 1
          again. The executor cancels the first request and starts the turn
          again. The log line ``ai_route.restart`` holds the two tiers, the
          requests that went out on the old tier and the time since the
          start, so the cost of the cancelled request can be read.
        * :data:`SETTLE_KEPT`. The window is open, and the answer moves
          nothing. The turn goes on. Unless the turn is *fresh*, the packs of
          the answer do not load, and ``ai_route.pack_late`` says so.
        * :data:`SETTLE_LATE`. Output left the run first. The turn keeps its
          tier. ``ai_route.answer_late`` gives the tier that the answer
          would have chosen, and ``ai_route.pack_late`` the packs it drops.
        """
        elapsed = round((time.monotonic() - self._opened_at) * 1000) if self._opened_at else 0
        packs = tuple(getattr(final, "packs", ()) or ()) if final is not None else ()
        if self.start != START_OPEN:
            if final is not None:
                _log.info(
                    "ai_route.answer_late", agent=self.agent, run_id=self.run_id,
                    tier=self.run_tier(), would_tier=final.run_tier(), kind=final.kind,
                    moved=final.request_shape() != self.request_shape(), elapsed_ms=elapsed,
                )
            self._drop_packs(packs, "late")
            return SETTLE_LATE
        if final is None or final.request_shape() == self.request_shape():
            self.start = START_KEPT
            if fresh:
                self.packs = packs
            else:
                self._drop_packs(packs, "kept")
            return SETTLE_KEPT
        before, reasoning = self.run_tier(), self.request_shape()[1]
        sent = self._requests
        self.kind, self.effort, self.packs = final.kind, final.effort, packs
        self.start = START_MOVED
        self._requests = 0
        _log.info(
            "ai_route.restart", agent=self.agent, run_id=self.run_id,
            from_tier=before, to_tier=self.run_tier(), from_reasoning=reasoning,
            to_reasoning=self.request_shape()[1], effort=normalise_effort(self.effort),
            kind=self.kind, sent=sent, elapsed_ms=elapsed, packs=len(packs),
        )
        return SETTLE_MOVED

    def _drop_packs(self, packs: tuple[str, ...], reason: str) -> None:
        """Log the packs of an answer that load nothing. Never swap tools in a turn."""
        if packs:
            _log.info(
                "ai_route.pack_late", agent=self.agent, run_id=self.run_id,
                packs=list(packs), reason=reason,
            )

    def run_choice(self) -> Choice:
        """The choice for the whole turn. No tool call changes it."""
        return choose(default=self.default, kind=self.kind, effort=self.effort,
                      context_tokens=self.context_tokens)

    def run_tier(self) -> str:
        """The tier of the whole run (§4.5).

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

    def restart(self) -> None:
        """Count again from the first request, after a Tier 1 fault.

        The executor calls it when it falls back to Tier 2. Tier 2 then
        numbers its own requests from 1, and no step gets two numbers. The
        turn's tier stays, so the fallback keeps the same model.
        """
        self._requests = 0

    def note_tool(self, name: str) -> None:
        """Log a hinted tool call. It moves no tier (owner, 2026-10-09).

        ``ai_route.hint_ignored`` holds the tool name, the hint's kind, the
        turn's tier and the tier the hint would have chosen under D90. It is
        for tuning the turn kind. A tool with no hint logs nothing. No line
        holds tenant text.
        """
        tool = str(name or "")
        hint = TOOL_HINTS.get(tool)
        if hint is None:
            return
        turn = self.run_choice()
        would = turn.tier if turn.reason == "effort" else tier_for_kind(hint, turn.tier)
        _log.info(
            HINT_IGNORED, agent=self.agent, run_id=self.run_id, tool=tool,
            hint=hint, tier=turn.tier, would_tier=would, kind=turn.kind,
        )

    def next_choice(self) -> Choice:
        """The tier of the next main request: the turn's tier, always."""
        return self.run_choice()

    def reasoning_effort(self) -> str | None:
        """:data:`REASONING_OFF` for every request of a turn that runs with
        thinking off (:func:`reasoning_off`), else None. The turn decides
        once, so no request of it mixes thinking on and off: DeepSeek refused
        that with a 400 in the probe of 2026-09-24."""
        if not reasoning_off(self.agent, kind=self.kind, effort=self.effort):
            return None
        if self._requests == 0:
            _log.info("ai_route.reasoning_off", agent=self.agent, run_id=self.run_id)
        return REASONING_OFF

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

    Every request of the turn gets the SAME tier (one model per turn, owner
    2026-10-09). It changes a copy of the request's options, never an agent
    object.
    """

    def __init__(self, policy: RunTierPolicy) -> None:
        self._policy = policy

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        choice = self._policy.next_choice()
        options = context.options if isinstance(context.options, dict) else {}
        context.options = {**options, "model": choice.tier}
        # Chat speed: thinking off for the whole turn, never over an effort
        # that the request already carries.
        effort = self._policy.reasoning_effort()
        if effort is not None and "reasoning_effort" not in options:
            context.options["reasoning_effort"] = effort
        self._policy.record(choice)
        await call_next()


class ToolHintRecorder(FunctionMiddleware):
    """Logs each hinted tool the model calls. It moves no tier (2026-10-09).

    It also seals a parallel start (§4.3, 2026-10-30) before the tool runs.
    A tool can write or show a card, so a turn never starts again after one.
    """

    def __init__(self, policy: RunTierPolicy) -> None:
        self._policy = policy

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        self._policy.seal()
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
