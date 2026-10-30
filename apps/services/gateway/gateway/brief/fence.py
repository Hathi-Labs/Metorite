"""The claim fence of the brief phrase step (WS-BRIEF BR-4b).

Spec: ``project-docs/specs/core_brief.md`` §4.4. A pure check: no database,
no model, no clock. BR-2a-2 calls :func:`check_phrase` on the parsed answer
of the model, and on a failure keeps the template lead and logs
``brief.phrase_refused`` with :attr:`FenceResult.reason`.

The rules, in order. The first that fails sets the reason.

* ``malformed``: the answer is not a non-empty list of objects with a
  non-empty ``text`` string and a list of string ``claims``.
* ``too_many_sentences``: more than :data:`MAX_SENTENCES` sentences.
* ``no_claim_id``: a sentence cites no claim.
* ``unknown_claim``: a sentence cites an id that is not a claim of the run.
* ``unbacked_number``: a sentence holds a number that no cited claim holds.

Numbers come from :func:`acb_common.assistant_profile.numbers_in`, the one
extractor. This module has no number pattern of its own. So "₹500" and "500"
are the same number, and "1,200" is "1" and "200".

The module judges the lead only. It never changes the words of a claim line.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acb_common.assistant_profile import numbers_in

MAX_SENTENCES = 3


@dataclass(frozen=True)
class FenceResult:
    """``ok`` is True, or ``reason`` holds a short stable code."""

    ok: bool
    reason: str | None = None


def _pass() -> FenceResult:
    return FenceResult(True)


def _fail(reason: str) -> FenceResult:
    return FenceResult(False, reason)


def _held(claim: Any) -> str:
    """The text that a claim holds: its words and its value."""
    if isinstance(claim, (list, tuple, set, frozenset)):
        return " ".join(str(part) for part in claim if part is not None)
    return "" if claim is None else str(claim)


def check_phrase(answer: Any, claims: Mapping[str, Any]) -> FenceResult:
    """Judge *answer* against *claims* (id to the text or value it holds).
    Never raises: a bad input is ``malformed``."""
    try:
        return _check(answer, claims)
    except Exception:
        return _fail("malformed")


def _check(answer: Any, claims: Mapping[str, Any]) -> FenceResult:
    if not isinstance(answer, list) or not answer or not isinstance(claims, Mapping):
        return _fail("malformed")
    parsed: list[tuple[str, list[str]]] = []
    for item in answer:
        if not isinstance(item, dict):
            return _fail("malformed")
        sentence, ids = item.get("text"), item.get("claims")
        if not isinstance(sentence, str) or not sentence.strip():
            return _fail("malformed")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return _fail("malformed")
        parsed.append((sentence, ids))
    if len(parsed) > MAX_SENTENCES:
        return _fail("too_many_sentences")
    for sentence, ids in parsed:
        if not ids:
            return _fail("no_claim_id")
        if any(i not in claims for i in ids):
            return _fail("unknown_claim")
        backed: set[str] = set()
        for i in ids:
            backed |= numbers_in(_held(claims[i]))
        if not numbers_in(sentence) <= backed:
            return _fail("unbacked_number")
    return _pass()
