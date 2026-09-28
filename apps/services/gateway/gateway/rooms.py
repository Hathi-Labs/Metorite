"""Room membership — the one predicate that replaces ``WHERE user_id = :uid``.

Spec: ``project-docs/specs/groups_sessions_authority.md`` §2 (participants
and visibility), ``docs/multiplayer/README.md`` §4.2 ("the thread is the room")
and §4.5 (``resolve_room_access`` replaces ``_thread_owner_ok`` everywhere).

A room is a ``chat_session`` row plus the people in it. Every read and write
path in chat and agent used to ask "is this session's ``user_id`` your email",
which is a question with exactly one right answer per session. This module asks
"what may you do in this room", which is the same question when the room has
one member — deliberately, so nothing about solo use changes.

Three facts decide the answer, and they are kept separate on purpose:

* **visibility** (``private`` | ``people`` | ``org``) answers *discovery* — may
  you find and open this at all.
* **participants** (``chat_session_participant``) answer *presence* — are you
  in it, and as what.
* **role** (``owner`` | ``member`` | ``viewer``) answers *capacity* — what may
  you do once inside.

What this module does NOT do is decide what the *agent* may do on your behalf.
That is the intersection rule (``acb_auth.resolve_session_access``), which folds
every participant's ``EffectiveAccess`` and is enforced at run start. Room role
and platform permission are different axes and collapsing them is how a room
becomes a privilege-escalation primitive: being a member here never grants a
permission you don't hold in the org, it can only narrow what the room's agents
may reach.

Two "we don't know" cases used to share one permissive answer, inherited from
``_thread_owner_ok``. They are now separated, because only one of them is
actually about a person's own work:

* **No ``chat_session`` row** (ephemeral run, a thread whose first turn has not
  persisted yet) still resolves to full access. Refusing it would break every
  brand-new conversation, and there is no other party for it to belong to.
* **A database error** now resolves to NO access. It used to resolve to owner —
  which meant a failed lookup handed the caller full read/send/cancel on
  somebody else's room, and the one moment we are least able to say who a
  transcript belongs to was the moment we granted it. Refusing costs nothing a
  Postgres outage was not already costing: the same store holds the transcript
  the caller is asking for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from acb_common import get_logger

_log = get_logger("gateway.rooms")

#: Room roles, most to least capable. Order is load-bearing: when several
#: subjects match one person (their email AND a group they're in AND `org`),
#: the most capable wins — being named individually as an owner is not undone
#: by also being in a group that was added as viewers.
ROOM_ROLES = ("owner", "member", "viewer")
_ROLE_RANK = {r: i for i, r in enumerate(ROOM_ROLES)}


@dataclass(frozen=True, slots=True)
class RoomAccess:
    """What one person may do in one room.

    ``role is None`` with ``can_read=False`` is the only "no" this type can
    express; every other combination is a graded yes.
    """

    session_id: str
    email: str
    role: str | None
    can_read: bool
    can_send: bool          # may address the room's agents
    can_cancel: bool        # may stop a run — anyone who can send can stop
    can_invite: bool        # may add or remove participants
    can_manage: bool        # may change visibility, floor mode, agents, delete
    #: True when a second distinct human subject is present. Everything that
    #: behaves differently in a room keys off THIS, not off role, so a session
    #: of one is byte-identical to the single-player system.
    is_shared: bool
    #: Human subjects resolved for this room, sorted. Empty for a session with
    #: no participant rows (pre-134 sessions).
    members: list[str] = field(default_factory=list)
    #: History waterline for a late joiner, when the room hides what predates
    #: them. ``None`` means "the whole transcript".
    since_message_ts: int | None = None
    since_stream_id: str | None = None
    #: The room's own settings, carried so callers don't re-query.
    visibility: str = "private"
    floor_mode: str = "open"
    history_visibility: str = "full"
    agent_name: str = ""
    #: True when the session row is missing entirely (ephemeral thread) or the
    #: lookup failed. Callers that must not create rooms out of thin air check
    #: this; callers that only need "don't lock the user out" ignore it.
    unknown_session: bool = False
    #: True when the lookup itself failed. Distinct from ``unknown_session``
    #: (which also covers "no row yet, and that is fine"): this one means we
    #: could not answer, so the answer is no. Only ``denied()`` reads it, to
    #: say "retry" rather than "you are not a participant" — a refusal that
    #: names the wrong reason is how an outage becomes a support ticket about
    #: permissions.
    resolve_failed: bool = False

    @property
    def is_member(self) -> bool:
        """True when this person holds a real place in the room.

        A reader who only got in through ``visibility='org'`` is not a member —
        they are a passer-by, and the distinction matters for the participant
        list and for the intersection fold.
        """
        return self.role is not None

    def denied(self, action: str) -> str:
        """A refusal that names the room rule, not just 'forbidden'."""
        if self.resolve_failed:
            return (
                "We could not check your access to this conversation just "
                "now. Please try again in a moment."
            )
        if not self.can_read:
            return "You are not a participant of this conversation."
        return (
            f"You are a {self.role or 'viewer'} in this room, which cannot "
            f"{action}. Ask an owner to change your role."
        )


def _capabilities(role: str | None, *, visibility: str) -> tuple[bool, bool, bool, bool, bool]:
    """(read, send, cancel, invite, manage) for a resolved room role."""
    if role == "owner":
        return True, True, True, True, True
    if role == "member":
        return True, True, True, False, False
    if role == "viewer":
        return True, False, False, False, False
    # No participant row. An org-visible room is readable by any authenticated
    # member of the org — that is what 'org' visibility means — but read is ALL
    # it grants: joining is an explicit act, not a side effect of looking.
    if visibility == "org":
        return True, False, False, False, False
    return False, False, False, False, False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

#: Which of a room's group subjects actually contain the caller.
#:
#: Module-level rather than inline in `_load_room` so a hermetic test can assert
#: on it without a database (`tenancy_and_visibility.md` §2 done-when 2 — the
#: DB-backed room tests skip green with no Postgres, so the tenant predicate
#: needs a string assertion that cannot skip).
#:
#: `g.organization_id = u.organization_id` is the tenant predicate. `org_group`
#: slugs are unique only *within* an organization (`UNIQUE (organization_id,
#: slug)`, `138_groups_and_session_participants.sql:49`), so joining on slug
#: alone matched the identically-named group in every other tenant — a
#: cross-organization match by construction once the tenant boundary is a row
#: rather than a deployment (`saas_multitenancy.md` §1 D15, §6.5). The org is
#: *derived* from `u`, the acting user's own row, so it cannot go stale the way
#: a literal `slug = 'default'` would (`tenancy_and_visibility.md` §2
#: done-when 1).
MY_GROUPS_SQL = """
    SELECT g.slug
    FROM org_group g
    JOIN org_group_member m ON m.group_id = g.id
    JOIN app_user u ON u.id = m.user_id
    WHERE u.email = :email
      AND g.slug = ANY(:slugs)
      AND g.organization_id = u.organization_id
"""


def _load_room(session_id: str, email: str) -> dict | None:
    """Read the session row and everything about this person's place in it.

    One round trip per concern, all inside one transaction, because a room's
    membership changing between the two reads would produce an access answer
    that was never true.
    """
    from acb_graph import get_session
    from sqlalchemy import text

    with get_session() as s:
        row = s.execute(
            text(
                "SELECT user_id, agent_name, "
                "       COALESCE(visibility, 'private')          AS visibility, "
                "       COALESCE(floor_mode, 'open')             AS floor_mode, "
                "       COALESCE(history_visibility, 'full')     AS history_visibility "
                "FROM chat_session WHERE id = :sid"
            ),
            {"sid": session_id},
        ).first()
        if row is None:
            return None

        parts = s.execute(
            text(
                "SELECT subject, role, join_message_ts, join_stream_id "
                "FROM chat_session_participant WHERE session_id = :sid"
            ),
            {"sid": session_id},
        ).fetchall()

        # Which of this room's group subjects contain the caller. Resolved here
        # rather than expanded per subject so one query answers all of them.
        group_slugs = [
            p.subject.split(":", 1)[1]
            for p in parts
            if p.subject.startswith("group:") and ":" in p.subject
        ]
        my_groups: set[str] = set()
        if group_slugs:
            rows = s.execute(
                text(MY_GROUPS_SQL),
                {"email": email, "slugs": group_slugs},
            ).fetchall()
            my_groups = {r.slug for r in rows}

        in_org = False
        if any(p.subject == "org" for p in parts) or row.visibility == "org":
            in_org = bool(
                s.execute(
                    text(
                        "SELECT 1 FROM app_user "
                        "WHERE email = :email AND COALESCE(status, 'active') = 'active'"
                    ),
                    {"email": email},
                ).first()
            )

    return {
        "session": row,
        "participants": parts,
        "my_groups": my_groups,
        "in_org": in_org,
    }


def _expand_members(participants, session_user_id: str) -> list[str]:
    """The human subjects named in this room, for the shared/solo decision.

    Group and ``org`` subjects are NOT expanded here. Expanding them would make
    ``is_shared`` depend on org headcount and turn every ``org``-shared room
    into a room of forty — which is true for the authority fold (where
    ``acb_auth.resolve_session_access`` does expand them, because a capability
    must survive every reader) but wrong for "is this a room or a private
    thread". A group subject counts as one other party, which is exactly what
    it is from the transcript's point of view.
    """
    seen: list[str] = []
    for p in participants:
        if p.subject not in seen:
            seen.append(p.subject)
    if not seen and session_user_id and "@" in session_user_id:
        seen.append(session_user_id)
    return sorted(seen)


def resolve_room_access(session_id: str, email: str) -> RoomAccess:
    """What ``email`` may do in room ``session_id``. Never raises.

    This is the single source of truth for room authorization. Callers get a
    graded answer rather than a boolean so that "may read but not send" — a
    viewer watching a run — is expressible, which is the whole point of
    read-only multiplayer.
    """
    email = (email or "").strip()

    def _unsaved_thread() -> RoomAccess:
        """A thread that has no row yet — a room of one, the caller's own.

        This is the ONLY permissive fallback. It is not a guess: a session id
        with no ``chat_session`` row belongs to nobody else, because the row is
        what would make it somebody's. Every new conversation passes through
        this state between the composer and the first persisted turn.
        """
        return RoomAccess(
            session_id=session_id, email=email, role="owner",
            can_read=True, can_send=True, can_cancel=True,
            can_invite=False, can_manage=False,
            is_shared=False, members=[email] if email else [],
            unknown_session=True,
        )

    def _undecidable() -> RoomAccess:
        """The lookup failed, so we cannot say — and cannot say yes.

        This used to return ``_unsaved_thread()``, i.e. full owner access on
        any database error. That is the wrong direction for the one input we
        have no information about: the room may well be somebody else's, and a
        transient failure would have handed over its transcript, its agents and
        its cancel button. Denying costs nothing extra during an outage — the
        transcript the caller wants lives in the store that just failed.
        """
        return RoomAccess(
            session_id=session_id, email=email, role=None,
            can_read=False, can_send=False, can_cancel=False,
            can_invite=False, can_manage=False,
            is_shared=False, members=[],
            unknown_session=True, resolve_failed=True,
        )

    if not session_id:
        return _unsaved_thread()

    try:
        loaded = _load_room(session_id, email)
    except Exception:
        _log.warning("rooms.resolve_failed", session_id=session_id, exc_info=True)
        return _undecidable()

    if loaded is None:
        # Ephemeral thread with no session row yet: the run path creates the
        # row on first persist. Treating this as "no access" would break every
        # brand-new conversation.
        return _unsaved_thread()

    row = loaded["session"]
    parts = loaded["participants"]
    visibility = row.visibility or "private"

    # Highest-ranked role among every subject that matches this person.
    role: str | None = None
    join_ts: int | None = None
    join_sid: str | None = None
    for p in parts:
        subject = p.subject
        matched = (
            subject == email
            or (subject.startswith("group:")
                and subject.split(":", 1)[1] in loaded["my_groups"])
            or (subject == "org" and loaded["in_org"])
        )
        if not matched:
            continue
        if role is None or _ROLE_RANK.get(p.role, 9) < _ROLE_RANK.get(role, 9):
            role = p.role
        # The waterline comes from the row that names this person directly;
        # a group's join time is the group's, not this member's.
        if subject == email:
            join_ts = p.join_message_ts
            join_sid = p.join_stream_id

    # The creator owns a session that has NO participant row at all: a legacy
    # session the 134 backfill never reached, or a browser-created session
    # before its first run. A room with any membership resolves from its rows
    # and grants only (WS-27bm S14 round 3, projects_ai_chat.md §20). An owner
    # who removed the creator has removed her, and the creator gets no owner
    # role back from `chat_session.user_id`.
    #
    # Round 5: a creator whose user_id is not an email ('default', the id of
    # a caller with no email) keeps the fallback in every room, as on main.
    # That id is outside the participant grammar (`routes/rooms._valid_subject`
    # takes an email, `group:<slug>` or `org`), so no owner row can stand for
    # it, and no owner can remove it.
    if (
        role is None and email and row.user_id == email
        and (not parts or "@" not in email)
    ):
        role = "owner"

    read, send, cancel, invite, manage = _capabilities(role, visibility=visibility)
    members = _expand_members(parts, row.user_id or "")

    since_ts = join_ts if (row.history_visibility == "since_join") else None
    since_sid = join_sid if (row.history_visibility == "since_join") else None

    return RoomAccess(
        session_id=session_id,
        email=email,
        role=role,
        can_read=read,
        can_send=send,
        can_cancel=cancel,
        can_invite=invite,
        can_manage=manage,
        is_shared=len(members) > 1,
        members=members,
        since_message_ts=since_ts,
        since_stream_id=since_sid,
        visibility=visibility,
        floor_mode=row.floor_mode or "open",
        history_visibility=row.history_visibility or "full",
        agent_name=row.agent_name or "",
    )


# ---------------------------------------------------------------------------
# The membership predicate, as SQL
# ---------------------------------------------------------------------------

#: Rooms this person may see in their session list.
#:
#: Three ways in, matching resolve_room_access: they created it and it has no
#: participant row yet (S14 round 3), a participant row names them (directly,
#: or through a group they belong to, or through `org`), or the room is
#: org-visible and they are an active member. Written as
#: one EXISTS-per-way rather than a join so a session is never returned twice
#: and the planner can use each subject index independently.
#:
#: The group arm carries the same tenant predicate as MY_GROUPS_SQL —
#: `g.organization_id = u.organization_id`, derived from the caller's own
#: `app_user` row. Without it the slug join admits a room shared with another
#: tenant's identically-slugged group (`saas_multitenancy.md` §6.5). Kept as a
#: predicate rather than an SQL `--` comment because callers concatenate this
#: constant into larger statements (`routes/chat.py:98,230,296,735`).
SESSION_VISIBLE_SQL = """
    (
        (
            s.user_id = :uid
            AND (
                s.user_id NOT LIKE '%@%'
                OR NOT EXISTS (
                    SELECT 1 FROM chat_session_participant p0
                    WHERE p0.session_id = s.id
                )
            )
        )
        OR EXISTS (
            SELECT 1 FROM chat_session_participant p
            WHERE p.session_id = s.id AND p.subject = :uid
        )
        OR EXISTS (
            SELECT 1 FROM chat_session_participant p
            JOIN org_group g   ON g.slug = substring(p.subject from 7)
            JOIN org_group_member gm ON gm.group_id = g.id
            JOIN app_user u    ON u.id = gm.user_id
            WHERE p.session_id = s.id
              AND p.subject LIKE 'group:%'
              AND u.email = :uid
              AND g.organization_id = u.organization_id
        )
        OR (
            EXISTS (
                SELECT 1 FROM chat_session_participant p
                WHERE p.session_id = s.id AND p.subject = 'org'
            )
            AND EXISTS (
                SELECT 1 FROM app_user u
                WHERE u.email = :uid AND COALESCE(u.status, 'active') = 'active'
            )
        )
        OR (
            s.visibility = 'org'
            AND EXISTS (
                SELECT 1 FROM app_user u
                WHERE u.email = :uid AND COALESCE(u.status, 'active') = 'active'
            )
        )
    )
"""
