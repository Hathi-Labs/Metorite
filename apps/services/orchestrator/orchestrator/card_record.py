"""The card record: each approved write card of a chat, with no model text.

WS-CO CO-17 (task 3516), ``project-docs/specs/chat_organization.md`` §16.3.
Migration: ``infra/postgres/<n>_chat_card_record.sql``. Dark behind
``CHAT_CARD_RECORD_ORGS`` (default empty).

The condense note of CO-16 may use a card that the member approved. Main kept
no durable record of one: ``ask_tools.last_card_outcome()`` lives in a context
variable, the outcome text sits in a tool result, and the title and detail of
the card are text that the model can shape. So this module writes ONE row when
a write card closes as ``approved`` (``ask_tools.CARD_APPROVED``):

    session, member, card id, outcome key, act kind, record link

**No model text.** The outcome key is a word of ``ask_tools.CARD_OUTCOMES``
(only ``approved`` is stored: a declined card writes no row). The act kind is
the module and function name of the tool that drew the card, and the tool
never says it. The link is an app path that the tool of the act sets once the
record exists (:func:`set_link`), and :func:`clean_link` accepts a path only.
No function of this module takes a title, a detail or a tool output.

**Tenancy (R5).** Every function opens ``acb_graph.tenant_session(org)``. The
org, the member and the chat are the run's own, read on the server
(``pending_ask._run_facts``). Nothing comes from a card event or a request
field. The INSERT selects the chat from ``chat_session`` under the bound
tenant, so a chat of another org gives no row (a foreign key check skips RLS).

**Who reads.** Only the member who approved the card. :func:`read_cards`
filters on the member and takes the visibility rule of the caller
(``gateway.rooms.SESSION_VISIBLE_SQL``), so a colleague in the same room reads
none. The member purge deletes the rows of a leaver
(``gateway/routes/admin/members.py``, key ``chat_card_rows``).

**Best effort.** The record never costs a card its answer. :func:`record_approved`
and :func:`set_link` return False on any fault, and log the type only.

**The sent-mail record (task 3594, migration 458).** A send tool sets the
provider id, the approved addresses and the time on the row of its card, once,
after the mail went (:func:`set_sent`). The run context of each later turn
states the last 5 sent acts of the chat (:func:`sent_acts`,
:func:`sent_acts_text`), so the chat does not send the same mail again.

Fences (R7): ``tests/unit/test_card_record.py``,
``tests/unit/test_card_record_r8.py`` (the SQL and RLS, as the NOBYPASSRLS app
role), ``tests/unit/test_sent_record.py`` and
``tests/unit/test_sent_record_r8.py``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from acb_common import get_settings

try:  # structlog is the house logger; fall back for a bare import
    import structlog

    _log = structlog.get_logger(__name__)
except Exception:  # pragma: no cover
    import logging

    _log = logging.getLogger(__name__)

TABLE = "chat_card_record"

#: The one outcome that a row stores.
OUTCOME = "approved"

_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_ACT_KIND = re.compile(r"^[a-z0-9_.]{1,120}$")
#: An app path: a slash, then no space, at most 300 characters (the CHECK).
_LINK = re.compile(r"^/\S*$")
_LINK_MAX = 300
#: The bound of the whole write of one card. A card never waits on it long.
_TIMEOUT_S = 3.0


def _parse_orgs(raw: str) -> frozenset[str]:
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def record_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_CARD_RECORD_ORGS`` holds the org, or holds ``*``.

    The one reader of the setting. Default OFF. It reads the settings on each
    call, so a SIGHUP reload applies a change with no restart. A fault reads
    as off. The org comes from the run, never from request input (R5).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        listed = _parse_orgs(str(getattr(get_settings(), "chat_card_record_orgs", "") or ""))
    except Exception:
        return False
    return "*" in listed or org in listed


def act_kind_of(fn: Any) -> str:
    """The act kind of the tool *fn*: ``module.function`` in lower case.

    It comes from the tool's own name, never from the card. A value that the
    CHECK would refuse reads as ``card``, so a row is never lost to a name."""
    mod = str(getattr(fn, "__module__", "") or "").split(".")[-1]
    name = str(getattr(fn, "__name__", "") or "")
    kind = re.sub(r"[^a-z0-9_.]", "_", f"{mod}.{name}".strip(".").lower())
    return kind if _ACT_KIND.match(kind) else "card"


def clean_link(link: Any) -> str | None:
    """*link* when it is an app path, else None. Free text never passes: a
    path starts with ``/`` and holds no white space."""
    if not isinstance(link, str):
        return None
    link = link.strip()
    if not link or len(link) > _LINK_MAX or not _LINK.match(link):
        return None
    return link


#: The insert. It selects the chat under the bound tenant, so a chat of
#: another org, or no chat, gives no row. A second insert of the same card
#: changes nothing.
_INSERT_SQL = f"""
INSERT INTO {TABLE} (session_id, member_email, request_id, outcome_key, act_kind)
SELECT s.id, :member, :rid, '{OUTCOME}', :act
  FROM chat_session s
 WHERE s.id = :sid
ON CONFLICT (session_id, request_id) DO NOTHING
"""

#: The link is set once, by the member's own row. A later set changes nothing.
_LINK_SQL = (
    f"UPDATE {TABLE} SET record_link = :link "
    "WHERE session_id = :sid AND request_id = :rid "
    "AND member_email = :member AND record_link IS NULL"
)

_COLUMNS = ("session_id, request_id, outcome_key, act_kind, record_link, "
            "message_id, created_at")

#: The id of a sent message: a token, never mail text (the CHECK).
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_.:@<>+=-]{1,200}$")

#: The message id is set once, by the member's own row, as the link is.
_MESSAGE_SQL = (
    f"UPDATE {TABLE} SET message_id = :mid "
    "WHERE session_id = :sid AND request_id = :rid "
    "AND member_email = :member AND message_id IS NULL"
)


def insert_row(organization_id: str, *, session_id: str, member: str,
               request_id: str, act_kind: str) -> bool:
    """Write the row of one approved card. True when a row was written.

    Sync: callers run it in a thread. False for a bad argument, for a repeat
    of the card, and for a chat that the bound tenant does not hold."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    member = member.strip().lower()
    if not (session_id and member and _REQUEST_ID.match(request_id)
            and _ACT_KIND.match(act_kind)):
        return False
    with tenant_session(organization_id) as s:
        res = s.execute(text(_INSERT_SQL), {
            "sid": session_id, "member": member, "rid": request_id, "act": act_kind})
        return bool(res.rowcount)


def update_link(organization_id: str, *, session_id: str, member: str,
                request_id: str, link: str) -> bool:
    """Set the record link of the member's own row, once. Sync."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    clean = clean_link(link)
    member = member.strip().lower()
    if clean is None or not (session_id and member and _REQUEST_ID.match(request_id)):
        return False
    with tenant_session(organization_id) as s:
        res = s.execute(text(_LINK_SQL), {
            "sid": session_id, "member": member, "rid": request_id, "link": clean})
        return bool(res.rowcount)


def update_message_id(organization_id: str, *, session_id: str, member: str,
                      request_id: str, message_id: str) -> bool:
    """Set the id of the message that a send card sent, once. Sync."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    member = member.strip().lower()
    mid = str(message_id or "").strip()
    if not (_MESSAGE_ID.match(mid) and session_id and member
            and _REQUEST_ID.match(request_id)):
        return False
    with tenant_session(organization_id) as s:
        res = s.execute(text(_MESSAGE_SQL), {
            "sid": session_id, "member": member, "rid": request_id, "mid": mid})
        return bool(res.rowcount)


# ── The sent-mail record (task 3594, migration 458) ─────────────────────────
#
# Run a8ad07f3 (2026-11-16): turn 1 sent a mail after an approved card. Turn 2
# did not see that tool result, called send_email again, and then told the
# member that neither mail went. So a send tool now sets the message id, the
# approved addresses and the time on the row of its card, once, AFTER the mail
# went (:func:`update_sent`). The run context of the next turn reads the last
# sent acts (:func:`read_sent_acts`) and states them (:func:`sent_acts_text`).

#: An address that the record keeps. Strict: lower case, no white space, no
#: quote, one ``@`` and a dotted host. An address that fails is left out.
_RECIPIENT = re.compile(
    r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+$")
_RECIPIENT_MAX_LEN = 254
#: The most addresses that one row keeps (the CHECK of migration 458).
RECIPIENTS_MAX = 50
#: The most sent acts that the run context names.
SENT_ACTS_MAX = 5
#: The most addresses that one line names. The rest is "and N more".
LINE_ADDRESSES_MAX = 5

#: The send is set once, by the member's own row, as the message id is.
_SENT_SQL = (
    f"UPDATE {TABLE} SET message_id = :mid, "
    "recipients = CAST(:rcp AS text[]), sent_at = now() "
    "WHERE session_id = :sid AND request_id = :rid "
    "AND member_email = :member AND message_id IS NULL"
)


def clean_recipients(addresses: Any) -> list[str]:
    """The addresses that the record may keep, in order, each once.

    Each address is cut of outer white space and set to lower case. An address
    that fails the strict form is left out, so no text reaches the record or
    the run context. At most :data:`RECIPIENTS_MAX`."""
    if isinstance(addresses, str) or not isinstance(addresses, (list, tuple)):
        return []
    out: list[str] = []
    for a in addresses:
        if not isinstance(a, str):
            continue
        a = a.strip().lower()
        if len(a) <= _RECIPIENT_MAX_LEN and _RECIPIENT.match(a) and a not in out:
            out.append(a)
        if len(out) >= RECIPIENTS_MAX:
            break
    return out


def update_sent(organization_id: str, *, session_id: str, member: str,
                request_id: str, message_id: str, recipients: Any) -> bool:
    """Set the message id, the approved addresses and the time of a send on
    the member's own row, once. Sync. False for a bad argument, for a row of
    another member, for a row that already holds a send, and for no row."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    member = member.strip().lower()
    mid = str(message_id or "").strip()
    people = clean_recipients(recipients)
    if not (people and _MESSAGE_ID.match(mid) and session_id and member
            and _REQUEST_ID.match(request_id)):
        return False
    with tenant_session(organization_id) as s:
        res = s.execute(text(_SENT_SQL), {
            "sid": session_id, "member": member, "rid": request_id, "mid": mid,
            "rcp": people})
        return bool(res.rowcount)


def read_sent_acts(s: Any, session_id: str, *, viewer: str, visible_sql: str,
                   limit: int = SENT_ACTS_MAX) -> list[dict[str, Any]]:
    """The last *limit* sent acts that *viewer* approved in the chat
    *session_id*, newest LAST.

    A sent act is a row with a message id. A declined, timed-out or closed
    card has no row, and an approved card whose send failed has no message
    id, so neither comes back. *s* and *visible_sql* are as in
    :func:`read_cards`: the bound tenant, the visibility rule, and the
    viewer's own rows only."""
    from sqlalchemy import text

    n = max(0, min(int(limit), SENT_ACTS_MAX))
    if not n:
        return []
    sql = (
        "SELECT r.request_id, r.act_kind, r.message_id, r.recipients, "
        "COALESCE(r.sent_at, r.created_at) AS sent_at "
        f"FROM {TABLE} r JOIN chat_session s ON s.id = r.session_id "
        "WHERE r.session_id = :sid AND r.member_email = :member "
        f"AND r.message_id IS NOT NULL AND {visible_sql} "
        "ORDER BY COALESCE(r.sent_at, r.created_at) DESC, r.request_id DESC "
        "LIMIT :n"
    )
    rows = s.execute(text(sql), {
        "sid": session_id, "member": viewer.strip().lower(), "uid": viewer,
        "n": n}).fetchall()
    return [dict(r._mapping) for r in reversed(rows)]


def _sent_acts_sync(organization_id: str, session_id: str, member: str) -> list[dict[str, Any]]:
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from gateway.rooms import SESSION_VISIBLE_SQL

    with tenant_session(organization_id) as s:
        return read_sent_acts(s, session_id, viewer=member,
                              visible_sql=SESSION_VISIBLE_SQL)


async def sent_acts(organization_id: str, session_id: str, member: str,
                    *, timeout_s: float = 1.0) -> list[dict[str, Any]]:
    """The last sent acts of *member* in the chat *session_id*, newest last.

    The org, the chat and the member come from the verified run binding,
    never from input (R5). The read is bound in time. It RAISES on a fault or
    a timeout, so the caller decides to give no line (fail open)."""
    member = str(member or "").strip().lower()
    if not (organization_id and session_id and member):
        return []
    return await asyncio.wait_for(
        asyncio.to_thread(_sent_acts_sync, organization_id, session_id, member),
        timeout_s)


#: The rule line after the sent acts. Written once in the block. Each
#: sentence is STE: under 20 words, no semicolon, no instruction in a fact.
SENT_ACTS_RULE = (
    "Do not send the same act again unless the member asks for it. "
    "Before you say that something was not sent, read the lines above.")


def _addresses_text(people: Any) -> str:
    """The addresses of one act for its line: the first
    :data:`LINE_ADDRESSES_MAX`, then "and N more". Only addresses that pass
    the strict form are named."""
    clean = clean_recipients(list(people or []))
    shown = ", ".join(clean[:LINE_ADDRESSES_MAX])
    more = len(clean) - LINE_ADDRESSES_MAX
    return f"{shown} and {more} more" if more > 0 else shown


def _time_text(at: Any) -> str:
    from datetime import UTC, datetime

    if not isinstance(at, datetime):
        return ""
    at = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    return at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def sent_act_line(act: dict[str, Any]) -> str:
    """One fact line of one sent act, or "" for a row that is not one.

    The act kind, the approved addresses, the time and the provider id. No
    subject, no body, no file name and no text from the mail: the row holds
    none. Each value is checked again, so a bad row gives no line."""
    mid = str(act.get("message_id") or "").strip()
    if not _MESSAGE_ID.match(mid):
        return ""
    people = _addresses_text(act.get("recipients"))
    when = _time_text(act.get("sent_at"))
    what = f"an email to {people}" if people else "an email"
    at = f", at {when}" if when else ""
    return (f"In this chat the member approved and the system sent: {what}{at}, "
            f"message {mid}.")


def sent_acts_text(acts: list[dict[str, Any]]) -> str:
    """The block of the run context: one fact line for each sent act, newest
    last, at most :data:`SENT_ACTS_MAX`, then :data:`SENT_ACTS_RULE` once.
    "" when no act gives a line."""
    lines = [ln for ln in (sent_act_line(a) for a in list(acts or [])[-SENT_ACTS_MAX:]) if ln]
    if not lines:
        return ""
    return "\n".join([*lines, SENT_ACTS_RULE])


def was_sent(rows: list[dict[str, Any]], request_id: str | None = None) -> bool:
    """The answer to "did it go?", from rows of :func:`read_cards` alone.

    True when a row (the one named by *request_id*, or any) holds a message
    id. A declined card has no row, a card with no message id is not a send
    that went, and no text of the mail is read."""
    return any(r.get("message_id") and (request_id is None or r.get("request_id") == request_id)
               for r in rows)


def read_cards(s: Any, session_id: str, *, viewer: str, visible_sql: str) -> list[dict[str, Any]]:
    """The cards that *viewer* approved in the chat *session_id*, oldest first.

    *s* is a session that the caller bound with ``tenant_session``. *visible_sql*
    is the visibility rule of the caller (``gateway.rooms.SESSION_VISIBLE_SQL``,
    which names ``s`` and ``:uid``), so a chat that the viewer cannot read gives
    no row. The member filter is the viewer's own: a colleague reads none."""
    from sqlalchemy import text

    sql = (
        f"SELECT {', '.join('r.' + c.strip() for c in _COLUMNS.split(','))} "
        f"FROM {TABLE} r JOIN chat_session s ON s.id = r.session_id "
        "WHERE r.session_id = :sid AND r.member_email = :member "
        f"AND {visible_sql} ORDER BY r.created_at, r.request_id"
    )
    rows = s.execute(text(sql), {
        "sid": session_id, "member": viewer.strip().lower(), "uid": viewer}).fetchall()
    return [dict(r._mapping) for r in rows]


async def _facts(thread_id: str) -> tuple[str | None, str]:
    from orchestrator.pending_ask import _run_facts

    org, _run, _agent, actor = await _run_facts(thread_id)
    return org, actor


async def record_approved(thread_id: str, request_id: str, act_kind: str,
                          responder: str = "") -> bool:
    """Write the row of a card that a member approved, in the chat
    *thread_id* (the run's own). The row names *responder*, the gateway's
    session member who answered, when given, and else the run's own member.
    In a room the two can differ, and the row names who approved.

    Dark: the flag is read first, from the run's tenant, before any Redis
    call. Never raises."""
    try:
        from acb_common.db import current_tenant

        bound = current_tenant()
        if bound is not None and not record_allowed(bound):
            # Off for the bound tenant: no Redis call, no wait on the card.
            return False

        async def _write() -> bool:
            org, actor = await _facts(thread_id)
            member = (responder or actor or "").strip().lower()
            if not org or not member or not record_allowed(org):
                return False
            return bool(await asyncio.to_thread(
                insert_row, org, session_id=thread_id, member=member,
                request_id=request_id, act_kind=act_kind))

        return await asyncio.wait_for(_write(), _TIMEOUT_S)
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        _log.warning("card_record.insert_failed", error=type(exc).__name__)
        return False


async def set_message_id(thread_id: str, request_id: str, message_id: Any) -> bool:
    """Set the id of the message that an approved send card sent. Never raises."""
    try:
        org, actor = await _facts(thread_id)
        if not org or not actor or not record_allowed(org):
            return False
        return bool(await asyncio.wait_for(asyncio.to_thread(
            update_message_id, org, session_id=thread_id, member=actor,
            request_id=request_id, message_id=str(message_id or "")), _TIMEOUT_S))
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        _log.warning("card_record.message_failed", error=type(exc).__name__)
        return False


async def set_sent(thread_id: str, request_id: str, message_id: Any,
                   recipients: Any) -> bool:
    """Task 3594: set the send of an approved send card, once: the provider
    id, the approved addresses and the time. A send tool calls it only AFTER
    the mail went. The member is the run's own. Never raises."""
    try:
        org, actor = await _facts(thread_id)
        if not org or not actor or not record_allowed(org):
            return False
        return bool(await asyncio.wait_for(asyncio.to_thread(
            update_sent, org, session_id=thread_id, member=actor,
            request_id=request_id, message_id=str(message_id or ""),
            recipients=list(recipients or [])), _TIMEOUT_S))
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        _log.warning("card_record.sent_failed", error=type(exc).__name__)
        return False


async def set_link(thread_id: str, request_id: str, link: Any) -> bool:
    """Set the link of the record that an approved card made. Never raises."""
    try:
        org, actor = await _facts(thread_id)
        if not org or not actor or not record_allowed(org):
            return False
        return bool(await asyncio.wait_for(asyncio.to_thread(
            update_link, org, session_id=thread_id, member=actor,
            request_id=request_id, link=str(link or "")), _TIMEOUT_S))
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        _log.warning("card_record.link_failed", error=type(exc).__name__)
        return False
