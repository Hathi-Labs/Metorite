"""The ``decide`` platform tool — WS-31 CP-13d (D75).

Spec: ``project-docs/specs/customer_console.md`` §6A.14 "CP-13d".

A core-floor tool, so every MAF agent has it, the main chat included. It asks
ONE typed question through the one tenant facade, :func:`acb_llm.decide`, and
returns a short line the model reads: the answer and how sure it is.

🔴 **It never calls the Console client.** ``acb_llm.decide`` is the one seam
(CLAUDE.md §4). A second decide client here would be a defect.

🔴 **It sends NO member, on purpose (the R11 finding, §6A.14 CP-13d).** The
run's member comes from ``event_payload["user_email"]``, and three gateway
doors pass a caller's payload through unchanged (``/agent/run/stream``,
``/agent/run``, ``/agent/run/async``). The webhook door spreads the sender's
payload. A tool cannot tell a server-bound member from a claimed one. On the
deployment arm ``X-CC-Member`` SELECTS the tenant, so a claimed member would
be a tenant chosen by request input. So the tool sends none:

* the org arm names its organization by the key, and the call serves;
* the deployment arm refuses locally in the client, with no request, and the
  tool returns :data:`UNAVAILABLE`.

The tool still sends ``agent`` and ``run_id`` from the run context
(``acb_common.get_run_context``), which only attribute a call.

⚠️ **It never logs ``context`` or the question.** Both are tenant content.

## Two engines, ONE tool name (WS-45 S1, D90)

``ai_tier_routing.md`` §6. The tool keeps the name ``decide``, and the
injection chain picks its engine per agent (:func:`decide_tool_for`):

* an agent that ``AI_TIER_ROUTING`` covers gets :func:`system_one_decide`. It
  asks the ``system-one`` agent (``acb_skills.system_one``) on OUR Router's
  ``tier-fast``, and it takes a batch in ``items``;
* every other agent gets :func:`decide`, the Jev engine above, and only while
  ``DECIDE_ENABLED`` is on, as before.

The two callables carry the same name and different annotations.
:func:`system_one_decide` carries ``open_world=False`` on the function
(``__tool_risk__``), because its only destination is the Router that the
run's own model requests already use (§6.6). So a covered run keeps it. The
Jev engine keeps its ``open_world=True`` registry entry, because a separate
sub-processor receives its content.

⚠️ **Not ``annotate()``.** ``annotate`` also writes ``TOOL_ANNOTATIONS`` by
NAME, and that entry is the Jev engine's. Writing ``open_world=False`` there
would clear the Jev engine for a covered Copilot run, which resolves a bare
name. So the System-1 callable sets ``__tool_risk__`` itself.

The System-1 engine carries the attribution of the run's own requests (the
``X-CC-*`` stamp of ``acb_llm.attribution``), not the no-member rule above.
It adds no new trust in request input, and it inherits the open R11 item.

## System 1 on the decision model (WS-48 N3, D93)

``data_narrowing_pipeline.md`` §9 N3, and the D93 boxes of
``ai_tier_routing.md`` §5 and §6.1. With ``SYSTEM_ONE_ON_DECIDE`` on, the
System-1 engine sends each typed item to ``tier-decide`` through the one
facade, :func:`acb_llm.decide`, with ``acb_llm.routed.run_attribution()``:

* ``yes_no`` goes out as a ``boolean`` question, and ``choice`` and ``score``
  keep their kind. Each option is a criterion key, and its text too;
* one request holds ONE state (the context) and at most 16 questions, so a
  batch of 17 to 20 items is 2 requests, and they run at the same time;
* an item the door would refuse (:func:`acb_llm.shape_refusal`) goes to
  ``tier-fast`` with no decide request;
* a failed request sends ITS items to System 1 on ``tier-fast``, and logs
  ``decide_tool.system_one_fallback`` with the reason code. A 400 or a 422
  is a caller bug, so that line logs at ``error``. Every failed item and
  every refused item goes out in ONE ``tier-fast`` request;
* the turn-kind question of ``tier_policy`` never comes here.

🔴 **A ``no_egress`` run (owner, 2026-10-09, amends Q4 of that spec).** While
``DECIDE_IN_NO_EGRESS`` is on (the default), a ``no_egress`` run sends a
SHORT typed item to ``tier-decide`` too: a context of at most
:data:`NO_EGRESS_CONTEXT_MAX` characters, a question of at most
:data:`NO_EGRESS_QUESTION_MAX` and options of at most
:data:`NO_EGRESS_OPTION_MAX` each. A longer item stays on ``tier-fast`` and
logs :data:`NOT_SHORT`. With the switch off, a ``no_egress`` run sends no
decide request, as before. :func:`decide_in_no_egress` is the one reader.

## A skill's own typed choices (owner, 2026-10-09)

:func:`ask_typed` lets a tool ask the same engine a small typed question, in
place of a main-model round. Projects asks which row a name means, and
whether a new task is a twin. It is on only for an agent that
``AI_TIER_ROUTING`` covers (:func:`typed_choices_on`), and it takes the same
route as the tool, so there is no second client.

The calling model reads the same lead and the same line. A decide answer has
no reason, so its line ends after the confidence. The thresholds of §5 apply
to both engines. With the flag off, nothing here runs.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.decide_tools")

#: The flag is off, the box is not wired, the tier is unbound, the box needs a
#: member, or the Console is out. The model goes on without the tool.
UNAVAILABLE = "decide is not available right now — answer from your own judgement."

#: The Console refused the request. The model may retry with a smaller one.
MALFORMED = (
    "decide could not use that question. Retry with a shorter question, "
    "fewer or shorter options, or less context, or answer from your own judgement."
)

_KINDS = ("yes_no", "choice", "score")
_QID = "q"


def _options(raw: str) -> list[str]:
    """The options, from a JSON list, one per line, or ``|``-separated."""
    text = (raw or "").strip()
    if not text:
        return []
    items: list[Any] | None = None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            items = parsed
    if items is None:
        items = text.splitlines() if "\n" in text else text.split("|")
    out: list[str] = []
    for item in items:
        option = str(item).strip()
        if option and option not in out:
            out.append(option)
    return out


def _p(value: float | None) -> str:
    return "?" if value is None else f"{value:.2f}"


def _format(kind: str, answer: Any) -> str:
    """One short line. The model reads it, so no raw object leaks."""
    if kind == "yes_no":
        p_yes = float(answer.probability)
        return f"yes (p={p_yes:.2f})" if p_yes >= 0.5 else f"no (p={1 - p_yes:.2f})"

    probs = dict(answer.probabilities)
    if kind == "score":
        # CP-13h: `score` is a 0-based POSITION, possibly fractional, and
        # `level` is the caller's key of the nearest one. The probabilities
        # are keyed by level, so the fallback confidence reads the level.
        position = answer.score
        level = getattr(answer, "level", None) or str(position)
        confidence = answer.confidence
        if confidence is None:
            confidence = probs.get(level)
        if isinstance(position, int | float) and not isinstance(position, bool):
            return f"{level} (position {position:g}, confidence {_p(confidence)})"
        return f"{level} (confidence {_p(confidence)})"

    pick = str(answer.choice)
    confidence = answer.confidence
    if confidence is None:
        confidence = probs.get(pick)
    others = sorted(
        ((k, v) for k, v in probs.items() if k != pick),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not others:
        return f"{pick} (confidence {_p(confidence)})"
    second, p_second = others[0]
    return f"{pick} (confidence {_p(confidence)}; next: {second} {p_second:.2f})"


def decide_tool_enabled() -> bool:
    """Whether this box offers the tool at all. ONE source of truth.

    The injection chain (``_collect_injectable_platform_tools``) and the
    addendum renderer (``addendum.rendered_parts``) both ask THIS function. So
    the prompt never names a tool that is not injected, and a box with
    ``DECIDE_ENABLED`` off pays no schema or addendum tokens for it. Any error
    reads as off.
    """
    try:
        from acb_common import get_settings

        return bool(get_settings().decide_enabled)
    except Exception:  # a broken settings read must not arm the tool
        return False


def _attribution() -> dict[str, str | None]:
    """``agent`` and ``run_id`` from the per-run context, and never a member."""
    try:
        from acb_common import get_run_context

        ctx = get_run_context() or {}
    except Exception:  # attribution never blocks the tool
        ctx = {}
    return {"agent": ctx.get("agent") or None, "run_id": ctx.get("run_id") or None}


async def decide(
    question: str, context: str, kind: str = "yes_no", options: str = ""
) -> str:
    """Fast, calibrated judgement for a call you would otherwise guess.

    Classify, pick or rate `context` (is this urgent, which project fits, how
    severe) and get the answer with its probability in under a second. It
    writes no text.
    kind: "yes_no", "choice" (options: 2 or more picks) or "score" (options:
    2 to 10 levels, lowest first). options: one per line.
    If it says unavailable, use your own judgement.
    """
    kind = (kind or "yes_no").strip().lower()
    if kind not in _KINDS:
        return 'decide: kind must be "yes_no", "choice" or "score".'
    if not (question or "").strip():
        return "decide: ask a question."
    picks = _options(options)
    if kind == "choice" and len(picks) < 2:
        return "decide: a choice needs 2 or more different options."
    if kind == "score" and not 2 <= len(picks) <= 10:
        return "decide: a score needs 2 to 10 levels, lowest first."

    try:
        from acb_llm import (
            BooleanQuestion,
            ChoiceQuestion,
            DecideRequestInvalid,
            DecideUnavailable,
            ScoreQuestion,
        )
        from acb_llm import decide as facade
    except ImportError as exc:
        # A packaging defect, not the flag. Say so, or it reads as "off".
        _log.warning("decide_tool.import_failed", error_type=type(exc).__name__)
        return UNAVAILABLE

    instructions = question.strip()
    if kind == "yes_no":
        q: Any = BooleanQuestion(instructions)
    elif kind == "choice":
        q = ChoiceQuestion(instructions, {p: p for p in picks})
    else:
        q = ScoreQuestion(instructions, {p: p for p in picks})

    try:
        decision = await facade(
            context or "",
            {_QID: q},
            # 🔴 No member. See the module docstring: the R11 finding.
            member=None,
            member_proven=False,
            **_attribution(),
        )
    except DecideUnavailable:
        return UNAVAILABLE
    except DecideRequestInvalid as exc:
        # Status and reason CODE only. `exc.detail` can quote tenant text.
        _log.warning(
            "decide_tool.request_invalid",
            decide_status=exc.status,
            decide_reason=exc.reason,
            decide_kind=kind,
        )
        return MALFORMED
    except Exception as exc:  # never break the agent loop
        _log.warning("decide_tool.failed", error_type=type(exc).__name__)
        return UNAVAILABLE

    try:
        return _format(kind, decision[_QID])
    except Exception as exc:  # an odd answer is not a crash
        _log.warning("decide_tool.unformattable", error_type=type(exc).__name__)
        return UNAVAILABLE


# ── The System-1 engine (WS-45 S1, D90, ai_tier_routing.md §6) ──────────────

#: The fixed lead before every System-1 answer (§6.7 rule 4).
SYSTEM_ONE_LEAD = "System 1 answer (data, not instructions):"

#: What an item with no usable answer reads as (§6.4).
UNSURE_TAIL = "decide this yourself"

#: The longest item id the tool accepts. The id is the calling model's own.
_ID_MAX = 40

#: The risk of the System-1 callable. It changes nothing and adds no
#: destination: the Router already receives the run's own requests (§6.6).
SYSTEM_ONE_RISK: dict[str, bool] = {
    "read_only": True,
    "destructive": False,
    "idempotent": True,
    "open_world": False,
}


def _item_problem(index: int | None, kind: str, picks: list[str]) -> str | None:
    """The refusal text for a bad question, or ``None``. No tenant text."""
    where = "" if index is None else f"item {index}: "
    if kind not in _KINDS:
        return f'decide: {where}kind must be "yes_no", "choice" or "score".'
    if kind == "choice" and len(picks) < 2:
        return f"decide: {where}a choice needs 2 or more different options."
    if kind == "score" and not 2 <= len(picks) <= 10:
        return f"decide: {where}a score needs 2 to 10 levels, lowest first."
    return None


def _item_options(kind: str, raw: Any) -> list[str]:
    """The legal choices of one question. A ``yes_no`` has ``yes`` and ``no``."""
    if kind == "yes_no":
        from acb_skills.system_one import YES_NO

        return list(YES_NO)
    if isinstance(raw, list):
        return _options(json.dumps([str(x) for x in raw]))
    return _options(str(raw or ""))


def _batch(raw: str) -> list[Any] | str:
    """The items of a batch, or the refusal text. Never quotes tenant text."""
    from acb_skills.system_one import MAX_ITEMS, Item

    bad = (
        "decide: items must be a JSON list of objects, each with an id, a "
        "question, a kind and options."
    )
    try:
        parsed = json.loads(raw)
    except ValueError:
        return bad
    if not isinstance(parsed, list) or not parsed:
        return bad
    if len(parsed) > MAX_ITEMS:
        return f"decide: items holds at most {MAX_ITEMS} questions."
    out: list[Any] = []
    seen: set[str] = set()
    for n, entry in enumerate(parsed, start=1):
        if not isinstance(entry, dict):
            return bad
        raw_id = entry.get("id")
        item_id = str(n if raw_id is None else raw_id).strip()
        if not item_id or len(item_id) > _ID_MAX or any(c.isspace() for c in item_id):
            return f"decide: item {n}: the id must be one short word."
        if item_id in seen:
            return f"decide: item {n}: two items have the same id."
        seen.add(item_id)
        question = str(entry.get("question") or "").strip()
        if not question:
            return f"decide: item {n}: ask a question."
        kind = str(entry.get("kind") or "yes_no").strip().lower()
        picks = _item_options(kind, entry.get("options")) if kind in _KINDS else []
        problem = _item_problem(n, kind, picks)
        if problem:
            return problem
        out.append(Item(item_id, question, kind, tuple(picks)))
    return out


def _run_threshold() -> float:
    """The threshold for this run's effort mode, from the run binding (§5)."""
    from acb_skills.tier_policy import system_one_threshold

    try:
        from acb_skills.write_artifact import artifact_context

        effort = (artifact_context() or {}).get("think_mode")
    except Exception:  # no binding reads as Auto
        effort = None
    return system_one_threshold(effort)


def _system_one_line(answer: Any, threshold: float, prefix: str) -> str:
    """One line per item (§6.4). Low confidence and no choice read as unsure."""
    confidence = answer.confidence
    if answer.choice is None or confidence is None or confidence < threshold:
        return f"{prefix}unsure (confidence {_p(confidence)}) — {UNSURE_TAIL}"
    tail = f" — {answer.reason}" if answer.reason else ""
    return f"{prefix}{answer.choice} (confidence {confidence:.2f}){tail}"


async def system_one_decide(
    question: str,
    context: str,
    kind: str = "yes_no",
    options: str = "",
    items: str = "",
) -> str:
    """Fast judgement on `context` for a call you would otherwise guess.

    Is this urgent, which project fits, how severe? No text.
    kind: "yes_no", "choice" (options: 2+ picks) or "score" (options: 2-10
    levels, lowest first). options: one per line.
    items: batch related questions, a JSON list of up to 20
    {id, question, kind, options}.
    On "unsure" or unavailable, judge yourself.
    """
    from acb_skills import system_one

    batch = bool((items or "").strip())
    if batch:
        parsed = _batch(items)
        if isinstance(parsed, str):
            return parsed
        asked: list[Any] = parsed
        log_kind = "batch"
    else:
        kind = (kind or "yes_no").strip().lower()
        if not (question or "").strip():
            return "decide: ask a question."
        picks = _item_options(kind, options) if kind in _KINDS else []
        problem = _item_problem(None, kind, picks)
        if problem:
            return problem
        asked = [system_one.Item(_QID, question.strip(), kind, tuple(picks))]
        log_kind = kind

    try:
        answers = await _answers_of(context or "", asked, log_kind)
    except system_one.SystemOneUnavailable as exc:
        # A reason CODE only. Never the context, a question or an option.
        _log.warning(
            "decide_tool.system_one", status=exc.reason, decide_kind=log_kind,
            items=len(asked),
        )
        return UNAVAILABLE
    except Exception as exc:  # never break the agent loop
        _log.warning("decide_tool.system_one_failed", error_type=type(exc).__name__)
        return UNAVAILABLE

    threshold = _run_threshold()
    lines = [
        _system_one_line(a, threshold, f"{a.id}: " if batch else "")
        for a in answers
    ]
    _log.info(
        "decide_tool.system_one", status="ok", decide_kind=log_kind,
        items=len(asked), confidences=[a.confidence for a in answers],
    )
    return "\n".join([SYSTEM_ONE_LEAD, *lines])


# ── D93: a typed item goes to `tier-decide` (WS-48 N3) ───────────────────────

#: The log event of each fallback to ``tier-fast`` (N3 done-when 4).
FALLBACK_EVENT = "decide_tool.system_one_fallback"

#: The longest that one decide request may take, end to end
#: (``data_narrowing_pipeline.md`` §3.5). The Console client has its own
#: bound of the same length, and this one holds the whole await.
DECIDE_TIMEOUT_S = 10.0


#: The routes of :func:`_decide_route`. ``open``: every typed item may go to
#: ``tier-decide``. ``short``: the run is ``no_egress``, so only a SHORT typed
#: item may go (:func:`_short_enough`).
ROUTE_OPEN = "open"
ROUTE_SHORT = "short"

#: The most characters of the context, of one question and of one option
#: that a ``no_egress`` run sends to ``tier-decide`` (owner, 2026-10-09).
#: The owner allows typed questions with SHORT summaries. A longer item is
#: free-form work, and it stays on ``tier-fast``, our own chat tier.
NO_EGRESS_CONTEXT_MAX = 1500
NO_EGRESS_QUESTION_MAX = 400
NO_EGRESS_OPTION_MAX = 120

#: The reason code of an item that a ``no_egress`` run keeps on ``tier-fast``
#: because it is not short.
NOT_SHORT = "no_egress_not_short"


def decide_in_no_egress() -> bool:
    """Whether a ``no_egress`` run may send a typed item to ``tier-decide``.

    The ONE reader of ``DECIDE_IN_NO_EGRESS`` (owner, 2026-10-09). The
    System-1 ``decide`` and the PICK step of ``narrowing`` both ask it. A
    broken read reads as OFF, so the run keeps the rule of before.
    """
    try:
        from acb_common import get_settings

        return bool(get_settings().decide_in_no_egress)
    except Exception:  # a broken read must not open a destination
        return False


def _decide_route() -> str | None:
    """The route of this call's typed items, or ``None`` for ``tier-fast``.

    * ``SYSTEM_ONE_ON_DECIDE`` off: ``None``.
    * A run that may send data off the platform: :data:`ROUTE_OPEN`.
    * A ``no_egress`` run: :data:`ROUTE_SHORT` while ``DECIDE_IN_NO_EGRESS``
      is on, else ``None``. ``no_egress_for_this_run`` fails closed, so a
      frame with no run binding takes the short route too.

    Any error reads as ``None``.
    """
    try:
        from acb_common import get_settings

        if not get_settings().system_one_on_decide:
            return None
        from acb_skills.egress import no_egress_for_this_run

        if not no_egress_for_this_run():
            return ROUTE_OPEN
        return ROUTE_SHORT if decide_in_no_egress() else None
    except Exception:  # a broken read must not move the engine
        return None


def _on_decide() -> bool:
    """Whether this call may send any typed item to ``tier-decide``."""
    return _decide_route() is not None


def short_context(context: Any) -> bool:
    """True when *context* fits the short bound of a ``no_egress`` run.

    A mapping or a list is measured as the JSON that the door receives. The
    PICK step of ``narrowing`` asks this too, so ONE bound holds every path
    from a ``no_egress`` run to ``tier-decide`` (review P1, 2026-10-09).
    """
    if not isinstance(context, str):
        context = json.dumps(context, ensure_ascii=False)
    return len(context) <= NO_EGRESS_CONTEXT_MAX


def _short_enough(context: str, item: Any) -> bool:
    """True when *item* about *context* is a SHORT typed question.

    A ``no_egress`` run sends only these to ``tier-decide``. The bound is on
    the text that leaves: the context, the question and each option.
    """
    return (
        short_context(context or "")
        and len(str(item.question or "")) <= NO_EGRESS_QUESTION_MAX
        and all(len(str(o)) <= NO_EGRESS_OPTION_MAX for o in item.options)
    )


async def _answers_of(context: str, asked: list[Any], log_kind: str) -> list[Any]:
    """The answers of *asked*, from the engine that this call uses."""
    from acb_skills import system_one

    route = _decide_route()
    if route is None:
        return await system_one.ask(context, asked)
    return await _ask_on_decide(context, asked, log_kind, short_only=route == ROUTE_SHORT)


# ── Typed choices for a skill's own tool (owner, 2026-10-09) ─────────────────


def typed_choices_on() -> bool:
    """Whether a tool of this run may ask the ``decide`` engine itself.

    A tool asks a small typed question (which status a name means, whether a
    new task is a twin) in place of a main-model round. It uses the engine of
    the System-1 ``decide``, so it is on only where that engine is: for an
    agent that ``AI_TIER_ROUTING`` covers. The calling agent comes from the
    run binding (R5). Any error reads as off.
    """
    try:
        from acb_skills.system_one import _calling_agent
        from acb_skills.tier_policy import tier_routing_on

        return tier_routing_on(_calling_agent())
    except Exception:  # a broken read must not add a request
        return False


async def ask_typed(context: str, items: list[Any], *, purpose: str) -> list[Any] | None:
    """The answers of *items*, from the ONE engine of the System-1 ``decide``.

    The same route as the tool (:func:`_answers_of`): ``tier-decide`` when
    ``SYSTEM_ONE_ON_DECIDE`` lets it, with the short bound in a ``no_egress``
    run, else System 1 on ``tier-fast``. No second client.

    Returns ``None`` when no engine answered, and never raises. *purpose* is
    a fixed code for the logs, for example ``projects.name``. The caller
    reads each answer with :func:`sure_choice`. Nothing here logs the
    context, a question or an option.
    """
    from acb_skills import system_one

    if not items or len(items) > system_one.MAX_ITEMS:
        return None
    try:
        answers = await _answers_of(context or "", list(items), purpose)
    except system_one.SystemOneUnavailable as exc:
        _log.info("decide_tool.typed_choice", purpose=purpose, status=exc.reason,
                  items=len(items))
        return None
    except Exception as exc:  # a tool must never break on its helper
        _log.warning("decide_tool.typed_choice_failed", purpose=purpose,
                     error_type=type(exc).__name__)
        return None
    _log.info("decide_tool.typed_choice", purpose=purpose, status="ok", items=len(items),
              confidences=[a.confidence for a in answers])
    return list(answers)


def sure_choice(answer: Any) -> str | None:
    """The choice of *answer* at or above the run's threshold, else ``None``.

    The thresholds of ``tier_policy`` (§5) apply, as for the tool's own line.
    """
    if answer is None:
        return None
    confidence = getattr(answer, "confidence", None)
    choice = getattr(answer, "choice", None)
    if choice is None or confidence is None or confidence < _run_threshold():
        return None
    return str(choice)


def _decide_question(item: Any) -> Any:
    """One System-1 item as the door's typed question."""
    from acb_llm import BooleanQuestion, ChoiceQuestion, ScoreQuestion

    if item.kind == "yes_no":
        return BooleanQuestion(item.question)
    criteria = {option: option for option in item.options}
    if item.kind == "choice":
        return ChoiceQuestion(item.question, criteria)
    return ScoreQuestion(item.question, criteria)


def _probability(value: Any) -> float | None:
    """*value* when it is a number from 0 to 1, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _level_of(item: Any, raw: Any) -> str | None:
    """The level that a decide ``score`` answer names, or ``None``."""
    from acb_skills.system_one import _match

    level = _match(getattr(raw, "level", None), item.options)
    if level is not None:
        return level
    position = getattr(raw, "score", None)
    if isinstance(position, str):
        return _match(position, item.options)
    if isinstance(position, bool) or not isinstance(position, int | float):
        return None
    if not math.isfinite(position):
        return None
    # The nearest level, clamped to the scale, as the Console reads it.
    index = min(max(math.floor(position + 0.5), 0), len(item.options) - 1)
    return item.options[index] if item.options else None


def _decide_answer(item: Any, raw: Any) -> Any:
    """A decide answer in System 1's shape, checked against the item's options.

    No reason: the door gives none. A choice outside the options, or a
    confidence that is not a probability, reads as ``unsure``, exactly as a
    System-1 answer does.
    """
    from acb_skills.system_one import Answer, _match

    try:
        if item.kind == "yes_no":
            p_yes = _probability(getattr(raw, "probability", None))
            if p_yes is None:
                return Answer(item.id, None, None, "")
            if p_yes >= 0.5:
                return Answer(item.id, "yes", p_yes, "")
            return Answer(item.id, "no", round(1.0 - p_yes, 6), "")
        if item.kind == "choice":
            choice = _match(getattr(raw, "choice", None), item.options)
        else:
            choice = _level_of(item, raw)
        confidence = getattr(raw, "confidence", None)
        if confidence is None and choice is not None:
            confidence = dict(getattr(raw, "probabilities", None) or {}).get(choice)
        return Answer(item.id, choice, _probability(confidence), "")
    except Exception:  # an odd answer is unsure, never a crash
        return Answer(item.id, None, None, "")


def _decide_attribution() -> dict[str, Any]:
    """``run_attribution()``, with the CALLING agent from the run binding.

    The run binding names this run's own agent, where a delegated run's log
    context can still name its parent (§6.5, as ``system_one`` does).
    """
    from acb_llm.routed import run_attribution

    from acb_skills.system_one import _calling_agent

    out = dict(run_attribution())
    calling = _calling_agent()
    if calling:
        out["agent"] = calling
    return out


#: A ``DecideUnavailable`` reason that is already a code: a word such as
#: ``disabled``, ``unwired`` or ``tier_unknown``, or ``HTTP 503``.
_REASON_CODE = re.compile(r"[a-z][a-z_]{0,39}|HTTP \d{3}")


def _reason_code(reason: Any) -> str:
    """A ``DecideUnavailable`` reason as a code. Never a sentence.

    The Console client gives a sentence for some refusals (a deployment key
    with no proven member) and a transport message for an outage. Both read
    as ``unavailable``, so a count of fallbacks by reason stays a count of
    codes.
    """
    text = str(reason or "")
    if text.startswith("unreadable answer"):
        return "unreadable"
    return text if _REASON_CODE.fullmatch(text) else "unavailable"


async def _one_decide(
    context: str,
    questions: dict[str, Any],
    attribution: dict[str, Any],
    number: int,
    log_kind: str,
) -> Any:
    """ONE decide request, or ``None`` when it failed. Never raises.

    Each failure logs :data:`FALLBACK_EVENT` with a reason CODE, the request
    number and the item count. It logs no tenant text.
    """
    from acb_llm import DecideRequestInvalid, DecideUnavailable
    from acb_llm import decide as facade

    where = {"request": number, "items": len(questions), "decide_kind": log_kind}
    try:
        return await asyncio.wait_for(
            facade(context, questions, **attribution), timeout=DECIDE_TIMEOUT_S,
        )
    except DecideRequestInvalid as exc:
        # A caller bug (`acb_llm/decide.py`). The fallback answers, and this
        # line keeps the bug loud.
        _log.error(
            FALLBACK_EVENT, reason="request_invalid", decide_status=exc.status,
            decide_reason=exc.reason, **where,
        )
    except DecideUnavailable as exc:
        _log.warning(
            FALLBACK_EVENT, reason=_reason_code(exc.reason), decide_status=exc.status,
            **where,
        )
    except TimeoutError:
        _log.warning(FALLBACK_EVENT, reason="timeout", **where)
    except Exception as exc:  # never break the agent loop
        _log.warning(FALLBACK_EVENT, reason=type(exc).__name__, **where)
    return None


async def _ask_on_decide(
    context: str, asked: list[Any], log_kind: str, *, short_only: bool = False,
) -> list[Any]:
    """Ask *asked* on ``tier-decide``, and the rest on ``tier-fast``. In order.

    *short_only* is the ``no_egress`` route (owner, 2026-10-09): an item that
    is not short (:func:`_short_enough`) goes to ``tier-fast`` with no decide
    request, and logs :data:`NOT_SHORT`.

    Raises :class:`~acb_skills.system_one.SystemOneUnavailable` only when no
    item got an answer from either engine, so the tool says ``UNAVAILABLE``
    as before. When some items got an answer, an item with none reads as
    ``unsure``.
    """
    from acb_skills import system_one

    try:
        from acb_llm import shape_refusal, split_questions

        sendable: dict[str, Any] = {}
        for item in asked:
            if short_only and not _short_enough(context, item):
                _log.info(FALLBACK_EVENT, reason=NOT_SHORT, request=0, items=1,
                          decide_kind=log_kind)
                continue
            question = _decide_question(item)
            code = shape_refusal(context, question)
            if code is None:
                sendable[item.id] = question
            else:
                _log.info(FALLBACK_EVENT, reason=code, request=0, items=1, decide_kind=log_kind)
        parts = split_questions(sendable)
        attribution = _decide_attribution() if parts else {}
    except Exception as exc:  # a fault here sends the batch to tier-fast
        _log.warning(
            FALLBACK_EVENT, reason=type(exc).__name__, request=0, items=len(asked),
            decide_kind=log_kind,
        )
        parts, attribution = [], {}
    decisions = await asyncio.gather(*(
        _one_decide(context, part, attribution, n, log_kind)
        for n, part in enumerate(parts, start=1)
    ))
    by_id = {item.id: item for item in asked}
    got: dict[str, Any] = {}
    for part, decision in zip(parts, decisions, strict=True):
        if decision is not None:
            for qid in part:
                got[qid] = _decide_answer(by_id[qid], decision[qid])

    rest = [item for item in asked if item.id not in got]
    if rest:
        try:
            fast = await system_one.ask(context, rest)
        except Exception as exc:
            if not got:
                raise
            _log.warning(
                "decide_tool.system_one_fallback_failed",
                error_type=type(exc).__name__, items=len(rest),
            )
            fast = [system_one.Answer(i.id, None, None, "") for i in rest]
        got.update({a.id: a for a in fast})
    _log.info(
        "decide_tool.system_one_engines", decide_kind=log_kind,
        decide_items=len(asked) - len(rest), fast_items=len(rest),
        decide_requests=len(parts),
    )
    return [got[item.id] for item in asked]


# The tool name is `decide`, the ONE name (§6.1). The risk sits on the
# function, so `egress._risk_of` reads it before any registry entry (§6.6).
system_one_decide.__name__ = "decide"
system_one_decide.__qualname__ = "decide"
system_one_decide.__tool_risk__ = SYSTEM_ONE_RISK  # type: ignore[attr-defined]


def decide_tool_for(agent_name: str | None) -> Callable[..., Any] | None:
    """The ``decide`` engine that *agent_name*'s run holds, or ``None``. ONE place.

    * The tier policy covers the agent: :func:`system_one_decide`, whatever
      ``DECIDE_ENABLED`` says (§6.1).
    * Else :func:`decide`, the Jev engine, while ``DECIDE_ENABLED`` is on.
    * Else nothing, so with ``AI_TIER_ROUTING`` empty the chain is as before.
    """
    from acb_skills.tier_policy import tier_routing_on

    if tier_routing_on(agent_name):
        return system_one_decide
    return decide if decide_tool_enabled() else None
