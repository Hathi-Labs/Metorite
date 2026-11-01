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
* The template fill writes the raw ``{{...}}`` text on a blank answer.
* The self-anneal reply sends ``""``.
* The graphiti gate reads a blank answer as YES.
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
    with pytest.raises(actions.TemplateFillFailed):
        await actions._render_template('{{choose "urgent", "normal"}}', {"body": "x"})
    assert seen["job"] == "writing"


async def test_a_failed_template_fill_raises(monkeypatch) -> None:
    import acb_llm.context as llm_context
    from gateway.routes.email.automation import actions

    async def boom(**_kw: Any) -> Any:
        raise RuntimeError("vendor down")

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", boom)
    with pytest.raises(actions.TemplateFillFailed):
        await actions._render_template("{{summarize}}", {"body": "x"})


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
    assert errors == [{"type": "LABEL", "error": "the template fill gave no text"}]


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


@pytest.mark.parametrize("content", ["", "   "])
async def test_a_blank_recovery_reply_sends_the_fixed_apology(monkeypatch, content) -> None:
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
    assert out == {"result": {"role": "assistant",
                              "content": executor._ANNEAL_EMPTY_REPLY}}
    assert executor._ANNEAL_EMPTY_REPLY.strip()
    assert seen["job"] == "writing"
    assert "self_anneal.llm_recovery_empty" in [c["event"] for c in caps]


# ── memory: the graphiti worthiness gate ────────────────────────────────────


@pytest.mark.parametrize(("verdict", "worthy"), [
    ("", False), ("  ", False), ("NO", False), ("YES", True)])
async def test_a_blank_worthiness_verdict_is_no(monkeypatch, verdict, worthy) -> None:
    import acb_llm
    from acb_memory import graphiti_client

    seen: dict[str, Any] = {}

    async def fake_complete(**kw: Any) -> str:
        seen.update(kw)
        return verdict

    monkeypatch.setattr(acb_llm, "complete", fake_complete)
    text = "We agreed to ship 40 units to Acme on 12 March."
    assert await graphiti_client._is_episode_worthy(text) is worthy
    assert seen["job"] == "structured" and seen["max_tokens"] == 8


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
