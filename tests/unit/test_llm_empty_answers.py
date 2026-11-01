"""An empty model answer is never read as a real one (task 1128).

The model seam returned a blank answer as a success, so each caller read it
as its own "no". These tests pin the fix at each call site of the owner's
table (2026-10-31) whose old reading of a blank answer was wrong. The digest
and the WhatsApp draft and nudge keep their tests beside their other tests:
``test_crm_digest.py``, ``test_whatsapp_drafting.py`` and
``test_whatsapp_commitments.py``. The seam log is in ``test_llm_jobs.py``.

Mutations this file catches (R7):

* The consult plan reads a blank answer as "no specialist needed" again.
* The rule pick reads a blank answer as "no rule fits", and burns the
  watermark. It must raise ``LLMUnavailable``, so the mail is tried again.
* A job has no limit on those tries, an outage counts as a blank answer, or a
  preview spends a try. The R8 half of the limit is in ``test_email_ai_cost.py``.
* The template fill writes the raw ``{{...}}`` text on a blank answer.
* The self-anneal finishes the run with ``""`` or a stock reply. A blank
  recovery must return None, so the first error is raised.
* The graphiti gate drops the episode on a blank answer, or logs no warning.
  A blank answer is uncertain, and the gate keeps what is uncertain.
* Custom Apps answers 200 with ``""``.
* The tier test passes on a blank answer.
* The sender pin prompt loses the word "JSON".
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog


def _resp(content: Any) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ── email: the consult plan of a draft ──────────────────────────────────────


@pytest.mark.parametrize("answer", [(None, "", "tier-fast"), (None, "  ", "tier-fast")])
async def test_a_blank_consult_plan_is_no_qualifier_not_no_specialist(
        monkeypatch, answer) -> None:
    from gateway.routes.email.automation import drafting

    seen: dict[str, Any] = {}

    async def llm(*_a: Any, **kw: Any) -> tuple[Any, str, str]:
        seen.update(kw)
        return answer

    activity: list[dict[str, Any]] = []

    async def on_activity(event: dict[str, Any]) -> None:
        activity.append(event)

    monkeypatch.setattr(drafting, "_llm_json", llm)
    out = await drafting._draft_consult_plan(
        {"from": "a@x.test", "subject": "Hi", "body": "hello"}, on_activity)
    assert out == []
    assert seen["job"] == "structured"
    [event] = [a for a in activity if a.get("kind") == "qualify"]
    assert "no specialist needed" not in event["detail"]
    assert event["detail"] == drafting._QUALIFIER_UNAVAILABLE


async def test_a_real_empty_consult_list_still_says_no_specialist(monkeypatch) -> None:
    from gateway.routes.email.automation import drafting

    async def llm(*_a: Any, **_k: Any) -> tuple[Any, str, str]:
        return {"kind": "scheduling", "consult": []}, "{}", "tier-fast"

    activity: list[dict[str, Any]] = []

    async def on_activity(event: dict[str, Any]) -> None:
        activity.append(event)

    monkeypatch.setattr(drafting, "_llm_json", llm)
    assert await drafting._draft_consult_plan({"body": "x"}, on_activity) == []
    [event] = [a for a in activity if a.get("kind") == "qualify"]
    assert event["detail"] == "no specialist needed"


# ── email: the rule pick ────────────────────────────────────────────────────

_EMAIL = {"from": "x@y.com", "subject": "Hi", "body": "hello", "to": ""}
_RULES = [{"id": "r1", "name": "Newsletter", "instructions": "newsletters"}]


@pytest.mark.parametrize(("pick", "event"), [
    ("_llm_pick_rule", "email.llm_pick_rule_empty"),
    ("_llm_pick_rules", "email.llm_pick_rules_empty"),
])
async def test_a_blank_rule_pick_is_an_outage_not_no_match(monkeypatch, pick, event) -> None:
    """A blank answer raises ``LLMUnavailable``, as a failed call does. The
    runner then keeps the watermark, and a later run classifies the mail.
    "No rule fits" would burn the watermark for good (repair round 1)."""
    from gateway.routes.email.automation import engine

    seen: dict[str, Any] = {}

    async def llm(*_a: Any, **kw: Any) -> tuple[Any, str, str]:
        seen.update(kw)
        return None, "  ", "deepseek/deepseek-flash"

    monkeypatch.setattr(engine, "_llm_json", llm)
    with structlog.testing.capture_logs() as caps, \
            pytest.raises(engine.LLMUnavailable, match=engine.EMPTY_RULE_ANSWER):
        await getattr(engine, pick)(_EMAIL, _RULES)
    assert seen["job"] == "structured"
    events = [c["event"] for c in caps]
    assert event in events
    assert not any(e.endswith(("_unparseable", "_failed")) for e in events)


# ── email: the limit on blank rule answers ──────────────────────────────────

_ORG = "55555555-5555-5555-5555-555555555555"
_ACC = "acc-blank-1"


class _Count:
    """The two commands of ``TenantRedis`` that the blank count uses."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def _name(self, k: Any) -> str:
        from acb_common.tenant_redis import TenantKey

        assert isinstance(k, TenantKey)
        if self.down:
            raise ConnectionError("redis is down")
        return k.value

    async def incr(self, k: Any, amount: int = 1) -> int:
        name = self._name(k)
        self.counts[name] = self.counts.get(name, 0) + amount
        return self.counts[name]

    async def expire(self, k: Any, seconds: int) -> bool:
        self.ttls[self._name(k)] = seconds
        return True


@pytest.fixture()
def blank(monkeypatch):
    """A model that always answers blank, with a tenant and a Redis bound."""
    from acb_common import tenant_redis
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.email.automation import engine

    redis = _Count()
    calls: list[int] = []

    async def llm(*_a: Any, **_k: Any) -> tuple[Any, str, str]:
        calls.append(1)
        return None, "  ", "deepseek/deepseek-flash"

    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda *a, **k: redis)
    monkeypatch.setattr(engine, "_llm_json", llm)
    token = bind_tenant(_ORG)
    yield SimpleNamespace(engine=engine, redis=redis, calls=calls)
    release_tenant(token)


def _job_read(engine: Any, **facts: Any) -> Any:
    return engine.MatchRead(**{
        "account_id": _ACC, "email": _EMAIL, "message_id": "m1",
        "ask_rules": _RULES, "bound_blank": True, **facts})


async def _blank_tries(engine: Any, read: Any, tries: int) -> list[Any]:
    """Ask ``tries`` times. Each item is the answer, or "raised"."""
    out: list[Any] = []
    for _ in range(tries):
        try:
            out.append(await engine.ask_rule_match(read))
        except engine.EmptyRuleAnswer:
            out.append("raised")
    return out


@pytest.mark.parametrize("multi", [False, True])
async def test_a_job_keeps_a_blank_mail_until_its_tries_are_spent(blank, multi) -> None:
    """Each blank answer under the limit raises, so the job stamps nothing.
    The answer at the limit is "no rule matched", so the job goes on."""
    limit = blank.engine.BLANK_RULE_TRIES
    assert 2 <= limit <= 5, "the limit is a small number of tries"
    read = _job_read(blank.engine, multi_rule=multi)
    assert await _blank_tries(blank.engine, read, limit) == ["raised"] * (limit - 1) + [[]]
    assert len(blank.calls) == limit


async def test_the_limit_logs_once(blank) -> None:
    read = _job_read(blank.engine)
    with structlog.testing.capture_logs() as caps:
        await _blank_tries(blank.engine, read, blank.engine.BLANK_RULE_TRIES + 2)
    lines = [c for c in caps if c["event"] == "email.rule_blank_tries_spent"]
    assert len(lines) == 1
    assert lines[0]["account_id"] == _ACC
    assert lines[0]["message_id"] == "m1"


async def test_each_mail_has_its_own_count_under_the_tenant(blank) -> None:
    engine = blank.engine
    for mail in ("m1", "m2"):
        await _blank_tries(engine, _job_read(engine, message_id=mail), 1)
    name = f"cc:{_ORG}:{engine.BLANK_RULE_NAMESPACE}:{_ACC}:"
    assert blank.redis.counts == {f"{name}m1": 1, f"{name}m2": 1}
    assert set(blank.redis.ttls.values()) == {engine.BLANK_RULE_TTL_SECS}


async def test_at_the_limit_the_matches_with_no_model_still_apply(blank) -> None:
    """The multi-rule mode keeps a static match. The blank answer took away
    only what the model would add."""
    rule = {"id": "r2", "name": "Static"}
    hit = {"rule": rule, "reason": "Matched static conditions.", "source": "static"}
    read = _job_read(blank.engine, multi_rule=True, decided=[hit], order=["r2", "r1"])
    answers = await _blank_tries(blank.engine, read, blank.engine.BLANK_RULE_TRIES)
    assert answers[-1] == [hit]


@pytest.mark.parametrize("multi", [False, True])
async def test_an_outage_never_counts_and_never_ends(blank, monkeypatch, multi) -> None:
    """A provider failure is no blank answer. It raises each time, as it did,
    so the mail waits for the provider however long that takes."""
    engine = blank.engine

    async def down(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider is down")

    monkeypatch.setattr(engine, "_llm_json", down)
    read = _job_read(engine, multi_rule=multi)
    for _ in range(engine.BLANK_RULE_TRIES + 3):
        with pytest.raises(engine.LLMUnavailable) as err:
            await engine.ask_rule_match(read)
        assert not isinstance(err.value, engine.EmptyRuleAnswer)
    assert blank.redis.counts == {}


async def test_a_preview_never_spends_a_try(blank) -> None:
    """A read with no ``bound_blank`` is a preview. It keeps raising, and the
    job later still has each of its tries."""
    limit = blank.engine.BLANK_RULE_TRIES
    read = _job_read(blank.engine, bound_blank=False)
    assert await _blank_tries(blank.engine, read, limit + 2) == ["raised"] * (limit + 2)
    assert blank.redis.counts == {}


async def test_the_request_paths_stay_previews(blank, monkeypatch) -> None:
    """The composed form serves the routes. It never counts, by its default."""
    engine = blank.engine
    rule = {**_RULES[0], "enabled": True}
    for name, value in (("_load_rules", [rule]), ("_is_reply_candidate", (True, "")),
                        ("_load_rule_patterns", {}), ("_load_rule_guidance", None),
                        ("_fetch_sender_history", []), ("_decide_member", None)):
        monkeypatch.setattr(engine, name, AsyncMock(return_value=value))
    for _ in range(engine.BLANK_RULE_TRIES + 1):
        with pytest.raises(engine.LLMUnavailable):
            await engine._match_email_to_rule(AsyncMock(), _ACC, _EMAIL, message_id="m1")
    assert len(blank.calls) == engine.BLANK_RULE_TRIES + 1
    assert blank.redis.counts == {}


@pytest.mark.parametrize("resolve", [True, False])
async def test_only_a_live_job_marks_its_read(monkeypatch, resolve) -> None:
    """``resolve`` is the live job, and a dry run is a preview."""
    from gateway.routes.email.automation import engine, replyzero

    read = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "read_rule_match", read)
    monkeypatch.setattr(replyzero, "read_status_first", AsyncMock(return_value=None))
    monkeypatch.setattr(replyzero, "_thread_is_conversation", AsyncMock(return_value=False))
    row = SimpleNamespace(id="m1", thread_id="t1")
    await engine.read_classification(AsyncMock(), _ACC, row, _EMAIL, resolve=resolve)
    assert read.await_args.kwargs["bound_blank"] is resolve


@pytest.mark.parametrize(("facts", "tenant"), [
    ({}, False),
    ({"message_id": None}, True),
])
async def test_with_no_tenant_or_no_mail_id_the_mail_stays(blank, facts, tenant) -> None:
    from acb_common.db import clear_tenant

    if not tenant:
        clear_tenant()
    read = _job_read(blank.engine, **facts)
    limit = blank.engine.BLANK_RULE_TRIES
    assert await _blank_tries(blank.engine, read, limit + 1) == ["raised"] * (limit + 1)
    assert blank.redis.counts == {}


async def test_with_redis_down_the_mail_stays(blank) -> None:
    """A lost mail costs more than one more model call, so a Redis outage
    keeps the old retry and never reads the mail as "no rule matched"."""
    blank.redis.down = True
    limit = blank.engine.BLANK_RULE_TRIES
    with structlog.testing.capture_logs() as caps:
        answers = await _blank_tries(blank.engine, _job_read(blank.engine), limit + 1)
    assert answers == ["raised"] * (limit + 1)
    assert "email.rule_blank_count_unavailable" in [c["event"] for c in caps]


@pytest.mark.parametrize(("pick", "verdict", "expected"), [
    ("_llm_pick_rule", {"index": -1}, None),
    ("_llm_pick_rules", {"matches": []}, []),
])
async def test_a_real_no_match_still_returns_no_match(
        monkeypatch, pick, verdict, expected) -> None:
    from gateway.routes.email.automation import engine

    async def llm(*_a: Any, **_k: Any) -> tuple[Any, str, str]:
        return verdict, "{}", "m"

    monkeypatch.setattr(engine, "_llm_json", llm)
    assert await getattr(engine, pick)(_EMAIL, _RULES) == expected


# ── email: the template fill ────────────────────────────────────────────────


@pytest.mark.parametrize("content", [None, "", "  "])
async def test_a_blank_template_fill_raises_and_never_returns_braces(
        monkeypatch, content) -> None:
    import acb_llm.context as llm_context
    from gateway.routes.email.automation import actions

    seen: dict[str, Any] = {}

    async def fake(**kw: Any) -> tuple[Any, str]:
        seen.update(kw)
        return _resp(content), "m"

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
    with pytest.raises(actions.TemplateFillFailed) as err:
        await actions._render_template('{{choose "urgent", "normal"}}', {"body": "x"})
    assert str(err.value) == actions.FILL_EMPTY
    assert seen["job"] == "writing"


@pytest.mark.parametrize(("error", "text"), [
    (RuntimeError("vendor down: SECRET"), "FILL_FAILED"),
    ("budget", "FILL_BUDGET"),
])
async def test_a_failed_template_fill_says_why_and_what_next(
        monkeypatch, error, text) -> None:
    """The member reads this in History: what happened, why, and the next
    step (``DESIGN_SYSTEM.md`` §10). A spent daily budget has its own words.
    No vendor text reaches it (repair round 1)."""
    import acb_llm.context as llm_context
    from email_ingestion.llm_cap import LLMBudgetExhausted
    from gateway.routes.email.automation import actions

    exc = LLMBudgetExhausted("limit 500") if error == "budget" else error

    async def boom(**_kw: Any) -> Any:
        raise exc

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", boom)
    with pytest.raises(actions.TemplateFillFailed) as err:
        await actions._render_template("{{summarize}}", {"body": "x"})
    message = str(err.value)
    assert message == getattr(actions, text)
    assert "SECRET" not in message and len(message) <= 160
    assert "did not run" in message and "again" in message


async def test_a_static_template_asks_no_model() -> None:
    from gateway.routes.email.automation import actions

    assert await actions._render_template("Receipt", {}) == "Receipt"
    assert await actions._render_template("", {}) == ""


async def test_a_blank_label_fill_fails_the_action_and_writes_no_label(
        monkeypatch) -> None:
    import acb_llm.context as llm_context
    from gateway.routes.email.automation import actions

    async def blank(**_kw: Any) -> tuple[Any, str]:
        return _resp(""), "m"

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", blank)
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
    errors: list[dict[str, str]] = []
    with patch.object(actions, "apply_label", AsyncMock()) as label:
        done = await actions._apply_rule_actions(
            db, object(), "msg-1", "pmid-1",
            [{"type": "LABEL", "label": '{{choose "a", "b"}}', "label_ai": True}],
            email={"from": "n@x.test", "subject": "s", "body": "b"},
            account_id="acc-1", errors_out=errors)
    assert done == []
    label.assert_not_awaited()
    assert errors == [{"type": "LABEL", "error": actions.FILL_EMPTY}]


# ── email: the sender pin prompt says JSON ──────────────────────────────────


async def test_the_sender_pin_prompt_says_json_and_names_its_job() -> None:
    """42 calls in 7 days answered 400: JSON mode with no "json" in the
    prompt. The seam adds the word too, and the prompt now holds it."""
    from gateway.routes.email.automation import learning as lg

    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[
        SimpleNamespace(subject=f"s{i}", snippet="x") for i in range(4)]))
    llm = AsyncMock(return_value=({"always": False}, "", ""))
    with patch.object(lg, "_llm_json", llm):
        await lg._ai_confirms_sender_pattern(
            db, "acc-1", "n@x.test", {"name": "Newsletter", "instructions": "n"})
    args, kwargs = llm.await_args
    model, messages = args
    assert model == "tier-fast"
    assert "json" in messages[0]["content"].lower()
    assert kwargs["job"] == "structured" and kwargs["max_tokens"] == 200


# ── orchestrator: the self-anneal reply ─────────────────────────────────────


@pytest.mark.parametrize(("content", "recovered"), [("", False), ("   ", False),
                                                    ("Sorry, try again.", True)])
async def test_a_blank_recovery_reply_is_no_recovery(monkeypatch, content, recovered) -> None:
    """A blank recovery answer returns None. The caller then goes on to the
    self-mutation step and raises the first error with its class, rather than
    finish the run with a blank or a stock reply (repair round 1)."""
    import acb_llm
    from orchestrator import executor

    seen: dict[str, Any] = {}

    async def fake_complete(**kw: Any) -> str:
        seen.update(kw)
        return content

    monkeypatch.setattr(acb_llm, "complete", fake_complete)
    with structlog.testing.capture_logs() as caps:
        out = await executor._llm_recovery(
            "projects-assistant", {"message": "hi"}, RuntimeError("x"), "")
    if recovered:
        assert out == {"result": {"role": "assistant", "content": content}}
    else:
        assert out is None
        assert "self_anneal.llm_recovery_empty" in [c["event"] for c in caps]
    assert seen["job"] == "writing"


def test_the_caller_raises_the_first_error_when_recovery_is_none() -> None:
    """Structural: the batch path returns a recovery only when it is not None,
    then raises ``AgentRunError`` with ``original=exc``."""
    import inspect

    from orchestrator import executor

    src = inspect.getsource(executor)
    at = src.index("recovery = await _self_anneal(")
    tail = src[at:at + 3000]
    assert "if recovery is not None:\n            return recovery" in tail
    assert "raise AgentRunError(" in tail and "original=exc" in tail


# ── memory: the graphiti worthiness gate ────────────────────────────────────


@pytest.mark.parametrize(("verdict", "worthy", "warned"), [
    ("", True, True), ("  ", True, True), ("NO", False, False), ("YES", True, False)])
async def test_a_blank_worthiness_verdict_records_and_warns(
        monkeypatch, verdict, worthy, warned) -> None:
    """The gate keeps what is uncertain (its docstring and its call site say
    so), and a blank answer is uncertain. Dropping it would lose memory with
    no sign (repair round 1)."""
    import acb_llm
    from acb_memory import graphiti_client

    seen: dict[str, Any] = {}

    async def fake_complete(**kw: Any) -> str:
        seen.update(kw)
        return verdict

    monkeypatch.setattr(acb_llm, "complete", fake_complete)
    text = "We agreed to ship 40 units to Acme on 12 March."
    with structlog.testing.capture_logs() as caps:
        assert await graphiti_client._is_episode_worthy(text) is worthy
    assert seen["job"] == "structured" and seen["max_tokens"] == 8
    lines = [c for c in caps if c["event"] == "graphiti.worthiness_gate_empty"]
    assert [c["log_level"] for c in lines] == (["warning"] if warned else [])


# ── settings: the tier test ─────────────────────────────────────────────────


@pytest.mark.parametrize(("reply", "ok"), [("", False), ("  ", False), ("OK", True)])
async def test_a_blank_tier_test_is_a_failure(monkeypatch, reply, ok) -> None:
    import acb_llm.client as client
    from gateway.routes import settings

    seen: dict[str, Any] = {}

    async def fake_complete(**kw: Any) -> str:
        seen.update(kw)
        return reply

    monkeypatch.setattr(client, "complete", fake_complete)
    out = await settings.test_tier(settings.TestRequest(tier_name="tier-fast"),
                                   _user=None)
    assert out.success is ok
    assert out.response == (reply.strip() or "(empty response)")
    assert seen["job"] == "structured" and seen["max_tokens"] == 64
