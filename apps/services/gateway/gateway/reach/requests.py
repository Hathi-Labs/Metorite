""""Tell me on WhatsApp" — the one call every app makes (WS-59 RE-5).

Spec: ``project-docs/specs/proactive_reach.md`` §11. Migration
``270_reach_requests.sql``.

:func:`tell_me` records ONE request for ONE member. It sends nothing. The
Reach loop reads the member's open requests on its next pass, turns each one
into a card of kind ``notify_me`` with the signal ``request:<id>``, and the
card takes the normal path: the budget, quiet hours, the 24-hour window and
the log.

**The member is always the recipient.** The caller passes the member from a
session or from the owner of a rule or a workflow, never from request input
(R5, R11). No request can address a third person (§2.2).

**It records when Reach is off too.** The loop delivers only for the orgs and
members that ``flags`` allows. A request for anyone else expires unread.

**Delivered means logged.** A request is open while it has not expired and
``reach_signal_log`` holds no row for its signal. The loop logs it ``sent``,
``skipped`` or ``expired``. So this table needs no state column, and the one
log decides "never twice" for cards and requests alike.

Every statement runs in ``tenant_session(org)``, and the organization column
takes its value from the bound tenant (``_BOUND_ORG``), never from a
parameter. ``tests/unit/test_reach_requests.py`` holds the rules and runs the
SQL on a real Postgres (R8).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.db import tenant_session
from sqlalchemy import text

from gateway.reach.outbox import _BOUND_ORG

#: The callers that may ask. A new caller adds its name here (§11.1).
SOURCES = frozenset({"email.rule", "chat", "workflow"})
URGENCIES = frozenset({"normal", "urgent"})
TITLE_MAX = 300
BODY_MAX = 1000
KEY_MAX = 300
ENTITY_MAX = 100
#: The sources where the member asks NOW, so the request is the member's own:
#: class ``urgent``, past the cap and past quiet hours (§11.3). A rule or a
#: workflow is never one, though its owner is the member too.
SELF_SOURCES = frozenset({"chat"})
#: The kind of the card a request becomes, and the prefix of its signal.
KIND = "notify_me"
SIGNAL_PREFIX = "request:"
#: The most open requests one pass reads for one member. The member's own
#: requests come first, so a backlog never starves one (review of RE-5, P1-2).
READ_LIMIT = 100
#: The most open requests of rules and workflows that one member may hold.
#: :func:`tell_me` refuses the next one with ``backlog_full`` (P1-2).
BACKLOG_LIMIT = 20
#: The most requests of the member's own in 24 hours. :func:`tell_me`
#: refuses the next one with ``daily_limit`` (review of RE-5, P3-1).
SELF_DAILY_LIMIT = 20

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
#: A link into the app: one leading slash, then no space, no control
#: character and no backslash. ``//host`` and ``/\\host`` open another site.
_HREF = re.compile(r"^/(?![/\\])[^\s\\\x00-\x1f\x7f]*$")

_INSERT_SQL = (
    "INSERT INTO reach_requests (organization_id, member_email, source, title, "
    "body, href, entity_type, entity_id, urgency, requested_by, dedupe_key) "
    f"VALUES ({_BOUND_ORG}, :m, :src, :title, :body, :href, :et, :eid, :urg, "
    ":by, :key) "
    "ON CONFLICT (organization_id, dedupe_key) DO NOTHING RETURNING id::text"
)

#: The member's requests that no log row closed yet, expired or not.
_UNDELIVERED_SQL = (
    "SELECT r.id::text AS id, r.source, r.title, r.body, r.href, r.entity_type, "
    "r.entity_id, r.urgency, r.requested_by, r.created_at, r.expires_at "
    "FROM reach_requests r "
    f"WHERE r.organization_id = {_BOUND_ORG} AND r.member_email = :m "
    "AND NOT EXISTS (SELECT 1 FROM reach_signal_log l "
    "WHERE l.organization_id = r.organization_id "
    "AND l.member_email = r.member_email "
    "AND l.signal_id = 'request:' || r.id::text) "
    "ORDER BY (r.source = 'chat') DESC, r.created_at, r.id LIMIT :lim"
)

#: The member's open requests of rules and workflows, and the member's own
#: requests of the last 24 hours. ``'chat'`` is the one source of
#: :data:`SELF_SOURCES`.
_COUNTS_SQL = (
    "SELECT (SELECT count(*) FROM reach_requests r "
    f"WHERE r.organization_id = {_BOUND_ORG} AND r.member_email = :m "
    "AND r.source <> 'chat' AND r.expires_at > now() "
    "AND NOT EXISTS (SELECT 1 FROM reach_signal_log l "
    "WHERE l.organization_id = r.organization_id "
    "AND l.member_email = r.member_email "
    "AND l.signal_id = 'request:' || r.id::text)) AS backlog, "
    "(SELECT count(*) FROM reach_requests r "
    f"WHERE r.organization_id = {_BOUND_ORG} AND r.member_email = :m "
    "AND r.source = 'chat' "
    "AND r.created_at > now() - interval '24 hours') AS own_today"
)


class RequestInvalid(ValueError):
    """A request that :func:`tell_me` refuses. The text names the field only."""


class RequestRefused(RequestInvalid):
    """A valid request that :func:`tell_me` refuses because of a limit.
    ``reason`` is ``backlog_full`` or ``daily_limit``. A caller that catches
    :class:`RequestInvalid` shows the text, so a rule's History says why."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


@dataclass(frozen=True)
class Request:
    """One recorded request, as the loop reads it."""

    id: str
    source: str
    title: str
    body: str | None
    href: str | None
    entity_type: str | None
    entity_id: str | None
    urgency: str
    requested_by: str
    created_at: datetime
    expires_at: datetime

    @property
    def signal_id(self) -> str:
        return f"{SIGNAL_PREFIX}{self.id}"


# ── The checks ──────────────────────────────────────────────────────────────


def _email(value: Any, field: str) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL.match(email):
        raise RequestInvalid(f"{field} must be an email address")
    return email


def clean_title(value: Any) -> str:
    """The title as one trimmed line, or a refusal."""
    title = " ".join(str(value or "").split())
    if not title:
        raise RequestInvalid("title is empty")
    if len(title) > TITLE_MAX:
        raise RequestInvalid(f"title is longer than {TITLE_MAX} characters")
    return title


def clean_body(value: Any) -> str | None:
    """The body, trimmed, or None. Refused over :data:`BODY_MAX`."""
    if value is None:
        return None
    body = str(value).strip()
    if len(body) > BODY_MAX:
        raise RequestInvalid(f"body is longer than {BODY_MAX} characters")
    return body or None


def clean_href(value: Any) -> str | None:
    """A relative link into the app, or None. A full URL is refused."""
    if value is None or value == "":
        return None
    href = str(value)
    if len(href) > 2000 or not _HREF.match(href):
        raise RequestInvalid("href must be a path in the app that starts with /")
    return href


def _entity(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise RequestInvalid("entity must be {type, id}")
    et = str(value.get("type") or "").strip()
    eid = str(value.get("id") or "").strip()
    if not et or not eid or len(et) > ENTITY_MAX or len(eid) > ENTITY_MAX:
        raise RequestInvalid("entity must be {type, id}")
    return et, eid


def clip(value: Any, limit: int) -> str:
    """*value* as one line of at most *limit* characters, with an ellipsis.
    For a caller that builds a title from text it does not control."""
    line = " ".join(str(value or "").split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


# ── The call ────────────────────────────────────────────────────────────────


async def tell_me(org: str, member: str, *, source: str, title: str,
                  body: str | None = None, href: str | None = None,
                  entity: dict[str, Any] | None = None, urgency: str = "normal",
                  requested_by: str, key: str | None = None) -> str | None:
    """Record one request for *member* in *org*. Answers its id, or None when
    *key* already holds a request. Sends nothing.

    Raises :class:`RequestInvalid` for a bad argument, and
    :class:`RequestRefused` over :data:`BACKLOG_LIMIT` or
    :data:`SELF_DAILY_LIMIT`. *member* comes from a session or from the owner
    of a rule or a workflow, never from request input."""
    if not org:
        raise RequestInvalid("org is missing")
    if source not in SOURCES:
        raise RequestInvalid("source is not known")
    if urgency not in URGENCIES:
        raise RequestInvalid("urgency must be normal or urgent")
    et, eid = _entity(entity)
    dedupe = str(key).strip() if key else f"auto:{uuid.uuid4()}"
    if not dedupe or len(dedupe) > KEY_MAX:
        raise RequestInvalid("key is empty or too long")
    params = {
        "m": _email(member, "member"), "src": source, "title": clean_title(title),
        "body": clean_body(body), "href": clean_href(href), "et": et, "eid": eid,
        "urg": urgency, "by": _email(requested_by, "requested_by"), "key": dedupe,
    }
    async with tenant_session(org) as db:
        backlog, own_today = (await db.execute(text(_COUNTS_SQL),
                                               {"m": params["m"]})).one()
        if source in SELF_SOURCES and int(own_today) >= SELF_DAILY_LIMIT:
            raise RequestRefused("daily_limit", f"{SELF_DAILY_LIMIT} requests in 24 hours "
                                 "is the limit")
        if source not in SELF_SOURCES and int(backlog) >= BACKLOG_LIMIT:
            raise RequestRefused("backlog_full", f"{BACKLOG_LIMIT} messages already wait "
                                 "for this member")
        row = (await db.execute(text(_INSERT_SQL), params)).first()
    return None if row is None else str(row[0])


async def reach_status(org: str | None, member: str | None) -> tuple[bool, bool]:
    """``(available, linked)`` of "Tell me on WhatsApp" for *member* in *org*.

    *available* is True when Reach may deliver to the member: the switch, the
    org list, the member allowlist and the WhatsApp channel. *linked* is True
    when the member also has a current WhatsApp link. The link is read only
    when Reach is available. The Rules UI, the AI rule generator and the
    email action read this one answer (review of RE-5, P1-4)."""
    from gateway.reach import flags
    from gateway.reach import whatsapp as channel

    email = str(member or "").strip().lower()
    if not org or not email or not flags.member_allowed(org, email) \
            or not channel.channel_ready(org):
        return False, False
    return True, await channel.linked_phone(org, email) is not None


async def undelivered(org: str, member: str) -> list[Request]:
    """The member's requests that no log row closed, oldest first, expired or
    not. :func:`split` parts them."""
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_UNDELIVERED_SQL), {
            "m": member.strip().lower(), "lim": READ_LIMIT})).mappings().all()
    return [Request(**dict(r)) for r in rows]


def split(rows: list[Request], now: datetime) -> tuple[list[Request], list[Request]]:
    """The open requests and the expired ones, each in the order of *rows*."""
    live = [r for r in rows if r.expires_at > now]
    gone = [r for r in rows if r.expires_at <= now]
    return live, gone


async def open_requests(org: str, member: str, now: datetime) -> list[Request]:
    """The member's requests that may still go: not expired, not delivered."""
    return split(await undelivered(org, member), now)[0]


def is_self(req: Request, member: str) -> bool:
    """True when the member asked for this now, in the assistant (§11.3)."""
    return req.source in SELF_SOURCES and req.requested_by == member.strip().lower()


def card(req: Request, member: str) -> dict[str, Any]:
    """The card of one request. A request is always new: the loop never
    baselines one, and the log alone says it went."""
    return {
        "signal_id": req.signal_id,
        "app": "reach",
        "kind": KIND,
        "group": "now",
        "state": "new",
        "title": req.title,
        "why": "",
        "href": req.href,
        "at": req.created_at.isoformat(),
        "facts": {
            "source": req.source,
            "urgency": req.urgency,
            "self": is_self(req, member),
            "body": req.body,
        },
    }
