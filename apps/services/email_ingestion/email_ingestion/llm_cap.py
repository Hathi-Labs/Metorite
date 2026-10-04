"""One cap and one daily budget for the model calls of the email automation.

WS-17 EM-T4b. Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6,
"EM-T4b". Three parts, and each one ships dark or harmless:

1. **The automation scope** (:func:`automation_scope`, :func:`automation_job`).
   A ContextVar holds the account id of one mailbox. The jobs that act on a
   mailbox open it: the hooks of the sync loop, the rules run, Process past,
   the Reply Zero jobs, the voice profile build, the learn-from-sent step and
   the two cleanup jobs. A call that a member drives never opens it: compose
   assist, a draft reply, the voice sample, the writing style, the rule
   generation and the chat. Outside the scope :func:`llm_slot` takes no
   permit and counts nothing, so a member never waits behind the sync loop.
2. **The cap.** One ``asyncio.Semaphore`` for the process, with
   ``email_llm_concurrency`` permits. 0 is the default, and 0 means no cap.
   One semaphore is correct because the gateway runs as ONE uvicorn process
   with no ``--workers`` (``deploy/hostinger/acb-gateway.service``).
3. **The budget.** A count of model requests for each mailbox for each UTC
   day, in tenant Redis, under the namespace :data:`BUDGET_NAMESPACE`.
   ``email_llm_budget_mode`` is ``off``, ``log`` or ``enforce``, and ``log``
   is the default. ``log`` never refuses a call. ``enforce`` raises
   :class:`LLMBudgetExhausted` past ``email_llm_daily_calls``, before the
   model call. ``enforce`` on a box is OWNER-GATE.

🔴 **``llm_slot`` wraps the LEAF model await only** (spec item 17). It never
wraps an enclosing function. A parent that held a permit while it awaited its
children would starve them at a cap of 1, and that is a deadlock. The decide
facade (``gateway.decide_features``) takes its own permit inside ``_ask_all``,
so a caller of ``ask`` or ``shadow`` holds no slot.

⚠️ **Re-entrant, as a guard** (spec item 7). A slot entered while this task
holds one takes no second permit and counts nothing. So does a slot in a task
that a held slot started. Such work is part of the call that holds the slot,
and the guard can never deadlock. The leaf rule keeps the case rare.

⚠️ **Order inside a slot: the permit, then the budget, then the call.** A call
that finds no free permit counts nothing. A Redis failure logs
``email.llm_budget_unavailable`` and the call runs, and the cap still binds.

⚠️ **This module must not import ``gateway``.** ``email_ingestion`` is the
lower layer (``tests/unit/test_email_layering.py``). The tenant comes from
``acb_common.db.current_tenant``, never from mail or a request.

Fence: ``tests/unit/test_email_llm_cap.py``.
"""

from __future__ import annotations

import asyncio
import functools
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from acb_common.tenant_redis import get_tenant_redis, key, organization_scope

__all__ = [
    "BUDGET_MODES",
    "BUDGET_NAMESPACE",
    "BUDGET_REDIS_TIMEOUT_S",
    "BUDGET_TTL_S",
    "CAP_WAIT_LOG_S",
    "DEFAULT_BUDGET_MODE",
    "THRESHOLDS_PCT",
    "LLMBudgetExhausted",
    "NoFreeSlot",
    "automation_job",
    "automation_scope",
    "budget_mode",
    "current_account",
    "llm_slot",
    "permits_in_use",
]

_log = get_logger("email_ingestion.llm_cap")

#: The Redis namespace of the budget. The key is
#: ``key(BUDGET_NAMESPACE, account_id, <UTC date>)``, so the wrapper puts the
#: organization in front of it, and two organizations never share a count.
BUDGET_NAMESPACE = "email-llm"

#: The life of one day's count. Two days, so a key outlives its own day.
BUDGET_TTL_S = 2 * 24 * 60 * 60

#: A wait for a permit longer than this logs ``email.llm_cap_wait``.
CAP_WAIT_LOG_S = 1.0

#: The bound on the two Redis commands of one count. A Redis that hangs must
#: not hold a model call, so past it the call runs uncounted.
BUDGET_REDIS_TIMEOUT_S = 2.0

#: The three budget modes, and the default.
BUDGET_MODES: tuple[str, ...] = ("off", "log", "enforce")
DEFAULT_BUDGET_MODE = "log"

#: The share of the limit at which ``email.llm_budget_count`` logs, once a
#: day for each mailbox (spec R-6).
THRESHOLDS_PCT: tuple[int, ...] = (50, 100)


class LLMBudgetExhausted(RuntimeError):
    """``enforce``: the mailbox spent its model requests for this UTC day.

    Raised before the model call, so no call is made. The rule match turns
    it into ``LLMUnavailable``, so the runner leaves ``rules_processed_at``
    NULL and a later cycle retries the mail (D-EM-8). The message holds the
    limit only, never tenant text.
    """


class NoFreeSlot(RuntimeError):
    """``llm_slot(wait=False)`` found no free permit. Nothing was counted.

    Only the ``decide`` shadow asks without a wait (spec item 16). It then
    logs ``decide.shadow_skipped`` with ``reason=cap`` and makes no call.
    """


# ── The automation scope ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Scope:
    """One mailbox job. ``account_id`` is the id the server holds."""

    account_id: str


_SCOPE: ContextVar[_Scope | None] = ContextVar("email_llm_scope", default=None)


@contextmanager
def automation_scope(account_id: Any) -> Iterator[None]:
    """Mark the calls in this block as the automation of one mailbox.

    The cap and the budget bind each model call inside. A falsy account id
    still opens the scope, so the cap binds, but the budget then counts
    nothing, because it has no mailbox to count against.
    """
    token = _SCOPE.set(_Scope(str(account_id).strip() if account_id else ""))
    try:
        yield
    finally:
        _reset(_SCOPE, token)


def automation_job[T](fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Open :func:`automation_scope` around a job whose first argument is the
    account id. The job's signature does not change."""

    @functools.wraps(fn)
    async def _wrapped(account_id: Any, *args: Any, **kwargs: Any) -> T:
        with automation_scope(account_id):
            return await fn(account_id, *args, **kwargs)

    return _wrapped


def current_account() -> str | None:
    """The account id of the open scope, or None outside the scope."""
    scope = _SCOPE.get()
    return scope.account_id if scope is not None else None


# ── The cap ─────────────────────────────────────────────────────────────────


class _Hold:
    """One held slot. ``active`` turns False when the slot ends, so a task
    that outlives the slot takes its own permit later."""

    __slots__ = ("active",)

    def __init__(self) -> None:
        self.active = True


_HOLD: ContextVar[_Hold | None] = ContextVar("email_llm_hold", default=None)

#: ``(loop, size, semaphore)``. A semaphore binds to one event loop, so a new
#: loop (each test has one) or a new size makes a new one. The box has one
#: loop and one size for the life of the process.
_SEM: tuple[Any, int, asyncio.Semaphore] | None = None

#: The permits held now, for the fences.
_in_use = 0


def _cap() -> int:
    try:
        return max(0, int(get_settings().email_llm_concurrency or 0))
    except (TypeError, ValueError):
        return 0


def _semaphore() -> asyncio.Semaphore | None:
    """The semaphore of the cap, or None when the cap is 0 (off)."""
    global _SEM
    size = _cap()
    if size <= 0:
        return None
    loop = asyncio.get_running_loop()
    if _SEM is None or _SEM[0] is not loop or _SEM[1] != size:
        _SEM = (loop, size, asyncio.Semaphore(size))
    return _SEM[2]


def permits_in_use() -> int:
    """The permits that model calls hold now. 0 with the cap off."""
    return _in_use


def _reset(var: ContextVar[Any], token: Token[Any]) -> None:
    """Undo a ``set``. A token from another context falls back to None."""
    try:
        var.reset(token)
    except (ValueError, RuntimeError):
        var.set(None)


@asynccontextmanager
async def llm_slot(*, requests: int = 1, wait: bool = True) -> AsyncIterator[None]:
    """Hold one permit of the cap, and count ``requests`` against the budget.

    Wrap the LEAF model await and nothing else (spec item 17)::

        async with llm_slot():
            resp, used = await acompletion_with_fallback(...)

    - Outside the automation scope it takes no permit and counts nothing.
    - Inside a held slot of this task, or of the task that started this one,
      it takes no second permit and counts nothing.
    - ``wait=False`` raises :class:`NoFreeSlot` at once when the cap is full.
    - ``enforce`` past the limit raises :class:`LLMBudgetExhausted` before
      the body runs.
    """
    global _in_use
    scope = _SCOPE.get()
    hold = _HOLD.get()
    if scope is None or (hold is not None and hold.active):
        yield
        return
    sem = _semaphore()
    acquired = False
    mine = _Hold()
    token: Token[Any] | None = None
    try:
        if sem is not None:
            if not wait and sem.locked():
                raise NoFreeSlot("no free model slot")
            started = time.monotonic()
            await sem.acquire()
            acquired = True
            _in_use += 1
            waited = time.monotonic() - started
            if waited > CAP_WAIT_LOG_S:
                _log.info(
                    "email.llm_cap_wait",
                    account_id=scope.account_id,
                    wait_ms=int(waited * 1000),
                    cap=_SEM[1] if _SEM else None,
                )
        token = _HOLD.set(mine)
        await _charge(scope.account_id, max(1, int(requests)))
        yield
    finally:
        mine.active = False
        if token is not None:
            _reset(_HOLD, token)
        if acquired and sem is not None:
            _in_use -= 1
            sem.release()


# ── The budget ──────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=8)
def _parse_mode(raw: str) -> str:
    """The budget mode of ``raw``. An unknown value reads as the default,
    ``log``, which never refuses a call, and logs once for each value."""
    mode = (raw or "").strip().lower()
    if not mode:
        return DEFAULT_BUDGET_MODE
    if mode in BUDGET_MODES:
        return mode
    _log.warning("email.llm_budget_mode_refused", value=mode[:20], used=DEFAULT_BUDGET_MODE)
    return DEFAULT_BUDGET_MODE


def budget_mode() -> str:
    """The budget mode of this process."""
    return _parse_mode(str(getattr(get_settings(), "email_llm_budget_mode", "") or ""))


def _now() -> datetime:
    """The clock of the budget day. A test may replace it."""
    return datetime.now(UTC)


def _utc_day() -> str:
    return _now().strftime("%Y-%m-%d")


async def _incr(org: str, account_id: str, amount: int) -> int:
    """Add ``amount`` to today's count of the mailbox, and return the count.

    The idiom of the attachment cache (``transport/attachments.py``): bind
    the organization for the call, then build the key inside the binding.
    """
    with organization_scope(org):
        client = get_tenant_redis()
        k = key(BUDGET_NAMESPACE, account_id, _utc_day())
        count = int(await client.incr(k, amount))
        await client.expire(k, BUDGET_TTL_S)
    return count


async def _charge(account_id: str, requests: int) -> None:
    """Count ``requests`` model requests for the mailbox, in ``log`` and in
    ``enforce``. Raises :class:`LLMBudgetExhausted` in ``enforce`` past the
    limit. Never raises for a Redis failure."""
    mode = budget_mode()
    if mode == "off" or not account_id:
        return
    try:
        limit = int(get_settings().email_llm_daily_calls or 0)
    except (TypeError, ValueError):
        limit = 0
    ids = {"account_id": account_id, "mode": mode}
    org = current_tenant()
    if not org:
        _log.warning("email.llm_budget_unavailable", **ids, reason="no_tenant")
        return
    try:
        async with asyncio.timeout(BUDGET_REDIS_TIMEOUT_S):
            count = await _incr(str(org), account_id, requests)
    except Exception as exc:  # Redis down, a timeout, an org id Redis refuses
        _log.warning("email.llm_budget_unavailable", **ids, reason=type(exc).__name__)
        return
    if limit < 1:
        return  # no limit: count only
    before = count - requests
    for pct in THRESHOLDS_PCT:
        mark = max(1, math.ceil(limit * pct / 100))
        if before < mark <= count:
            _log.info("email.llm_budget_count", **ids, pct=pct, count=count, limit=limit)
    if before <= limit < count:
        _log.warning("email.llm_budget_exceeded", **ids, count=count, limit=limit)
    if mode == "enforce" and count > limit:
        raise LLMBudgetExhausted(f"the mailbox spent its {limit} model requests for today")


def reset_for_tests() -> None:
    """Drop the semaphore, the count of held permits and the parsed mode.
    Tests only."""
    global _SEM, _in_use
    _SEM = None
    _in_use = 0
    _parse_mode.cache_clear()
