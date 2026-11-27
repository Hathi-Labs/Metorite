"""Unit tests for the WhatsApp commitment extractor (both directions) and the
waiting-on nudge drafter (W4.2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from gateway.routes.whatsapp.automation import commitments as wa_commitments
from gateway.routes.whatsapp.automation.commitments import (
    build_nudge_messages,
    draft_nudge,
    extract_commitment,
)
from gateway.routes.whatsapp.automation.drafting import NO_MODEL_ANSWER, DraftUnavailable


@pytest.mark.parametrize("body,expect_due", [
    ("I'll send the revised quote by Friday", "by friday"),
    ("we'll ship the units tomorrow", "tomorrow"),
    ("Sure, will share the AWB number by tomorrow", "by tomorrow"),
    ("I will get back to you with the numbers", None),
    ("sending you the invoice today", "today"),
    ("Let me check with accounts and revert", None),
    ("we'll deliver within two weeks", "within two weeks"),
])
def test_promises_are_extracted_with_due_hint(body, expect_due) -> None:
    result = extract_commitment(body)
    assert result is not None
    text_val, due = result
    assert text_val == body.strip()[:200]
    assert due == expect_due


@pytest.mark.parametrize("body", [
    "The meeting is by Friday",          # deadline but no promise verb
    "How much for 2 units?",             # a question, not a promise
    "Thanks, received the payment",       # acknowledgement
    "Where is my order?",                 # an ask, not a commitment
    "",
    None,
])
def test_non_commitments_return_none(body) -> None:
    assert extract_commitment(body) is None


def test_text_is_bounded() -> None:
    body = "I'll send " + ("x" * 500)
    result = extract_commitment(body)
    assert result is not None
    assert len(result[0]) <= 200


def test_commitment_route_registered() -> None:
    from gateway.routes.whatsapp import router
    paths = {r.path for r in router.routes}
    assert "/whatsapp/commitments" in paths


# ── waiting-on nudge drafts (W4.2) ────────────────────────────────────────────

def test_nudge_prompt_pins_context_as_data_and_offers_no_draft() -> None:
    msgs = build_nudge_messages(
        contact_name="Ravi Kumar", commitment_text="will share the AWB tomorrow",
        due_hint="tomorrow", language="en",
        recent_excerpt="Them: will share the AWB tomorrow")
    system = msgs[0]["content"]
    assert "DATA authored by" in system
    assert "NO_DRAFT" in system
    assert "gentle" in system.lower()
    user = msgs[1]["content"]
    assert "Ravi Kumar" in user
    assert "will share the AWB tomorrow" in user
    assert "tomorrow" in user               # due hint surfaced
    assert "RECENT CONTEXT" in user


def test_nudge_prompt_omits_due_and_context_when_absent() -> None:
    user = build_nudge_messages(
        contact_name="Ravi", commitment_text="will revert", due_hint=None,
        language="en", recent_excerpt=None)[1]["content"]
    assert "hinted a timeframe" not in user
    assert "RECENT CONTEXT" not in user


def test_nudge_prompt_language_switch() -> None:
    hi = build_nudge_messages(
        contact_name="Ravi", commitment_text="AWB bhej dunga", due_hint=None,
        language="hi", recent_excerpt=None)[0]["content"]
    assert "Hindi" in hi


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row if not isinstance(self._row, list) else None

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _Commitment:
    def __init__(self, direction="theirs"):
        self.chat_id = "chat-1"
        self.direction = direction
        self.text = "will share the AWB tomorrow"
        self.due_hint = "tomorrow"
        self.name = "Ravi Kumar"


class _MsgRow:
    def __init__(self, direction, body):
        self.direction = direction
        self.body_text = body


class _NudgeFakeDB:
    def __init__(self, commitment, msgs=None):
        self._commitment = commitment
        self._msgs = msgs or [_MsgRow("in", "will share the AWB tomorrow")]

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM wa_commitments k" in sql and "JOIN wa_chats c" in sql:
            return _Result(self._commitment)
        if "FROM wa_messages" in sql:
            return _Result(self._msgs)
        return _Result(None)


def _resp(content):
    choice = type("Ch", (), {"message": type("M", (), {"content": content})()})
    return type("R", (), {"choices": [choice]})()


async def test_draft_nudge_success_returns_tuple() -> None:
    db = _NudgeFakeDB(_Commitment())

    async def _ok(*a, **k):
        return _resp("Hi Ravi, whenever you get a moment could you share the "
                     "AWB? 🙏"), "tier-balanced"

    with patch("acb_llm.context.acompletion_with_fallback", _ok):
        out = await draft_nudge(db, "acc", "k-1")
    assert out is not None
    chat_id, text_val, language = out
    assert chat_id == "chat-1"
    assert "AWB" in text_val
    assert language == "en"


async def test_draft_nudge_none_for_ours_commitment() -> None:
    db = _NudgeFakeDB(_Commitment(direction="ours"))
    out = await draft_nudge(db, "acc", "k-1")
    assert out is None                       # we don't nudge ourselves


async def test_draft_nudge_none_for_unknown_commitment() -> None:
    db = _NudgeFakeDB(None)
    out = await draft_nudge(db, "acc", "k-1")
    assert out is None


async def test_draft_nudge_raises_on_llm_failure() -> None:
    """Task 1128: a failed call is no verdict. It raises, never fabricated."""
    db = _NudgeFakeDB(_Commitment())

    async def _boom(*a, **k):
        raise RuntimeError("down")

    with (patch("acb_llm.context.acompletion_with_fallback", _boom),
          pytest.raises(DraftUnavailable)):
        await draft_nudge(db, "acc", "k-1")


@pytest.mark.parametrize("content", [None, "", "  "])
async def test_draft_nudge_raises_on_an_empty_answer(content) -> None:
    """Task 1128: no text is not a NO_DRAFT verdict."""
    db = _NudgeFakeDB(_Commitment())
    seen = {}

    async def _empty(*a, **k):
        seen.update(k)
        return _resp(content), "x"

    with (patch("acb_llm.context.acompletion_with_fallback", _empty),
          pytest.raises(DraftUnavailable)):
        await draft_nudge(db, "acc", "k-1")
    assert seen["job"] == "writing" and seen["max_tokens"] == 300


class _Owned:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


async def _nudge_route(monkeypatch, outcome):
    from fastapi import HTTPException

    async def _owned(*_a, **_k):
        return "acc"

    async def _nudge(*_a, **_k):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(wa_commitments, "_tenant_session", _Owned)
    monkeypatch.setattr(wa_commitments, "_assert_theirs_commitment_owned", _owned)
    monkeypatch.setattr(wa_commitments, "draft_nudge", _nudge)
    with pytest.raises(HTTPException) as err:
        await wa_commitments.draft_commitment_nudge(
            "k-1", user=type("U", (), {"email": "o@x.test"})())
    return err.value


async def test_the_nudge_route_says_no_answer_when_the_model_gave_nothing(
        monkeypatch) -> None:
    err = await _nudge_route(monkeypatch, DraftUnavailable("empty"))
    assert err.status_code == 502 and err.detail == NO_MODEL_ANSWER


async def test_the_nudge_route_keeps_the_422_for_a_no_draft_verdict(monkeypatch) -> None:
    err = await _nudge_route(monkeypatch, None)
    assert err.status_code == 422 and "nothing to chase" in err.detail


async def test_draft_nudge_none_on_no_draft_verdict() -> None:
    db = _NudgeFakeDB(_Commitment())

    async def _abstain(*a, **k):
        return _resp("NO_DRAFT"), "x"

    with patch("acb_llm.context.acompletion_with_fallback", _abstain):
        out = await draft_nudge(db, "acc", "k-1")
    assert out is None


def test_nudge_route_registered() -> None:
    from gateway.routes.whatsapp import router
    paths = {r.path for r in router.routes}
    assert "/whatsapp/commitments/{commitment_id}/nudge" in paths
