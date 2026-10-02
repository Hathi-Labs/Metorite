"""The ``decide`` mode registry, and the one shadow helper (WS-17 EM-T5, CP-13e).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.4, and the four
adoption rules in ``project-docs/specs/customer_console.md`` §6A.14.

Each feature that may ask ``acb_llm.decide`` has ONE mode here: ``off``,
``shadow`` or ``on``. Every default is ``off``. Two settings override it:

- ``decide_feature_modes`` holds ``feature=mode`` pairs, with a comma between
  pairs.
- ``decide_feature_orgs`` lists the organization ids that may run a mode
  other than ``off``. An empty list allows no organization.

🔴 **EM-T5 refuses ``on``.** It resolves to ``off`` and logs
``decide.mode_refused``. EM-T5b lifts this with measured thresholds and a
confidence gate. An unknown feature or an unknown mode also resolves to
``off`` and logs ``decide.mode_refused``.

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
id, option keys or booleans, numbers and the ``request_id``. They hold no
subject, body, sender, reason or rule name.

H-44's feature-to-tier registry absorbs this registry when H-44 is built. It
holds a mode and no tier, so it is not a second vocabulary.

Fence: ``tests/unit/test_email_decide_shadow.py``.
"""
from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from acb_common import get_logger, get_settings

from gateway.db import current_tenant

__all__ = [
    "CHOICE_OPTION_LIMIT",
    "CRITERION_CLIP",
    "DEFAULT_MODES",
    "FEATURES",
    "MODES",
    "SHADOW_BOUND_S",
    "Comparison",
    "clip",
    "compare_boolean",
    "compare_choice",
    "mode_for",
    "shadow",
]

_log = get_logger("gateway.decide_features")

#: The four features EM-T5 wires, in the order of the spec (§10.4.4 item 9).
FEATURES: tuple[str, ...] = (
    "email.cold_check",
    "email.sender_pin",
    "email.thread_status",
    "email.rule_pick",
)

#: The three modes. ``on`` is a word the parser knows and refuses in EM-T5.
MODES: tuple[str, ...] = ("off", "shadow", "on")

#: Every feature starts ``off``.
DEFAULT_MODES: Mapping[str, str] = MappingProxyType({f: "off" for f in FEATURES})

#: The bound on one shadow ``decide`` call, in seconds. Read at call time, so
#: a test may make it short. The default is the spec's 5 seconds.
SHADOW_BOUND_S: float = 5.0

#: The Console's limit on the options of one choice question
#: (``customer_console/decide.py`` ``MAX_CHOICE_OPTIONS``). A rule question
#: with N rules has N + 1 options, so N may not be more than 254.
CHOICE_OPTION_LIMIT = 255

#: Each criterion is clipped to this many characters. The Console allows
#: 4000, and the spec clips at 1000 (§10.4.4 item 10).
CRITERION_CLIP = 1000


def clip(text: str, limit: int = CRITERION_CLIP) -> str:
    """``text`` cut to ``limit`` characters."""
    return text if len(text) <= limit else text[:limit]


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
            # 🔴 EM-T5 has no confidence gate, so `on` is refused (item 3).
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
    ``extra`` holds feature-specific flags, such as the old ``confident``.
    """

    old: Any
    new: Any
    agree: bool
    options: int
    confidence: float | None = None
    probability: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


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
) -> Callable[[Any, Any], Comparison]:
    """A comparison for one choice question. Keys are compared as given."""

    def _compare(old_result: Any, decision: Any) -> Comparison:
        answer = decision[qid]
        old = old_key_of(old_result)
        return Comparison(
            old=old,
            new=answer.choice,
            agree=old == answer.choice,
            options=options,
            confidence=answer.confidence,
            extra=dict(extra_of(old_result)) if extra_of else {},
        )

    return _compare


# ── The helper ──────────────────────────────────────────────────────────────

#: What a site hands over: the state and the questions, or None to skip.
Build = Callable[[], "tuple[Any, Mapping[str, Any]] | None"]


async def _ask(
    feature: str,
    account_id: str | None,
    state: Any,
    questions: Mapping[str, Any],
    attribution: Mapping[str, Any],
) -> tuple[Any, float]:
    """One bounded ``decide`` call. Returns ``(decision or None, latency_ms)``.

    Never raises. Each failure logs its own line and returns None.
    """
    # The package attribute, as `acb_skills/decide_tools.py` reads it, so one
    # monkeypatch of `acb_llm.decide` reaches every caller.
    from acb_llm import DecideRequestInvalid, DecideUnavailable
    from acb_llm import decide as facade

    started = time.monotonic()
    try:
        decision = await asyncio.wait_for(
            facade(state, questions, **attribution),
            timeout=SHADOW_BOUND_S,
        )
    except DecideUnavailable as exc:
        _log.info(
            "decide.fallback",
            decide_feature=feature,
            account_id=account_id,
            decide_reason=exc.reason,
        )
        return None, _ms(started)
    except TimeoutError:
        _log.info(
            "decide.fallback",
            decide_feature=feature,
            account_id=account_id,
            decide_reason="timeout",
        )
        return None, _ms(started)
    except DecideRequestInvalid as exc:
        # A caller bug. Loud, and it never stops triage (item 8).
        _log.error(
            "decide.shadow_invalid",
            decide_feature=feature,
            account_id=account_id,
            decide_reason=exc.reason,
            decide_status=exc.status,
        )
        return None, _ms(started)
    except Exception as exc:  # shadow must never break triage
        # The type name only. A message can quote the request.
        _log.warning(
            "decide.shadow_failed",
            decide_feature=feature,
            account_id=account_id,
            error_type=type(exc).__name__,
        )
        return None, _ms(started)
    return decision, _ms(started)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def shadow[T](
    feature: str,
    old: Callable[[], Awaitable[T]],
    *,
    account_id: str | None,
    build: Build,
    compare: Callable[[T, Any], Comparison],
) -> T:
    """Run the old call, and in ``shadow`` mode ask ``decide`` beside it.

    ALWAYS returns the old call's result, and re-raises its exception.

    - ``old``: a no-argument function that makes the old LLM call.
    - ``build``: returns ``(state, questions)``, or None to skip ``decide``
      for this call (for example, more options than the Console allows). It
      runs only in ``shadow`` mode.
    - ``compare``: turns the old result and the ``Decision`` into a
      :class:`Comparison` for the log line.
    """
    if mode_for(feature) != "shadow":
        return await old()

    try:
        request = build()
    except Exception as exc:  # a bad question never stops triage
        _log.warning(
            "decide.shadow_failed",
            decide_feature=feature,
            account_id=account_id,
            error_type=type(exc).__name__,
        )
        request = None
    if request is None:
        _log.info(
            "decide.shadow_skipped", decide_feature=feature, account_id=account_id
        )
        return await old()

    from acb_llm.routed import run_attribution

    state, questions = request
    task = asyncio.create_task(
        _ask(feature, account_id, state, questions, run_attribution())
    )
    try:
        result = await old()
    except BaseException:
        task.cancel()
        raise
    # The task is bounded by SHADOW_BOUND_S from its start, so this wait adds
    # at most that much to the old answer.
    decision, latency_ms = await task
    if decision is not None:
        _log_comparison(feature, account_id, result, decision, compare, latency_ms)
    return result


def _log_comparison(
    feature: str,
    account_id: str | None,
    result: Any,
    decision: Any,
    compare: Callable[[Any, Any], Comparison],
    latency_ms: int,
) -> None:
    try:
        cmp = compare(result, decision)
    except Exception as exc:  # the log line must never break triage
        _log.warning(
            "decide.shadow_failed",
            decide_feature=feature,
            account_id=account_id,
            error_type=type(exc).__name__,
        )
        return
    _log.info(
        "decide.shadow",
        decide_feature=feature,
        account_id=account_id,
        old=cmp.old,
        new=cmp.new,
        agree=cmp.agree,
        confidence=cmp.confidence,
        probability=cmp.probability,
        latency_ms=latency_ms,
        options=cmp.options,
        request_id=getattr(decision, "request_id", None),
        **{f"old_{k}": v for k, v in cmp.extra.items()},
    )
