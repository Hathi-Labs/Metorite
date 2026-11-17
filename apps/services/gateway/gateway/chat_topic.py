"""The topics of the chat list (WS-CO CO-2a, task 3501).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163.1,
D163.2). Migration: ``infra/postgres/<n>_chat_topic.sql``.

A topic is ``company`` (one short list for the org) or ``member`` (the
private topic of one member). The chat rail of CO-2b groups a member's chats
by topic. The topic of a chat lives on the per-member row of the chat, and
``gateway/chat_session_member.py`` is the one module that writes it.

This is the ONE module of the gateway that runs SQL on the table. The member
purge (``routes/admin/members.py``, key ``chat_topics``) deletes the member
topics of a leaver by its own clause, as it does for each table of a person.
Fence: ``tests/unit/test_chat_topic.py``.

The rules:

* **The member is the caller.** The routes pass the address of the session,
  never a request field (R11). A member topic keys on the lower-case address
  (:func:`member_key`).
* **The org is the bound tenant.** Each function takes a session that the
  caller opened with ``acb_graph.tenant_session(org)``. RLS binds the org, and
  the DEFAULT of ``organization_id`` stamps it. No function takes an org.
* **A member reads company topics and their own topics, and nothing else**
  (D163.2). :data:`_VISIBLE` is that rule, and each statement holds it in its
  WHERE. RLS covers the org only, so this module is the member half. An admin
  reads no member topic of a colleague here either: the answer for a
  colleague's topic is the answer for an id that does not exist.
* **Only an admin changes a company topic** (§6). An admin holds
  :data:`TOPIC_ADMIN`. The write statements hold the rule in their WHERE too,
  so a read and a write cannot disagree.
* **One name for each list.** The unique index keys on the org, the scope, the
  owner and the name in lower case. So a member topic never clashes with the
  topic of a colleague, and a refusal never shows a colleague's name.
* **A merge deletes the source.** It first locks both rows with
  ``SELECT ... FOR UPDATE`` in id order (:data:`_LOCK_SQL`), and checks them
  again under the lock. So two merges of X into Y and Y into X cannot both
  win, and a merge into a target that an admin hides at that moment is
  refused. CO-2b: after the checks and before the delete, the merge moves
  each per-member row of the source to the target, in the same transaction
  (``chat_session_member.repoint_topic``). The foreign key of ``topic_id``
  uses ``ON DELETE SET NULL (topic_id)`` (Postgres 15 and later). A plain
  ``SET NULL`` on a composite key also clears ``organization_id``.
* **A move locks its topic first** (:func:`topic_for_chat`, ``FOR KEY
  SHARE``), and then writes the per-member row. A merge locks the topics
  first too, so a move and a merge cannot deadlock. A move into a topic
  that a merge takes away is refused, or lands on the target.
* **A hidden company topic is an admin's to see.** For a member who is not
  an admin, its id answers as an id that does not exist, so a member cannot
  tell a hidden topic from no topic.
* **The switch.** ``CHAT_TOPICS_ORGS`` lists the orgs. :func:`topics_allowed`
  is its one reader. Off, each topic route answers 404.
* **The automatic pass (CO-1).** It sets the topic of a chat through
  ``chat_session_member.set_auto_topic`` (CO-2b). That function writes
  ``topic_id`` with ``set_by = 'auto'`` ONLY where no row exists, or where
  ``set_by`` is NULL or already ``'auto'``. It never writes over a row with
  ``set_by = 'member'`` (D163).
  The pass names a topic through :func:`find_or_create_member_topic`: a
  company topic that shows, or the member's own topic, of that name. Else it
  makes a PRIVATE topic of that member (D163.2), never a company topic.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

#: The table. Each statement of this module names it through this constant.
TABLE = "chat_topic"

#: The longest name, in characters. The CHECK of the migration holds the same.
NAME_MAX = 40

#: The capability of an admin of company topics: the house rule for an
#: org-wide write (``routes/projects/core.ORG_VOCABULARY_WRITE``). A company
#: topic shows to each member, so ``admin:settings:manage`` changes it.
TOPIC_ADMIN = "admin:settings:manage"

#: The teams of ``workbench/control_plane/src/lib/centers.ts``, in its order:
#: the ``name`` of each Center, without the word "Center". The fence
#: ``test_chat_topic.py`` reads the TypeScript, so the two lists cannot drift.
TEAMS: tuple[str, ...] = ("Sales", "Marketing", "Finance", "Operations", "People", "Company")

#: The topic of a chat that fits no team (§6).
GENERAL = "General"

#: The categories that a name may not hold: control characters, surrogates,
#: private use, and the line and paragraph marks. A format character (``Cf``)
#: stays legal, because a joiner and the tag characters build emoji such as a
#: flag. An unassigned code point (``Cn``) stays legal, because a new emoji is
#: unassigned in an older Unicode table.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Zl", "Zp"})

#: The bidi controls, which can make a name read as another name: the
#: embeddings and overrides (U+202A to U+202E), the isolates (U+2066 to
#: U+2069), and the marks LRM, RLM and ALM (U+200E, U+200F, U+061C). They are
#: ``Cf``, so the store refuses them by code point.
_BIDI_CONTROLS = frozenset(chr(c) for c in (
    *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C))

#: The categories of a character that does not show by itself: controls,
#: format characters (a joiner, a tag character), surrogates, private use,
#: unassigned code points, and each kind of space and line mark.
_UNSEEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})

#: Characters of a category that shows, which draw nothing all the same: the
#: Hangul fillers, the blank Braille cell, the combining grapheme joiner, the
#: two Khmer inherent vowels, the variation selectors, and the Mongolian free
#: variation selectors with the vowel separator (U+180B to U+180F).
_UNSEEN_POINTS = frozenset(chr(c) for c in (
    0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x034F, 0x17B4, 0x17B5,
    *range(0x180B, 0x1810), *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)))


def _shows(ch: str) -> bool:
    """True when *ch* draws something by itself."""
    return ch not in _UNSEEN_POINTS and unicodedata.category(ch) not in _UNSEEN_CATEGORIES


class TopicNameError(ValueError):
    """A name that the store refuses. The message is for the member."""


def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def topics_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_TOPICS_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the setting. It reads the settings on each call, so a
    reload (SIGHUP) applies a change with no restart. A fault reads as off.
    The org comes from the session, never from request input (R5).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_topics_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed


def member_key(email: str | None) -> str:
    """The key of the member in the table: the address, trimmed, in lower case."""
    return str(email or "").strip().lower()


def clean_name(raw: object) -> str:
    """The name of a topic, trimmed. :class:`TopicNameError` for a bad one.

    A name holds 1 to :data:`NAME_MAX` characters after the trim. The trim
    removes each white space at either end, a no-break space too. A name holds
    no character of :data:`_REFUSED_CATEGORIES` and no bidi control. An emoji
    sequence, a flag of tag characters and a new emoji stay legal. The CHECK
    of the migration refuses ``[[:cntrl:]]`` only, so this rule is the
    stricter one, and never the looser one.

    A name must hold at least one character that shows (:func:`_shows`). A
    name of zero-width characters, of fillers or of variation selectors is
    refused, because the list would show a blank row. A new emoji alone is
    unassigned (``Cn``) in an older Unicode table, so it is refused too. With
    a word beside it, it passes.
    """
    if not isinstance(raw, str):
        raise TopicNameError("A topic name must be text.")
    name = raw.strip()
    if not name:
        raise TopicNameError("Give the topic a name.")
    if len(name) > NAME_MAX:
        raise TopicNameError(f"A topic name can have {NAME_MAX} characters at most.")
    for ch in name:
        if ch in _BIDI_CONTROLS or unicodedata.category(ch) in _REFUSED_CATEGORIES:
            raise TopicNameError("A topic name cannot hold a control character.")
    if not any(_shows(ch) for ch in name):
        raise TopicNameError("A topic name must hold at least one character that shows.")
    return name


def seed_names(teams: Iterable[str] = TEAMS) -> tuple[str, ...]:
    """The names of the first company list: each team, then General (§6).

    Pure: it reads no table and writes none. Each name passes
    :func:`clean_name`, and a second name in another case drops out. CO-2a
    runs no seed. The slice that seeds an org calls this.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in (*teams, GENERAL):
        name = clean_name(raw)
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return tuple(out)


def topic_id(raw: object) -> str | None:
    """The id as a canonical UUID string, or None for any other shape.

    A route answers None as "not found", the same answer as an id of another
    member. So the shape of an id is no oracle, and no SQL runs on it.
    """
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError, AttributeError):
        return None


@dataclass(frozen=True)
class Topic:
    """One topic that the caller may read."""

    id: str
    name: str
    scope: Literal["company", "member"]
    hidden_at: datetime | None

    def as_json(self) -> dict[str, Any]:
        """The wire shape. It names no owner: a listed member topic is the
        caller's own."""
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "mine": self.scope == "member",
            "hidden": self.hidden_at is not None,
        }


#: The read rule (D163.1, D163.2): a company topic, or a member topic of the
#: caller. RLS has already bound the org. ``:member`` is :func:`member_key`.
_VISIBLE = "(t.scope = 'company' OR (t.scope = 'member' AND t.owner_email = :member))"

#: The write rule: the caller's own member topic, or a company topic for an
#: admin. ``:admin`` is a bound boolean that the route takes from the session.
_WRITABLE = (
    "((t.scope = 'member' AND t.owner_email = :member) "
    "OR (t.scope = 'company' AND CAST(:admin AS boolean)))"
)

_COLUMNS = "t.id::text AS id, t.name, t.scope, t.hidden_at"

#: The list: company topics first, then the caller's own, each by name. A
#: hidden company topic shows only when ``:with_hidden`` (an admin's read).
_LIST_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} t "
    f"WHERE {_VISIBLE} AND (t.hidden_at IS NULL OR CAST(:with_hidden AS boolean)) "
    "ORDER BY CASE t.scope WHEN 'company' THEN 0 ELSE 1 END, lower(t.name), t.id"
)

#: The rule of a hidden company topic: only an admin reads it. ``:admin`` is
#: the bound boolean of the session. A member topic is never hidden by code.
_SHOWN = "(t.hidden_at IS NULL OR CAST(:admin AS boolean))"

#: One topic that the caller may read. A hidden company topic reads as no
#: topic for a member who is not an admin.
_ONE_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} t "
    f"WHERE t.id = CAST(:id AS uuid) AND {_VISIBLE} AND {_SHOWN}"
)

#: The two rows of a merge, locked in id order before any check, so two
#: merges that cross take the locks in one order and never both win. The
#: read rule holds here too.
_LOCK_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} t "
    "WHERE t.id IN (CAST(:id AS uuid), CAST(:into AS uuid)) "
    f"AND {_VISIBLE} AND {_SHOWN} "
    "ORDER BY t.id FOR UPDATE"
)

#: The topic that a member may put a chat in (CO-2b): a company topic that
#: shows, or the member's own topic. It holds the row with ``FOR KEY SHARE``
#: until the commit, BEFORE the move writes the per-member row. A merge locks
#: topics ``FOR UPDATE`` before it moves the rows of the chats, so both take
#: the topic first, and they cannot deadlock. A move that waits on a merge
#: of its topic then finds the topic gone and is refused.
_FOR_CHAT_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} t "
    f"WHERE t.id = CAST(:id AS uuid) AND {_VISIBLE} AND t.hidden_at IS NULL "
    "FOR KEY SHARE"
)

#: A new member topic. The org comes from the DEFAULT, the owner from the
#: session. A name that the member already holds gives no row.
_CREATE_SQL = (
    f"INSERT INTO {TABLE} (name, scope, owner_email) "
    "VALUES (:name, 'member', :member) "
    "ON CONFLICT DO NOTHING "
    "RETURNING id::text AS id, name, scope, hidden_at"
)

#: The topic of one name that the caller may read and that shows, in any
#: case: a company topic first, then the caller's own. A hidden company topic
#: and a colleague's topic never match.
_FIND_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} t "
    f"WHERE {_VISIBLE} AND (t.scope = 'member' OR t.hidden_at IS NULL) "
    "AND lower(t.name) = lower(:name) "
    "ORDER BY CASE t.scope WHEN 'company' THEN 0 ELSE 1 END, t.id LIMIT 1"
)

#: A rename. The write rule is in the WHERE, so a topic that the caller may
#: not change gives no row.
_RENAME_SQL = (
    f"UPDATE {TABLE} t SET name = :name "
    f"WHERE t.id = CAST(:id AS uuid) AND {_WRITABLE} "
    f"RETURNING {_COLUMNS}"
)

#: Hide or show a company topic. Only an admin, and only a company topic. A
#: second hide keeps the time of the first.
_HIDE_SQL = (
    f"UPDATE {TABLE} t SET hidden_at = CASE WHEN CAST(:hidden AS boolean) "
    "THEN COALESCE(t.hidden_at, now()) ELSE NULL END "
    "WHERE t.id = CAST(:id AS uuid) AND t.scope = 'company' AND CAST(:admin AS boolean) "
    f"RETURNING {_COLUMNS}"
)

#: A merge of one company topic into another that shows. Only an admin. The
#: source goes. ⚠️ CO-2b moves the chats of the source first (module doc).
_MERGE_SQL = (
    f"DELETE FROM {TABLE} t "
    "WHERE t.id = CAST(:id AS uuid) AND t.scope = 'company' AND CAST(:admin AS boolean) "
    "AND CAST(:id AS uuid) <> CAST(:into AS uuid) "
    f"AND EXISTS (SELECT 1 FROM {TABLE} g WHERE g.id = CAST(:into AS uuid) "
    "AND g.scope = 'company' AND g.hidden_at IS NULL) "
    "RETURNING t.id::text AS id"
)


def _topic(row: Any) -> Topic:
    return Topic(id=row.id, name=row.name, scope=row.scope, hidden_at=row.hidden_at)


def _is_unique_violation(exc: BaseException) -> bool:
    """True for a unique violation (SQLSTATE 23505), on any driver."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == "23505"


def list_topics(s: Any, member: str, *, with_hidden: bool = False) -> list[Topic]:
    """The topics that *member* may read, company topics first.

    *with_hidden* adds the hidden company topics. The route passes True only
    for an admin who asks for them. A member topic of a colleague never shows.
    """
    from sqlalchemy import text

    rows = s.execute(text(_LIST_SQL), {
        "member": member_key(member), "with_hidden": bool(with_hidden)}).fetchall()
    return [_topic(r) for r in rows]


def get_topic(s: Any, member: str, tid: str, *, admin: bool = False) -> Topic | None:
    """One topic that *member* may read, or None.

    A hidden company topic is None unless *admin*, so a member who is not an
    admin cannot tell it from an id that does not exist.
    """
    from sqlalchemy import text

    row = s.execute(text(_ONE_SQL), {
        "id": tid, "member": member_key(member), "admin": bool(admin)}).first()
    return _topic(row) if row is not None else None


def topic_for_chat(s: Any, member: str, tid: str) -> Topic | None:
    """The topic that *member* may put a chat in, locked until the commit.

    CO-2b: the move of a member and the automatic pass call it FIRST, in
    their own transaction (:data:`_FOR_CHAT_SQL`). None for a topic of a
    colleague, a hidden company topic, an id that does not exist, and a topic
    that a merge took away while this call waited. *tid* must come from
    :func:`topic_id`.
    """
    from sqlalchemy import text

    row = s.execute(text(_FOR_CHAT_SQL), {"id": tid, "member": member_key(member)}).first()
    return _topic(row) if row is not None else None


def create_member_topic(s: Any, member: str, name: str) -> Topic | None:
    """A new private topic of *member*. None when the member holds the name.

    *name* must come from :func:`clean_name`. The name of a colleague's topic
    or of a company topic is no clash, so the answer shows no other list.
    """
    from sqlalchemy import text

    row = s.execute(text(_CREATE_SQL), {"name": name, "member": member_key(member)}).first()
    return _topic(row) if row is not None else None


def find_or_create_member_topic(s: Any, member: str, name: str) -> Topic:
    """The topic of *name* for *member*. The automatic pass (CO-1) calls it.

    Idempotent. A company topic that shows, or the member's own topic, of the
    same name in any case gives that topic. Else it makes a PRIVATE topic of
    the member (D163.2), never a company topic. A colleague's topic never
    matches, so the answer never shows that a colleague holds the name.
    :class:`TopicNameError` for a bad name.
    """
    from sqlalchemy import text

    clean = clean_name(name)
    params = {"name": clean, "member": member_key(member)}
    # Two tries: a create that loses a race to the same name of the same
    # member finds that topic on the second read.
    for _ in range(2):
        row = s.execute(text(_FIND_SQL), params).first()
        if row is not None:
            return _topic(row)
        made = create_member_topic(s, member, clean)
        if made is not None:
            return made
    raise RuntimeError("chat_topic: the topic was neither found nor made")


Outcome = Literal["ok", "missing", "forbidden", "duplicate", "not_company", "same"]


@dataclass(frozen=True)
class Change:
    """The outcome of a change. ``topic`` is set only for ``ok``."""

    outcome: Outcome
    topic: Topic | None = None


def rename_topic(s: Any, tid: str, *, member: str, name: str, admin: bool) -> Change:
    """Rename a topic. The member renames their own topic. An admin renames a
    company topic. *name* must come from :func:`clean_name`.

    ``missing``: the caller may not read the topic. ``forbidden``: a company
    topic and no admin. ``duplicate``: the list holds the name.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    seen = get_topic(s, member, tid, admin=admin)
    if seen is None:
        return Change("missing")
    if seen.scope == "company" and not admin:
        return Change("forbidden")
    params = {"id": tid, "member": member_key(member), "admin": bool(admin), "name": name}
    try:
        # A savepoint, so a unique violation leaves the transaction usable.
        with s.begin_nested():
            row = s.execute(text(_RENAME_SQL), params).first()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            return Change("duplicate")
        raise
    if row is None:
        return Change("missing")
    return Change("ok", _topic(row))


def set_hidden(s: Any, tid: str, *, member: str, hidden: bool, admin: bool) -> Change:
    """Hide or show a company topic. Only an admin.

    ``missing``: the caller may not read the topic. ``not_company``: the
    caller's own member topic. ``forbidden``: no admin.
    """
    from sqlalchemy import text

    seen = get_topic(s, member, tid, admin=admin)
    if seen is None:
        return Change("missing")
    if seen.scope != "company":
        return Change("not_company")
    if not admin:
        return Change("forbidden")
    row = s.execute(text(_HIDE_SQL), {
        "id": tid, "hidden": bool(hidden), "admin": bool(admin)}).first()
    if row is None:
        return Change("missing")
    return Change("ok", _topic(row))


def merge_company_topics(s: Any, tid: str, *, into: str, member: str, admin: bool) -> Change:
    """Merge the company topic *tid* into the company topic *into*. Only an admin.

    The source goes, and the target stays. ``same``: one topic. ``missing``:
    either topic is not a company topic that the caller may read, or the
    target is hidden. ``not_company``: a member topic. ``forbidden``: no admin.

    :data:`_LOCK_SQL` locks both rows in id order FIRST, and each check reads
    the locked rows. A second merge that crosses this one waits for the lock.
    It then finds its source or its target gone, and gets ``missing``. A hide
    of the target that commits first makes the target hidden here.

    CO-2b: after the checks, and before the delete, the chats of the source
    move to the target in the same transaction
    (``chat_session_member.repoint_topic``). Each row keeps its ``set_by``,
    so a member's move stays a member's move.
    """
    from sqlalchemy import text

    from gateway import chat_session_member

    if tid == into:
        return Change("same")
    locked = {r.id: _topic(r) for r in s.execute(text(_LOCK_SQL), {
        "id": tid, "into": into, "member": member_key(member), "admin": bool(admin)})}
    source, target = locked.get(tid), locked.get(into)
    if source is None or target is None:
        return Change("missing")
    if source.scope != "company" or target.scope != "company":
        return Change("not_company")
    if not admin:
        return Change("forbidden")
    if target.hidden_at is not None:
        return Change("missing")
    chat_session_member.repoint_topic(s, tid, into=into)
    gone = s.execute(text(_MERGE_SQL), {
        "id": tid, "into": into, "admin": bool(admin)}).first()
    if gone is None:
        # The checks above read the locked rows, so the delete cannot miss.
        # If it does, the raise rolls the move of the chats back too, and a
        # half merge never commits.
        raise RuntimeError("chat_topic: the locked source of a merge was not deleted")
    return Change("ok", target)
