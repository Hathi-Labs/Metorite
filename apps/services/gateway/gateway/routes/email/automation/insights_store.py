"""Automation · Insights — the fact store and its one write path.

WS-17 EM-T14a. Spec: ``project-docs/specs/email_app_master_plan.md`` §13.3,
§13.4, §13.5 items 6 and 9, and §13.9.1. Decisions D-EM-39, D-EM-41, D-EM-42
and D-EM-44.

This module holds three things, and nothing calls them yet. EM-T14b-2 adds the
job that calls :func:`write_facts`. It ships dark.

* :func:`insights_enabled` — the ONE reader of ``EMAIL_INSIGHTS`` and
  ``EMAIL_INSIGHTS_ORGS``. The organization comes from ``current_tenant()``,
  never from request input (R11).
* :data:`FACT_FIELDS` — the closed list of fact types of §13.4, with the fields
  of each type. The write path refuses any other type, so a new type needs no
  migration. A later slice imports this list. It never keeps a copy.
* :func:`write_facts` — the ONE writer of ``email_insights``. It takes
  CHECKED facts (:class:`Fact`). The checks of §13.5 item 5 run before it, in
  EM-T14b-1. It still trusts no field: it drops a field outside the type, cuts
  each text to its cap, and takes ``counterpart_email`` from the message row.

**The write, in one session (§13.5 item 9).** The caller opens
``_tenant_session()`` and passes it in. :func:`write_facts`:

1. checks that the message is in the mailbox, that the file is in the message,
   and that the opt-in of the mailbox still holds. A miss writes nothing.
2. deletes the facts of that message and source with an OLDER version of the
   same extractor. A fact whose key the new write carries again stays, so the
   upsert keeps its ``state``.
3. upserts each fact on ``(account_id, dedupe_key)``. On a conflict it keeps
   ``message_id``, ``attachment_id``, ``quote``, ``counterpart_email`` and
   ``state`` of the first row.
4. sets ``email_messages.insights_at``.

It writes ``organization_id`` from ``current_tenant()``. FORCE row level
security then refuses a row of another organization (WITH CHECK).

Fence: ``tests/unit/test_email_insights_store.py`` (R8, as the role
``acb_app_h3rls``).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from acb_common import get_settings
from gateway.db import TenantUnbound, current_tenant
from gateway.routes.email.automation.identity import _domain_of
from gateway.routes.email.core import _log
from sqlalchemy import text

__all__ = [
    "ALL_ORGS",
    "CAPS",
    "DOMAINS",
    "DOMAIN_OF",
    "FACT_FIELDS",
    "Fact",
    "WriteResult",
    "clean_text",
    "dedupe_key",
    "insights_enabled",
    "write_facts",
]

#: The value of ``email_insights_orgs`` that allows every organization.
ALL_ORGS = "*"

#: The four tabs of §13.3. The column CHECK holds the same set.
DOMAINS: tuple[str, ...] = ("finance", "projects", "sales", "company")

_FIN = frozenset({"direction", "counterpart", "ref", "amount", "currency", "due_on"})
_FIN_NO_DUE = _FIN - {"due_on"}
_PROJ = frozenset({"counterpart", "ref", "due_on"})
_SALES = frozenset({"counterpart", "ref", "amount", "currency", "due_on"})

#: §13.4: each type, its domain, and its optional fields. Every type also has
#: ``title`` and ``quote``. The write path drops each other field.
FACT_FIELDS: MappingProxyType[str, tuple[str, frozenset[str]]] = MappingProxyType({
    # finance (EM-T14b)
    "invoice": ("finance", _FIN),
    "payment_request": ("finance", _FIN),
    "purchase_order": ("finance", _FIN),
    "payment_confirmation": ("finance", _FIN_NO_DUE),
    "credit_note": ("finance", _FIN_NO_DUE),
    # projects (EM-T14e)
    "deadline": ("projects", _PROJ),
    "request": ("projects", _PROJ),
    "blocker": ("projects", _PROJ),
    "delivery": ("projects", _PROJ),
    # sales (EM-T14f)
    "lead": ("sales", _SALES),
    "quote": ("sales", _SALES),
    "order": ("sales", _SALES),
    "deal_signal": ("sales", _SALES),
    # company (EM-T14g)
    "hiring": ("company", _PROJ),
    "vendor": ("company", _PROJ),
    "legal": ("company", _PROJ),
})

#: The domain of each type. A type names one domain, so a fact carries no
#: domain of its own and the two cannot disagree.
DOMAIN_OF: MappingProxyType[str, str] = MappingProxyType(
    {t: d for t, (d, _f) in FACT_FIELDS.items()})

#: The cap of each text field, in characters (§13.3).
CAPS: MappingProxyType[str, int] = MappingProxyType({
    "title": 120, "counterpart": 120, "ref": 64, "quote": 200,
})

_DIRECTIONS = frozenset({"payable", "receivable"})
_CURRENCY = re.compile(r"^[A-Z]{3}$")
#: ``<extractor>-<n>``, for example ``fin-1``. The number orders the versions.
_VERSION = re.compile(r"^([a-z]+)-([0-9]{1,6})$")
#: An amount fits ``numeric(18,2)`` below this bound.
_AMOUNT_BOUND = Decimal("1e16")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Fact:
    """One CHECKED fact. EM-T14b-1 builds it from the answer of a model,
    after the checks of §13.5 item 5. Code parsed ``amount``, ``currency``
    and ``due_on`` from ``quote``, and code set ``confidence`` (D-EM-41)."""

    fact_type: str
    title: str
    quote: str
    confidence: float
    direction: str | None = None
    counterpart: str | None = None
    ref: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    due_on: date | None = None


@dataclass(frozen=True)
class WriteResult:
    """What one :func:`write_facts` call did. ``refused`` names the check that
    stopped the write, and is None when the write ran."""

    written: int = 0
    deleted: int = 0
    dropped: int = 0
    refused: str | None = None


def _parse_orgs(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def insights_enabled() -> bool:
    """True when the flag is on and the bound organization is on the list.

    No tenant bound is false, also with ``*``: every Insights job binds the
    tenant of the mailbox, so a call without one is not a mailbox job. An
    empty list allows no organization.
    """
    settings = get_settings()
    if not getattr(settings, "email_insights", False):
        return False
    org = current_tenant()
    if not org:
        return False
    orgs = _parse_orgs(getattr(settings, "email_insights_orgs", "") or "")
    return ALL_ORGS in orgs or str(org) in orgs


def clean_text(value: Any, cap: int) -> str | None:
    """``value`` with each control and format character removed, the space
    at each end removed, and cut to ``cap`` characters. None or empty is None.
    A cut keeps a prefix, so a quote that is in its source stays in it."""
    if not isinstance(value, str):
        return None
    kept = "".join(
        ch for ch in value
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf"))
    kept = kept.strip()[:cap].strip()
    return kept or None


def _clean_amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        return None
    try:
        amount = Decimal(value).quantize(_CENT)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or abs(amount) >= _AMOUNT_BOUND:
        return None
    return amount


def _clean_due(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _clean(fact: Fact) -> dict[str, Any] | None:
    """The row of one fact, or None when the write path refuses it."""
    spec = FACT_FIELDS.get(getattr(fact, "fact_type", None) or "")
    if spec is None:
        return None
    domain, fields = spec
    title = clean_text(fact.title, CAPS["title"])
    quote = clean_text(fact.quote, CAPS["quote"])
    conf = fact.confidence
    if (title is None or quote is None or isinstance(conf, bool)
            or not isinstance(conf, (int, float)) or not 0 <= conf <= 1):
        return None
    row: dict[str, Any] = {
        "domain": domain, "fact_type": fact.fact_type, "title": title,
        "quote": quote, "confidence": float(conf),
        "direction": None, "counterpart": None, "ref": None,
        "amount": None, "currency": None, "due_on": None,
    }
    if "direction" in fields and fact.direction in _DIRECTIONS:
        row["direction"] = fact.direction
    if "counterpart" in fields:
        row["counterpart"] = clean_text(fact.counterpart, CAPS["counterpart"])
    if "ref" in fields:
        row["ref"] = clean_text(fact.ref, CAPS["ref"])
    if "amount" in fields:
        row["amount"] = _clean_amount(fact.amount)
    if ("currency" in fields and isinstance(fact.currency, str)
            and _CURRENCY.match(fact.currency)):
        row["currency"] = fact.currency
    if "due_on" in fields:
        row["due_on"] = _clean_due(fact.due_on)
    return row


def dedupe_key(row: dict[str, Any], *, message_id: str,
               counterpart_email: str | None) -> str:
    """§13.5 item 6, in lower case: ``type|ref|amount|currency|counterpart
    domain``. A fact with no ``ref`` and no amount uses ``type|message
    id|quote hash``, so a reply that quotes an invoice again gives no second
    card. The domain comes from the sender address, never from a model."""
    if row["ref"] is None and row["amount"] is None:
        folded = " ".join(row["quote"].split()).lower()
        digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()[:16]
        return f"{row['fact_type']}|{message_id}|{digest}".lower()
    amount = f"{row['amount']:.2f}" if row["amount"] is not None else ""
    return "|".join((
        row["fact_type"], row["ref"] or "", amount, row["currency"] or "",
        _domain_of(counterpart_email or ""),
    )).lower()


def _uuid_or_none(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


_MESSAGE_SQL = text(
    "SELECT lower(m.from_address->>'email') AS sender "
    "FROM email_messages m "
    "WHERE m.id = CAST(:mid AS uuid) AND m.account_id = CAST(:aid AS uuid)")

_FILE_SQL = text(
    "SELECT 1 FROM email_attachments "
    "WHERE id = CAST(:att AS uuid) AND message_id = CAST(:mid AS uuid)")

_OPT_IN_SQL = text(
    "SELECT insights_enabled FROM email_assistant_settings "
    "WHERE account_id = CAST(:aid AS uuid)")

# Same extractor, LOWER number, same message and source, and a key that this
# write does not carry again. The CASE keeps the cast away from a version that
# does not match: Postgres gives no order to the parts of an AND.
_DELETE_OLDER_SQL = text(
    "DELETE FROM email_insights "
    "WHERE account_id = CAST(:aid AS uuid) "
    "AND message_id = CAST(:mid AS uuid) "
    "AND attachment_id IS NOT DISTINCT FROM CAST(:att AS uuid) "
    "AND split_part(extractor_version, '-', 1) = :family "
    "AND (CASE WHEN extractor_version ~ '^[a-z]+-[0-9]{1,6}$' "
    "     THEN split_part(extractor_version, '-', 2)::int END) < :num "
    "AND NOT (dedupe_key = ANY(CAST(:keys AS text[])))")

# On a conflict the first row keeps its source (message_id, attachment_id),
# its quote, its counterpart_email and the member's state.
_UPSERT_SQL = text(
    "INSERT INTO email_insights (organization_id, account_id, message_id, "
    "attachment_id, domain, fact_type, direction, title, counterpart, "
    "counterpart_email, ref, amount, currency, due_on, quote, confidence, "
    "extractor_version, dedupe_key) "
    "VALUES (CAST(:org AS uuid), CAST(:aid AS uuid), CAST(:mid AS uuid), "
    "CAST(:att AS uuid), :domain, :fact_type, :direction, :title, "
    ":counterpart, :counterpart_email, :ref, :amount, :currency, :due_on, "
    ":quote, :confidence, :version, :dedupe_key) "
    "ON CONFLICT (account_id, dedupe_key) DO UPDATE SET "
    "domain = EXCLUDED.domain, fact_type = EXCLUDED.fact_type, "
    "direction = EXCLUDED.direction, title = EXCLUDED.title, "
    "counterpart = EXCLUDED.counterpart, ref = EXCLUDED.ref, "
    "amount = EXCLUDED.amount, currency = EXCLUDED.currency, "
    "due_on = EXCLUDED.due_on, confidence = EXCLUDED.confidence, "
    "extractor_version = EXCLUDED.extractor_version, updated_at = now()")

_MARK_READ_SQL = text(
    "UPDATE email_messages SET insights_at = now() "
    "WHERE id = CAST(:mid AS uuid) AND account_id = CAST(:aid AS uuid)")


def _refuse(reason: str, **ids: Any) -> WriteResult:
    _log.warning("email.insights.write_refused", reason=reason, **ids)
    return WriteResult(refused=reason)


async def write_facts(
    db: Any,
    account_id: str,
    message_id: str,
    attachment_id: str | None,
    version: str,
    facts: list[Fact],
) -> WriteResult:
    """Write the checked ``facts`` of one source of one message.

    ``attachment_id`` None means the body. ``version`` is the extractor
    version, for example ``fin-1``. ``db`` is a session of
    ``_tenant_session()``. The caller commits it.

    A message that is not in the mailbox, a file that is not in the message,
    or a mailbox whose opt-in is off writes nothing, and ``refused`` names
    the check. A fact that the write path refuses counts in ``dropped``.

    Raises:
        TenantUnbound: no tenant is bound to this context.
        ValueError: ``version`` is not ``<extractor>-<n>``.
    """
    org = current_tenant()
    if not org:
        raise TenantUnbound("write_facts needs the tenant of the mailbox")
    match = _VERSION.match(version or "")
    if not match:
        raise ValueError(f"extractor version {version!r} is not <name>-<n>")
    family, num = match.group(1), int(match.group(2))

    aid, mid = _uuid_or_none(account_id), _uuid_or_none(message_id)
    att = _uuid_or_none(attachment_id) if attachment_id is not None else None
    if aid is None or mid is None or (attachment_id is not None and att is None):
        return _refuse("bad_id")
    ids = {"account_id": aid, "message_id": mid, "attachment_id": att}

    # 1. The checks. RLS binds the organization; these bind the mailbox.
    msg = (await db.execute(_MESSAGE_SQL, {"mid": mid, "aid": aid})).fetchone()
    if msg is None:
        return _refuse("message_not_in_mailbox", **ids)
    if att is not None and (await db.execute(
            _FILE_SQL, {"att": att, "mid": mid})).fetchone() is None:
        return _refuse("file_not_in_message", **ids)
    opt_in = (await db.execute(_OPT_IN_SQL, {"aid": aid})).fetchone()
    if opt_in is None or not opt_in.insights_enabled:
        return _refuse("not_opted_in", **ids)

    counterpart_email = (msg.sender or "").strip() or None
    rows: list[dict[str, Any]] = []
    dropped = 0
    for fact in facts:
        row = _clean(fact)
        if row is None:
            dropped += 1
            continue
        row["dedupe_key"] = dedupe_key(
            row, message_id=mid, counterpart_email=counterpart_email)
        rows.append(row)

    # 2. The older facts of this message and source.
    deleted = (await db.execute(_DELETE_OLDER_SQL, {
        "aid": aid, "mid": mid, "att": att, "family": family, "num": num,
        "keys": [r["dedupe_key"] for r in rows],
    })).rowcount or 0

    # 3. One statement for each fact, so two facts with one key in one call
    #    meet the conflict arm and the first one stays.
    for row in rows:
        await db.execute(_UPSERT_SQL, {
            **row, "org": str(org), "aid": aid, "mid": mid, "att": att,
            "counterpart_email": counterpart_email, "version": version,
        })

    # 4. The job read this message.
    await db.execute(_MARK_READ_SQL, {"mid": mid, "aid": aid})

    _log.info("email.insights.write", written=len(rows), deleted=deleted,
              dropped=dropped, extractor_version=version, **ids)
    return WriteResult(written=len(rows), deleted=deleted, dropped=dropped)
