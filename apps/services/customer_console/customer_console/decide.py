"""The ``decide`` door's body and its limits — pure, and checked before we spend.

Spec: ``project-docs/specs/customer_console.md`` §6A.14 — the wire contract,
and clause 13.

🔴 **The Router validates the request BEFORE it spends** (clause 13). Each
limit below is checked before the chain resolves and before a vendor sees a
byte. A breach is a 400 that NAMES the rule, so the caller can fix the
request instead of guessing.

**Pure functions only**, like ``catalog.py``. No database and no network, so
each rule is testable without either.
"""
from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field

from customer_console.handlers import QUESTION_TYPES, Question

__all__ = [
    "MAX_CHOICE_OPTIONS",
    "MAX_QUESTIONS",
    "MAX_SCORE_LEVELS",
    "MAX_STATE_TOKENS",
    "MIN_SCORE_LEVELS",
    "DecideQuestion",
    "DecideRequest",
    "decide_refusal",
    "estimated_state_tokens",
    "questions_of",
]

#: The vendor's own ceiling on the options in one ``choice`` question.
MAX_CHOICE_OPTIONS = 255

#: The vendor's own range for the ordered levels of one ``score`` question.
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10

#: How many questions one request may carry. *Agent default* — the vendor
#: states no limit (clause 13). Each question adds to one bill, so a ceiling
#: exists for the same reason ``n`` is clamped on the image door.
MAX_QUESTIONS = 16

#: The vendor's window for ``state``, in tokens. The request window is 64k,
#: and 32k of it is for ``state`` plus the longest question.
MAX_STATE_TOKENS = 32_000

#: Characters per token for the estimate clause 13 names. The estimate is
#: ours and deliberately crude. The vendor's own count still decides the bill.
_CHARS_PER_TOKEN = 4


class DecideQuestion(BaseModel):
    """One question, in OUR words. ``type`` is checked by
    :func:`decide_refusal`, so a bad type is a 400 that names the rule and
    not a 422 that names a schema."""

    type: str = Field(min_length=1, max_length=32)
    instructions: str = Field(min_length=1)
    #: The options of a ``choice``, the ordered levels of a ``score``, or the
    #: meaning of true and false for a ``boolean``. Key → description.
    criteria: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class DecideRequest(BaseModel):
    """One ``POST /v1/decide`` body, addressed to a TIER.

    ⚠️ ``extra="forbid"``, as on every other serving body. Everything the
    caller may forward is named here. A ``model`` field does not exist: the
    caller names a tier, and the Router picks the model (D32.7).
    """

    #: 🔴 A TIER ALIAS, never a model id. ``tier-decide`` is the one tier
    #: that `033` registers for the task.
    tier: str = Field(min_length=1)
    #: What the decision is about. A string, an object or an array.
    state: str | dict[str, Any] | list[Any]
    #: Keyed by an id the caller picks. The answer comes back under the
    #: same id.
    questions: dict[str, DecideQuestion]
    #: The caller's own correlation id, trusted for nothing.
    client_ref: str | None = None

    model_config = {"extra": "forbid"}


def estimated_state_tokens(state: Any) -> int:
    """``state`` in tokens, estimated as characters divided by four.

    A structured ``state`` is measured as the JSON the vendor receives.
    """
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


def decide_refusal(req: DecideRequest) -> str | None:
    """The first clause-13 rule this request breaks, in words, or None.

    The door turns a non-None answer into a 400. Nothing has resolved and
    nothing has been spent at that point, so the refusal costs nothing.
    """
    count = len(req.questions)
    if count == 0:
        return "a decide request needs one or more questions"
    if count > MAX_QUESTIONS:
        return f"a decide request takes at most {MAX_QUESTIONS} questions, and this one has {count}"

    for qid, question in req.questions.items():
        if question.type not in QUESTION_TYPES:
            return (
                f"question {qid!r}: the type must be one of "
                f"{', '.join(sorted(QUESTION_TYPES))}"
            )
        options = len(question.criteria)
        if question.type == "choice" and options > MAX_CHOICE_OPTIONS:
            return (
                f"question {qid!r}: a choice takes at most {MAX_CHOICE_OPTIONS} "
                f"options, and this one has {options}"
            )
        if question.type == "score" and not (MIN_SCORE_LEVELS <= options <= MAX_SCORE_LEVELS):
            return (
                f"question {qid!r}: a score takes from {MIN_SCORE_LEVELS} to "
                f"{MAX_SCORE_LEVELS} levels, and this one has {options}"
            )

    tokens = estimated_state_tokens(req.state)
    if tokens > MAX_STATE_TOKENS:
        return (
            f"state is about {tokens} tokens (characters divided by "
            f"{_CHARS_PER_TOKEN}), and the limit is {MAX_STATE_TOKENS}"
        )
    return None


def questions_of(req: DecideRequest) -> dict[str, Question]:
    """The request's questions as the handler's typed objects."""
    return {
        qid: Question(
            type=question.type,
            instructions=question.instructions,
            criteria=dict(question.criteria),
        )
        for qid, question in req.questions.items()
    }
