"""Automation · Insights — the read route of the fact table.

WS-17 EM-T14c. Spec: ``project-docs/specs/email_app_master_plan.md`` §13.8 and
§13.9.3. Decisions D-EM-4, D-EM-23, D-EM-28 and D-EM-46.

``GET /email/insights`` lists the facts of ``email_insights`` for the mailboxes
of the caller, with one total for each currency and direction. The assistant
tool ``query_insights`` reads it, so a question about invoices reads the facts
and not the inbox. ``insights_store.write_facts`` is the one writer of the
table. This module only reads it.

**The owner scope (D-EM-4, D-EM-46).** Each read goes through
``_account_scope(account_id, params, pooled_only=True)``, with the table
aliased as ``em``. So a fact of another member of the same organization never
reads, and a mailbox that the member keeps separate stays out of All inboxes
(D-EM-28). The member comes from ``get_current_user`` and the organization
from the bound tenant, never from the request (R5, R11).

**The rules of the answer.**

* ``available`` is the flag (``insights_enabled()``). While it is false the
  route opens no session and answers no rows. It ships dark.
* ``enabled`` is the opt-in of the mailbox (D-EM-39). In All inboxes it is
  true when one mailbox in the scope is opted in. A missing settings row reads
  as false.
* In All inboxes, two rows with one ``dedupe_key`` in two mailboxes of the
  member fold into one row, and each total counts it once.
* The totals come from ONE SQL query, grouped by currency and direction. A
  row with a NULL currency or a NULL amount adds to no total, and nothing adds
  two currencies (§13.0 rule 3).
* Each amount is a decimal string, so no float rounds it on the way out.
* The window uses UTC dates. A time zone of the member is a later change.

Fence: ``tests/unit/test_email_insights_route.py`` (R8, as the role
``acb_app_h3rls``) and ``tests/unit/test_email_owner_scope_fence.py``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.email.automation.insights_store import (
    DOMAINS,
    FACT_FIELDS,
    insights_enabled,
)
from gateway.routes.email.core import _account_scope, _tenant_session, router
from sqlalchemy import text

__all__ = ["MAX_LIMIT", "STATES", "WINDOWS", "list_insights"]

#: The values of ``window`` (§13.8). ``open`` and ``all`` put no bound on the
#: due date. The tool sends ``state=all`` with ``window=all``.
WINDOWS: tuple[str, ...] = ("overdue", "next_7_days", "next_30_days", "open", "all")
#: The values of ``state``. ``all`` lists each mark of the member.
STATES: tuple[str, ...] = ("open", "done", "dismissed", "all")
#: The most rows of one page (§13.8).
MAX_LIMIT = 50
#: The cap of the ``counterpart`` filter, the cap of the column.
_COUNTERPART_CAP = 120

_COLUMNS = (
    "em.id, em.account_id, em.message_id, em.attachment_id, em.domain, "
    "em.fact_type, em.direction, em.title, em.counterpart, "
    "em.counterpart_email, em.ref, em.amount, em.currency, em.due_on, "
    "em.quote, em.confidence, em.state, em.created_at")


def _today() -> date:
    """Today as a UTC date (§13.8)."""
    return datetime.now(UTC).date()


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _window_sql(window: str, params: dict[str, Any]) -> str | None:
    """The predicate of ``window`` on ``em.due_on``, or None for no bound."""
    if window in ("open", "all"):
        return None
    today = _today()
    params["today"] = today
    if window == "overdue":
        return "em.due_on < :today"
    params["until"] = today + timedelta(days=7 if window == "next_7_days" else 30)
    return "em.due_on BETWEEN :today AND :until"


def _like(value: str) -> str:
    """``value`` as an ILIKE pattern that matches it as plain text."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "account_id": str(r.account_id),
        "message_id": str(r.message_id),
        "attachment_id": str(r.attachment_id) if r.attachment_id else None,
        "domain": r.domain,
        "fact_type": r.fact_type,
        "direction": r.direction,
        "title": r.title,
        "counterpart": r.counterpart,
        "counterpart_email": r.counterpart_email,
        "ref": r.ref,
        "amount": _money(r.amount),
        "currency": r.currency.strip() if r.currency else None,
        "due_on": r.due_on.isoformat() if r.due_on else None,
        "quote": r.quote,
        "confidence": round(float(r.confidence), 2),
        "state": r.state,
    }


def _dark(*, available: bool, enabled: bool) -> dict[str, Any]:
    return {"available": available, "enabled": enabled, "rows": [],
            "total_count": 0, "truncated": False, "totals": []}


@router.get("/insights")
async def list_insights(
    account_id: str | None = Query(None),
    domain: str | None = Query(None),
    fact_type: str | None = Query(None),
    window: str = Query("open"),
    counterpart: str | None = Query(None),
    state: str = Query("open"),
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The facts of the caller's mailboxes, and the totals from SQL (§13.8).

    ``account_id`` names one mailbox. With none, the read covers All inboxes:
    each mailbox of the member that is not kept separate. The answer is
    ``{available, enabled, rows, total_count, truncated, totals}``.
    """
    if domain is not None and domain not in DOMAINS:
        raise _bad(f"domain must be one of {', '.join(DOMAINS)}")
    if fact_type is not None and fact_type not in FACT_FIELDS:
        raise _bad("fact_type is not a known type")
    if window not in WINDOWS:
        raise _bad(f"window must be one of {', '.join(WINDOWS)}")
    if state not in STATES:
        raise _bad(f"state must be one of {', '.join(STATES)}")
    if account_id:
        try:
            account_id = str(uuid.UUID(account_id))
        except ValueError:
            raise _bad("account_id is not a valid id") from None
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    # Dark: the flag of the bound organization. No session, no rows.
    if not insights_enabled():
        return _dark(available=False, enabled=False)

    params: dict[str, Any] = {"uid": user.email or "anonymous"}
    # The owner scope of the member (D-EM-4). ``pooled_only`` keeps a
    # separate mailbox out of All inboxes (D-EM-28). It reads ``em``.
    scope = _account_scope(account_id or None, params, pooled_only=True)

    where = [scope]
    if domain is not None:
        where.append("em.domain = :domain")
        params["domain"] = domain
    if fact_type is not None:
        where.append("em.fact_type = :fact_type")
        params["fact_type"] = fact_type
    if state != "all":
        where.append("em.state = :state")
        params["state"] = state
    due = _window_sql(window, params)
    if due is not None:
        where.append(due)
    who = (counterpart or "").strip()[:_COUNTERPART_CAP]
    if who:
        where.append("(em.counterpart ILIKE :who ESCAPE '\\' "
                     "OR em.counterpart_email ILIKE :who ESCAPE '\\')")
        params["who"] = _like(who)

    # The fold of All inboxes: one row for each dedupe key, the oldest. With
    # one mailbox the unique index already holds one row for each key.
    folded = (
        f"SELECT DISTINCT ON (em.dedupe_key) {_COLUMNS} "
        f"FROM email_insights em WHERE {' AND '.join(where)} "
        "ORDER BY em.dedupe_key, em.created_at, em.id")

    async with _tenant_session() as db:
        enabled = (await db.execute(text(
            "SELECT COALESCE(bool_or(em.insights_enabled), false) "
            f"FROM email_assistant_settings em WHERE {scope}"), params)).scalar()
        total = (await db.execute(text(
            f"WITH folded AS ({folded}) SELECT count(*) FROM folded"),
            params)).scalar() or 0
        rows = (await db.execute(text(
            f"WITH folded AS ({folded}) SELECT * FROM folded f "
            "ORDER BY f.due_on ASC NULLS LAST, f.created_at DESC, f.id "
            "LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset})).fetchall()
        # The totals: ONE query, by currency and direction. A row with no
        # currency or no amount adds to no total, and no sum spans two
        # currencies (§13.0 rule 3).
        totals = (await db.execute(text(
            f"WITH folded AS ({folded}) "
            "SELECT f.currency, f.direction, sum(f.amount) AS amount, "
            "count(*) AS count FROM folded f "
            "WHERE f.currency IS NOT NULL AND f.amount IS NOT NULL "
            "GROUP BY f.currency, f.direction "
            "ORDER BY f.currency, f.direction NULLS LAST"), params)).fetchall()

    return {
        "available": True,
        "enabled": bool(enabled),
        "rows": [_row(r) for r in rows],
        "total_count": int(total),
        "truncated": offset + len(rows) < int(total),
        "totals": [
            {"currency": t.currency.strip() if t.currency else None,
             "direction": t.direction,
             "amount": _money(t.amount), "count": int(t.count)}
            for t in totals
        ],
    }
