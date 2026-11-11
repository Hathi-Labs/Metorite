"""Unit tests for orchestrator.triage.email (WBS 1.4).

DB-side ``record(AuditEvent)`` calls degrade gracefully when Postgres is not
available (see acb_audit.log.record), so we run these tests with ``audit=False``
to keep them silent and side-effect-free. With ``audit=False`` no tenant is
needed, so they pass ``organization_id=None`` (task 2173).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Awaitable, Callable

import pytest

from orchestrator.triage import (
    EmailMessage,
    EmailTriageDecision,
    classify,
    classify_by_rules,
)


def _msg(**overrides: object) -> EmailMessage:
    base: dict[str, object] = {
        "message_id": "m-1",
        "from_addr": "alice@example.com",
        "to_addrs": ["bob@fracktal.in"],
        "subject": "",
        "snippet": "",
        "body": "",
        "received_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
        "headers": {},
    }
    base.update(overrides)
    return EmailMessage(**base)  # type: ignore[arg-type]


# ---- Rule pass ------------------------------------------------------------

def test_newsletter_header_drops() -> None:
    d = classify_by_rules(_msg(headers={"List-Unsubscribe": "<...>"}))
    assert d.label == "newsletter"
    assert d.suggested_route == "drop"
    assert d.confidence >= 0.9


def test_bot_sender_is_automated() -> None:
    d = classify_by_rules(_msg(from_addr="noreply@clickup.com"))
    assert d.label == "automated"
    assert d.suggested_route == "ingest_only"


def test_internal_only_is_admin() -> None:
    d = classify_by_rules(
        _msg(from_addr="hr@fracktal.in", to_addrs=["ops@fracktal.in"])
    )
    assert d.label == "internal_admin"


def test_sales_lead_subject() -> None:
    d = classify_by_rules(_msg(subject="Quotation request for 100 units"))
    assert d.label == "sales_lead"
    assert d.suggested_route == "sales_agent"


def test_customer_request() -> None:
    d = classify_by_rules(
        _msg(
            from_addr="ravi@acmebot.com",
            subject="Help",
            body="our printer is not working since yesterday",
        )
    )
    assert d.label == "customer_request"
    assert d.suggested_route == "delivery_agent"


def test_delivery_update() -> None:
    d = classify_by_rules(
        _msg(
            from_addr="info@dhl.com",
            subject="Update",
            body="your package has been dispatched and is in transit",
        )
    )
    assert d.label == "delivery_update"


def test_meeting_logistics() -> None:
    d = classify_by_rules(
        _msg(
            subject="Reschedule meeting",
            body="can we reschedule our sync to Friday? google meet link below",
        )
    )
    assert d.label == "meeting_logistics"


def test_followup_pattern() -> None:
    d = classify_by_rules(
        _msg(
            from_addr="buyer@externalcorp.com",
            subject="hi",
            body="just circling back on the proposal",
        )
    )
    assert d.label == "sales_followup"


def test_default_other_low_confidence() -> None:
    d = classify_by_rules(_msg(from_addr="x@y.com", subject="hi", body="hello"))
    assert d.label == "other"
    assert d.confidence < 0.5


# ---- Async wrapper / LLM fallback -----------------------------------------

@pytest.mark.asyncio
async def test_classify_skips_llm_for_high_confidence_rules() -> None:
    calls: list[int] = []

    async def fake_llm(_messages: list[dict[str, str]]) -> str:
        calls.append(1)
        return "{}"

    d = await classify(
        _msg(headers={"List-Unsubscribe": "<...>"}),
        llm_caller=fake_llm,
        organization_id=None,
        audit=False,
    )
    assert d.label == "newsletter"
    assert calls == []
    assert d.source == "rule"


@pytest.mark.asyncio
async def test_classify_uses_llm_when_rules_uncertain() -> None:
    async def fake_llm(_messages: list[dict[str, str]]) -> str:
        return json.dumps(
            {
                "label": "needs_human",
                "confidence": 0.65,
                "rationale": "ambiguous",
                "suggested_route": "needs_human_review",
            }
        )

    d = await classify(
        _msg(from_addr="x@y.com", subject="hi", body="hello"),
        llm_caller=fake_llm,
        organization_id=None,
        audit=False,
    )
    assert d.label == "needs_human"
    assert d.source == "llm"
    assert d.tier_used == 1


@pytest.mark.asyncio
async def test_classify_llm_garbage_falls_back_safely() -> None:
    async def bad_llm(_messages: list[dict[str, str]]) -> str:
        return "I don't know"

    d = await classify(
        _msg(from_addr="x@y.com", subject="hi", body="hello"),
        llm_caller=bad_llm,
        organization_id=None,
        audit=False,
    )
    assert d.label == "needs_human"
    assert d.source == "fallback"


@pytest.mark.asyncio
async def test_classify_without_llm_caller_returns_rule_result() -> None:
    d = await classify(_msg(from_addr="x@y.com", subject="hi"), organization_id=None, audit=False)
    assert d.source == "rule"
    assert isinstance(d, EmailTriageDecision)
