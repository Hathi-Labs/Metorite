"""The ONE tenant facade for the ``decide`` task (WS-31 CP-13c, D75).

Spec: ``project-docs/specs/customer_console.md`` §6A.14 "CP-13c · The tenant
client".

Every app caller that wants a typed decision goes through :func:`decide`. It
takes typed question objects and returns typed answers, and it never returns a
raw dict. A second decide client in an app is a defect (CLAUDE.md §4).

🔴 **There is NO local path, on purpose.** litellm cannot call TypeSafe's Jev
from the SDK, so a box that cannot reach the Console Router gets
:class:`DecideUnavailable`, and the caller keeps its current LLM path
(§6A.14 adoption rule 3).

🔴 **``DECIDE_ENABLED`` is the master switch, default OFF, and OWNER-ONLY.**
Off, :func:`decide` raises before it imports the Console client, so it makes
no network call. Importing this module does nothing either.

⚠️ **The Console is the ONE validator of clause 13's limits.** This module
copies none of them. A breach comes back as :class:`DecideRequestInvalid`
with the Console's own words.

⚠️ **This module must never import ``customer_console``.** It reaches the
Console through ``acb_auth.console_resolve``, the one Console HTTP client,
and ``tests/unit/test_console_dependency_boundary.py`` fences both rules.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from acb_common import get_logger, get_settings

__all__ = [
    "DECIDE_TIER",
    "BooleanAnswer",
    "BooleanQuestion",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "DecideError",
    "DecideRequestInvalid",
    "DecideUnavailable",
    "Decision",
    "ScoreAnswer",
    "ScoreQuestion",
    "decide",
]

_log = get_logger("acb_llm.decide")

#: The hidden tier the Console's migration `033` registers for the task. The
#: caller names a tier and never a model (D32.7).
DECIDE_TIER = "tier-decide"

#: The Router's verdicts that mean "degrade to your old path". Each keeps its
#: name in the reason, so a log line says WHY the feature fell back.
_DEGRADE_VERDICTS: dict[int, str] = {
    402: "insufficient_credits",
    403: "forbidden",
}

#: The Router's verdicts that mean "this request is wrong". A 400 is the
#: clause-13 refusal. A 422 is a body the door could not parse. Both are a
#: caller fault that must be loud.
_INVALID_VERDICTS = frozenset({400, 422})

#: The ONE 400 that is not the caller's fault. Nobody has bound
#: ``tier-decide`` on the Console, which is the production state until an
#: operator does. The door says so with a structured ``reason`` code (CP-13c),
#: so the facade reads a code and never the sentence. It degrades, exactly as
#: a bound tier with no key does (a 503).
_TIER_UNKNOWN = "tier_unknown"


# ── Exceptions ──────────────────────────────────────────────────────────────


class DecideError(Exception):
    """The base of every error :func:`decide` raises."""


class DecideUnavailable(DecideError):
    """No decision is available. The caller takes its current LLM path.

    ``reason`` names why: ``"disabled"`` for the switch, the Console client's
    own words for an unwired box or a missing member, ``"HTTP 503"`` for an
    outage, and ``"insufficient_credits"`` or ``"forbidden"`` for a 402 or a
    403 verdict.
    """

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class DecideRequestInvalid(DecideError):
    """The Console refused the request itself (400 or 422).

    This is NOT a fallback case. It is a caller bug, and a silent fallback
    would hide it for ever. A caller lets it surface.

    ⚠️ **The message holds the status and a reason CODE only.** The
    Console's ``detail`` can quote question ids, criterion keys or the
    request itself, which is tenant text. So it rides on :attr:`detail`,
    which neither ``str()`` nor ``repr()`` prints, and a logged exception
    carries no tenant text.
    """

    def __init__(self, status: int, detail: Any) -> None:
        reason = detail.get("reason") if isinstance(detail, Mapping) else None
        self.status = status
        self.reason = reason if isinstance(reason, str) else "request_invalid"
        super().__init__(f"HTTP {status}: {self.reason}")
        #: The Console's own words. Read it to debug. Do not log it.
        self.detail = detail

    def __repr__(self) -> str:
        return f"DecideRequestInvalid(status={self.status}, reason={self.reason!r})"


# ── Questions ───────────────────────────────────────────────────────────────


def _frozen(criteria: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(criteria))


@dataclass(frozen=True)
class BooleanQuestion:
    """A yes-or-no question. The answer is a probability of yes.

    ``criteria`` may say what true and what false mean, keyed by ``"true"``
    and ``"false"``.
    """

    instructions: str
    criteria: Mapping[str, str] = field(default_factory=dict)
    type: str = field(default="boolean", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", _frozen(self.criteria))


@dataclass(frozen=True)
class ChoiceQuestion:
    """Pick one option. ``criteria`` maps each option key to its meaning."""

    instructions: str
    criteria: Mapping[str, str]
    type: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", _frozen(self.criteria))


@dataclass(frozen=True)
class ScoreQuestion:
    """Pick one level on an ordered scale. ``criteria`` maps each level key
    to its meaning, in order."""

    instructions: str
    criteria: Mapping[str, str]
    type: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", _frozen(self.criteria))


Question = BooleanQuestion | ChoiceQuestion | ScoreQuestion


# ── Answers ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BooleanAnswer:
    """The probability, from 0 to 1, that the answer is yes."""

    probability: float


@dataclass(frozen=True)
class ChoiceAnswer:
    """The chosen option key, the per-option map and the confidence."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float | None


@dataclass(frozen=True)
class ScoreAnswer:
    """A position on the caller's ordered scale (§6A.14 wire contract, CP-13h).

    ``score`` is the 0-based level POSITION, and it can be fractional (1.3).
    ``level`` is the caller's key of the nearest position, or None when the
    Console sent none. ``probabilities`` are keyed by the caller's level keys.
    """

    score: str | int | float
    probabilities: Mapping[str, float]
    confidence: float | None
    level: str | None = None


Answer = BooleanAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class Decision:
    """The answers, keyed by the question ids the caller chose.

    ``answers`` is read-only. ``request_id`` joins this call to its
    ``usage_event`` row on the Console.
    """

    answers: Mapping[str, Answer]
    request_id: str | None = None

    def __getitem__(self, qid: str) -> Answer:
        return self.answers[qid]


# ── Parsing ─────────────────────────────────────────────────────────────────


class _Unreadable(Exception):
    """A 200 body we cannot read as typed answers."""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _probabilities(value: Any) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        return MappingProxyType({})
    out: dict[str, float] = {}
    for key, raw in value.items():
        number = _number(raw)
        if number is not None:
            out[str(key)] = number
    return MappingProxyType(out)


def _answer(question: Question, raw: Any) -> Answer:
    """One answer, typed by OUR question and never by the body."""
    if not isinstance(raw, Mapping):
        raise _Unreadable("an answer is not an object")
    if raw.get("type") != question.type:
        raise _Unreadable("an answer's type does not match its question")

    if isinstance(question, BooleanQuestion):
        probability = _number(raw.get("probability"))
        if probability is None:
            raise _Unreadable("a boolean answer has no probability")
        return BooleanAnswer(probability=probability)

    confidence = _number(raw.get("confidence"))
    probabilities = _probabilities(raw.get("probabilities"))
    if isinstance(question, ChoiceQuestion):
        choice = raw.get("choice")
        if not isinstance(choice, str):
            raise _Unreadable("a choice answer has no choice")
        return ChoiceAnswer(
            choice=choice, probabilities=probabilities, confidence=confidence
        )

    # 🔴 CP-13h: the position can be FRACTIONAL. Refusing a float turned a
    # billed answer into DecideUnavailable. A bool, NaN or infinity is not
    # a position.
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, str | int | float):
        raise _Unreadable("a score answer has no score")
    if isinstance(score, float) and not math.isfinite(score):
        raise _Unreadable("a score answer has no score")
    level = raw.get("level")
    return ScoreAnswer(
        score=score,
        probabilities=probabilities,
        confidence=confidence,
        level=level if isinstance(level, str) else None,
    )


def _decision(body: Mapping[str, Any], questions: Mapping[str, Question]) -> Decision:
    raw_answers = body.get("answers")
    if not isinstance(raw_answers, Mapping):
        raise _Unreadable("the body has no answers")
    answers: dict[str, Answer] = {}
    for qid, question in questions.items():
        if qid not in raw_answers:
            raise _Unreadable("an answer is missing")
        answers[qid] = _answer(question, raw_answers[qid])
    request_id = body.get("request_id")
    return Decision(
        answers=MappingProxyType(answers),
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _wire_questions(questions: Mapping[str, Question]) -> dict[str, dict[str, Any]]:
    """Our typed questions, as the door's wire body. A caller bug raises."""
    if not isinstance(questions, Mapping) or not questions:
        raise TypeError("decide() needs a non-empty mapping of question id to question")
    out: dict[str, dict[str, Any]] = {}
    for qid, question in questions.items():
        # A non-str id would go out as `str(qid)` and come back under that
        # key, so every answer would read as missing AFTER the call is billed.
        if not isinstance(qid, str):
            raise TypeError(f"a question id must be a str, not {type(qid).__name__}")
        if not isinstance(question, BooleanQuestion | ChoiceQuestion | ScoreQuestion):
            raise TypeError(
                f"question {qid!r} must be a BooleanQuestion, ChoiceQuestion "
                f"or ScoreQuestion, not {type(question).__name__}"
            )
        out[qid] = {
            "type": question.type,
            "instructions": question.instructions,
            "criteria": dict(question.criteria),
        }
    return out


# ── The facade ──────────────────────────────────────────────────────────────


async def decide(
    state: str | Mapping[str, Any] | list[Any],
    questions: Mapping[str, Question],
    *,
    member: str | None = None,
    member_proven: bool = False,
    agent: str | None = None,
    module_slug: str | None = None,
    run_id: str | None = None,
) -> Decision:
    """Ask typed questions about ``state``, and get typed answers back.

    Raises:
        DecideUnavailable: the switch is off, the box is not wired, a
            deployment-key box got no member, the Console is out, nobody
            bound ``tier-decide`` (400 ``tier_unknown``), or the Router
            answered 402 or 403. Catch it and take the old path.
        DecideRequestInvalid: the Console refused the request (any other 400,
            or a 422). That is a bug to fix. Let it surface.
        TypeError: a question id is not a str, or ``questions`` holds
            something that is not one of the three question types.
    """
    if not get_settings().decide_enabled:
        # 🔴 Before the import and before any I/O. Off means no call.
        raise DecideUnavailable("disabled")

    payload = {
        "tier": DECIDE_TIER,
        "state": dict(state) if isinstance(state, Mapping) else state,
        "questions": _wire_questions(questions),
    }

    # Imported here, so importing `acb_llm` never imports the Console client
    # or `acb_auth`'s FastAPI surface.
    from acb_auth import console_resolve

    try:
        status, body = await console_resolve.decide_on_console(
            payload,
            member=member,
            member_proven=member_proven,
            agent=agent,
            module_slug=module_slug,
            run_id=run_id,
        )
    except console_resolve.ConsoleRouterUnavailable as exc:
        reason = str(exc) or "unavailable"
        _log.info("decide.unavailable", extra={"decide_reason": reason})
        raise DecideUnavailable(reason) from exc

    if status == 200:
        try:
            return _decision(body, questions)
        except _Unreadable as exc:
            # A 200 we cannot read. The Console already checked the shape, so
            # this is drift. The caller degrades, and the log says so.
            _log.warning("decide.unreadable", extra={"decide_reason": str(exc)})
            raise DecideUnavailable(f"unreadable answer: {exc}", status=status) from exc

    detail = body.get("detail")
    if (
        status == 400
        and isinstance(detail, Mapping)
        and detail.get("reason") == _TIER_UNKNOWN
    ):
        _log.info("decide.unavailable", extra={"decide_reason": _TIER_UNKNOWN, "decide_status": status})
        raise DecideUnavailable(_TIER_UNKNOWN, status=status)

    if status in _INVALID_VERDICTS:
        raise DecideRequestInvalid(status, detail)

    reason = _DEGRADE_VERDICTS.get(status, f"HTTP {status}")
    _log.info("decide.unavailable", extra={"decide_reason": reason, "decide_status": status})
    raise DecideUnavailable(reason, status=status)
