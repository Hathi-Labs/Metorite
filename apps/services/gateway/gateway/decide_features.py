"""The ``decide`` mode registry, and the one shadow helper (WS-17 EM-T5, CP-13e).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.4 and §10.4.8
(EM-T5b-1), the four adoption rules in
``project-docs/specs/customer_console.md`` §6A.14, and its "Question
conventions" table.

Each feature that may ask ``acb_llm.decide`` has ONE mode here: ``off``,
``shadow`` or ``on``. Every default is ``off``. Two settings override it:

- ``decide_feature_modes`` holds ``feature=mode`` pairs, with a comma between
  pairs.
- ``decide_feature_orgs`` lists the organization ids that may run a mode
  other than ``off``. An empty list allows no organization.

🔴 **``on`` is still refused.** It resolves to ``off`` and logs
``decide.mode_refused``. EM-T5b-2 lifts this for the four email features. An
unknown feature or an unknown mode also resolves to ``off`` and logs
``decide.mode_refused``.

EM-T5b-1 (2026-10-02): ``email.rule_pick`` is now ``email.rule_match``, which
covers both the one-rule mode and the multi-rule mode. A ``build`` may return
a LIST of requests. :func:`shadow` runs them at the same time inside the one
bound, and merges the answers by question id. One failed request means no
decision for that email.

🔴 **The feature ALWAYS acts on the old answer.** :func:`shadow` runs the old
LLM call and ``decide`` at the same time, logs both answers, and returns the
old one. ``decide`` has a 5-second bound, so a slow ``decide`` cannot delay
the old answer by more than that. No error from ``decide`` stops triage.

⚠️ **When the mode is ``off``, the helper awaits the old call and does
nothing else.** It builds no question, imports no part of ``acb_llm`` and
makes no call.

⚠️ **The identity comes from the run context** (``acb_llm.routed.
run_attribution``), never from mail or from a request. ``job_member_scope``
binds the mailbox owner for the email jobs.

⚠️ **The log lines carry no tenant text.** They hold the feature, the account
id, the message id, option keys or booleans, numbers and each ``request_id``.
They hold no subject, body, sender, reason or rule name.

H-44's feature-to-tier registry absorbs this registry when H-44 is built. It
holds a mode and no tier, so it is not a second vocabulary.

Fences: ``tests/unit/test_email_decide_shadow.py`` and
``tests/unit/test_email_decide_questions.py``.
"""
from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from acb_common import get_logger, get_settings

from gateway.db import current_tenant

__all__ = [
    "CHOICE_OPTION_LIMIT",
    "CHOICE_TEXT_BUDGET",
    "CORRECTIONS_CLIP",
    "CRITERION_CLIP",
    "DEFAULT_MODES",
    "FEATURES",
    "MODES",
    "QUESTION_LIMIT",
    "SHADOW_BOUND_S",
    "Comparison",
    "clip",
    "clip_choice",
    "clip_fact",
    "compare_boolean",
    "compare_choice",
    "instructions",
    "mode_for",
    "shadow",
    "top_margin",
]

_log = get_logger("gateway.decide_features")

#: The four features, in the order of the spec (§10.4.4 item 9). EM-T5b-1
#: renamed ``email.rule_pick`` to ``email.rule_match``, because it now covers
#: the multi-rule mode too. The old name is unknown, so it resolves to ``off``
#: and logs ``decide.mode_refused``.
FEATURES: tuple[str, ...] = (
    "email.cold_check",
    "email.sender_pin",
    "email.thread_status",
    "email.rule_match",
)

#: The three modes. ``on`` is a word the parser knows and still refuses.
MODES: tuple[str, ...] = ("off", "shadow", "on")

#: Every feature starts ``off``.
DEFAULT_MODES: Mapping[str, str] = MappingProxyType({f: "off" for f in FEATURES})

#: The bound on the ``decide`` requests of one shadow call, in seconds. Read
#: at call time, so a test may make it short. The default is the spec's 5
#: seconds. All the requests of one call share this one bound.
SHADOW_BOUND_S: float = 5.0

#: The Console's limit on the options of one choice question
#: (``customer_console/decide.py`` ``MAX_CHOICE_OPTIONS``). A choice over N
#: rules has N + 1 options, so N may not be more than 254.
CHOICE_OPTION_LIMIT = 255

#: The Console's limit on the questions of one request
#: (``customer_console/decide.py`` ``MAX_QUESTIONS``). A longer list goes out
#: as more requests, which run at the same time (§10.4.8 item 7).
QUESTION_LIMIT = 16

#: Each criterion is clipped to this many characters. The Console allows
#: 4000, and the spec clips at 1000 (§10.4.4 item 10).
CRITERION_CLIP = 1000

#: The characters that ALL the options of one choice share. The Console
#: refuses a request when ``state`` plus the longest question passes 32k
#: tokens (128k characters). A choice over 254 rules at 1000 characters each
#: would pass it, so each option gets an equal share of this budget, and
#: never more than :data:`CRITERION_CLIP`.
CHOICE_TEXT_BUDGET = 80_000

#: The user's corrections in an ``instructions`` text are clipped to this
#: many characters (§10.4.8 item 6).
CORRECTIONS_CLIP = 1500

#: The header of the corrections block. The words are the spec's.
_CORRECTIONS_HEADER = "Corrections from the user. They override the guidance:"


def clip(text: str, limit: int = CRITERION_CLIP) -> str:
    """``text`` cut to ``limit`` characters."""
    return text if len(text) <= limit else text[:limit]


#: The characters that JSON gives to each character it escapes. Every other
#: control character becomes ``\\u00XX``, six characters.
_SHORT_ESCAPES = frozenset('"\\\b\f\n\r\t')


def _escaped_length(ch: str) -> int:
    if ch in _SHORT_ESCAPES:
        return 2
    return 6 if ord(ch) < 0x20 else 1


def clip_fact(value: Any, limit: int) -> str:
    """A fact for a ``decide`` state, as text, bounded twice.

    The text keeps ``limit`` characters or fewer, as the old prompt did. Its
    JSON-escaped form also keeps ``2 * limit`` characters or fewer. The
    Console measures a state as the JSON that it receives, where one control
    character takes six characters. So a raw clip alone lets a hostile email
    push a request past the window, and a refused request is a lost decision.
    Ordinary text (newlines and quotes take two) keeps its full raw length.
    A non-text value becomes text, and None becomes "".
    """
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = clip(text, limit)
    budget = 2 * limit
    if len(text) * 6 <= budget:
        return text
    used = 0
    for i, ch in enumerate(text):
        used += _escaped_length(ch)
        if used > budget:
            return text[:i]
    return text


def clip_choice(criteria: Mapping[str, str]) -> dict[str, str]:
    """The options of one choice, each clipped to its share of the budget."""
    share = max(1, min(CRITERION_CLIP, CHOICE_TEXT_BUDGET // max(1, len(criteria))))
    return {key: clip(text, share) for key, text in criteria.items()}


def instructions(
    question: str, guidance: Sequence[str], corrections: Sequence[str] = (),
) -> str:
    """One question line, a "Guidance:" block, and the user's corrections.

    The wire takes ``instructions`` as a string (§6A.14 "Question
    conventions"). Each ``guidance`` line carries its own leading ``"- "``.
    The corrections keep the order given, and the caller passes them newest
    first. Their text is clipped to :data:`CORRECTIONS_CLIP` characters.
    """
    parts = [question]
    if guidance:
        parts.append("Guidance:")
        parts.extend(guidance)
    notes = [n.strip() for n in corrections if n and n.strip()]
    if notes:
        parts.append(_CORRECTIONS_HEADER)
        parts.append(clip("\n".join(f"- {n}" for n in notes), CORRECTIONS_CLIP))
    return "\n".join(parts)


def top_margin(probabilities: Mapping[str, float]) -> float | None:
    """The top probability minus the second one, or None with fewer than two."""
    values = sorted((float(v) for v in probabilities.values()), reverse=True)
    if len(values) < 2:
        return None
    return round(values[0] - values[1], 4)


# ── The registry ────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=32)
def _parse_modes(raw: str) -> Mapping[str, str]:
    """The modes that ``raw`` sets, on top of the defaults.

    Cached on the raw string, so a refused pair logs once for each process
    and each value, not once for each email.
    """
    modes = dict(DEFAULT_MODES)
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        feature, sep, mode = pair.partition("=")
        feature, mode = feature.strip(), mode.strip().lower()
        if not sep or feature not in modes or mode not in MODES:
            _log.warning(
                "decide.mode_refused",
                decide_feature=feature[:80],
                decide_mode=mode[:20],
                decide_reason="unknown",
            )
            if feature in modes:
                # A refused value turns a KNOWN feature off, also after an
                # earlier valid pair for it, exactly as `on` does (item 2).
                modes[feature] = "off"
            continue
        if mode == "on":
            # 🔴 `on` waits for EM-T5b-2, so it is refused (item 3).
            _log.warning(
                "decide.mode_refused",
                decide_feature=feature,
                decide_mode=mode,
                decide_reason="on_refused",
            )
            modes[feature] = "off"
            continue
        modes[feature] = mode
    return MappingProxyType(modes)


@functools.lru_cache(maxsize=32)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The organization ids in ``raw``. Empty means none."""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def mode_for(feature: str) -> str:
    """The mode of ``feature`` for THIS call.

    The configured mode, and then the organization list. The organization
    comes from ``current_tenant()``. A call with no tenant, or with a tenant
    not on the list, is ``off``.
    """
    settings = get_settings()
    mode = _parse_modes(settings.decide_feature_modes or "").get(feature, "off")
    if mode == "off":
        return "off"
    org = current_tenant()
    if not org or str(org) not in _parse_orgs(settings.decide_feature_orgs or ""):
        return "off"
    return mode


# ── The comparison ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Comparison:
    """The two answers of one shadow call, as the log line needs them.

    ``old`` and ``new`` are booleans or option keys, never tenant text.
    ``confidence`` is set for a choice and ``probability`` for a boolean.
    ``extra`` holds feature-specific flags of the OLD answer, such as the old
    ``confident``, and each logs as ``old_<key>``. ``fields`` holds more keys
    and numbers, such as a margin or ``p_r0``, and each logs as it is named.
    """

    old: Any
    new: Any
    agree: bool
    options: int
    confidence: float | None = None
    probability: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    fields: Mapping[str, Any] = field(default_factory=dict)


def compare_boolean(
    old_of: Callable[[Any], bool], *, qid: str, threshold: float = 0.5
) -> Callable[[Any, Any], Comparison]:
    """A comparison for one boolean question.

    ``new`` is True when the probability is at ``threshold`` or above.
    """

    def _compare(old_result: Any, decision: Any) -> Comparison:
        probability = float(decision[qid].probability)
        old = bool(old_of(old_result))
        new = probability >= threshold
        return Comparison(
            old=old, new=new, agree=old == new, options=2, probability=probability
        )

    return _compare


def compare_choice(
    old_key_of: Callable[[Any], str],
    *,
    qid: str,
    options: int,
    extra_of: Callable[[Any], Mapping[str, Any]] | None = None,
    keys: Collection[str] | None = None,
) -> Callable[[Any, Any], Comparison]:
    """A comparison for one choice question. Keys are compared as given.

    With ``keys``, an answer that is not one of our option keys logs as
    ``"unknown"``, so the log never carries a raw string from the vendor.
    The log also gets the margin of the top two probabilities (§6A.14
    "Question conventions").
    """

    def _compare(old_result: Any, decision: Any) -> Comparison:
        answer = decision[qid]
        old = old_key_of(old_result)
        new = answer.choice if keys is None or answer.choice in keys else "unknown"
        return Comparison(
            old=old,
            new=new,
            agree=old == new,
            options=options,
            confidence=answer.confidence,
            extra=dict(extra_of(old_result)) if extra_of else {},
            fields={"margin": top_margin(answer.probabilities)},
        )

    return _compare


# ── The helper ──────────────────────────────────────────────────────────────

#: One request: the state and its questions.
Request = tuple[Any, Mapping[str, Any]]

#: What a site hands over: one request, a list of requests, or None to skip.
Build = Callable[[], "Request | list[Request] | None"]


@dataclass(frozen=True)
class _Asked:
    """The merged answer of all the requests of one call."""

    decision: Any
    request_ids: tuple[str | None, ...]
    latency_ms: int
    questions: int
    requests: int


def _requests_of(built: Any) -> list[Request]:
    """The requests a ``build`` returned. A tuple is one request, a list is
    many, and a request with no questions is dropped."""
    if built is None:
        return []
    many = built if isinstance(built, list) else [built]
    return [(state, questions) for state, questions in many if questions]


async def _ask_all(
    feature: str,
    ids: Mapping[str, Any],
    requests: list[Request],
    attribution: Mapping[str, Any],
) -> _Asked | None:
    """All the ``decide`` requests of one call, at the same time, inside one
    bound. Returns the merged answers, or None.

    Never raises. One failed request means no decision, because a partial
    set of answers is not a decision about the email (§10.4.8 item 7). Each
    failure logs its own line.
    """
    # The package attributes, as `acb_skills/decide_tools.py` reads them, so
    # one monkeypatch of `acb_llm.decide` reaches every caller.
    from acb_llm import DecideRequestInvalid, DecideUnavailable, Decision
    from acb_llm import decide as facade

    started = time.monotonic()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(facade(state, questions, **attribution) for state, questions in requests),
                return_exceptions=True,
            ),
            timeout=SHADOW_BOUND_S,
        )
    except TimeoutError:
        _log.info("decide.fallback", **ids, decide_reason="timeout")
        return None
    except Exception as exc:  # a broken facade never stops triage
        _log.warning("decide.shadow_failed", **ids, error_type=type(exc).__name__)
        return None

    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        invalid = next((e for e in failures if isinstance(e, DecideRequestInvalid)), None)
        unavailable = next((e for e in failures if isinstance(e, DecideUnavailable)), None)
        if invalid is not None:
            # A caller bug. Loud, and it never stops triage (item 8).
            _log.error(
                "decide.shadow_invalid",
                **ids,
                decide_reason=invalid.reason,
                decide_status=invalid.status,
            )
        elif unavailable is not None:
            _log.info("decide.fallback", **ids, decide_reason=unavailable.reason)
        else:
            # The type name only. A message can quote the request.
            _log.warning(
                "decide.shadow_failed", **ids, error_type=type(failures[0]).__name__
            )
        return None

    try:
        answers: dict[str, Any] = {}
        for decision in results:
            answers.update(decision.answers)
        request_ids = tuple(getattr(d, "request_id", None) for d in results)
        merged = Decision(answers=MappingProxyType(answers), request_id=request_ids[0])
    except Exception as exc:  # a reply that is not a Decision never stops triage
        _log.warning("decide.shadow_failed", **ids, error_type=type(exc).__name__)
        return None
    return _Asked(
        decision=merged,
        request_ids=request_ids,
        latency_ms=_ms(started),
        questions=sum(len(q) for _, q in requests),
        requests=len(requests),
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def shadow[T](
    feature: str,
    old: Callable[[], Awaitable[T]],
    *,
    account_id: str | None,
    build: Build,
    compare: Callable[[T, Any], Comparison],
    message_id: str | None = None,
) -> T:
    """Run the old call, and in ``shadow`` mode ask ``decide`` beside it.

    ALWAYS returns the old call's result, and re-raises its exception.

    - ``old``: a no-argument function that makes the old LLM call.
    - ``build``: returns one ``(state, questions)`` request, a LIST of them,
      or None to skip ``decide`` for this call. It runs only in ``shadow``
      mode. A list runs at the same time, inside the one bound.
    - ``compare``: turns the old result and the merged ``Decision`` into a
      :class:`Comparison` for the log line.
    - ``message_id``: the email this call is about. Each log line holds it,
      so a reader can find the email without its text.
    """
    if mode_for(feature) != "shadow":
        return await old()

    ids = {
        "decide_feature": feature,
        "account_id": account_id,
        "message_id": str(message_id) if message_id is not None else None,
    }
    try:
        requests = _requests_of(build())
    except Exception as exc:  # a bad question never stops triage
        _log.warning("decide.shadow_failed", **ids, error_type=type(exc).__name__)
        requests = []
    if not requests:
        _log.info("decide.shadow_skipped", **ids)
        return await old()

    from acb_llm.routed import run_attribution

    task = asyncio.create_task(
        _ask_all(feature, ids, requests, run_attribution())
    )
    try:
        result = await old()
    except BaseException:
        task.cancel()
        raise
    # The task is bounded by SHADOW_BOUND_S from its start, so this wait adds
    # at most that much to the old answer.
    asked = await task
    if asked is not None:
        _log_comparison(ids, result, asked, compare)
    return result


def _log_comparison(
    ids: Mapping[str, Any],
    result: Any,
    asked: _Asked,
    compare: Callable[[Any, Any], Comparison],
) -> None:
    try:
        cmp = compare(result, asked.decision)
    except Exception as exc:  # the log line must never break triage
        _log.warning("decide.shadow_failed", **ids, error_type=type(exc).__name__)
        return
    record: dict[str, Any] = {f"old_{k}": v for k, v in cmp.extra.items()}
    record.update(cmp.fields)
    # The fixed keys win over a feature's own, so no feature can rename them.
    record.update(
        ids,
        old=cmp.old,
        new=cmp.new,
        agree=cmp.agree,
        confidence=cmp.confidence,
        probability=cmp.probability,
        latency_ms=asked.latency_ms,
        options=cmp.options,
        request_id=asked.request_ids[0],
        request_ids=list(asked.request_ids),
        questions=asked.questions,
        requests=asked.requests,
    )
    _log.info("decide.shadow", **record)
