"""The per-member row of a chat: its pin (WS-CO CO-11) and its topic (CO-2b).

Spec: ``project-docs/specs/chat_organization.md`` §6, §10 and §18.
Migrations: ``infra/postgres/<n>_chat_session_member.sql`` and
``<n>_chat_session_member_topic.sql``.

``chat_session_member`` holds one row for each chat and each member. The row
holds the pin (``pinned_at``) and the topic of the chat for that member
(``topic_id`` and ``set_by``).

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

The topic of a chat (CO-2b, D163):

* **A move is the member's.** :func:`set_topic` writes ``topic_id`` with
  ``set_by = 'member'``. A move to "No topic" writes NULL and keeps
  ``set_by = 'member'``, so the pass never puts a topic back.
* **The pass never overrides the member.** :func:`set_auto_topic` writes
  ``set_by = 'auto'`` ONLY where no row exists, or where ``set_by`` is NULL or
  ``'auto'`` (:data:`_SET_AUTO_SQL`).
* **The topic first, then the row.** Each write calls
  ``chat_topic.topic_for_chat`` first. It holds the topic with ``FOR KEY
  SHARE`` and applies the member rule of D163.2: a company topic that shows,
  or the member's own topic. A merge locks its topics first too, so the two
  cannot deadlock. The write then selects the chat under the visibility rule,
  as the pin does.
* **A merge keeps who set it.** :func:`repoint_topic` moves the rows of one
  topic to another, and keeps ``set_by``. Only the merge of ``chat_topic.py``
  calls it, after its locks and before its delete.
* **The read sends a topic that shows.** :func:`topics_of_chats` gives the
  caller's own topic of each chat, and only a topic of
  ``chat_topic.list_topics``: a hidden company topic reads as no topic.
* **An archived chat takes no topic, as it takes no pin.** The move and the
  pass select no archived chat, and the route answers 409. The read sends no
  topic for an archived chat. The archive writes no row, so a restore shows
  the topic again.
* **The switch.** ``CHAT_TOPICS_ORGS``, read by ``chat_topic.topics_allowed``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from gateway.rooms import SESSION_VISIBLE_SQL

if TYPE_CHECKING:
    from gateway.chat_topic import Topic

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


# ── The topic of a chat (WS-CO CO-2b, D163) ─────────────────────────────────


@dataclass(frozen=True)
class TopicMove:
    """The outcome of a move of the member, or of the automatic pass.

    ``set``: the row holds the topic. ``topic`` is None for "No topic".
    ``kept``: the pass found a move of the member, and changed nothing.
    ``no_topic``: the member may not use the topic, or a merge took it away.
    ``missing``: the member cannot read the chat, or it does not exist.
    ``archived``: the chat is archived. A topic waits for a restore, as a pin
    does (CO-11). Nothing changed for each state but ``set``.
    """

    state: Literal["set", "kept", "no_topic", "missing", "archived"]
    topic: Topic | None = None


#: The move of a member. One statement: it selects the chat under the bound
#: tenant and the visibility rule, as the pin does, and never an archived chat
#: (the pin rule of CO-11). A chat that the member cannot read gives no row.
#: The member's choice wins over each earlier value, "No topic" too.
_SET_TOPIC_SQL = f"""
INSERT INTO {TABLE} (session_id, member_email, topic_id, set_by)
SELECT s.id, :member, CAST(:topic AS uuid), 'member'
  FROM chat_session s
 WHERE s.id = :id
   AND s.archived_at IS NULL
   AND {SESSION_VISIBLE_SQL}
ON CONFLICT (session_id, member_email)
DO UPDATE SET topic_id = EXCLUDED.topic_id, set_by = 'member'
RETURNING set_by
"""

#: The topic of the automatic pass (CO-1). It fills a row that is missing, a
#: pin row with no topic (``set_by`` NULL) and a row of the pass. It never
#: writes over a move of the member (D163): the WHERE of the DO UPDATE leaves
#: that row as it is, and the statement returns no row. It files no archived
#: chat, as the move does.
_SET_AUTO_SQL = f"""
INSERT INTO {TABLE} (session_id, member_email, topic_id, set_by)
SELECT s.id, :member, CAST(:topic AS uuid), 'auto'
  FROM chat_session s
 WHERE s.id = :id
   AND s.archived_at IS NULL
   AND {SESSION_VISIBLE_SQL}
ON CONFLICT (session_id, member_email)
DO UPDATE SET topic_id = EXCLUDED.topic_id, set_by = 'auto'
 WHERE {TABLE}.set_by IS NULL OR {TABLE}.set_by = 'auto'
RETURNING set_by
"""

#: The merge of two company topics moves each row of the source to the
#: target. It keeps ``set_by``. The bound org stands beside RLS, as the
#: member purge has it.
_REPOINT_SQL = (
    f"UPDATE {TABLE} SET topic_id = CAST(:into AS uuid) "
    "WHERE topic_id = CAST(:src AS uuid) "
    "AND organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
)

#: The topics of one member, on the chats of the list.
_TOPICS_SQL = (
    f"SELECT session_id, topic_id::text AS topic_id FROM {TABLE} "
    "WHERE member_email = :member AND topic_id IS NOT NULL "
    "AND session_id = ANY(CAST(:ids AS text[]))"
)


def _usable(s: Any, member: str, topic_id: str | None) -> Topic | None:
    """The topic, locked, when *member* may put a chat in it. Else None."""
    from gateway import chat_topic

    tid = chat_topic.topic_id(topic_id)
    if tid is None:
        return None
    return chat_topic.topic_for_chat(s, member, tid)


def set_topic(s: Any, session_id: str, *, viewer: str, topic_id: str | None) -> TopicMove:
    """The member moves *session_id* into the topic *topic_id*, on a bound
    session *s*. None moves it to "No topic", and the pass then leaves it.

    The topic comes first, and it is locked until the commit
    (``chat_topic.topic_for_chat``). A colleague's topic, a hidden company
    topic and an unknown id give ``no_topic``. Then the row: a chat that the
    member cannot read gives ``missing``, and an archived chat ``archived``.
    """
    from sqlalchemy import text

    topic: Topic | None = None
    if topic_id is not None:
        topic = _usable(s, viewer, topic_id)
        if topic is None:
            return TopicMove("no_topic")
    params = {"id": session_id, "uid": viewer, "member": member_key(viewer),
              "topic": topic.id if topic is not None else None}
    if s.execute(text(_SET_TOPIC_SQL), params).first() is None:
        return TopicMove(_why_not(s, params))
    return TopicMove("set", topic)


def _why_not(s: Any, params: dict[str, Any]) -> Literal["missing", "archived"]:
    """Why a write selected no chat: the member cannot read it, or it is
    archived. The same read as the pin (:data:`_VISIBLE_SQL`)."""
    from sqlalchemy import text

    seen = s.execute(text(_VISIBLE_SQL), params).first()
    return "missing" if seen is None else "archived"


def clear_topic(s: Any, session_id: str, *, viewer: str) -> TopicMove:
    """The member moves *session_id* to "No topic". The row keeps
    ``set_by = 'member'``, so the automatic pass does not put a topic back."""
    return set_topic(s, session_id, viewer=viewer, topic_id=None)


def set_auto_topic(s: Any, session_id: str, *, member: str, topic_id: str) -> TopicMove:
    """The automatic pass (CO-1) files *session_id* under *topic_id* for
    *member*, on a bound session *s*.

    ``kept`` when the member moved the chat, so a move survives each pass
    (D163). The pass names its topic through
    ``chat_topic.find_or_create_member_topic``. The same member rule holds
    here: a colleague's topic and a hidden company topic give ``no_topic``.

    ⚠️ *member* is the address as the chat stores it, as for :func:`pin`.
    The visibility rule binds it as it is, and only the row keys on
    :func:`member_key`. An address in another case than the participant row
    of the chat reads the chat as one the member cannot read: ``missing``.
    """
    from sqlalchemy import text

    topic = _usable(s, member, topic_id)
    if topic is None:
        return TopicMove("no_topic")
    params = {"id": session_id, "uid": member, "member": member_key(member), "topic": topic.id}
    if s.execute(text(_SET_AUTO_SQL), params).first() is not None:
        return TopicMove("set", topic)
    seen = s.execute(text(_VISIBLE_SQL), params).first()
    if seen is None:
        return TopicMove("missing")
    if seen.archived_at is not None:
        return TopicMove("archived")
    return TopicMove("kept")


def repoint_topic(s: Any, src: str, *, into: str) -> int:
    """Move each row of the topic *src* to the topic *into*, and keep its
    ``set_by``. The number of rows. Only ``chat_topic.merge_company_topics``
    calls it, after its locks and before its delete, in its transaction."""
    from sqlalchemy import text

    return int(s.execute(text(_REPOINT_SQL), {"src": src, "into": into}).rowcount or 0)


def topics_of(s: Any, session_ids: Iterable[str], member: str) -> dict[str, str]:
    """The topic id of each chat of *session_ids* for *member*. A chat with no
    topic is not in the answer."""
    from sqlalchemy import text

    ids = [str(i) for i in session_ids]
    if not ids:
        return {}
    rows = s.execute(text(_TOPICS_SQL), {"member": member_key(member), "ids": ids}).fetchall()
    return {r.session_id: r.topic_id for r in rows}


def topics_of_chats(s: Any, session_ids: Iterable[str], member: str) -> dict[str, Topic]:
    """The topic of each chat of *session_ids* for *member*, for the list.

    Only a topic of ``chat_topic.list_topics``: a company topic that shows, or
    the member's own topic. So a hidden company topic reads as no topic, and
    the chat sits under its date group.
    """
    from gateway import chat_topic

    ids = topics_of(s, session_ids, member)
    if not ids:
        return {}
    shown = {t.id: t for t in chat_topic.list_topics(s, member)}
    return {sid: shown[tid] for sid, tid in ids.items() if tid in shown}


def topic_fields(topic: Topic | None, *, archived: bool = False) -> dict[str, Any]:
    """The topic keys of one row of the chat list. No owner and no scope.

    An archived chat reads as no topic, as it reads as not pinned
    (:func:`pin_fields`). The archive writes no row, so a restore shows the
    topic again.
    """
    on = topic is not None and not archived
    return {"topicId": topic.id if on and topic else None,
            "topicName": topic.name if on and topic else None}
