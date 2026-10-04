"""WS-17 EM-T4b: one cap and one daily budget for the email model calls.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, "EM-T4b" (items
1 to 20, "Done when" and F1 to F8). The module under test is
``apps/services/email_ingestion/email_ingestion/llm_cap.py``.

The rules these pin:

- The automation scope marks the calls that the cap and the budget bind. The
  mailbox jobs open it. A call that a member drives never does, and outside
  the scope ``llm_slot`` takes no permit and counts nothing.
- The cap is one semaphore for the process. 0 means no cap, and 0 is the
  shipped default. ``llm_slot`` wraps the LEAF model await only, so a cap of
  1 never deadlocks. A nested slot takes no second permit.
- The budget counts model requests for each mailbox for each UTC day in
  tenant Redis. ``log`` never refuses. ``enforce`` refuses past the limit
  with ``LLMBudgetExhausted`` and makes no model call.
- A call that reaches no model counts nothing (review round 1, finding B).
  A refusal, a body that raises and a body that times out give the count
  back. The 50% and 100% lines log after a call that succeeded only.
- A Redis that fails or hangs costs one bound of 0.25 s, once. A breaker
  then skips the count for 60 s, and the call runs (finding D).
- ``decide``: one ``_ask_all`` call holds one permit and counts each request
  that gets an answer. The shadow takes a permit only when one is free. In
  ``on`` a spent budget gives no decision with the reason ``budget``.
- At the budget the runner leaves ``rules_processed_at`` NULL, in ``on`` and
  in ``off``, and a static rule still applies (R8).
- An AST fence finds each model await of ``routes/email``, ``email_ingestion``
  and ``decide_features.py`` inside ``llm_slot``. It also refuses each call in
  a slot that is not a leaf, apart from the gather of ``_ask_all`` (finding
  A). Companion tests prove that it can fail (F8).

Redis is a FAKE raw client under the REAL ``TenantRedis`` wrapper, so each key
is built by ``tenant_redis.key`` exactly as on the box. The R8 classes run the
real rules job on a real Postgres, as the non-owner role under FORCE RLS.

Run (real Postgres for the R8 class)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_llm_cap.py -v -rs
"""

from __future__ import annotations

import ast
import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import acb_llm as decide_mod
import acb_llm.context as llm_context
import pytest
import structlog
from acb_auth import UserContext
from acb_auth.roles import UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import Settings
from acb_common.tenant_redis import KEY_ROOT, TenantRedis
from email_ingestion import llm_cap
from gateway import decide_features as df
from gateway.routes.email import core as email_core
from gateway.routes.email import scheduler_hooks as hooks_mod
from gateway.routes.email.automation import engine as eng
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import runner as runner_mod
from sqlalchemy import text

from tests.unit.test_email_automation_tenancy import (
    _FakeProvider,
    _patch_providers,
    _seed_message,
)
from tests.unit.test_email_decide_on import _as_app, _logged, _seed_rule, _stamps
from tests.unit.test_email_decide_questions import _RULES, EMAIL, FakeDecide
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

REPO = Path(__file__).resolve().parents[2]
ORG = "55555555-5555-5555-5555-555555555555"
OTHER_ORG = "66666666-6666-6666-6666-666666666666"
ACC = "acc-cap-1"
OTHER_ACC = "acc-cap-2"
OWNER = "owner@acme-cap.example"


# ── Fixtures and fakes ──────────────────────────────────────────────────────


_ENV = (
    "EMAIL_LLM_CONCURRENCY",
    "EMAIL_LLM_DAILY_CALLS",
    "EMAIL_LLM_BUDGET_MODE",
    "DECIDE_FEATURE_MODES",
    "DECIDE_FEATURE_ORGS",
)


def _clear() -> None:
    get_settings.cache_clear()
    llm_cap.reset_for_tests()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()
    df._cooldown_until.clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    _clear()
    yield
    monkeypatch.undo()
    _clear()


def _settings(
    monkeypatch, *, cap: int | None = None, mode: str | None = None, limit: int | None = None
) -> None:
    if cap is not None:
        monkeypatch.setenv("EMAIL_LLM_CONCURRENCY", str(cap))
    if mode is not None:
        monkeypatch.setenv("EMAIL_LLM_BUDGET_MODE", mode)
    if limit is not None:
        monkeypatch.setenv("EMAIL_LLM_DAILY_CALLS", str(limit))
    _clear()


def _decide_modes(monkeypatch, modes: str, orgs: str = "*") -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", orgs)
    _clear()


class FakeRedisClient:
    """The raw redis-py client under ``TenantRedis``. It records each command
    with the wire key the wrapper built, and keeps the counts."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: BaseException | None = None
        self.hang = False

    async def incr(self, name: str, amount: int = 1) -> int:
        self.calls.append(("incr", name, amount))
        if self.fail is not None:
            raise self.fail
        if self.hang:
            await asyncio.sleep(30)
        self.store[name] = int(self.store.get(name, 0)) + amount
        return self.store[name]

    async def expire(self, name: str, seconds: int) -> bool:
        self.calls.append(("expire", name, seconds))
        return True

    async def decrby(self, name: str, amount: int = 1) -> int:
        self.calls.append(("decrby", name, amount))
        if self.fail is not None:
            raise self.fail
        self.store[name] = int(self.store.get(name, 0)) - amount
        return self.store[name]

    @property
    def keys(self) -> list[str]:
        return [name for verb, name, _ in self.calls if verb == "incr"]


@pytest.fixture(autouse=True)
def redis(monkeypatch) -> FakeRedisClient:
    """Every test here gets the fake. No test reaches a real Redis."""
    fake = FakeRedisClient()
    monkeypatch.setattr(llm_cap, "get_tenant_redis", lambda binary=False: TenantRedis(fake))
    return fake


@pytest.fixture()
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


def _day(offset: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=offset)).strftime("%Y-%m-%d")


def _wire_key(org: str, account: str, day: str | None = None) -> str:
    return ":".join((KEY_ROOT, org, llm_cap.BUDGET_NAMESPACE, account, day or _day()))


class FakeModel:
    """``acompletion_with_fallback``. It records the calls that run at one
    time, the permits held and the scope of each call."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.permits: list[int] = []
        self.accounts: list[str | None] = []
        self.fail: BaseException | None = None

    async def __call__(self, model: str | None = None, messages: Any = None, **kw: Any):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.permits.append(llm_cap.permits_in_use())
        self.accounts.append(llm_cap.current_account())
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        if self.fail is not None:
            raise self.fail
        if kw.get("response_format"):
            content = '{"index": -1, "consult": [], "kind": "other", "status": "DONE"}'
        else:
            content = "Hi Bob,\n\nThanks for the note."
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)]), model or "fake"


@pytest.fixture()
def model(monkeypatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
    return fake


async def _ask_model() -> Any:
    data, _content, _used = await email_core._llm_json(
        "tier-fast", [{"role": "user", "content": "x"}], max_tokens=10
    )
    return data


def _events(caps: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == event]


# ── Done when 1: a cap of 2 runs two calls at once at most ──────────────────


async def test_a_cap_of_two_never_runs_more_than_two_calls_at_once(
    monkeypatch,
    tenant,
    model,
) -> None:
    _settings(monkeypatch, cap=2)
    model.delay = 0.05
    with llm_cap.automation_scope(ACC):
        await asyncio.wait_for(asyncio.gather(*(_ask_model() for _ in range(5))), 5)
    assert model.calls == 5
    assert model.peak == 2
    assert max(model.permits) <= 2
    assert llm_cap.permits_in_use() == 0


# ── Done when 2: a nested slot completes with a cap of 1 ────────────────────


async def test_a_nested_slot_completes_with_a_cap_of_one(monkeypatch, tenant, redis) -> None:
    _settings(monkeypatch, cap=1, mode="log", limit=100)
    seen: list[int] = []

    async def nested() -> None:
        with llm_cap.automation_scope(ACC):
            async with llm_cap.llm_slot(), llm_cap.llm_slot():
                seen.append(llm_cap.permits_in_use())

    await asyncio.wait_for(nested(), timeout=2)
    assert seen == [1], "the nested slot took a second permit"
    assert redis.store == {_wire_key(ORG, ACC): 1}, "the nested slot counted again"


async def test_a_task_started_in_a_held_slot_runs_under_it(monkeypatch, tenant) -> None:
    """The guard of item 7: a child of a held slot never waits for the
    permit its parent holds, so a cap of 1 cannot deadlock."""
    _settings(monkeypatch, cap=1)
    seen: list[int] = []

    async def child() -> None:
        async with llm_cap.llm_slot():
            seen.append(llm_cap.permits_in_use())

    with llm_cap.automation_scope(ACC):
        async with llm_cap.llm_slot():
            await asyncio.wait_for(asyncio.create_task(child()), timeout=2)
    assert seen == [1]


# ── Done when 3 and F4: outside the scope, and the shipped defaults ─────────


async def test_a_call_outside_the_scope_takes_no_slot_and_counts_nothing(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, cap=1, mode="enforce", limit=1)
    redis.store[_wire_key(ORG, ACC)] = 50
    model.delay = 0.05
    await asyncio.wait_for(asyncio.gather(*(_ask_model() for _ in range(3))), 5)
    assert model.calls == 3
    assert model.peak == 3, "a call outside the scope waited for the cap"
    assert model.permits == [0, 0, 0]
    assert model.accounts == [None, None, None]
    assert redis.calls == []


def test_the_shipped_defaults_are_no_cap_a_limit_of_2000_and_log() -> None:
    fields = Settings.model_fields
    assert fields["email_llm_concurrency"].default == 0
    assert fields["email_llm_daily_calls"].default == 2000
    assert fields["email_llm_budget_mode"].default == "log"


async def test_with_the_shipped_defaults_no_call_takes_a_permit(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    """F4. The cap ships dark: five calls in the scope run at one time, and
    none of them holds a permit. The budget still counts in `log`."""
    assert llm_cap._cap() == 0
    model.delay = 0.05
    with llm_cap.automation_scope(ACC):
        await asyncio.wait_for(asyncio.gather(*(_ask_model() for _ in range(5))), 5)
    assert model.peak == 5
    assert model.permits == [0] * 5
    assert llm_cap._SEM is None, "the default made a semaphore"
    assert llm_cap.budget_mode() == "log"
    assert redis.store == {_wire_key(ORG, ACC): 5}


# ── F3: a gather of two children at a cap of 1 ──────────────────────────────


async def test_two_children_at_a_cap_of_one_never_overlap_and_never_deadlock(
    monkeypatch,
    tenant,
    model,
) -> None:
    """F3. The parent holds no permit (the leaf rule), so each child takes
    its own permit at its own model await, one after the other."""
    _settings(monkeypatch, cap=1)
    model.delay = 0.05

    async def parent() -> None:
        with llm_cap.automation_scope(ACC):
            await asyncio.gather(_ask_model(), _ask_model())

    await asyncio.wait_for(parent(), timeout=2)
    assert model.calls == 2
    assert model.peak == 1
    assert model.permits == [1, 1]


async def test_a_wait_past_one_second_logs_the_cap_wait(monkeypatch, tenant) -> None:
    _settings(monkeypatch, cap=1)
    monkeypatch.setattr(llm_cap, "CAP_WAIT_LOG_S", 0.02)

    async def hold() -> None:
        async with llm_cap.llm_slot():
            await asyncio.sleep(0.1)

    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        first = asyncio.create_task(hold())
        await asyncio.sleep(0)
        await asyncio.wait_for(hold(), timeout=2)
        await first
    waits = _events(caps, "email.llm_cap_wait")
    assert len(waits) == 1 and waits[0]["wait_ms"] >= 20
    assert waits[0]["account_id"] == ACC


# ── Done when 4, 6 and F7: the budget in `enforce` and in `log` ─────────────


async def test_enforce_refuses_call_2001_before_the_model(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="enforce")
    assert int(get_settings().email_llm_daily_calls) == 2000
    with llm_cap.automation_scope(ACC):
        for _ in range(2000):
            await _ask_model()
        assert model.calls == 2000
        with pytest.raises(llm_cap.LLMBudgetExhausted):
            await _ask_model()
    assert model.calls == 2000, "call 2001 reached the model"
    # Finding B: the refused call reached no model, so it gave its count back.
    assert redis.store[_wire_key(ORG, ACC)] == 2000


async def test_log_runs_call_2001_and_logs_exceeded_once(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="log")
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        for _ in range(2002):
            await _ask_model()
    assert model.calls == 2002
    exceeded = _events(caps, "email.llm_budget_exceeded")
    assert [(e["count"], e["limit"], e["mode"]) for e in exceeded] == [(2001, 2000, "log")]


async def test_log_counts_at_fifty_and_a_hundred_percent_once_a_day(
    monkeypatch,
    tenant,
    model,
) -> None:
    """F7. Each line fires once for each mailbox for each UTC day."""
    _settings(monkeypatch, mode="log", limit=4)
    with structlog.testing.capture_logs() as caps:
        for account in (ACC, OTHER_ACC):
            with llm_cap.automation_scope(account):
                for _ in range(7):
                    await _ask_model()
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        monkeypatch.setattr(llm_cap, "_now", lambda: tomorrow)
        with llm_cap.automation_scope(ACC):
            for _ in range(2):
                await _ask_model()
    lines = [
        (e["account_id"], e["pct"], e["count"]) for e in _events(caps, "email.llm_budget_count")
    ]
    assert lines == [
        (ACC, 50, 2),
        (ACC, 100, 4),
        (OTHER_ACC, 50, 2),
        (OTHER_ACC, 100, 4),
        (ACC, 50, 2),
    ]
    assert [e["account_id"] for e in _events(caps, "email.llm_budget_exceeded")] == [ACC, OTHER_ACC]
    assert model.calls == 16, "log refused a call"


@pytest.mark.parametrize(
    ("before", "requests", "pcts", "exceeded"),
    [
        (0, 4, [50, 100], False),  # one charge crosses both marks
        (3, 2, [100], True),  # and the limit
        (5, 1, [], False),  # past the limit, each line is behind it
    ],
)
async def test_a_charge_of_many_requests_logs_each_mark_it_crosses(
    monkeypatch,
    tenant,
    redis,
    before,
    requests,
    pcts,
    exceeded,
) -> None:
    _settings(monkeypatch, mode="log", limit=4)
    redis.store[_wire_key(ORG, ACC)] = before
    with structlog.testing.capture_logs() as caps:
        charge = await llm_cap._charge(ACC, requests)
        assert _events(caps, "email.llm_budget_count") == [], "a mark logged before the call"
        await charge.settle()
    assert [e["pct"] for e in _events(caps, "email.llm_budget_count")] == pcts
    assert bool(_events(caps, "email.llm_budget_exceeded")) is exceeded


async def test_off_counts_nothing(monkeypatch, tenant, model, redis) -> None:
    _settings(monkeypatch, cap=1, mode="off")
    with llm_cap.automation_scope(ACC):
        await _ask_model()
    assert model.calls == 1 and redis.calls == []


async def test_an_unknown_mode_reads_as_log_and_says_so_once(monkeypatch, tenant, redis) -> None:
    _settings(monkeypatch, mode="enforced")
    with structlog.testing.capture_logs() as caps:
        assert llm_cap.budget_mode() == "log"
        assert llm_cap.budget_mode() == "log"
    assert len(_events(caps, "email.llm_budget_mode_refused")) == 1


# ── Review round 1, finding B: a call that reached no model counts nothing ──


async def test_a_call_that_fails_gives_its_count_back_and_logs_no_mark(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    """A Router outage must not read as a busy mailbox (R-6). The count goes
    in before the call and comes back out when the call raises. The 50% mark
    logs only after a call that succeeded."""
    _settings(monkeypatch, mode="log", limit=2)
    model.fail = RuntimeError("router down")
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await _ask_model()
    assert model.calls == 3
    assert redis.store == {_wire_key(ORG, ACC): 0}, "a failed call kept its count"
    assert _events(caps, "email.llm_budget_count") == []
    assert _events(caps, "email.llm_budget_exceeded") == []
    model.fail = None
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        await _ask_model()
    assert redis.store == {_wire_key(ORG, ACC): 1}
    assert [e["pct"] for e in _events(caps, "email.llm_budget_count")] == [50]


async def test_a_call_that_times_out_gives_its_count_back(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="log", limit=100)
    model.delay = 1.0
    with llm_cap.automation_scope(ACC), pytest.raises(TimeoutError):
        await asyncio.wait_for(_ask_model(), timeout=0.05)
    assert redis.store == {_wire_key(ORG, ACC): 0}
    assert llm_cap.permits_in_use() == 0


async def test_enforce_gives_the_refused_count_back_and_logs_once_a_day(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    """A refusal reached no model, so the count stays at the limit. The
    refusal still logs `email.llm_budget_exceeded` once a day, not once for
    each refused call."""
    _settings(monkeypatch, mode="enforce", limit=1)
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        await _ask_model()
        for _ in range(3):
            with pytest.raises(llm_cap.LLMBudgetExhausted):
                await _ask_model()
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        monkeypatch.setattr(llm_cap, "_now", lambda: tomorrow)
        await _ask_model()
        with pytest.raises(llm_cap.LLMBudgetExhausted):
            await _ask_model()
    assert model.calls == 2
    assert redis.store == {_wire_key(ORG, ACC): 1, _wire_key(ORG, ACC, _day(1)): 1}
    exceeded = _events(caps, "email.llm_budget_exceeded")
    assert [(e["count"], e["limit"], e["mode"]) for e in exceeded] == [
        (2, 1, "enforce"),
        (2, 1, "enforce"),
    ]


def _answers_except(failing: set[str]):
    async def fake(state, questions, **kw):
        qid = next(iter(questions))
        if qid in failing:
            raise decide_mod.DecideUnavailable("unreachable")
        return decide_mod.Decision(
            answers=MappingProxyType({qid: decide_mod.BooleanAnswer(probability=0.9)}),
            request_id=f"r-{qid}",
        )

    return fake


@pytest.mark.parametrize(
    ("failing", "counted", "marks"),
    [
        (set(), 3, [50]),
        ({"q1"}, 2, [50]),
        ({"q0", "q1", "q2"}, 0, []),
    ],
    ids=["all-answered", "one-failed", "router-down"],
)
async def test_ask_all_counts_only_the_requests_that_got_an_answer(
    monkeypatch,
    tenant,
    redis,
    failing,
    counted,
    marks,
) -> None:
    """The gather of `_ask_all` returns each failure and never raises, so
    `_ask_all` settles the charge itself after the slot."""
    _settings(monkeypatch, mode="log", limit=4)
    monkeypatch.setattr(decide_mod, "decide", _answers_except(failing))
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        await df._ask_all("email.rule_match", {"account_id": ACC}, _requests(3), {}, on=True)
    assert redis.store == {_wire_key(ORG, ACC): counted}
    assert [e["pct"] for e in _events(caps, "email.llm_budget_count")] == marks


# ── Review round 1, finding D: a Redis that hangs costs one bound, once ─────


async def test_a_redis_that_hangs_costs_the_bound_once_and_the_breaker_closes_after_60_s(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    """The first call waits 0.25 s at most. The breaker then skips the count
    for 60 s, so the next calls send no Redis command and wait for nothing.
    In `enforce` the call runs while the breaker is open (it fails open)."""
    assert llm_cap.BUDGET_REDIS_TIMEOUT_S == 0.25
    assert llm_cap.BUDGET_BREAKER_S == 60.0
    _settings(monkeypatch, mode="enforce", limit=1)
    now = [1000.0]
    monkeypatch.setattr(llm_cap, "_clock", lambda: now[0])
    redis.hang = True
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        started = time.monotonic()
        await asyncio.wait_for(_ask_model(), timeout=2)
        first = time.monotonic() - started
        started = time.monotonic()
        for _ in range(5):
            await _ask_model()
        rest = time.monotonic() - started
    assert 0.2 <= first < 0.6, f"the first call waited {first:.2f}s"
    assert rest < 0.1, f"five calls inside the breaker waited {rest:.2f}s"
    assert len(redis.keys) == 1, "a call inside the breaker sent a Redis command"
    assert model.calls == 6, "enforce refused a call while the breaker was open"
    lines = _events(caps, "email.llm_budget_unavailable")
    assert [(e["reason"], e["skip_s"]) for e in lines] == [("TimeoutError", 60)]
    redis.hang = False
    now[0] += 59.0
    with llm_cap.automation_scope(ACC):
        await _ask_model()
    assert len(redis.keys) == 1, "the breaker closed before 60 s"
    now[0] += 1.5
    with llm_cap.automation_scope(ACC):
        await _ask_model()
    assert len(redis.keys) == 2
    assert redis.store == {_wire_key(ORG, ACC): 1}


# ── Done when 7: the key carries the organization ───────────────────────────


async def test_the_key_of_one_mailbox_id_differs_between_two_orgs(
    monkeypatch,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="log")
    for org in (ORG, OTHER_ORG):
        token = bind_tenant(org)
        try:
            with llm_cap.automation_scope(ACC):
                await _ask_model()
        finally:
            release_tenant(token)
    first, second = redis.keys
    assert first != second
    assert first.startswith(f"{KEY_ROOT}:{ORG}:email-llm:")
    assert second.startswith(f"{KEY_ROOT}:{OTHER_ORG}:email-llm:")
    assert first.endswith(f":{ACC}:{_day()}") and second.endswith(f":{ACC}:{_day()}")
    expires = [(name, ttl) for verb, name, ttl in redis.calls if verb == "expire"]
    assert expires == [(first, 2 * 24 * 3600), (second, 2 * 24 * 3600)]


async def test_with_no_tenant_the_budget_counts_nothing_and_the_call_runs(
    monkeypatch,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="enforce", limit=1)
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        await _ask_model()
    assert model.calls == 1 and redis.calls == []
    assert _events(caps, "email.llm_budget_unavailable")[0]["reason"] == "no_tenant"


# ── Done when 8: Redis down ─────────────────────────────────────────────────


async def test_with_redis_down_the_call_runs_and_the_cap_still_binds(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, cap=1, mode="enforce", limit=1)
    redis.fail = ConnectionError("redis is down")
    model.delay = 0.05
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        await asyncio.wait_for(asyncio.gather(_ask_model(), _ask_model()), 5)
    assert model.calls == 2
    assert model.peak == 1, "the cap let go while Redis was down"
    # Finding D: the first failure opens the breaker and logs once. The
    # second call sends no Redis command.
    reasons = [e["reason"] for e in _events(caps, "email.llm_budget_unavailable")]
    assert reasons == ["ConnectionError"]
    assert len(redis.keys) == 1


# ── Done when 9, F5, F1 hermetic: the `decide` facade ───────────────────────


def _requests(n: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [({"n": i}, {f"q{i}": object()}) for i in range(n)]


async def test_one_ask_all_takes_one_permit_and_counts_each_request(
    monkeypatch,
    tenant,
    redis,
) -> None:
    """F5. One bound covers all the requests of one call, so the call holds
    one permit, and the budget counts the three requests it sends."""
    _settings(monkeypatch, cap=3, mode="log", limit=100)
    seen: list[int] = []

    async def fake(state, questions, **kw):
        seen.append(llm_cap.permits_in_use())
        await asyncio.sleep(0.01)
        qid = next(iter(questions))
        return decide_mod.Decision(
            answers=MappingProxyType({qid: decide_mod.BooleanAnswer(probability=0.9)}),
            request_id=f"r-{qid}",
        )

    monkeypatch.setattr(decide_mod, "decide", fake)
    with llm_cap.automation_scope(ACC):
        asked = await df._ask_all(
            "email.rule_match", {"account_id": ACC}, _requests(3), {}, on=True
        )
    assert asked is not None and asked.requests == 3
    assert seen == [1, 1, 1], "a request took a permit of its own"
    assert redis.store == {_wire_key(ORG, ACC): 3}


async def test_on_at_the_budget_asks_nothing_and_gives_the_reason_budget(
    monkeypatch,
    tenant,
    redis,
) -> None:
    """F1, hermetic half: `ask` makes no Router call, and the rule match
    raises `DecisionUnavailable`."""
    _settings(monkeypatch, mode="enforce", limit=1)
    _decide_modes(monkeypatch, "email.rule_match=on")
    redis.store[_wire_key(ORG, ACC)] = 1
    fake = FakeDecide(p=0.9)
    monkeypatch.setattr(decide_mod, "decide", fake)
    with (
        structlog.testing.capture_logs() as caps,
        llm_cap.automation_scope(ACC),
        pytest.raises(eng.DecisionUnavailable),
    ):
        await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC, message_id="m1")
    assert fake.calls == []
    assert [e["decide_reason"] for e in _events(caps, "decide.unavailable")] == ["budget"]


async def test_off_at_the_budget_raises_llm_unavailable_with_no_model_call(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    """F2, hermetic half: the `_old` path turns the spent budget into
    `LLMUnavailable` (item 12), so the runner skips the stamp."""
    _settings(monkeypatch, mode="enforce", limit=1)
    redis.store[_wire_key(ORG, ACC)] = 1
    with llm_cap.automation_scope(ACC), pytest.raises(eng.LLMUnavailable) as caught:
        await eng._llm_pick_rule(EMAIL, _RULES, account_id=ACC, message_id="m1")
    assert not isinstance(caught.value, eng.DecisionUnavailable)
    assert isinstance(caught.value.__cause__, llm_cap.LLMBudgetExhausted)
    assert model.calls == 0


@pytest.mark.parametrize("cap", [1, 2])
async def test_a_full_cap_skips_the_shadow_with_no_extra_wait(
    monkeypatch,
    tenant,
    model,
    cap,
) -> None:
    """Done when 9 and item 16. At a cap of 1 the old call holds the only
    permit, so the shadow finds none, asks nothing and adds no wait. At a
    cap of 2 (the control) the shadow asks."""
    _settings(monkeypatch, cap=cap)
    _decide_modes(monkeypatch, "email.rule_match=shadow")
    model.delay = 0.05
    calls: list[int] = []

    async def slow_decide(state, questions, **kw):
        calls.append(1)
        await asyncio.sleep(0.5)
        return decide_mod.Decision(
            answers=MappingProxyType({"q": decide_mod.BooleanAnswer(probability=0.9)}),
            request_id="r",
        )

    monkeypatch.setattr(decide_mod, "decide", slow_decide)
    started = time.monotonic()
    with structlog.testing.capture_logs() as caps, llm_cap.automation_scope(ACC):
        out = await df.shadow(
            "email.rule_match",
            _ask_model,
            account_id=ACC,
            message_id="m1",
            build=lambda: ({"s": 1}, {"q": object()}),
            compare=lambda old, decision: df.Comparison(old=True, new=True, agree=True, options=2),
        )
    elapsed = time.monotonic() - started
    assert out == {"index": -1, "consult": [], "kind": "other", "status": "DONE"}
    skipped = [e for e in _events(caps, "decide.shadow_skipped") if e.get("reason") == "cap"]
    if cap == 1:
        assert calls == [], "the shadow asked with no free slot"
        assert len(skipped) == 1
        assert elapsed < 0.4, f"the old answer waited {elapsed:.2f}s"
    else:
        assert calls == [1] and skipped == []


# ── F6: a call that a member drives ─────────────────────────────────────────


@pytest.fixture()
def member_box(monkeypatch, tenant, redis, model):
    """Each member call would be refused if the budget bound it, and would
    show a permit if the cap bound it."""
    _settings(monkeypatch, cap=1, mode="enforce", limit=1)
    redis.store[_wire_key(ORG, ACC)] = 50
    return SimpleNamespace(redis=redis, model=model)


def _session_of(row: Any = None):
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            fetchone=MagicMock(return_value=row), fetchall=MagicMock(return_value=[])
        )
    )

    @asynccontextmanager
    async def session():
        yield db

    return session


def _member() -> UserContext:
    return UserContext(email=OWNER, role=UserRole.EMPLOYEE, organization_id=ORG)


def _assert_unbound(box) -> None:
    assert box.model.calls >= 1
    assert box.model.permits == [0] * box.model.calls
    assert box.model.accounts == [None] * box.model.calls
    assert box.redis.calls == []


async def test_compose_assist_takes_no_permit_and_counts_nothing(monkeypatch, member_box) -> None:
    from gateway.routes.email.automation import drafting as dr

    monkeypatch.setattr(dr, "_tenant_session", _session_of())
    monkeypatch.setattr(dr, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(dr, "_load_assistant_about", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(dr, "_account_models", AsyncMock(return_value={"compose": "tier-fast"}))
    monkeypatch.setattr(
        dr,
        "resolve_self",
        AsyncMock(return_value=SimpleNamespace(address="box@acme-cap.example", label="")),
    )
    out = await dr._compose_assist_run(
        dr.ComposeAssistRequest(account_id=ACC, to=["bob@vendor.example"]), _member()
    )
    assert out["draft"]
    _assert_unbound(member_box)


async def test_draft_reply_takes_no_permit_and_counts_nothing(monkeypatch, member_box) -> None:
    import acb_memory
    import acb_skills.memory_tools as memory_tools
    from gateway.routes.email.automation import drafting as dr

    email = {**EMAIL, "thread_id": "t-1", "thread": ""}
    monkeypatch.setattr(dr, "_tenant_session", _session_of())
    monkeypatch.setattr(dr, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(dr, "_build_reply_context", AsyncMock(return_value=email))
    monkeypatch.setattr(dr, "_load_assistant_about", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(dr, "_account_models", AsyncMock(return_value={"compose": "tier-fast"}))
    monkeypatch.setattr(dr, "_store_ai_draft", AsyncMock())
    monkeypatch.setattr(memory_tools, "remember", AsyncMock(return_value="no relevant"))
    monkeypatch.setattr(acb_memory, "add_memories_background", AsyncMock())
    out = await dr.draft_reply_smart(
        dr.DraftReplyRequest(account_id=ACC, message_id="m1"), _member()
    )
    assert out["draft"]
    _assert_unbound(member_box)


async def test_the_voice_sample_takes_no_permit_and_counts_nothing(monkeypatch, member_box) -> None:
    from gateway.routes.email.automation import voice_profile as vp

    row = SimpleNamespace(style_guide="- Keep it short.", traits={})
    monkeypatch.setattr(vp, "_tenant_session", _session_of(row))
    monkeypatch.setattr(vp, "_assert_account_owner", AsyncMock())
    out = await vp.sample_voice_profile(vp.VoiceProfileSampleRequest(account_id=ACC), _member())
    assert out["sample"]
    _assert_unbound(member_box)


async def test_a_model_call_from_the_chat_takes_no_permit_and_counts_nothing(
    monkeypatch,
    member_box,
) -> None:
    """The chat is exempt (item 15). Even a tool of the chat that calls the
    email model seam in this process runs outside the automation scope."""
    import orchestrator.executor as executor
    from gateway.routes.email.automation import chat

    async def fake_stream(name, payload, **kw):
        await _ask_model()
        yield 'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}'

    monkeypatch.setattr(executor, "run_agent_stream", fake_stream)
    monkeypatch.setattr(chat, "_build_chat_context", AsyncMock(return_value=(None, [])))
    resp = await chat.ai_chat(
        chat.AIChatRequest(messages=[{"role": "user", "content": "hello"}]), _member(), MagicMock()
    )
    body = [chunk async for chunk in resp.body_iterator]
    assert any('"content"' in str(c) for c in body)
    _assert_unbound(member_box)


# ── Items 5 and 6: who opens the scope ──────────────────────────────────────


_AUTOMATION = "apps/services/gateway/gateway/routes/email/automation"

#: Item 5: the functions that open the scope with `@automation_job`. Review
#: round 1 (finding C) added the Reply Zero backfill, because the Reply Zero
#: list starts it as a BackgroundTask on a cold mailbox.
SCOPE_JOBS: dict[str, tuple[str, ...]] = {
    f"{_AUTOMATION}/runner.py": ("_run_rules_job", "_process_past_emails_job"),
    f"{_AUTOMATION}/replyzero.py": (
        "_reclassify_reply_zero_job",
        "_mark_thread_replied",
        "_maybe_classify_threads",
    ),
    f"{_AUTOMATION}/voice_profile.py": ("_build_voice_profile_job",),
    f"{_AUTOMATION}/drafting.py": ("_learn_from_sent",),
    f"{_AUTOMATION}/cleanup.py": ("_sweep_job", "_backfill_and_clean_job"),
}

#: Item 5: the functions that a member drives. None of them opens the scope.
MEMBER_DRIVEN: dict[str, tuple[str, ...]] = {
    f"{_AUTOMATION}/drafting.py": ("_compose_assist_run", "draft_reply_smart"),
    f"{_AUTOMATION}/voice_profile.py": ("sample_voice_profile",),
    f"{_AUTOMATION}/assistant.py": ("_llm_writing_style", "generate_writing_style"),
    f"{_AUTOMATION}/rules.py": ("_llm_generate_rules",),
    f"{_AUTOMATION}/chat.py": ("ai_chat",),
}


def _functions(rel: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _decorator_names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        out.add(target.id if isinstance(target, ast.Name) else getattr(target, "attr", ""))
    return out


def test_each_mailbox_job_opens_the_scope() -> None:
    missing = [
        f"{rel}::{name}"
        for rel, names in SCOPE_JOBS.items()
        for name in names
        if "automation_job" not in _decorator_names(_functions(rel)[name])
    ]
    assert not missing, f"a mailbox job opens no automation scope: {missing}"


def test_no_member_driven_call_opens_the_scope() -> None:
    offenders = []
    for rel, names in MEMBER_DRIVEN.items():
        fns = _functions(rel)
        for name in names:
            fn = fns[name]
            calls = {
                getattr(c.func, "id", getattr(c.func, "attr", ""))
                for c in ast.walk(fn)
                if isinstance(c, ast.Call)
            }
            if "automation_job" in _decorator_names(fn) or calls & {
                "automation_scope",
                "automation_job",
            }:
                offenders.append(f"{rel}::{name}")
    assert not offenders, f"a member-driven call opens the scope: {offenders}"


async def test_as_mailbox_owner_opens_the_scope_of_its_mailbox(monkeypatch) -> None:
    monkeypatch.setattr(hooks_mod, "mailbox_owner", AsyncMock(return_value=OWNER))
    seen: list[str | None] = []

    async def job(account_id: str) -> None:
        seen.append(llm_cap.current_account())

    await hooks_mod.as_mailbox_owner(job)(ACC)
    assert seen == [ACC]
    assert llm_cap.current_account() is None, "the scope outlived the job"


async def test_the_rules_job_runs_in_the_scope_of_its_mailbox(monkeypatch) -> None:
    seen: list[str | None] = []

    @asynccontextmanager
    async def session():
        seen.append(llm_cap.current_account())
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        yield db

    monkeypatch.setattr(runner_mod, "_tenant_session", session)
    await runner_mod._run_rules_job(ACC, 5, False, OWNER)
    assert seen == [ACC]


async def test_the_cold_start_backfill_of_reply_zero_opens_the_scope(monkeypatch, tenant) -> None:
    """Finding C. A member opens the Reply Zero list of a mailbox with no
    status row. The route starts the backfill as a BackgroundTask. The route
    itself runs outside the scope, and the backfill runs inside it."""
    from fastapi import BackgroundTasks

    seen: list[str | None] = []
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            fetchone=MagicMock(return_value=None),
            fetchall=MagicMock(return_value=[]),
            scalar=MagicMock(return_value=0),
        )
    )

    @asynccontextmanager
    async def session():
        seen.append(llm_cap.current_account())
        yield db

    monkeypatch.setattr(rz, "_tenant_session", session)
    monkeypatch.setattr(rz, "_assert_account_owner", AsyncMock())
    background = BackgroundTasks()
    await rz.reply_zero(background, account_id=ACC, type="needs_reply", limit=50, user=_member())
    assert [t.func.__name__ for t in background.tasks] == ["_maybe_classify_threads"]
    in_route = len(seen)
    assert in_route >= 1 and set(seen) == {None}, "the route itself opened the scope"
    await background()
    assert len(seen) > in_route, "the backfill opened no session"
    assert set(seen[in_route:]) == {ACC}, "the cold-start backfill ran outside the scope"


def test_automation_job_keeps_the_signature() -> None:
    import inspect

    params = list(inspect.signature(runner_mod._run_rules_job).parameters)
    assert params == ["account_id", "limit", "dry_run", "user_email"]
    assert runner_mod._run_rules_job.__name__ == "_run_rules_job"


def test_the_cap_module_does_not_import_the_gateway() -> None:
    tree = ast.parse(
        (REPO / "apps/services/email_ingestion/email_ingestion/llm_cap.py").read_text(
            encoding="utf-8"
        )
    )
    roots = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    roots |= {
        (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    }
    assert "gateway" not in roots


# ── Item 13: the thread status writes no `· auto` fallback ──────────────────


async def test_a_spent_budget_raises_from_the_status_call_and_writes_nothing(
    monkeypatch,
    tenant,
    model,
    redis,
) -> None:
    _settings(monkeypatch, mode="enforce", limit=1)
    redis.store[_wire_key(ORG, ACC)] = 1
    ctx = SimpleNamespace(
        thread_text="t",
        our_side_last=False,
        last_message_id="m1",
        last_message_at=None,
        messages=None,
    )
    monkeypatch.setattr(rz, "build_thread_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(rz, "_thread_is_self_only", AsyncMock(return_value=False))
    monkeypatch.setattr(rz, "_status_corrections_block", AsyncMock(return_value=""))
    upsert = AsyncMock()
    monkeypatch.setattr(rz, "_upsert_thread_status", upsert)
    with llm_cap.automation_scope(ACC):
        with pytest.raises(llm_cap.LLMBudgetExhausted):
            await rz._llm_determine_thread_status("t", OWNER, "", account_id=ACC)
        with pytest.raises(llm_cap.LLMBudgetExhausted):
            await rz.recompute_thread_status(
                AsyncMock(), ACC, "t-1", trigger="inbound", about="", acc_email=OWNER
            )
    upsert.assert_not_awaited()
    assert model.calls == 0


async def test_another_model_failure_still_writes_the_auto_fallback(
    monkeypatch,
    tenant,
) -> None:
    """The control of item 13: the old fallback stays for every other
    failure."""

    async def broken(*a, **kw):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", broken)
    assert await rz._llm_determine_thread_status(
        "t", OWNER, "", account_id=ACC, user_sent_last=False
    ) == ("FYI", False)


# ── F8: the AST fence ───────────────────────────────────────────────────────


#: The leaf model awaits. Each one sits lexically inside `llm_slot`. The
#: `decide` leaf is `acb_llm.decide`, found by its import alias.
MODEL_CALLEES = frozenset(
    {"acompletion_with_fallback", "acompletion_stream_text", "aembedding", "run_agent"}
)

#: The `decide` facade. It takes its own permit at its leaf inside
#: `_ask_all`, so a call of it sits OUTSIDE any slot. A parent that held a
#: slot around it would hold a permit while its child waits (item 17).
DECIDE_FACADE = frozenset({"ask", "shadow", "_ask_all"})

#: Item 15 and item 20, named so a reader sees why they are not above. A
#: member drives `run_agent_stream` (the chat), and Mem0 is out of the slice.
EXEMPT = {
    "run_agent_stream": "item 15, the chat",
    "remember": "item 20, Mem0",
    "add_memories_background": "item 20, Mem0",
}

_FENCE_ROOTS = (
    "apps/services/gateway/gateway/routes/email",
    "apps/services/email_ingestion/email_ingestion",
)
_FACADE_MODULE = "apps/services/gateway/gateway/decide_features.py"

#: Review round 1, finding A. A task that a held slot starts runs under that
#: slot, with no permit and no count of its own (item 7). So a slot around a
#: call that is not a leaf runs the model calls inside it uncapped and
#: uncounted. The fence refuses each such call in a slot. This is the ONE
#: exception: the gather of the `decide` requests in `_ask_all`, because one
#: bound and one permit cover all of them (item 19). Each call in its
#: arguments must be the `decide` leaf, so a gather of anything else in the
#: same place is still a finding. The fence reads it as
#: (the facade module, the enclosing function, the callee).
GATHER_EXCEPTION = (_FACADE_MODULE, "_ask_all", "gather")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    callee: str


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _is_slot(item: ast.withitem) -> bool:
    expr = item.context_expr
    return isinstance(expr, ast.Call) and _callee(expr) == "llm_slot"


def _aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """(the `decide` leaf names, the facade module names, the bare facade
    function names) that this source imports."""
    leaves: set[str] = set()
    modules: set[str] = set()
    functions: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "acb_llm":
            leaves |= {a.asname or a.name for a in n.names if a.name == "decide"}
        elif isinstance(n, ast.ImportFrom) and n.module == "gateway":
            modules |= {a.asname or a.name for a in n.names if a.name == "decide_features"}
        elif isinstance(n, ast.ImportFrom) and n.module == "gateway.decide_features":
            functions |= {a.asname or a.name for a in n.names if a.name in DECIDE_FACADE}
        elif isinstance(n, ast.Import):
            modules |= {
                a.asname for a in n.names if a.name == "gateway.decide_features" and a.asname
            }
    return leaves, modules, functions


def _is_facade(call: ast.Call, modules: set[str], functions: set[str]) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in functions
    return (
        isinstance(f, ast.Attribute)
        and f.attr in DECIDE_FACADE
        and isinstance(f.value, ast.Name)
        and f.value.id in modules
    )


def _kind_of(call: ast.Call, leaves: set[str], modules: set[str], functions: set[str]) -> str:
    """ "facade", "leaf" or "" for a call."""
    if _is_facade(call, modules, functions):
        return "facade"
    return "leaf" if _callee(call) in leaves else ""


def scan(source: str, path: str, *, facade_module: bool = False) -> tuple[list[Finding], Counter]:
    """The fence. Returns its findings and what it saw, so a blind fence
    fails on its own counts.

    Three kinds of finding:

    - ``unslotted``: a leaf model call outside each slot.
    - ``facade_in_slot``: a `decide` facade call inside a slot.
    - ``non_leaf_in_slot``: any other call inside a slot, for example
      `_llm_json`, `_llm_draft_reply`, a function of the email packages,
      `create_task` or `gather`. :data:`GATHER_EXCEPTION` is the one call
      that it lets through.
    """
    tree = ast.parse(source)
    leaves, modules, functions = _aliases(tree)
    leaves |= MODEL_CALLEES
    if facade_module:
        functions |= DECIDE_FACADE
    findings: list[Finding] = []
    seen: Counter = Counter()

    def kind(call: ast.Call) -> str:
        return _kind_of(call, leaves, modules, functions)

    def visit(node: ast.AST, slotted: bool, funcs: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A body runs where it is called, not where it is written.
            slotted = False
            funcs = (*funcs, node.name)
        elif isinstance(node, ast.Lambda):
            slotted = False
        elif isinstance(node, (ast.With, ast.AsyncWith)) and any(_is_slot(i) for i in node.items):
            for item in node.items:
                visit(item, slotted, funcs)
            for stmt in node.body:
                visit(stmt, True, funcs)
            return
        elif isinstance(node, ast.Call):
            found = kind(node)
            excepted = bool(
                slotted and not found and facade_module and _is_the_gather(node, funcs, kind)
            )
            if found or excepted:
                seen[found or "gather_exception"] += 1
            label = _label(found, slotted=slotted, excepted=excepted)
            if label:
                findings.append(Finding(path, node.lineno, label, _callee(node)))
        for child in ast.iter_child_nodes(node):
            visit(child, slotted, funcs)

    visit(tree, False, ())
    return findings, seen


def _is_the_gather(call: ast.Call, funcs: tuple[str, ...], kind: Callable[[ast.Call], str]) -> bool:
    """True for :data:`GATHER_EXCEPTION`: a `gather` in `_ask_all` whose
    arguments call the `decide` leaf and nothing else."""
    _module, function, callee = GATHER_EXCEPTION
    if function not in funcs or _callee(call) != callee:
        return False
    inner = [
        c
        for arg in (*call.args, *(k.value for k in call.keywords))
        for c in ast.walk(arg)
        if isinstance(c, ast.Call)
    ]
    return bool(inner) and all(kind(c) == "leaf" for c in inner)


def _label(found: str, *, slotted: bool, excepted: bool) -> str:
    """The finding for one call, or "" for none."""
    if found == "leaf":
        return "" if slotted else "unslotted"
    if found == "facade":
        return "facade_in_slot" if slotted else ""
    return "non_leaf_in_slot" if slotted and not excepted else ""


def _fence_files() -> list[Path]:
    files = [
        p
        for root in _FENCE_ROOTS
        for p in (REPO / root).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    return [*files, REPO / _FACADE_MODULE]


def test_each_model_await_sits_inside_llm_slot() -> None:
    """F8. The fence over `routes/email/**`, `email_ingestion/**` and
    `gateway/decide_features.py`."""
    findings: list[Finding] = []
    total: Counter = Counter()
    for path in _fence_files():
        rel = str(path.relative_to(REPO)).replace("\\", "/")
        found, seen = scan(
            path.read_text(encoding="utf-8"), rel, facade_module=rel == _FACADE_MODULE
        )
        findings += found
        total += seen
    assert not findings, (
        "a model await outside `llm_slot`, or a call inside one that is not "
        f"a leaf (EM-T4b items 7, 15 and 17, review round 1 finding A): {findings}"
    )
    # Measured 2026-10-04: 13 leaves and 11 facade calls. A fence that sees
    # fewer has gone blind, for example after a rename.
    assert total["leaf"] >= 12 and total["facade"] >= 10, total
    # The one exception is used once, by `_ask_all`, and nowhere else.
    assert total["gather_exception"] == 1, total


def test_the_facade_takes_its_permit_in_ask_all_only() -> None:
    fns = _functions(_FACADE_MODULE)
    for name in ("ask", "shadow"):
        calls = {_callee(c) for c in ast.walk(fns[name]) if isinstance(c, ast.Call)}
        assert "_ask_all" in calls, f"{name} reaches decide without _ask_all"
    slots = [
        w
        for w in ast.walk(fns["_ask_all"])
        if isinstance(w, ast.AsyncWith) and any(_is_slot(i) for i in w.items)
    ]
    assert len(slots) == 1


def test_the_exempt_calls_are_named_and_outside_the_callee_list() -> None:
    assert not set(EXEMPT) & MODEL_CALLEES
    chat_src = (REPO / f"{_AUTOMATION}/chat.py").read_text(encoding="utf-8")
    assert "run_agent_stream(" in chat_src and "llm_slot" not in chat_src


# The companion tests: the fence fails on each shape it exists to catch.


@pytest.mark.parametrize(
    "source",
    [
        "async def f():\n    await acompletion_with_fallback(model='x')\n",
        "async def f():\n    await acompletion_stream_text(model='x', on_delta=None)\n",
        "async def f():\n    await litellm.aembedding(model='x', input=[])\n",
        "async def f():\n    await asyncio.wait_for(run_agent('a', {}), timeout=1)\n",
        "from acb_llm import decide as facade\nasync def f():\n    await facade(1, 2)\n",
        "import acb_llm\nfrom acb_llm import decide\nasync def f():\n    await decide(1, 2)\n",
        # A function written inside a slot runs where it is called.
        "async def f():\n    async with llm_slot():\n"
        "        async def g():\n            await acompletion_with_fallback()\n"
        "        await g()\n",
    ],
    ids=[
        "completion",
        "stream",
        "embedding",
        "run_agent",
        "decide-alias",
        "decide-bare",
        "def-in-slot",
    ],
)
def test_the_fence_finds_an_unslotted_leaf(source) -> None:
    findings, _ = scan(source, "<synthetic>")
    assert [f.kind for f in findings if f.kind == "unslotted"] == ["unslotted"]
    # Only the def-in-slot shape has a second finding: the call of `g` in
    # the slot is not a leaf (finding A).
    others = [(f.kind, f.callee) for f in findings if f.kind != "unslotted"]
    assert others == ([("non_leaf_in_slot", "g")] if "def g" in source else [])


@pytest.mark.parametrize(
    "source",
    [
        "from gateway import decide_features\nasync def f():\n"
        "    async with llm_slot():\n        await decide_features.ask('x')\n",
        "from gateway.decide_features import shadow\nasync def f():\n"
        "    async with llm_slot():\n        await shadow('x', old)\n",
    ],
    ids=["ask", "shadow"],
)
def test_the_fence_finds_a_facade_call_inside_a_slot(source) -> None:
    findings, _ = scan(source, "<synthetic>")
    assert [f.kind for f in findings] == ["facade_in_slot"]


def test_the_fence_passes_the_right_shapes_and_counts_them() -> None:
    source = (
        "from acb_llm import decide as facade\n"
        "from gateway import decide_features\n"
        "async def f():\n"
        "    prompt = build(x)\n"
        "    async with llm_slot():\n"
        "        await acompletion_with_fallback(messages=prompt)\n"
        "    async with llm_slot(requests=2, wait=False):\n"
        "        await facade(1, 2)\n"
        "    async with asyncio.timeout(9), llm_slot():\n"
        "        await run_agent('a', {})\n"
        "    await decide_features.shadow('x', old)\n"
        "    await run_agent_stream('chat')\n"
    )
    findings, seen = scan(source, "<synthetic>")
    assert findings == []
    assert seen == Counter(leaf=3, facade=1)


# Review round 1, finding A: each call in a slot that is not a leaf.


@pytest.mark.parametrize(
    ("source", "callees"),
    [
        (
            "async def f():\n    async with llm_slot():\n"
            "        await asyncio.gather(_llm_json('m', []), _llm_json('m', []))\n",
            ["gather", "_llm_json", "_llm_json"],
        ),
        (
            "async def f():\n    async with llm_slot():\n"
            "        draft = await _llm_draft_reply(email, about)\n",
            ["_llm_draft_reply"],
        ),
        (
            "async def f():\n    async with llm_slot():\n"
            "        task = asyncio.create_task(acompletion_with_fallback())\n"
            "        await task\n",
            ["create_task"],
        ),
        (
            "async def f():\n    async with llm_slot():\n"
            "        await asyncio.wait_for(run_agent('a', {}), timeout=9)\n",
            ["wait_for"],
        ),
        (
            "async def f():\n    async with llm_slot():\n"
            "        async with llm_slot():\n"
            "            await acompletion_with_fallback()\n",
            ["llm_slot"],
        ),
    ],
    ids=["gather-of-llm-json", "drafter", "create-task", "wait-for", "nested-slot"],
)
def test_the_fence_finds_a_call_in_a_slot_that_is_not_a_leaf(source, callees) -> None:
    findings, _ = scan(source, "<synthetic>")
    assert {f.kind for f in findings} == {"non_leaf_in_slot"}
    assert sorted(f.callee for f in findings) == sorted(callees)


_ASK_ALL_SHAPE = (
    "import asyncio\n"
    "from acb_llm import decide as facade\n"
    "async def {fn}(requests):\n"
    "    async def _send():\n"
    "        async with llm_slot(requests=len(requests), settle=False) as charge:\n"
    "            results = await asyncio.gather(\n"
    "                *({calls} for state, questions in requests),\n"
    "                return_exceptions=True,\n"
    "            )\n"
    "        return charge, results\n"
    "    return await _send()\n"
)


def test_the_one_exception_is_the_gather_of_decide_in_ask_all() -> None:
    source = _ASK_ALL_SHAPE.format(fn="_ask_all", calls="facade(state, questions)")
    findings, seen = scan(source, _FACADE_MODULE, facade_module=True)
    assert findings == []
    assert seen["gather_exception"] == 1


@pytest.mark.parametrize(
    ("fn", "calls", "facade_module"),
    [
        ("_ask_all", "_llm_json(state, questions)", True),
        ("_ask_all", "facade(_llm_json(state), questions)", True),
        ("_ask_some", "facade(state, questions)", True),
        ("_ask_all", "facade(state, questions)", False),
    ],
    ids=["gather-of-llm-json", "llm-json-in-an-argument", "another-function", "another-module"],
)
def test_the_exception_is_narrow(fn, calls, facade_module) -> None:
    source = _ASK_ALL_SHAPE.format(fn=fn, calls=calls)
    findings, seen = scan(source, "<synthetic>", facade_module=facade_module)
    assert "non_leaf_in_slot" in {f.kind for f in findings}
    assert seen["gather_exception"] == 0


def _wrap_in_slot(source: str, function: str, callee: str) -> str:
    """Wrap the first statement of ``function`` whose value awaits a call
    of ``callee`` in ``async with llm_slot():``, and return the new source.
    Mutation proof on a real file, in memory."""
    tree = ast.parse(source)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function
    )

    def awaits_callee(stmt: ast.stmt) -> bool:
        value = getattr(stmt, "value", None)
        value = value.value if isinstance(value, ast.Await) else value
        return isinstance(value, ast.Call) and _callee(value) == callee

    for parent in ast.walk(fn):
        for name in ("body", "orelse", "finalbody"):
            stmts = getattr(parent, name, None)
            if not isinstance(stmts, list):
                continue
            for i, stmt in enumerate(stmts):
                if isinstance(stmt, (ast.Assign, ast.Expr, ast.Return)) and awaits_callee(stmt):
                    slot = ast.Call(func=ast.Name("llm_slot", ast.Load()), args=[], keywords=[])
                    stmts[i] = ast.AsyncWith(items=[ast.withitem(context_expr=slot)], body=[stmt])
                    return ast.unparse(ast.fix_missing_locations(tree))
    raise AssertionError(f"no statement of {function} awaits {callee}")


@pytest.mark.parametrize(
    ("function", "callee", "callees"),
    [
        # ITEM1-b of the build's mutation table: it survived the old fence.
        ("_orchestrate_draft", "gather", {"gather", "_memory_context", "_draft_consult_plan"}),
        ("_orchestrate_draft", "_llm_draft_reply", {"_llm_draft_reply"}),
    ],
    ids=["item1-b-gather", "drafter"],
)
def test_the_fence_fails_on_the_real_drafter_with_a_slot_around_a_non_leaf(
    function, callee, callees
) -> None:
    rel = f"{_AUTOMATION}/drafting.py"
    mutated = _wrap_in_slot((REPO / rel).read_text(encoding="utf-8"), function, callee)
    findings, _ = scan(mutated, rel)
    assert {f.kind for f in findings} == {"non_leaf_in_slot"}
    assert callees <= {f.callee for f in findings}


def test_the_fence_fails_on_the_real_seam_without_its_slot() -> None:
    """Mutation proof on a real file: take `llm_slot` out of `_llm_json` and
    the fence names it."""
    rel = "apps/services/gateway/gateway/routes/email/core.py"
    src = (REPO / rel).read_text(encoding="utf-8")
    mutated = src.replace("async with llm_slot():", "if True:", 1)
    assert mutated != src
    findings, _ = scan(mutated, rel)
    assert [(f.kind, f.callee) for f in findings] == [("unslotted", "acompletion_with_fallback")]


# ── R8: the runner at the budget, on a real database ────────────────────────


def _seed_static_rule(admin, *, org: str, account_id: str, sender: str) -> str:
    with admin.begin() as c:
        rid = str(
            c.execute(
                text(
                    "INSERT INTO email_rules (account_id, name, from_pattern, enabled, "
                    "organization_id) VALUES (CAST(:a AS uuid), 'Static', :f, true, "
                    "CAST(:o AS uuid)) RETURNING id"
                ),
                {"a": account_id, "f": sender, "o": org},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO email_actions (rule_id, type, label, organization_id) "
                "VALUES (CAST(:r AS uuid), 'LABEL', 'Static', CAST(:o AS uuid))"
            ),
            {"r": rid, "o": org},
        )
    return rid


def _mailbox_at_its_budget(p, redis: FakeRedisClient) -> tuple[str, str, str, str]:
    """A mailbox of org B with one AI rule, one static rule, and one message
    for each. Its count for today already stands at the limit of 1.
    Returns (account, owner, the static message, the AI message)."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@llm-cap.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    now = datetime.now(UTC)
    _seed_rule(
        p.admin_engine,
        org=p.org_b,
        account_id=acc,
        name="Receipt",
        instructions="Receipts and invoices.",
        created_at=now - timedelta(hours=1),
    )
    _seed_static_rule(p.admin_engine, org=p.org_b, account_id=acc, sender="static@vendor-cap.test")
    static = _seed_message(
        p.admin_engine,
        org=p.org_b,
        account_id=acc,
        sender="static@vendor-cap.test",
        thread_id=f"t-s-{acc}",
        received_at=now - timedelta(minutes=10),
    )
    ai = _seed_message(
        p.admin_engine,
        org=p.org_b,
        account_id=acc,
        sender="ai@other-cap.test",
        thread_id=f"t-a-{acc}",
        received_at=now - timedelta(minutes=5),
    )
    redis.store[_wire_key(p.org_b, acc)] = 1
    return acc, owner, static, ai


@_DB_GATE
class TestTheRunnerAtTheBudget:
    """Done when 5, F1 and F2: the REAL rules job, as the non-owner role
    under FORCE RLS. The AI message stays unstamped for a later cycle, and
    the static rule still applies to the other message."""

    async def test_on_makes_no_router_call_and_leaves_the_mail_undecided(
        self,
        promoted,  # noqa: F811
        app_engine,  # noqa: F811
        monkeypatch,
        redis,
        model,
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc, owner, static, ai = _mailbox_at_its_budget(p, redis)
        _settings(monkeypatch, mode="enforce", limit=1)
        _decide_modes(monkeypatch, "email.rule_match=on")
        fake = FakeDecide(p=0.92)
        monkeypatch.setattr(decide_mod, "decide", fake)
        _patch_providers(monkeypatch, _FakeProvider())
        with structlog.testing.capture_logs() as caps:
            async with _as_app(p, p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, owner)
        assert fake.calls == [], "a Router call ran past the budget"
        assert model.calls == 0
        assert [e["decide_reason"] for e in _events(caps, "decide.unavailable")] == ["budget"]
        assert redis.store[_wire_key(p.org_b, acc)] == 1, "a refused request counted"
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[ai] is None, "the undecided message was stamped"
        assert stamps[static] is not None, "the static rule did not apply"
        assert [(r["mid"], r["status"], r["rule_name"]) for r in _logged(p.admin_engine, acc)] == [
            (static, "APPLIED", "Static")
        ]
        skipped = _events(caps, "email.classify_unavailable_skip")
        assert [e["message_id"] for e in skipped] == [ai]

    async def test_off_takes_the_old_path_and_leaves_the_mail_undecided(
        self,
        promoted,  # noqa: F811
        app_engine,  # noqa: F811
        monkeypatch,
        redis,
        model,
    ):
        p = promoted
        acc, owner, static, ai = _mailbox_at_its_budget(p, redis)
        _settings(monkeypatch, mode="enforce", limit=1)
        _patch_providers(monkeypatch, _FakeProvider())
        with structlog.testing.capture_logs() as caps:
            async with _as_app(p, p.org_b):
                await runner_mod._run_rules_job(acc, 50, False, owner)
        assert model.calls == 0, "the old path reached the model past the budget"
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[ai] is None and stamps[static] is not None
        assert [e["message_id"] for e in _events(caps, "email.classify_unavailable_skip")] == [ai]
        # Review round 1, finding B: the refused call reached no model, so it
        # gave its count back. The count stays at the limit.
        assert redis.store[_wire_key(p.org_b, acc)] == 1, "a refused call counted"

    async def test_in_log_the_same_run_decides_both_messages(
        self,
        promoted,  # noqa: F811
        app_engine,  # noqa: F811
        monkeypatch,
        redis,
        model,
    ):
        """The control: `log` never refuses, so the AI message is decided."""
        p = promoted
        acc, owner, static, ai = _mailbox_at_its_budget(p, redis)
        _settings(monkeypatch, mode="log", limit=1)
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, owner)
        assert model.calls == 1
        stamps = _stamps(p.admin_engine, acc)
        assert stamps[ai] is not None and stamps[static] is not None
