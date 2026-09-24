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
"""
from __future__ import annotations

import json
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

    pick = str(answer.choice if kind == "choice" else answer.score)
    probs = dict(answer.probabilities)
    confidence = answer.confidence
    if confidence is None:
        confidence = probs.get(pick)
    if kind == "score":
        return f"{pick} (confidence {_p(confidence)})"
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
