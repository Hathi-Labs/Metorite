"""The per-member row of a chat, and its pin (WS-CO CO-11).

Spec: ``project-docs/specs/chat_organization.md`` §10 and §18. Migration:
``infra/postgres/<n>_chat_session_member.sql``.

``chat_session_member`` holds one row for each chat and each member. Today the
row holds one value, ``pinned_at``. CO-2 (task 3501) adds the topic of the
chat to the same row, and its SQL to this module.

This is the ONE module of the gateway that runs SQL on the table. The member
purge (``routes/admin/members.py``, key ``chat_member_rows``) deletes the rows
of a leaver by its own clause, as it does for each table of a person. Fence:
``tests/unit/test_chat_pin.py``.

The rules:

* **The member is the caller.** The routes pass the address of the session,
  never a request field (R11). The row keys on the lower-case address
  (:func:`member_key`). The visibility rule binds the exact address, as each
  reader of ``SESSION_VISIBLE_SQL`` does.
* **The org is the bound tenant.** Each function takes a session that the
  caller opened with ``acb_graph.tenant_session(org)``. RLS binds the org, and
  the DEFAULT of ``organization_id`` stamps it. No function takes an org.
* **A pin reads the chat first.** The INSERT selects the chat from
  ``chat_session`` under the bound tenant and the visibility rule of
  ``gateway/rooms.py``. A foreign key check skips RLS, so no statement names a
  chat by its id alone. A chat of another org, a private chat of a colleague
  and a room that the member is not in give no row.
* **A pin is the member's own.** Each read and each write filters on the
  member, so a colleague in the same room reads no pin of the member.
* **An archived chat is not pinned.** No archive writes here. The read leaves
  out an archived chat (:func:`pin_fields`), so an archive takes the chat out
  of Pinned for each member of a room at once. A restore shows the pin again.
* **The switch.** ``CHAT_PIN_ORGS`` lists the orgs. :func:`pin_allowed` is its
  one reader. Off, the chat list sends today's statement and no pin key, the
  shell read says ``chat_pin: false``, and the pin routes answer 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from gateway.rooms import SESSION_VISIBLE_SQL

#: The table. Each statement of this module names it through this constant.
TABLE = "chat_session_member"


def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def pin_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_PIN_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the setting. It reads the settings on each call, so a
    reload (SIGHUP) applies a change with no restart. A fault reads as off.
    The org comes from the session, never from request input (R5).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_pin_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed


def member_key(email: str | None) -> str:
    """The key of the member in the table: the address, trimmed, in lower case."""
    return str(email or "").strip().lower()


@dataclass(frozen=True)
class PinResult:
    """The outcome of a pin.

    ``pinned``: the chat is pinned, and ``at`` is the time of the first pin.
    ``archived``: the chat is archived, and nothing changed. ``missing``: the
    member cannot read the chat, or it does not exist, and nothing changed.
    """

    state: Literal["pinned", "archived", "missing"]
    at: datetime | None = None


#: The pin. One statement: it selects the chat under the bound tenant and the
#: visibility rule, and an archived chat gives no row. A second pin keeps the
#: time of the first, so the act is idempotent.
_PIN_SQL = f"""
INSERT INTO {TABLE} (session_id, member_email, pinned_at)
SELECT s.id, :member, now()
  FROM chat_session s
 WHERE s.id = :id
   AND s.archived_at IS NULL
   AND {SESSION_VISIBLE_SQL}
ON CONFLICT (session_id, member_email)
DO UPDATE SET pinned_at = COALESCE({TABLE}.pinned_at, EXCLUDED.pinned_at)
RETURNING pinned_at
"""

#: Whether the member can read the chat, and whether it is archived.
_VISIBLE_SQL = (
    "SELECT s.archived_at FROM chat_session s "
    f"WHERE s.id = :id AND {SESSION_VISIBLE_SQL}"
)

#: The unpin. It clears the pin and keeps the row, so the topic of CO-2 can
#: live in the same row. It touches the member's own row only.
_UNPIN_SQL = (
    f"UPDATE {TABLE} SET pinned_at = NULL "
    "WHERE session_id = :id AND member_email = :member"
)

#: The pins of one member, for the chat list.
_PINS_SQL = (
    f"SELECT session_id, pinned_at FROM {TABLE} "
    "WHERE member_email = :member AND pinned_at IS NOT NULL"
)


def pin(s: Any, session_id: str, *, viewer: str) -> PinResult:
    """Pin *session_id* for *viewer*, on a bound session *s*.

    *viewer* is the address of the session. The visibility rule binds it as
    it is, and the row keys on :func:`member_key` of it.
    """
    from sqlalchemy import text

    params = {"id": session_id, "uid": viewer, "member": member_key(viewer)}
    at = s.execute(text(_PIN_SQL), params).scalar()
    if at is not None:
        return PinResult("pinned", at)
    seen = s.execute(text(_VISIBLE_SQL), params).first()
    if seen is None:
        return PinResult("missing")
    return PinResult("archived")


def unpin(s: Any, session_id: str, *, viewer: str) -> bool:
    """Clear the pin of *viewer* on *session_id*. False when the member cannot
    read the chat. A chat with no pin is no fault: the act is idempotent."""
    from sqlalchemy import text

    params = {"id": session_id, "uid": viewer, "member": member_key(viewer)}
    if s.execute(text(_VISIBLE_SQL), params).first() is None:
        return False
    s.execute(text(_UNPIN_SQL), params)
    return True


def pinned_at_of(s: Any, member: str) -> dict[str, datetime]:
    """The pins of *member*: the chat id, and the time of the pin."""
    from sqlalchemy import text

    rows = s.execute(text(_PINS_SQL), {"member": member_key(member)}).fetchall()
    return {r.session_id: r.pinned_at for r in rows}


def pin_fields(at: datetime | None, *, archived: bool) -> dict[str, Any]:
    """The pin keys of one row of the chat list.

    An archived chat reads as not pinned, so an archive takes the chat out of
    Pinned. ``pinnedAt`` orders the Pinned group: the newest pin first.
    """
    on = at is not None and not archived
    return {"pinned": on, "pinnedAt": at.isoformat() if on and at else None}
