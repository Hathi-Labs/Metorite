"""The topics of the chat list (WS-CO CO-2a, task 3501).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163.1,
D163.2). Migration: ``infra/postgres/<n>_chat_topic.sql``.

A topic is ``company`` (one short list for the org) or ``member`` (the
private topic of one member). The chat rail of CO-2b groups a member's chats
by topic. This slice holds the store and its routes only. No chat names a
topic yet.

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
* **A merge deletes the source.** No chat names a topic in CO-2a. ⚠️ CO-2b
  adds ``topic_id`` to the per-member row of a chat, and its merge must move
  each row of the source to the target in the same transaction, before the
  delete.
* **The switch.** ``CHAT_TOPICS_ORGS`` lists the orgs. :func:`topics_allowed`
  is its one reader. Off, each topic route answers 404.
* **The automatic pass (CO-1).** It sets the topic of a chat through one
  function of CO-2b. That function writes ``topic_id`` with ``set_by =
  'auto'`` ONLY where no row exists, or where ``set_by`` is already
  ``'auto'``. It never writes over a row with ``set_by = 'member'`` (D163).
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

#: Characters that a name may hold although their category is ``Cf``: the
#: two joiners of an emoji sequence and of some scripts.
_JOINERS = frozenset({"\u200c", "\u200d"})

#: The categories that a name may not hold: control and format characters,
#: the line and paragraph marks, surrogates, private use and unassigned.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


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

    A name holds 1 to :data:`NAME_MAX` characters after the trim, and no
    control, format or line character. The two joiners stay legal.
    """
    if not isinstance(raw, str):
        raise TopicNameError("A topic name must be text.")
    name = raw.strip()
    if not name:
        raise TopicNameError("Give the topic a name.")
    if len(name) > NAME_MAX:
        raise TopicNameError(f"A topic name can have {NAME_MAX} characters at most.")
    for ch in name:
        if ch not in _JOINERS and unicodedata.category(ch) in _REFUSED_CATEGORIES:
            raise TopicNameError("A topic name cannot hold a control character.")
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

#: One topic that the caller may read, hidden or not.
_ONE_SQL = f"SELECT {_COLUMNS} FROM {TABLE} t WHERE t.id = CAST(:id AS uuid) AND {_VISIBLE}"

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


def get_topic(s: Any, member: str, tid: str) -> Topic | None:
    """One topic that *member* may read, or None."""
    from sqlalchemy import text

    row = s.execute(text(_ONE_SQL), {"id": tid, "member": member_key(member)}).first()
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

    seen = get_topic(s, member, tid)
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

    seen = get_topic(s, member, tid)
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
    """
    from sqlalchemy import text

    if tid == into:
        return Change("same")
    source = get_topic(s, member, tid)
    target = get_topic(s, member, into)
    if source is None or target is None:
        return Change("missing")
    if source.scope != "company" or target.scope != "company":
        return Change("not_company")
    if not admin:
        return Change("forbidden")
    if target.hidden_at is not None:
        return Change("missing")
    gone = s.execute(text(_MERGE_SQL), {
        "id": tid, "into": into, "admin": bool(admin)}).first()
    if gone is None:
        return Change("missing")
    return Change("ok", target)
