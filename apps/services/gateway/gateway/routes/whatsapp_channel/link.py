"""Chat on WhatsApp — the member's link code (WS-47 WAC-1).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.2 ("The routes
(WAC-1)", "A new code replaces the old one"), §5.3 and §5.9.

    GET  /me/whatsapp-link       → the channel state and MY links in this org
    POST /me/whatsapp-link/code  → a new single-use code and its wa.me link

**A router of its own, never the ``/whatsapp`` router.** That router is gated
on ``feature:whatsapp`` for the inbox app (WS-20). This channel is a Chat
entry point, because the run of WAC-3 is a Chat run, so this router is gated
on ``feature:chat`` (``require_feature_router("chat")``).

**What the routes never take from the request.** The member is the session's
``UserContext.email``. The organization is ``current_tenant()``, which
``acb_auth.deps`` binds from the authenticated identity. Neither route reads a
body, a query or a path parameter, so there is nothing a caller can claim
(R5, R11).

**The refusals come before any write.** With ``WHATSAPP_ASSISTANT_ENABLED``
off, or the bound organization absent from ``WHATSAPP_ASSISTANT_ORGS``, both
routes answer 404 with a plain sentence. The channel is dark, so it does not
exist for this member. With no display number on the box, the GET answers
``enabled: false`` and the POST answers 503, because a code with no link to
send it to is a code nobody can use.

**The code.** 10 characters from Crockford's base-32 alphabet (no I, L, O or
U), which is 50 bits. The table stores only its SHA-256 in hex
(:func:`code_hash`). The plain code leaves this module once, in the POST
answer, and goes into no log line. It expires 15 minutes after the database
clock wrote it. WAC-2 redeems it. That slice must upper-case the text it
receives before it hashes it, and may map ``O`` to ``0`` and ``I``/``L`` to
``1`` as Crockford decoding does.

**One live code per member per org.** The POST takes an advisory lock on
(org, member), revokes the member's earlier ``pending`` row, and inserts the
new one, in one transaction. ``uq_whatsapp_member_links_one_pending`` is the
backstop, so two concurrent POSTs cannot leave two live codes.

**At most 10 codes per member per org in one hour** (WAC-2, §5.4). Under the
same lock, the POST counts the member's rows from ``created_at``. The 11th
answers 429 with a plain sentence, and writes nothing.

Fences: ``tests/unit/test_wac_link_code.py`` (database-free) and
``tests/unit/test_wac_link_table.py`` (R8, FORCE RLS as a non-privileged role).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Response
from gateway.db import current_tenant, tenant_session
from gateway.routes.whatsapp_channel import flags
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger(__name__)

router = APIRouter(
    prefix="/me/whatsapp-link",
    tags=["whatsapp-channel"],
    dependencies=[require_feature_router("chat")],
)

#: Crockford's base-32 alphabet: 32 symbols, no I, L, O or U.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10
CODE_TTL_MINUTES = 15
ISSUE_LIMIT_PER_HOUR = 10

CLOSED_DETAIL = "Chat on WhatsApp is not available for your organization."
NOT_SET_UP_DETAIL = "Chat on WhatsApp is not set up on this deployment yet."
NO_MEMBER_DETAIL = "Sign in as a member of an organization to link a phone."
TOO_MANY_DETAIL = (
    "You asked for too many link codes in the last hour. Wait, then try again."
)

_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('wac_link_code:' || :org || ':' || :email, 0))"
)

#: Every row is one issued code, whatever its status now.
_ISSUED_LAST_HOUR_SQL = """
SELECT count(*) FROM whatsapp_member_links
 WHERE organization_id = CAST(:org AS uuid)
   AND member_email = :email
   AND created_at > now() - interval '1 hour'
"""

_REVOKE_PENDING_SQL = """
UPDATE whatsapp_member_links
   SET status = 'revoked', revoked_at = now()
 WHERE organization_id = CAST(:org AS uuid)
   AND member_email = :email
   AND status = 'pending'
"""

_INSERT_PENDING_SQL = f"""
INSERT INTO whatsapp_member_links
       (organization_id, member_email, status, code_hash, code_expires_at)
VALUES (CAST(:org AS uuid), :email, 'pending', :hash,
        now() + interval '{CODE_TTL_MINUTES} minutes')
RETURNING id::text AS id, code_expires_at
"""

#: The member's own rows in the bound org. A revoked row and an expired code
#: are history, so the page does not show them. RLS binds the org, and the
#: explicit predicate serves the index.
_MY_LINKS_SQL = """
SELECT l.id::text              AS id,
       l.organization_id::text AS organization_id,
       o.display_name          AS organization_name,
       l.status, l.linked_at, l.is_current, l.code_expires_at, l.wa_id
  FROM whatsapp_member_links l
  JOIN organization o ON o.id = l.organization_id
 WHERE l.organization_id = CAST(:org AS uuid)
   AND l.member_email = :email
   AND (l.status = 'active'
        OR (l.status = 'pending' AND l.code_expires_at > now()))
 ORDER BY l.created_at DESC
"""


def new_code() -> str:
    """A fresh link code: 10 symbols from the alphabet, from ``secrets``."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def code_hash(code: str) -> str:
    """The SHA-256 of *code* in hex. The only form the table holds."""
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _member(user: UserContext) -> str:
    """The session's member address, in lower case. Never a request field."""
    email = (user.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=401, detail=NO_MEMBER_DETAIL)
    return email


def _open_org() -> str:
    """The bound organization, when the channel is open for it. Else 404."""
    org = current_tenant()
    if not flags.org_allowed(org):
        raise HTTPException(status_code=404, detail=CLOSED_DETAIL)
    return str(org)


class LinkView(BaseModel):
    """One of the caller's own links. The phone shows as its last 4 digits.

    ``id`` is the row id. The page keys its rows on it, because one member
    can hold two active links in one org once WAC-2 links phones.
    """

    id: str
    organization_id: str
    organization_name: str | None
    status: str
    linked_at: str | None
    is_current: bool
    expires_at: str | None
    phone_hint: str | None


class LinkState(BaseModel):
    """``enabled`` is false only when the box has no display number."""

    enabled: bool
    display_number: str | None
    code_ttl_minutes: int
    links: list[LinkView]


class IssuedCode(BaseModel):
    """The plain code, once. Nothing stores or logs it."""

    code: str
    expires_at: str
    link: str
    display_number: str
    code_ttl_minutes: int


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _link_view(row: Any) -> LinkView:
    wa_id = row["wa_id"]
    return LinkView(
        id=row["id"],
        organization_id=row["organization_id"],
        organization_name=row["organization_name"],
        status=row["status"],
        linked_at=_iso(row["linked_at"]),
        is_current=bool(row["is_current"]),
        expires_at=_iso(row["code_expires_at"])
        if row["status"] == "pending" else None,
        phone_hint=wa_id[-4:] if wa_id else None,
    )


def _audit(*, email: str, org: str, link_id: str, revoked: int) -> None:
    """Append the issue to the audit log, best-effort (gateway checklist 5).

    The payload holds ids and a count. It never holds the code or its hash.
    """
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{email}",
            action="whatsapp_link.code_issued",
            target=f"whatsapp_member_link:{link_id}",
            payload={"revoked_pending": revoked},
            organization_id=org,
        ))
    except Exception as exc:
        _log.warning("whatsapp_channel.audit_failed", error=str(exc)[:200])


@router.get("")
async def get_whatsapp_link(
    user: UserContext = Depends(get_current_user),
) -> LinkState:
    """The channel state and the caller's own links in the bound org."""
    email = _member(user)
    org = _open_org()
    number = flags.display_number()
    async with tenant_session() as db:
        rows = (
            await db.execute(text(_MY_LINKS_SQL), {"org": org, "email": email})
        ).mappings().all()
    return LinkState(
        enabled=number is not None,
        display_number=number,
        code_ttl_minutes=CODE_TTL_MINUTES,
        links=[_link_view(r) for r in rows],
    )


@router.post("/code", status_code=201)
async def issue_whatsapp_link_code(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> IssuedCode:
    """Issue a single-use link code, and revoke the member's earlier one."""
    email = _member(user)
    org = _open_org()
    number = flags.display_number()
    if number is None:
        raise HTTPException(status_code=503, detail=NOT_SET_UP_DETAIL)

    code = new_code()
    async with tenant_session() as db:
        await db.execute(text(_LOCK_SQL), {"org": org, "email": email})
        issued = (await db.execute(
            text(_ISSUED_LAST_HOUR_SQL), {"org": org, "email": email},
        )).scalar_one()
        if issued >= ISSUE_LIMIT_PER_HOUR:
            # The raise rolls the transaction back: nothing is written.
            _log.info("whatsapp_channel.code_limited", organization_id=org)
            raise HTTPException(status_code=429, detail=TOO_MANY_DETAIL)
        revoked = await db.execute(
            text(_REVOKE_PENDING_SQL), {"org": org, "email": email}
        )
        row = (
            await db.execute(
                text(_INSERT_PENDING_SQL),
                {"org": org, "email": email, "hash": code_hash(code)},
            )
        ).mappings().one()

    # Ids and counts only: never the code, and never its hash.
    count = revoked.rowcount or 0
    _log.info("whatsapp_channel.code_issued", organization_id=org,
              link_id=row["id"], revoked=count)
    _audit(email=email, org=org, link_id=row["id"], revoked=count)

    # The answer carries the plain code, so no cache may keep it.
    response.headers["Cache-Control"] = "no-store"
    return IssuedCode(
        code=code,
        expires_at=_iso(row["code_expires_at"]) or "",
        link=flags.wa_me_link(code, number),
        display_number=number,
        code_ttl_minutes=CODE_TTL_MINUTES,
    )
