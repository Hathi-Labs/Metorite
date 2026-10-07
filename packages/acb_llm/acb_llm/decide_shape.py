"""The SHAPE of a ``decide`` request, for a caller that splits or routes.

WS-48 N3 (D93), ``data_narrowing_pipeline.md`` §9 N1 and N3.

⚠️ **This is not the facade, and it is not a validator.** The Console stays
the ONE validator of clause 13, and :mod:`acb_llm.decide` copies none of its
limits (``test_acb_llm_decide.py`` fences that). A caller that sends more
than one question needs two facts BEFORE it sends:

* :func:`split_questions` cuts a batch into requests of at most
  :data:`MAX_QUESTIONS`. One request holds ONE ``state``;
* :func:`shape_refusal` names the rule that the door would break for one
  question, so the caller can send that question to its old path with no
  request and no ``error`` line. A 400 stays loud for a real caller bug.

The System-1 ``decide`` tool (``acb_skills.decide_tools``) and the pick step
of the narrowing pipeline (``acb_skills.narrowing``) read them here, so there
is ONE copy on the tenant side. Each value is a copy of
``customer_console/decide.py``, because this package may not import it
(``test_console_dependency_boundary.py``).
``tests/unit/test_acb_llm_decide.py`` pins each copy to the source of record,
and checks :func:`shape_refusal` against the door's own ``decide_refusal``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from acb_llm.decide import ChoiceQuestion, Question, ScoreQuestion

__all__ = [
    "MAX_CHOICE_OPTIONS",
    "MAX_CRITERION_CHARS",
    "MAX_CRITERION_KEY_CHARS",
    "MAX_INSTRUCTIONS_CHARS",
    "MAX_QUESTIONS",
    "MAX_SCORE_LEVELS",
    "MAX_STATE_TOKENS",
    "MIN_SCORE_LEVELS",
    "shape_refusal",
    "split_questions",
]


#: The most questions in ONE request. One request holds ONE ``state``.
MAX_QUESTIONS = 16
#: The most options of one ``choice`` question.
MAX_CHOICE_OPTIONS = 255
#: The range of the levels of one ``score`` question.
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
#: The longest ``instructions`` of one question, in characters.
MAX_INSTRUCTIONS_CHARS = 4_000
#: The longest criterion key, and the longest criterion description.
MAX_CRITERION_KEY_CHARS = 200
MAX_CRITERION_CHARS = 4_000
#: The window for ``state`` plus the longest question, in tokens of four
#: characters.
MAX_STATE_TOKENS = 32_000
_CHARS_PER_TOKEN = 4


def split_questions(
    questions: Mapping[str, Question], size: int = MAX_QUESTIONS,
) -> list[dict[str, Question]]:
    """*questions* in parts of at most *size*, in their order.

    Each part is ONE request about the same ``state``. 20 questions give 2
    parts, 16 and 4. An empty mapping gives no part.
    """
    if size < 1:
        raise ValueError("size must be 1 or more")
    items = list(questions.items())
    return [dict(items[n:n + size]) for n in range(0, len(items), size)]


def shape_refusal(state: str | Mapping[str, Any] | list[Any], question: Question) -> str | None:
    """A reason CODE when the door would refuse *question* about *state*.

    ``None`` when the question fits. The code names the rule and holds no
    tenant text. It checks the rules of ONE question, and the window of
    *state* plus that question. The count of questions is
    :func:`split_questions`'s rule.
    """
    count = len(question.criteria)
    if isinstance(question, ChoiceQuestion) and count > MAX_CHOICE_OPTIONS:
        return "too_many_options"
    if isinstance(question, ScoreQuestion) and not (
        MIN_SCORE_LEVELS <= count <= MAX_SCORE_LEVELS
    ):
        return "score_levels"
    if len(question.instructions) > MAX_INSTRUCTIONS_CHARS:
        return "instructions_too_long"
    for key, value in question.criteria.items():
        if len(key) > MAX_CRITERION_KEY_CHARS:
            return "criterion_key_too_long"
        if len(value) > MAX_CRITERION_CHARS:
            return "criterion_too_long"
    text = state if isinstance(state, str) else json.dumps(
        dict(state) if isinstance(state, Mapping) else state, ensure_ascii=False,
    )
    size = len(question.instructions) + sum(
        len(k) + len(v) for k, v in question.criteria.items()
    )
    if math.ceil((len(text) + size) / _CHARS_PER_TOKEN) > MAX_STATE_TOKENS:
        return "window_too_large"
    return None
