"""Cut the AI cost of the email app (WS-17, 2026-10-09).

Measured on production, 2026-10-02 to 10-08 (``usage_event``, module
``email``): the old thread-status call made 4,755 calls on ``tier-balanced``
and 4,616 on ``tier-powerful``, 10,598 credits, and each one stopped at
exactly 500 output tokens. The Router binds both tiers to
``deepseek/deepseek-v4-pro``, a model that thinks by default. Four fixes,
each with its fence here:

1. **The root cause.** The status call asks for no reasoning
   (``replyzero._STATUS_THINKING``), so the reasoning cannot use up the cap
   before the JSON. :class:`TestTheRootCause`.
2. **No retry on the same model.** The one retry is ``tier-fast``, a
   different and cheaper model, and no retry runs when it resolves to the
   model of the first try. :class:`TestTheRetry`.
3. **No re-ask storm.** A guessed status (``· auto``) waits
   ``_PROVISIONAL_RECHECK_HOURS`` before the backfill asks again, and one
   rules run asks once for each thread. :class:`TestNoReAskStorm` (R8).
4. **The digest brief is cached** for each mailbox, UTC day and input, in
   tenant Redis. :class:`TestTheBriefCache`.
5. **``read_email`` reads the new text.** ``quoting.strip_for_reading``
   cuts the quoted thread, the signature and the legal footer, and
   ``full=True`` still reads the whole body. :class:`TestReadingText`,
   :class:`TestTheTrimRoute` (R8) and :class:`TestTheAgentTool`.

Mutations this file catches (R7). Each one ran red, and the file came back
to the committed SHA after each one:

* ``_old`` drops ``thinking=`` ->
  ``test_the_status_call_asks_for_no_reasoning_and_reads_the_answer``;
* ``_STATUS_RETRY_MODEL = "tier-powerful"`` ->
  ``test_the_retry_is_a_different_model``;
* ``_status_retry_model`` loses its same-model check ->
  ``test_no_retry_when_both_tiers_bind_one_model``;
* ``_NEEDS_STATUS_SQL`` loses its ``classified_at`` clause ->
  ``test_a_guess_is_asked_once_in_the_window`` and
  ``test_the_count_agrees_with_the_selection``;
* the runner loses ``asked_status`` ->
  ``test_one_run_asks_once_for_each_thread``;
* ``_digest_brief`` skips ``_cached_brief`` ->
  ``test_a_second_load_reads_the_cache``;
* ``_brief_cache_key`` loses the hash of the input ->
  ``test_new_mail_refreshes_the_brief``;
* ``strip_for_reading`` loses ``strip_signature`` or ``strip_disclaimer`` ->
  the fixture cases of :class:`TestReadingText`;
* ``get_message`` trims with no ``trim`` -> ``test_no_trim_returns_the_whole_body``;
* review round 1: ``strip_disclaimer`` cuts from the first footer-like
  paragraph to the end, or ``strip_signature`` takes any line under a
  closing line as a name, or ``strip_for_reading`` cuts a forward ->
  ``test_mail_that_comes_back_whole``;
* review round 1: ``_status_retry_model`` reads the local table on the
  Router -> ``test_on_the_router_the_local_table_does_not_decide``.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import acb_llm as decide_mod
import acb_llm.context as llm_context
import pytest
import structlog
from acb_common import get_settings, tenant_redis
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from gateway import decide_features as df
from gateway.routes.email import core as email_core
from gateway.routes.email import digest as digest_mod
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import runner as runner_mod
from gateway.routes.email.quoting import (
    split_quoted_text,
    strip_disclaimer,
    strip_for_reading,
    strip_signature,
)
from sqlalchemy import text

from tests.unit.test_email_automation_tenancy import (
    _FakeProvider,
    _is_match_ask,
    _patch_providers,
    _seed_done_rule,
    _seed_label_rule,
    _seed_message,
    _watch_model,
)
from tests.unit.test_email_decide_on import _as_app, _seed_rule, _status_rows
from tests.unit.test_email_decide_questions import FakeDecide
from tests.unit.test_email_keep_separate import _account, _as_member, _mail
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ORG = "55555555-5555-5555-5555-555555555555"
OTHER_ORG = "66666666-6666-6666-6666-666666666666"
ACC = "acc-ai-cost-1"
ANSWER = '{"status": "REPLY", "rationale": "The supplier asked a question."}'


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("DECIDE_FEATURE_MODES", "")
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "")
    _clear()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear()


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()
    df._cooldown_until.clear()


def _modes(monkeypatch, modes: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "*")
    _clear()


@pytest.fixture()
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


# ── A vendor that thinks, as deepseek-v4-pro does ───────────────────────────


def _response(content: str, finish: str, completion_tokens: int,
              reasoning: str = "") -> SimpleNamespace:
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(completion_tokens=completion_tokens))


class ThinkingVendor:
    """``acompletion_with_fallback`` with the measured behaviour of the model.

    Unless the request carries ``thinking={"type": "disabled"}``, the model
    reasons first and the reasoning fills the whole output cap. The reply is
    then the FAILING SHAPE of production: ``content`` empty, the reasoning in
    ``reasoning_content`` and ``finish_reason="length"``.
    ``ignores_flag`` makes it reason whatever the request says.
    """

    def __init__(self, answer: str = ANSWER, *, ignores_flag: bool = False) -> None:
        self.answer = answer
        self.ignores_flag = ignores_flag
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, model, messages, max_tokens, temperature, **extra):
        self.calls.append({"model": model, "max_tokens": max_tokens, **extra})
        thinks = self.ignores_flag or extra.get("thinking") != {"type": "disabled"}
        if thinks:
            return _response("", "length", max_tokens,
                             reasoning="The last message asks about the finish. "
                                       * 40), model
        return _response(self.answer, "stop", 24), model


@pytest.fixture()
def vendor(monkeypatch) -> ThinkingVendor:
    fake = ThinkingVendor()
    monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
    return fake


async def _ask(user_sent_last: bool = False, **kw) -> tuple[str, bool]:
    thread = [{"side": "other_party", "from": "priya@supplier.example",
               "to": "owner@acme.example", "cc": "", "owner_cc_only": False,
               "date": "", "subject": "Quote", "attachments": "",
               "body": "Could you confirm the finish?"}]
    return await rz._llm_determine_thread_status(
        "From: priya@supplier.example\nCould you confirm the finish?",
        "owner@acme.example", "", user_sent_last=user_sent_last,
        account_id=ACC, thread_messages=thread, **kw)


class TestTheRootCause:

    async def test_the_old_request_gets_the_failing_shape(self, vendor) -> None:
        """The request as it was (no ``thinking``, a cap of 500) reads None:
        the reasoning used up the cap, and ``content`` is empty."""
        data, content, _used = await email_core._llm_json(
            "tier-balanced", [{"role": "user", "content": "x"}], max_tokens=500)
        assert data is None and content == ""
        assert vendor.calls[0]["max_tokens"] == 500

    async def test_the_status_call_asks_for_no_reasoning_and_reads_the_answer(
        self, vendor, tenant,
    ) -> None:
        assert await _ask() == ("REPLY", True)
        assert len(vendor.calls) == 1, "a readable answer needs no retry"
        call = vendor.calls[0]
        assert call["thinking"] == {"type": "disabled"}
        assert call["model"] == "tier-balanced"
        assert call["max_tokens"] == rz._STATUS_MAX_TOKENS <= 300

    async def test_the_router_forwards_thinking(self) -> None:
        """The Router path sends only the keys it names. ``thinking`` must be
        one, or the fix never reaches the vendor."""
        from acb_llm.routed import _FORWARDABLE

        assert "thinking" in _FORWARDABLE

    async def test_a_vendor_that_ignores_the_flag_falls_back_and_says_why(
        self, monkeypatch, tenant,
    ) -> None:
        fake = ThinkingVendor(ignores_flag=True)
        monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
        with structlog.testing.capture_logs() as caps:
            out = await _ask()
        assert out == ("FYI", False)
        lines = [c for c in caps if c["event"] == "email.determine_status_unreadable"]
        assert [line["content_chars"] for line in lines] == [0, 0]
        assert all("content" not in line for line in lines), "the log holds no reply text"


class TestTheRetry:

    async def test_the_retry_is_a_different_model(self, monkeypatch, tenant) -> None:
        fake = ThinkingVendor(answer='{"status": "maybe"}')
        monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
        monkeypatch.setattr(llm_context, "resolve_underlying_model",
                            lambda m: {"tier-balanced": "deepseek/deepseek-v4-pro",
                                       "tier-powerful": "deepseek/deepseek-v4-pro",
                                       "tier-fast": "deepseek/deepseek-v4-flash"}.get(m, m))
        assert await _ask(user_sent_last=True) == ("AWAITING_REPLY", False)
        models = [c["model"] for c in fake.calls]
        assert models == ["tier-balanced", "tier-fast"]
        assert "tier-powerful" not in models
        assert all(c["thinking"] == {"type": "disabled"} for c in fake.calls)

    async def test_no_retry_when_both_tiers_bind_one_model(
        self, monkeypatch, tenant,
    ) -> None:
        fake = ThinkingVendor(answer="{}")
        monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
        monkeypatch.setattr(llm_context, "resolve_underlying_model",
                            lambda m: "deepseek/deepseek-v4-pro")
        assert await _ask() == ("FYI", False)
        assert [c["model"] for c in fake.calls] == ["tier-balanced"]

    async def test_on_the_router_the_local_table_does_not_decide(
        self, monkeypatch, tenant,
    ) -> None:
        """Review round 1: the gateway table is not the Console binding."""
        import acb_llm.routed as routed

        fake = ThinkingVendor(answer="{}")
        monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
        monkeypatch.setattr(llm_context, "resolve_underlying_model",
                            lambda m: "deepseek/deepseek-v4-pro")
        monkeypatch.setattr(routed, "routing_is_on", lambda: True)
        await _ask()
        assert [c["model"] for c in fake.calls] == ["tier-balanced", "tier-fast"]

    def test_the_seed_keeps_the_status_tier_constraint(self) -> None:
        """``replyzero.STATUS_TIER_CONSTRAINT`` on the seed binding of a new
        Console: two different models, each one a model that takes
        ``thinking``. A live binding is an operator act, so this is the
        fence of the seed only."""
        import re

        seed = (Path(__file__).resolve().parents[2]
                / "infra/customer_console/002_seed_catalog.sql").read_text(encoding="utf-8")
        bound = dict(re.findall(r"\('(tier-[a-z]+)',\s*'([^']+)'", seed))
        fast, balanced = bound["tier-fast"], bound["tier-balanced"]
        assert fast != balanced
        assert all(m.startswith(("deepseek/", "anthropic/")) for m in (fast, balanced))

    async def test_shadow_still_returns_the_old_answer(self, vendor, monkeypatch,
                                                       tenant) -> None:
        """The decide path is untouched: in ``shadow`` it is asked beside the
        old call, and the old answer decides."""
        _modes(monkeypatch, "email.thread_status=shadow")
        fake = FakeDecide(choices={"status": "DONE"})
        monkeypatch.setattr(decide_mod, "decide", fake)
        assert await _ask() == ("REPLY", True)
        assert len(fake.calls) == 1 and len(vendor.calls) == 1

    async def test_on_makes_no_llm_call(self, vendor, monkeypatch, tenant) -> None:
        _modes(monkeypatch, "email.thread_status=on")
        fake = FakeDecide(choices={"status": "DONE"})
        monkeypatch.setattr(decide_mod, "decide", fake)
        out = await _ask(member="owner@acme.example")
        assert out == ("DONE", True)
        assert vendor.calls == []


# ── R8: no re-ask storm ─────────────────────────────────────────────────────


def _sent_thread(p, *, reason: str, classified_hours_ago: float) -> tuple[str, str]:
    """A mailbox of org B whose sent-last thread holds a stored status."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@ai-cost.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
               instructions="Receipts.", created_at=datetime.now(UTC) - timedelta(hours=9))
    tid = f"t-guess-{acc}"
    _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid,
                  sender="x@ext-ai-cost.test",
                  received_at=datetime.now(UTC) - timedelta(hours=8, minutes=30))
    sent = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                         thread_id=tid, sender=owner,
                         received_at=datetime.now(UTC) - timedelta(hours=8))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason, classified_at, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, 'AWAITING', "
            "CAST(:m AS uuid), now(), :r, now() - make_interval(secs => :s), "
            "CAST(:o AS uuid))"),
            {"a": acc, "t": tid, "m": sent, "r": reason,
             "s": classified_hours_ago * 3600, "o": p.org_b})
    return acc, tid


def _count_asks(monkeypatch, verdict: tuple[str, bool]) -> list[str]:
    asks: list[str] = []

    async def fake(thread_text, user_email, about, **kw):
        asks.append(kw.get("message_id") or "")
        return verdict

    monkeypatch.setattr(rz, "_llm_determine_thread_status", fake)
    return asks


@_DB_GATE
class TestNoReAskStorm:

    async def test_a_guess_is_asked_once_in_the_window(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The ask keeps failing, so each answer is a new guess. Two backfill
        cycles ask once: the first guess starts a new window."""
        _assert_non_priv(app_engine)
        p = promoted
        acc, tid = _sent_thread(
            p, reason="Replied — AWAITING_REPLY · auto",
            classified_hours_ago=rz._PROVISIONAL_RECHECK_HOURS + 1)
        asks = _count_asks(monkeypatch, ("AWAITING_REPLY", False))
        _patch_providers(monkeypatch, _FakeProvider())
        for _ in range(3):
            async with _as_app(p, p.org_b):
                await rz._maybe_classify_threads(acc)
        assert len(asks) == 1, f"a guess was asked {len(asks)} times in one window"
        assert _status_rows(p.admin_engine, acc) == [
            {"thread_id": tid, "status": "AWAITING",
             "reason": "Replied — AWAITING_REPLY · auto"}]

    async def test_a_fresh_guess_waits(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        acc, _tid = _sent_thread(p, reason="Replied — AWAITING_REPLY · auto",
                                 classified_hours_ago=0.1)
        asks = _count_asks(monkeypatch, ("DONE", True))
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)
        assert asks == []

    async def test_the_count_agrees_with_the_selection(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The reclassify drain stops when the count is zero, so the count
        must not hold a guess that the selection waits on."""
        p = promoted
        fresh, _t1 = _sent_thread(p, reason="Replied — DONE · auto",
                                  classified_hours_ago=0.1)
        old, _t2 = _sent_thread(p, reason="Replied — DONE · auto",
                                classified_hours_ago=rz._PROVISIONAL_RECHECK_HOURS + 1)
        async with _as_app(p, p.org_b), email_core._tenant_session() as db:
            counts = (await rz._count_reply_zero_backlog(db, fresh),
                      await rz._count_reply_zero_backlog(db, old))
        assert counts == (0, 1)

    async def test_one_run_asks_once_for_each_thread(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Three new rows of one conversation, one rules run: one status ask.
        Each row still gets the status rule and its stamp."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@ai-cost.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = datetime.now(UTC)
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Needs Reply",
                   instructions="Emails I need to respond to.",
                   created_at=now - timedelta(hours=1))
        tid = f"t-burst-{acc}"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      thread_id=tid, sender=owner, received_at=now - timedelta(minutes=40))
        new = [_seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                             thread_id=tid, sender="y@ext-ai-cost.test",
                             received_at=now - timedelta(minutes=m))
               for m in (30, 20, 10)]
        asks = _count_asks(monkeypatch, ("REPLY", True))
        monkeypatch.setattr(runner_mod, "ask_rule_match", AsyncMock(return_value=[]))
        _patch_providers(monkeypatch, _FakeProvider())
        async with _as_app(p, p.org_b):
            await runner_mod._run_rules_job(acc, 50, False, "scheduler")
        assert len(asks) == 1, f"one thread cost {len(asks)} status asks in one run"
        with p.admin_engine.connect() as c:
            stamped = c.execute(text(
                "SELECT count(*) FROM email_messages WHERE id = ANY(CAST(:ids AS uuid[])) "
                "AND rules_processed_at IS NOT NULL"), {"ids": new}).scalar_one()
        assert stamped == 3


# ── The digest brief cache ──────────────────────────────────────────────────


class FakeTenantRedis:
    """``TenantRedis`` in memory: it takes a ``TenantKey`` and nothing else.

    EM-T16 PR-A: ``mget`` records each batch read, ``set`` records NO time to
    live, and ``fail`` makes each command raise as a Redis that is down."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.reads: list[list[str]] = []
        self.fail = fail

    def _check(self, k) -> str:
        assert isinstance(k, tenant_redis.TenantKey)
        if self.fail:
            raise ConnectionError("redis is down")
        return k.value

    async def get(self, k):
        return self.store.get(self._check(k))

    async def mget(self, keys):
        names = [self._check(k) for k in keys]
        self.reads.append(names)
        return [self.store.get(n) for n in names]

    async def setex(self, k, ttl, value):
        name = self._check(k)
        self.store[name] = value
        self.ttls[name] = ttl

    async def set(self, k, value, **kw):
        name = self._check(k)
        self.store[name] = value
        self.ttls[name] = kw.get("ex")


def _brief_db() -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(morning_brief_enabled=True)))
    return db


BACKLOG = [{"subject": "Quote for 200 brackets", "who": "Priya", "age_days": 2}]


@pytest.fixture()
def brief_env(monkeypatch):
    redis = FakeTenantRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda *a, **k: redis)
    calls: list[Any] = []

    async def fake_llm(model, messages, **kw):
        calls.append(messages[-1]["content"])
        return {"brief": f"Reply to Priya first ({len(calls)})."}, "{}", model

    monkeypatch.setattr(digest_mod, "_llm_json", fake_llm)
    return SimpleNamespace(redis=redis, calls=calls)


class TestTheBriefCache:

    async def test_a_second_load_reads_the_cache(self, brief_env, tenant) -> None:
        first = await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        second = await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        assert first == second == "Reply to Priya first (1)."
        assert len(brief_env.calls) == 1, "the second load asked the model again"

    async def test_new_mail_refreshes_the_brief(self, brief_env, tenant) -> None:
        await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        more = [*BACKLOG, {"subject": "Invoice 4471", "who": "Accounts", "age_days": 0}]
        out = await digest_mod._digest_brief(_brief_db(), ACC, more, [])
        assert out == "Reply to Priya first (2)."
        assert len(brief_env.calls) == 2

    async def test_a_new_prompt_refreshes_the_brief(
        self, brief_env, tenant, monkeypatch,
    ) -> None:
        """Review round 1: a deploy that changes the prompt does not serve
        the brief of the old prompt."""
        await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        monkeypatch.setattr(digest_mod, "_BRIEF_SYSTEM", "Write one sentence.")
        await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        assert len(brief_env.calls) == 2

    async def test_the_key_is_the_tenant_mailbox_and_day(self, brief_env, tenant) -> None:
        await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        (stored,) = brief_env.redis.store
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert stored.startswith(f"cc:{ORG}:{digest_mod.BRIEF_CACHE_NAMESPACE}:{ACC}:{day}:")
        assert brief_env.redis.ttls[stored] == digest_mod.BRIEF_CACHE_TTL_SECS

    async def test_another_organization_reads_nothing(self, brief_env) -> None:
        for org in (ORG, OTHER_ORG):
            token = bind_tenant(org)
            try:
                await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
            finally:
                release_tenant(token)
        assert len(brief_env.calls) == 2

    async def test_with_no_tenant_the_brief_still_comes(self, brief_env) -> None:
        out = await digest_mod._digest_brief(_brief_db(), ACC, BACKLOG, [])
        assert out and brief_env.redis.store == {}

    async def test_a_brief_that_is_off_costs_no_call(self, brief_env, tenant) -> None:
        db = AsyncMock()
        db.execute.return_value = MagicMock(fetchone=MagicMock(
            return_value=SimpleNamespace(morning_brief_enabled=False)))
        assert await digest_mod._digest_brief(db, ACC, BACKLOG, []) == ""
        assert brief_env.calls == []


# ── EM-T16 PR-A, A2: the back-off of an undecided status ask in `on` (R8) ───
#
# Spec: ``email_app_master_plan.md`` §10.4.17 PR-A (D-EM-62). In ``on`` of
# ``email.thread_status``, with ``EMAIL_TRIAGE_ONCE_PER_CYCLE``, an undecided
# status ask of the backfill keeps a mark for 30 minutes, and the selection
# skips a marked thread before its caps. Three asks write the mark: (a)
# ``ask_status_first``, (b) ``_resolve_on`` on an undecided Block S status
# and (c) ``_mark_thread_replied`` on a sent thread. (d) An undecided rule
# match and a spent budget write none.


def _backoff_env(monkeypatch, *, flag: bool = True, modes: str = "email.thread_status=on",
                 fail: bool = False) -> SimpleNamespace:
    _modes(monkeypatch, modes)
    monkeypatch.setenv("EMAIL_TRIAGE_ONCE_PER_CYCLE", "true" if flag else "false")
    _clear()
    redis = FakeTenantRedis(fail=fail)
    got: list[int] = []

    def _client(*_a, **_k):
        got.append(1)
        return redis

    monkeypatch.setattr(tenant_redis, "get_tenant_redis", _client)
    return SimpleNamespace(redis=redis, got=got)


def _decide_gives_nothing(monkeypatch) -> list[str]:
    """``decide`` answers no request. Returns the question ids of each call."""
    asks: list[str] = []

    async def _none(_state, questions, **_kw):
        asks.append(",".join(sorted(questions)))
        raise decide_mod.DecideUnavailable("HTTP 503")

    monkeypatch.setattr(decide_mod, "decide", _none)
    return asks


def _status_asks_of(asks: list[str]) -> list[str]:
    return [a for a in asks if not _is_match_ask("decide", a)]


def _gap_conversation(p, *, label: str) -> tuple[str, str, str]:
    """A mailbox of org B with Receipt and the conversation rule Done, and a
    thread that is a conversation: our older reply in ``sent``, then an
    inbox row from outside. Returns (account, thread, inbox row)."""
    acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t16.test")
    _seed_label_rule(p.admin_engine, org=p.org_b, account_id=acc)
    _seed_done_rule(p.admin_engine, org=p.org_b, account_id=acc)
    return (acc, *_conversation_thread(p, acc, label))


def _conversation_thread(p, acc: str, label: str, *, age_h: float = 0.0) -> tuple[str, str]:
    tid = f"t-t16-{label}-{acc}"
    when = datetime.now(UTC) - timedelta(hours=age_h)
    _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                  thread_id=tid, received_at=when - timedelta(hours=1))
    mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                        thread_id=tid, received_at=when)
    return tid, mid


def _gap_new_thread(p, *, label: str) -> tuple[str, str, str]:
    """A mailbox of org B with the conversation rules Reply and Done, and a
    new thread of one inbox row. The match picks Reply, then Block S asks."""
    acc = _seed_account(p.admin_engine, org=p.org_b, owner="b@em-t16.test")
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Reply",
               instructions="A mail that asks a question.",
               created_at=datetime.now(UTC) - timedelta(days=2))
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_rules SET system_type = 'REPLY' "
                       "WHERE account_id = CAST(:a AS uuid)"), {"a": acc})
    _seed_done_rule(p.admin_engine, org=p.org_b, account_id=acc)
    tid = f"t-t16-{label}-{acc}"
    mid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid)
    return acc, tid, mid


def _sent_last(p, *, label: str) -> tuple[str, str, str]:
    """A mailbox of org B and a thread whose latest row is our reply in
    ``sent``, with no status row. Returns (account, thread, sent row)."""
    owner = f"owner-{uuid.uuid4().hex[:8]}@em-t16.test"
    acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, name="Receipt",
               instructions="Receipts.", created_at=datetime.now(UTC) - timedelta(hours=9))
    tid = f"t-t16-{label}-{acc}"
    _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid,
                  sender="x@ext-em-t16.test",
                  received_at=datetime.now(UTC) - timedelta(hours=8, minutes=30))
    sent = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                         thread_id=tid, sender=owner,
                         received_at=datetime.now(UTC) - timedelta(hours=8))
    return acc, tid, sent


async def _cycles(p, acc: str, n: int) -> None:
    for _ in range(n):
        async with _as_app(p, p.org_b):
            await rz._maybe_classify_threads(acc)


def _mark(p, acc: str, tid: str, mid: str) -> str:
    return f"cc:{p.org_b}:{rz.STATUS_BACKOFF_NAMESPACE}:{acc}:{tid}:{mid}"


@_DB_GATE
class TestTheOnBackoff:
    """A2, R8 on the phase-4 catalog as the non-owner role. Only ``decide``,
    the old completion and Redis are fakes."""

    async def test_an_undecided_first_ask_waits_and_new_mail_asks_again(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """(a). Three cycles ask once. The mark is tenant-scoped, holds no
        verdict and lives 1800 seconds, and no status row is written
        (D-EM-8). A new message makes a new key, so the next cycle asks."""
        _assert_non_priv(app_engine)
        p = promoted
        env = _backoff_env(monkeypatch)
        acc, tid, mid = _gap_conversation(p, label="first")
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 3)

        assert len(_status_asks_of(asks)) == 1, f"asked {asks} in one window"
        assert env.redis.store == {_mark(p, acc, tid, mid): "1"}
        assert env.redis.ttls == {_mark(p, acc, tid, mid): 1800}
        assert _status_rows(p.admin_engine, acc) == [], "an undecided ask wrote a row"

        new = _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread_id=tid,
                            received_at=datetime.now(UTC) + timedelta(minutes=1))
        await _cycles(p, acc, 1)
        assert len(_status_asks_of(asks)) == 2, "a new message did not ask at once"
        assert _mark(p, acc, tid, new) in env.redis.store

    async def test_an_undecided_late_ask_waits(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """(b). The match picks Reply, Block S asks, and ``_resolve_on``
        rejects the undecided status. The next cycle pays neither ask."""
        p = promoted
        env = _backoff_env(monkeypatch)
        acc, tid, mid = _gap_new_thread(p, label="late")
        seen: list[tuple[str, int]] = []
        _watch_model(monkeypatch, {"open": 0}, seen, match=True)
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 2)

        assert len(_status_asks_of(asks)) == 1, asks
        assert [leaf for leaf, _n in seen] == ["completion"], seen
        assert env.redis.store == {_mark(p, acc, tid, mid): "1"}
        assert _status_rows(p.admin_engine, acc) == []

    async def test_an_undecided_sent_ask_waits(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """(c). ``_mark_thread_replied`` returns the undecided state, and the
        backfill keeps the mark of the sent row."""
        p = promoted
        env = _backoff_env(monkeypatch)
        acc, tid, sent = _sent_last(p, label="sent")
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 3)

        assert len(_status_asks_of(asks)) == 1, asks
        assert env.redis.store == {_mark(p, acc, tid, sent): "1"}
        assert env.redis.ttls == {_mark(p, acc, tid, sent): 1800}
        assert _status_rows(p.admin_engine, acc) == []

    async def test_an_undecided_rule_match_keeps_no_mark(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """(d). The rule match is in ``on`` too and gives nothing. That is
        not a status ask, so each cycle asks again."""
        p = promoted
        env = _backoff_env(
            monkeypatch, modes="email.thread_status=on,email.rule_match=on")
        acc, _tid, _mid = _gap_new_thread(p, label="match")
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 2)

        match_asks = [a for a in asks if _is_match_ask("decide", a)]
        assert len(match_asks) == 2 and _status_asks_of(asks) == [], asks
        assert env.redis.store == {}

    async def test_a_spent_budget_keeps_no_mark(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """(d). ``LLMBudgetExhausted`` is not an undecided ask."""
        from email_ingestion.llm_cap import LLMBudgetExhausted

        p = promoted
        env = _backoff_env(monkeypatch)
        acc, _tid, _sent = _sent_last(p, label="budget")
        _patch_providers(monkeypatch, _FakeProvider())
        spent: list[int] = []

        async def _budget(*_a, **_kw):
            spent.append(1)
            raise LLMBudgetExhausted("limit 1")

        monkeypatch.setattr(rz, "_llm_determine_thread_status", _budget)
        await _cycles(p, acc, 2)
        assert len(spent) == 2 and env.redis.store == {}

    @pytest.mark.parametrize("modes", ["email.thread_status=shadow", ""])
    async def test_outside_on_the_flag_keeps_no_mark(
        self, modes, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The back-off is for ``on`` only. With the flag on in ``shadow`` or
        ``off``, a sent thread whose ask is undecided keeps no mark, and the
        backfill reads no mark. ``_PROVISIONAL_RECHECK_HOURS`` does this job
        outside ``on``."""
        p = promoted
        env = _backoff_env(monkeypatch, modes=modes)
        acc, tid, _sent = _sent_last(p, label=f"outside-{modes or 'off'}")
        _patch_providers(monkeypatch, _FakeProvider())
        replied: list[str] = []

        async def _undecided(account_id, thread_id, *_a, **_kw):
            replied.append(thread_id)
            return rz.UNDECIDED

        monkeypatch.setattr(rz, "_mark_thread_replied", _undecided)
        await _cycles(p, acc, 2)

        assert replied == [tid, tid], replied
        assert env.redis.store == {} and env.redis.reads == [] and env.got == []

    async def test_flag_off_asks_in_each_cycle_and_touches_no_redis(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        env = _backoff_env(monkeypatch, flag=False)
        acc, _tid, _mid = _gap_conversation(p, label="off")
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 3)

        assert len(_status_asks_of(asks)) == 3, asks
        assert env.got == [] and env.redis.store == {}

    async def test_a_redis_failure_reads_as_no_mark(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        p = promoted
        _backoff_env(monkeypatch, fail=True)
        acc, _tid, _mid = _gap_conversation(p, label="down")
        _patch_providers(monkeypatch, _FakeProvider())
        asks = _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 2)

        assert len(_status_asks_of(asks)) == 2, asks

    async def test_a_marked_thread_takes_no_slot_of_the_cap(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The skip comes BEFORE ``_BACKFILL_INBOUND_CAP``. With a cap of one,
        the second cycle reaches the older thread."""
        p = promoted
        env = _backoff_env(monkeypatch)
        acc, newer, newer_mid = _gap_conversation(p, label="cap-new")
        older, older_mid = _conversation_thread(p, acc, "cap-old", age_h=2)
        monkeypatch.setattr(rz, "_BACKFILL_INBOUND_CAP", 1)
        _patch_providers(monkeypatch, _FakeProvider())
        _decide_gives_nothing(monkeypatch)

        await _cycles(p, acc, 2)

        assert set(env.redis.store) == {_mark(p, acc, newer, newer_mid),
                                        _mark(p, acc, older, older_mid)}


class TestTheBackoffNeedsATenant:
    """R5: with no tenant bound, no mark is read or written, and no Redis
    client is taken."""

    async def test_no_tenant_no_mark(self, monkeypatch) -> None:
        env = _backoff_env(monkeypatch)
        token = clear_tenant()
        try:
            row = SimpleNamespace(thread_id="t1", id="m1")
            assert await rz._status_backoff_marked(ACC, [row]) == set()
            await rz._mark_status_backoff(ACC, "t1", "m1")
        finally:
            release_tenant(token)
        assert env.got == [] and env.redis.store == {} and env.redis.reads == []

    async def test_a_bound_tenant_reads_in_one_batch(self, monkeypatch, tenant) -> None:
        env = _backoff_env(monkeypatch)
        await rz._mark_status_backoff(ACC, "t1", "m1")
        rows = [SimpleNamespace(thread_id="t1", id="m1"),
                SimpleNamespace(thread_id="t1x", id="m2")]
        assert await rz._status_backoff_marked(ACC, rows) == {"t1"}
        prefix = f"cc:{ORG}:{rz.STATUS_BACKOFF_NAMESPACE}:{ACC}:"
        assert env.redis.reads == [[f"{prefix}t1:m1", f"{prefix}t1x:m2"]]
        assert rz.STATUS_BACKOFF_TTL_SECS == 1800
        assert env.redis.ttls == {f"{prefix}t1:m1": 1800}


# ── The text that read_email gives the model ────────────────────────────────

_SIG_CORP = (
    "Best regards,\nRahul Mehta\nSenior Purchase Manager | Acme Components Pvt Ltd\n"
    "+91 98450 12345 | rahul.mehta@acme-components.example\n"
    "Plot 14, KIADB Industrial Area, Bengaluru 560099\nwww.acme-components.example")
_DISCLAIMER = (
    "CONFIDENTIALITY NOTICE: This e-mail and any attachments are confidential and "
    "intended solely for the use of the addressee. If you are not the intended "
    "recipient, please notify the sender and delete it. Any unauthorised use, "
    "disclosure or copying is prohibited. Acme accepts no liability for any virus.")
_OLD_QUOTE = (
    "________________________________\nFrom: Priya Rao <priya@supplier.example>\n"
    "Sent: Tuesday, October 6, 2026 10:12 AM\nTo: Rahul Mehta <rahul.mehta@acme-components.example>\n"
    "Subject: RE: Quote for 200 brackets\n\nHi Rahul,\n\nWe can do 200 aluminium brackets "
    "at 4.10 each with a lead time of three weeks. Could you confirm the finish, anodised "
    "or raw, so we can lock the price? The price holds until Friday.\n\nThanks,\nPriya Rao\n"
    "Sales, Supplier Metals\n+91 80 4000 1234\n\n________________________________\n"
    "From: Rahul Mehta\nSent: Monday, October 5, 2026 4:40 PM\nTo: Priya Rao\n"
    "Subject: Quote for 200 brackets\n\nHi Priya, please quote 200 brackets to drawing "
    "B-114 rev C, delivered to Bengaluru.\n\n" + _SIG_CORP + "\n\n" + _DISCLAIMER)

#: Real-shaped mail, synthetic text. (name, body, words that stay, words
#: that go). The report quotes the share that these lose.
FIXTURES: list[tuple[str, str, list[str], list[str]]] = [
    ("outlook reply, signature, footer, two quotes",
     "Hi Priya,\n\nAnodised, please. Go ahead and lock the price.\n\n" + _SIG_CORP
     + "\n\n" + _DISCLAIMER + "\n\n" + _OLD_QUOTE,
     ["Anodised, please", "Best regards,", "Rahul Mehta"],
     ["+91 98450", "CONFIDENTIALITY", "Sent: Tuesday", "lead time"]),
    ("gmail reply",
     "Sounds good, Thursday at 3 works.\n\nBest,\nArjun\n\n"
     "On Tue, 6 Oct 2026 at 10:12, Priya Rao <priya@supplier.example> wrote:\n"
     "> Can we meet on Thursday to go over the drawings?\n> Priya",
     ["Thursday at 3 works", "Arjun"], ["Can we meet"]),
    ("rfc signature",
     "Please find the revised drawing attached.\n\n-- \nRahul Mehta\nMechanical Lead\n"
     "+91 98450 12345\n",
     ["revised drawing"], ["Mechanical Lead", "+91"]),
    ("mobile footer",
     "Approved. Go ahead with the order.\n\nSent from my iPhone",
     ["Approved"], ["iPhone"]),
    ("footer under a signature",
     "Invoice 4471 is attached, due on 30 October.\n\nRegards,\nAccounts Team\n"
     "Acme Components Pvt Ltd\naccounts@acme-components.example\n\n" + _DISCLAIMER,
     ["Invoice 4471", "Regards,", "Accounts Team"],
     ["accounts@acme", "CONFIDENTIALITY"]),
    ("reply quoting a reply, two quote levels",
     "Hi Meera,\n\nThe bore is fine now, I measured 12.01 mm.\n\nThanks,\nRahul\n\n"
     "On Wed, 7 Oct 2026 at 09:30, Meera QA <qa@plant.example> wrote:\n"
     + "".join(f"> Measured bore 12.0{n} mm on part {n}. Please re-check.\n"
               for n in range(9))
     + "> On Tue, 6 Oct 2026, Rahul wrote:\n>> Parts are on the bench.\n",
     ["12.01 mm", "Rahul"], ["Please re-check", "on the bench"]),
    ("footer with no signature",
     "Please ship the 40 units by Friday.\n\nThis email and any files transmitted "
     "with it are confidential and intended solely for the use of the individual "
     "to whom they are addressed. If you have received this email in error, "
     "please notify the system manager.\n\nPlease consider the environment "
     "before printing this email.",
     ["ship the 40 units"], ["confidential", "environment"]),
]

#: Mail that must come back whole.
UNCHANGED = [
    "Thanks,\nPriya",
    "The parts left on Monday.\nSent via DHL Express, tracking 12345.\n\nThanks",
    "Hi team,\n\nThis message is confidential, so do not forward it.\n\n"
    "The Q3 numbers are in the sheet.",
    "---------- Forwarded message ---------\nFrom: QA <qa@plant.example>\n"
    "Subject: NCR\n\nThe bore is out of tolerance.",
    "Call me at +91 98450 12345 when you land.\n\nCheers,\nAnil",
    # Review round 1: each of these lost the member's own words.
    "Dear Ravi,\n\nThis email confirms your offer of employment as a design "
    "engineer from 1 November. The terms are confidential. Please sign and "
    "return the attached letter by 15 October so we can book your laptop.",
    "Hi Legal,\n\nConfidentiality question: can we share the drawings with the "
    "sub-contractor under our NDA?\n\nThanks,\nPriya",
    "Hi all,\n\nThe review is on Friday at 10.\n\nIf you are not able to attend, "
    "please delete this invite and tell me who will come in your place, so that "
    "the room booking stays right for the whole team.\n\nThe agenda is in the "
    "shared folder.",
    "Hi team,\n\nImportant notice: the office is closed on Friday for the audit. "
    "The audit papers are confidential, so keep them in the locked cabinet.\n\n"
    "Work from home if you can.",
    "Deal is approved.\n\nCheers,\nRavi\nP.S. The new number for the client is "
    "+1 415 555 0101, call them today.",
    "Hi Sam,\n\nThanks\n\nI got the parts. Two issues:\n1. The bracket is bent.\n"
    "2. Call me at +91 98450 12345 about it.\nSee https://drive.example/p/1",
    "Stock count:\nBolts | 400\n--\nNuts | 250\n--\nWashers | 900",
    # A footer-like paragraph with the member's words AFTER it.
    "Hi,\n\nThis message contains confidential pricing that is intended only for "
    "the Acme buyers, so please do not disclose it to anyone outside the team.\n\n"
    "The price is 4.10 for each bracket.",
    # A sentence under a closing line is not a name.
    "Got it.\n\nThanks\nCall Ravi at the site office today, he has the keys.\n"
    "+91 98450 12345",
    "FYI, see below. Can you check the tolerance on the bore?\n\n"
    "---------- Forwarded message ---------\nFrom: QA <qa@plant.example>\n"
    "Date: Wed, 7 Oct 2026\nSubject: NCR 2231\n\nMeasured bore 12.08 mm.",
]


def share_removed() -> tuple[int, int]:
    """(chars before, chars after) over :data:`FIXTURES`."""
    before = sum(len(body) for _n, body, _k, _g in FIXTURES)
    after = sum(len(strip_for_reading(body)) for _n, body, _k, _g in FIXTURES)
    return before, after


class TestReadingText:

    @pytest.mark.parametrize(("name", "body", "kept", "gone"), FIXTURES,
                             ids=[f[0] for f in FIXTURES])
    def test_the_fixture(self, name, body, kept, gone) -> None:
        out = strip_for_reading(body)
        for words in kept:
            assert words in out, f"{name}: lost {words!r}"
        for words in gone:
            assert words not in out, f"{name}: kept {words!r}"

    @pytest.mark.parametrize("body", UNCHANGED)
    def test_mail_that_comes_back_whole(self, body) -> None:
        assert strip_for_reading(body) == body

    def test_each_step_keeps_text_it_cannot_cut(self) -> None:
        assert strip_signature("-- \nonly a signature") == "-- \nonly a signature"
        assert strip_disclaimer(_DISCLAIMER) == _DISCLAIMER
        assert strip_for_reading("") == ""

    def test_the_quote_split_is_the_one_seam(self) -> None:
        body = "Yes.\n\n> earlier"
        assert strip_for_reading(body) == split_quoted_text(body)[0] == "Yes."

    def test_the_share_removed(self) -> None:
        before, after = share_removed()
        assert after / before < 0.5, f"only {1 - after / before:.0%} removed"


_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load_agent():
    spec = importlib.util.spec_from_file_location("ea_ai_cost", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agent()


class TestTheAgentTool:

    @pytest.fixture()
    def gw(self, monkeypatch):
        rec: list[tuple[str, dict]] = []
        whole = FIXTURES[0][1]

        async def fake_get(path, params=None):
            rec.append((path, dict(params or {})))
            if path.endswith("/full-body"):
                return {"subject": "S", "from": "a@b.example", "body_text": whole}
            trim = (params or {}).get("trim") == "true"
            body = strip_for_reading(whole) if trim else whole
            return {"from_address": {"name": "A", "email": "a@b.example"},
                    "subject": "Hi", "body_text": body, "body_trimmed": trim,
                    "to_addresses": [], "cc_addresses": [], "attachments": []}

        monkeypatch.setattr(agents, "_get", fake_get)
        return SimpleNamespace(rec=rec, whole=whole)

    async def test_the_default_read_asks_for_the_new_text(self, gw) -> None:
        out = await agents.read_email("m1")
        assert gw.rec[-1] == ("/email/messages/m1", {"trim": "true"})
        assert "Anodised, please" in out and "CONFIDENTIALITY" not in out
        assert "full=true" in out, "the model is told how to read the rest"

    async def test_full_reads_the_whole_body(self, gw) -> None:
        out = await agents.read_email("m1", full=True)
        assert gw.rec[-1] == ("/email/messages/m1/full-body", {})
        assert out.endswith(gw.whole[:12000])
        assert "removed" not in out


# ── R8: the route ───────────────────────────────────────────────────────────


@_DB_GATE
class TestTheTrimRoute:

    def _setup(self, p):
        from acb_auth.roles import UserContext, UserRole

        tag = uuid.uuid4().hex[:8]
        owner = f"member-{tag}@ai-cost.test"
        box = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
        mid = _mail(p.admin_engine, org=p.org_b, account_id=box,
                    sender=f"s-{tag}@sender.test", body=FIXTURES[0][1])
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        return mid, me

    async def test_trim_returns_the_new_text_and_keeps_the_store(
        self, promoted, app_engine,  # noqa: F811
    ):
        from gateway.routes.email.transport import messages as messages_mod

        _assert_non_priv(app_engine)
        p = promoted
        mid, me = self._setup(p)
        async with _as_member(p, p.org_b):
            got = await messages_mod.get_message(mid, user=me, mark_read=False, trim=True)
        assert got.body_trimmed is True
        assert got.body_text == strip_for_reading(FIXTURES[0][1])
        with p.admin_engine.connect() as c:
            stored = c.execute(text(
                "SELECT body_text FROM email_messages WHERE id = CAST(:m AS uuid)"),
                {"m": mid}).scalar_one()
        assert stored == FIXTURES[0][1], "the trim must not reach the store"

    async def test_no_trim_returns_the_whole_body(
        self, promoted, app_engine,  # noqa: F811
    ):
        from gateway.routes.email.transport import messages as messages_mod

        p = promoted
        mid, me = self._setup(p)
        async with _as_member(p, p.org_b):
            got = await messages_mod.get_message(mid, user=me, mark_read=False)
        assert got.body_trimmed is False
        assert got.body_text == FIXTURES[0][1]
