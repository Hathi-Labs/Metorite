"""One cap and one daily budget for the model calls of the email automation.

WS-17 EM-T4b. Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6,
"EM-T4b". Three parts, and each one ships dark or harmless:

1. **The automation scope** (:func:`automation_scope`, :func:`automation_job`).
   A ContextVar holds the account id of one mailbox. The jobs that act on a
   mailbox open it: the hooks of the sync loop, the rules run, Process past,
   the Reply Zero jobs and backfill, the voice profile build, the
   learn-from-sent step and the two cleanup jobs. A call that a member drives
   never opens it: compose assist, a draft reply, the voice sample, the
   writing style, the rule generation and the chat. Outside the scope
   :func:`llm_slot` takes no permit and counts nothing, so a member never
   waits behind the sync loop.
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

🔴 **``llm_slot`` wraps the LEAF model await only** (spec items 7 and 17). It
never wraps an enclosing function. A task that a held slot starts runs under
that slot, with no permit and no count of its own. So a slot around a
function that makes its own model calls would run those calls uncapped and
uncounted. The AST fence of the test keeps each call that is not a leaf out
of a slot. Its one exception is the gather of the ``decide`` requests in
``gateway.decide_features._ask_all``, which holds one slot for all of them.

⚠️ **Re-entrant, as a guard** (spec item 7). A slot entered while this task
holds one takes no second permit and counts nothing. So does a slot in a task
that a held slot started. Such work is part of the call that holds the slot,
and the guard can never deadlock at a cap of 1.

⚠️ **Order inside a slot: the permit, then the count, then the call.** A call
that finds no free permit counts nothing. The count goes before the call, so
``enforce`` holds under concurrency. A call that reaches no model counts
nothing (review round 1, finding B). A refusal in ``enforce``, and a body
that raises or times out, give the count back. The 50% and 100% lines log
after a body that succeeded, never for a count that went back.

⚠️ **A Redis that fails costs one bound, once** (review round 1, finding D).
Each Redis command waits :data:`BUDGET_REDIS_TIMEOUT_S` at most. A failure
or a timeout opens a breaker for :data:`BUDGET_BREAKER_S`, and logs
``email.llm_budget_unavailable`` once. While the breaker is open, the budget
counts nothing and the call runs, in each mode. So ``enforce`` fails open
while Redis is down. The cap still binds.

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
    "BUDGET_BREAKER_S",
    "BUDGET_MODES",
    "BUDGET_NAMESPACE",
    "BUDGET_REDIS_TIMEOUT_S",
    "BUDGET_TTL_S",
    "CAP_WAIT_LOG_S",
    "DEFAULT_BUDGET_MODE",
    "THRESHOLDS_PCT",
    "Charge",
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

#: The bound on each Redis command of the budget. A Redis that hangs must not
#: hold a model call, so past it the call runs uncounted. It was 2 seconds
#: until review round 1, and that added 2 seconds to each model call of the
#: live rule match while Redis hung (finding D).
BUDGET_REDIS_TIMEOUT_S = 0.25

#: After a Redis failure or a timeout, the budget counts nothing for this
#: long. So a Redis that hangs costs the bound once, and not on each call.
BUDGET_BREAKER_S = 60.0

#: The three budget modes, and the default.
BUDGET_MODES: tuple[str, ...] = ("off", "log", "enforce")
DEFAULT_BUDGET_MODE = "log"

#: The share of the limit at which ``email.llm_budget_count`` logs, once a
#: day for each mailbox (spec R-6).
THRESHOLDS_PCT: tuple[int, ...] = (50, 100)


class LLMBudgetExhausted(RuntimeError):
    """``enforce``: the mailbox spent its model requests for this UTC day.

    Raised before the model call, so no call is made, and the refused
    request counts nothing. The rule match turns it into ``LLMUnavailable``,
    so the runner leaves ``rules_processed_at`` NULL and a later cycle
    retries the mail (D-EM-8). The message holds the limit only, never
    tenant text.
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
async def llm_slot(
    *, requests: int = 1, wait: bool = True, settle: bool = True
) -> AsyncIterator[Charge]:
    """Hold one permit of the cap, and count ``requests`` against the budget.

    Wrap the LEAF model await and nothing else (spec items 7 and 17)::

        async with llm_slot():
            resp, used = await acompletion_with_fallback(...)

    - Outside the automation scope it takes no permit and counts nothing.
    - Inside a held slot of this task, or of the task that started this one,
      it takes no second permit and counts nothing.
    - ``wait=False`` raises :class:`NoFreeSlot` at once when the cap is full.
    - ``enforce`` past the limit raises :class:`LLMBudgetExhausted` before
      the body runs, and gives the refused count back.
    - A body that raises, or that a timeout cancels, gives its count back.
    - After a body that succeeded it logs the 50% and 100% lines.
      ``settle=False`` leaves that to the caller, which must then await
      :meth:`Charge.settle` after the block. Only ``_ask_all`` does it,
      because its gather returns each failure and never raises.

    It yields the :class:`Charge` of the call. Outside the scope that charge
    counts nothing.
    """
    global _in_use
    scope = _SCOPE.get()
    hold = _HOLD.get()
    if scope is None or (hold is not None and hold.active):
        yield Charge()
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
        charge = await _charge(scope.account_id, max(1, int(requests)))
        try:
            yield charge
        except (Exception, asyncio.CancelledError):
            # The call reached no model answer, so it counts nothing.
            await charge.give_back()
            raise
        if settle:
            await charge.settle()
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


def _clock() -> float:
    """The clock of the breaker, in seconds. A test may replace it."""
    return time.monotonic()


#: The time of :func:`_clock` at which the breaker closes. 0.0 is closed.
_breaker_until = 0.0

#: The mailboxes whose refusal in ``enforce`` this process logged, as
#: ``(org, account_id) -> UTC day``. A refusal gives its count back, so the
#: count alone cannot say that the line already logged today.
_refusal_logged: dict[tuple[str, str], str] = {}


def _breaker_open() -> bool:
    return _clock() < _breaker_until


def _open_breaker(ids: dict[str, Any], reason: str) -> None:
    """Count nothing for :data:`BUDGET_BREAKER_S`. Logs once when it opens."""
    global _breaker_until
    if _breaker_open():
        return
    _breaker_until = _clock() + BUDGET_BREAKER_S
    _log.warning("email.llm_budget_unavailable", **ids, reason=reason, skip_s=int(BUDGET_BREAKER_S))


async def _budget_io(
    org: str, account_id: str, day: str, ids: dict[str, Any], *, amount: int
) -> int | None:
    """Add ``amount`` to the count of the mailbox for ``day``, and return the
    count. A negative ``amount`` gives requests back.

    Returns None, and never raises, when the breaker is open or Redis fails.
    The idiom of the attachment cache (``transport/attachments.py``): bind
    the organization for the call, then build the key inside the binding.
    """
    if _breaker_open():
        return None
    try:
        with organization_scope(org):
            k = key(BUDGET_NAMESPACE, account_id, day)
            try:
                client = get_tenant_redis()
                async with asyncio.timeout(BUDGET_REDIS_TIMEOUT_S):
                    if amount < 0:
                        return int(await client.decrby(k, -amount))
                    count = int(await client.incr(k, amount))
                    await client.expire(k, BUDGET_TTL_S)
                    return count
            except Exception as exc:  # Redis down, or a Redis that hangs
                _open_breaker(ids, type(exc).__name__)
                return None
    except Exception as exc:  # an id that the key rules refuse: this call only
        _log.warning("email.llm_budget_unavailable", **ids, reason=type(exc).__name__)
        return None


@dataclass
class Charge:
    """One count of model requests against the budget of one mailbox.

    :func:`llm_slot` yields it. ``amount`` is what Redis holds for this call
    now, and 0 means that the call counts nothing. The day is fixed when the
    count goes in, so a give-back after midnight hits the same key.
    """

    org: str = ""
    account_id: str = ""
    day: str = ""
    mode: str = "off"
    limit: int = 0
    before: int = 0
    amount: int = 0
    settled: bool = False

    def _ids(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "mode": self.mode}

    async def give_back(self, requests: int | None = None) -> None:
        """Take ``requests`` (all, when None) out of the count. Never raises."""
        n = self.amount if requests is None else max(0, min(int(requests), self.amount))
        if n <= 0:
            return
        self.amount -= n
        await _budget_io(self.org, self.account_id, self.day, self._ids(), amount=-n)

    async def settle(self, failed: int = 0) -> None:
        """Give back the ``failed`` requests, then log the marks that the
        rest crossed. Runs once. Never raises."""
        if self.settled:
            return
        self.settled = True
        await self.give_back(failed)
        self._log_marks()

    def _log_marks(self) -> None:
        if self.limit < 1 or self.amount <= 0:
            return
        count = self.before + self.amount
        for pct in THRESHOLDS_PCT:
            mark = max(1, math.ceil(self.limit * pct / 100))
            if self.before < mark <= count:
                _log.info(
                    "email.llm_budget_count", **self._ids(), pct=pct, count=count, limit=self.limit
                )
        if self.before <= self.limit < count:
            _log.warning("email.llm_budget_exceeded", **self._ids(), count=count, limit=self.limit)


async def _charge(account_id: str, requests: int) -> Charge:
    """Count ``requests`` model requests for the mailbox, in ``log`` and in
    ``enforce``. In ``enforce`` past the limit it gives them back and raises
    :class:`LLMBudgetExhausted`. Never raises for a Redis failure."""
    mode = budget_mode()
    if mode == "off" or not account_id:
        return Charge()
    try:
        limit = int(get_settings().email_llm_daily_calls or 0)
    except (TypeError, ValueError):
        limit = 0
    ids = {"account_id": account_id, "mode": mode}
    org = current_tenant()
    if not org:
        _log.warning("email.llm_budget_unavailable", **ids, reason="no_tenant")
        return Charge()
    day = _utc_day()
    count = await _budget_io(str(org), account_id, day, ids, amount=requests)
    if count is None:
        return Charge()
    charge = Charge(
        org=str(org),
        account_id=account_id,
        day=day,
        mode=mode,
        limit=limit,
        before=count - requests,
        amount=requests,
    )
    if mode == "enforce" and limit >= 1 and count > limit:
        await charge.give_back()
        mailbox = (str(org), account_id)
        if _refusal_logged.get(mailbox) != day:
            _refusal_logged[mailbox] = day
            _log.warning("email.llm_budget_exceeded", **ids, count=count, limit=limit)
        raise LLMBudgetExhausted(f"the mailbox spent its {limit} model requests for today")
    return charge


def reset_for_tests() -> None:
    """Drop the semaphore, the count of held permits, the parsed mode, the
    breaker and the refusal log. Tests only."""
    global _SEM, _in_use, _breaker_until
    _SEM = None
    _in_use = 0
    _breaker_until = 0.0
    _refusal_logged.clear()
    _parse_mode.cache_clear()
