"""Email triage classifier — WBS 1.4.

Two-stage decision:

    1. **Rule pass** (`classify_by_rules`) — explicit, no-LLM heuristics for
       the easy 60–70 % of inbox traffic (DKIM/List-Unsubscribe headers,
       known bot senders, subject patterns). Cheap, explainable, runs in µs.

    2. **LLM fallback** (`classify_with_llm`) — only invoked when the rule
       pass returns confidence < ``RULE_CONFIDENCE_THRESHOLD``. Uses Tier-1
       (Qwen3/Gemini Flash Lite) with a strict JSON schema and a single-shot
       budget; if parsing fails we degrade gracefully to ``needs_human``.

The public entry-point :func:`classify` accepts a fully-populated
:class:`EmailMessage` and returns an :class:`EmailTriageDecision`. It is
``async`` because the LLM branch needs ``acb_llm.complete`` (which uses
``litellm.acompletion``); the rule path itself is sync-friendly.

Offline-safe: when ``llm_caller`` is omitted, the LLM branch is skipped and
ambiguous messages return ``EmailTriageDecision(label='needs_human',
source='fallback', ...)``. This keeps the module unit-testable with no proxy.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

from acb_audit import AuditEvent, record

from .schema import EmailMessage, EmailTriageDecision, TriageLabel

# Tier-1 confidence floor — below this we ask the LLM.
RULE_CONFIDENCE_THRESHOLD: float = 0.75

# ---- Allowed-domain / fixture-friendly knobs ------------------------------

#: Domains we treat as "our company"; auto-classified as `internal_admin`
#: unless a strong customer-facing rule fires first.
INTERNAL_DOMAINS: set[str] = {"fracktal.in", "metorite.ai"}

#: Bot prefixes for noreply / automated mail.
_BOT_LOCAL_PARTS: tuple[str, ...] = (
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "notifications",
    "notification",
    "mailer-daemon",
    "postmaster",
    "alerts",
    "alert",
    "support@",
    "automated",
    "bounces",
)

_NEWSLETTER_HEADER_KEYS = ("list-unsubscribe", "list-id", "feedback-id")

_SALES_LEAD_SUBJECT_RX = re.compile(
    r"\b(quote|quotation|enquiry|inquiry|interested in|pricing|RFQ|proposal request|product\s+info)\b",
    re.IGNORECASE,
)

_SALES_FOLLOWUP_BODY_RX = re.compile(
    r"\b(any update|status|circling back|gentle reminder|following up|haven'?t heard)\b",
    re.IGNORECASE,
)

_CUSTOMER_REQUEST_RX = re.compile(
    r"\b(issue|problem|not working|broken|stuck|defect|RMA|return|refund|complaint)\b",
    re.IGNORECASE,
)

_DELIVERY_UPDATE_RX = re.compile(
    r"\b(shipped|dispatch(ed)?|tracking|courier|delivered|in transit|out for delivery)\b",
    re.IGNORECASE,
)

_MEETING_RX = re.compile(
    r"\b(invitation|meeting|calendar|reschedule|standup|sync up|catch up|zoom link|google meet|teams meeting)\b",
    re.IGNORECASE,
)


# ---- Helpers --------------------------------------------------------------

def _domain(addr: str | None) -> str:
    if not addr or "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[1].lower()


def _is_internal(addr: str | None) -> bool:
    return _domain(addr) in INTERNAL_DOMAINS


def _is_bot_sender(addr: str | None) -> bool:
    if not addr:
        return False
    local = addr.split("@", 1)[0].lower()
    return any(local.startswith(p) or p in local for p in _BOT_LOCAL_PARTS)


def _has_newsletter_headers(msg: EmailMessage) -> bool:
    hk = {k.lower() for k in msg.headers}
    return any(k in hk for k in _NEWSLETTER_HEADER_KEYS)


# ---- Rule pass ------------------------------------------------------------

def classify_by_rules(msg: EmailMessage) -> EmailTriageDecision:
    """Pure-Python heuristic classifier. Returns the *highest-confidence*
    rule that fires, or a low-confidence ``other`` if nothing matches."""
    text = msg.text
    from_addr = str(msg.from_addr) if msg.from_addr else ""

    # 1) Auto-mail / newsletter detection — strong signal.
    if _has_newsletter_headers(msg):
        return _d(
            "newsletter",
            0.95,
            "rule",
            "List-Unsubscribe / List-Id header present",
            "drop",
        )
    if _is_bot_sender(from_addr):
        # Notification mails are not always junk (e.g. CI or vendor updates),
        # so we route them to ingest-only rather than drop.
        return _d(
            "automated",
            0.9,
            "rule",
            f"sender '{from_addr}' looks automated",
            "ingest_only",
        )

    # 2) Internal admin chatter (HR, all-hands, ops).
    if _is_internal(from_addr) and all(_is_internal(a) for a in msg.to_addrs):
        return _d(
            "internal_admin",
            0.8,
            "rule",
            "all-internal sender+recipients",
            "ingest_only",
        )

    # 3) Strong customer-facing patterns (subject first, body second).
    if _SALES_LEAD_SUBJECT_RX.search(msg.subject):
        return _d(
            "sales_lead",
            0.85,
            "rule",
            f"subject matches sales-lead pattern",
            "sales_agent",
        )
    if _CUSTOMER_REQUEST_RX.search(text):
        return _d(
            "customer_request",
            0.8,
            "rule",
            "body matches customer-request pattern",
            "delivery_agent",
        )
    if _DELIVERY_UPDATE_RX.search(text):
        return _d(
            "delivery_update",
            0.8,
            "rule",
            "body matches delivery-update pattern",
            "delivery_agent",
        )
    if _MEETING_RX.search(text):
        return _d(
            "meeting_logistics",
            0.75,
            "rule",
            "body matches meeting-logistics pattern",
            "ops_inbox",
        )
    if _SALES_FOLLOWUP_BODY_RX.search(text):
        return _d(
            "sales_followup",
            0.75,
            "rule",
            "body matches sales-followup pattern",
            "sales_agent",
        )

    # 4) Default — let the LLM decide.
    return _d(
        "other",
        0.3,
        "rule",
        "no strong heuristic matched",
        "needs_human_review",
    )


def _d(
    label: TriageLabel,
    confidence: float,
    source: str,
    rationale: str,
    route: str,
) -> EmailTriageDecision:
    return EmailTriageDecision(
        label=label,
        confidence=confidence,
        source=source,  # type: ignore[arg-type]
        rationale=rationale,
        suggested_route=route,  # type: ignore[arg-type]
    )


# ---- LLM fallback ---------------------------------------------------------

LLMCaller = Callable[[list[dict[str, str]]], Awaitable[str]]

_LLM_SYSTEM = (
    "You are the AI Company Brain email triage classifier. Read the email and "
    "return STRICT JSON with keys label, confidence, rationale, suggested_route. "
    "Allowed labels: spam, newsletter, automated, internal_admin, sales_lead, "
    "sales_followup, customer_request, delivery_update, meeting_logistics, "
    "needs_human, other. Allowed routes: drop, ingest_only, sales_agent, "
    "delivery_agent, ops_inbox, needs_human_review. Confidence in [0,1]. "
    "Be conservative — when in doubt, label=needs_human."
)


def _render_for_llm(msg: EmailMessage) -> str:
    body = (msg.body or msg.snippet)[:1500]
    return (
        f"From: {msg.from_name or ''} <{msg.from_addr}>\n"
        f"To: {', '.join(str(a) for a in msg.to_addrs)}\n"
        f"Subject: {msg.subject}\n"
        f"---\n{body}\n"
    )


async def classify_with_llm(
    msg: EmailMessage,
    *,
    llm_caller: LLMCaller,
    tier: int = 1,
) -> EmailTriageDecision:
    """Tier-1 LLM classifier; expects the caller to return raw assistant text.

    Injecting ``llm_caller`` (rather than importing ``acb_llm.complete``
    directly) keeps this module testable: production call passes a partial
    bound to ``LLMTier.TIER_1``; unit tests pass an in-memory fake.
    """
    messages = [
        {"role": "system", "content": _LLM_SYSTEM},
        {"role": "user", "content": _render_for_llm(msg)},
    ]
    try:
        raw = await llm_caller(messages)
        data = _extract_json(raw)
        decision = EmailTriageDecision(
            label=data["label"],
            confidence=float(data.get("confidence", 0.5)),
            source="llm",
            rationale=str(data.get("rationale", "")),
            suggested_route=data.get("suggested_route", "needs_human_review"),
            tier_used=tier,
        )
        return decision
    except Exception as exc:
        return _d(
            "needs_human",
            0.0,
            "fallback",
            f"LLM parse failed: {type(exc).__name__}",
            "needs_human_review",
        )


def _extract_json(raw: str) -> dict[str, object]:
    """Pull the first ``{ ... }`` block out of an LLM response. Defensive
    because tier-1 models sometimes prefix prose despite the system prompt."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM output")
    return json.loads(raw[start : end + 1])


# ---- Public entry-point ---------------------------------------------------

async def classify(
    msg: EmailMessage,
    *,
    llm_caller: LLMCaller | None = None,
    audit: bool = True,
) -> EmailTriageDecision:
    """Top-level triage: rule-first, then optional LLM tiebreaker.

    Always records an `audit_event` row (``action='email_triage'``) so the
    Annealer can later mine the (input, decision, source) triples.
    """
    decision = classify_by_rules(msg)
    if (
        decision.confidence < RULE_CONFIDENCE_THRESHOLD
        and llm_caller is not None
    ):
        decision = await classify_with_llm(msg, llm_caller=llm_caller)

    if audit:
        record(
            AuditEvent(
                actor="agent:triage",
                action="email_triage",
                target=f"email:{msg.message_id}",
                payload={
                    "from": str(msg.from_addr),
                    "subject": msg.subject[:200],
                    "label": decision.label,
                    "confidence": decision.confidence,
                    "source": decision.source,
                    "route": decision.suggested_route,
                },
            )
        )
    return decision


__all__ = [
    "RULE_CONFIDENCE_THRESHOLD",
    "classify",
    "classify_by_rules",
    "classify_with_llm",
]
