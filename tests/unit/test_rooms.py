"""Room membership: what each person may do in a shared conversation.

Spec: ``project-docs/specs/groups_sessions_authority.md`` §2,
``docs/multiplayer/README.md`` §4.2, §5.4.

The properties, asserted against a live database:

1. **Solo is unchanged.** A session with one owner resolves exactly as the
   single-owner predicate did, and a session that does not exist at all still
   resolves permissively — the contract ``_thread_owner_ok`` established and
   ``resolve_room_access`` inherited, because a solo operator must never be
   locked out of a brand-new thread. A lookup that FAILS is the other half of
   that pair and resolves the opposite way: see §0.
2. **A viewer reads and cannot send.** That is the whole of read-only
   multiplayer, and it is the one capability split the room roles exist for.
3. **Membership beats ownership.** Someone with a participant row sees a
   session they did not create; someone with neither sees nothing.
4. **Groups expand at read time**, so leaving a group closes the door on the
   next request with no fan-out write.
5. **The most capable matching subject wins.** Being named an owner is not
   undone by also being in a group that was added as viewers.
6. **A room always keeps an owner** — the last one can neither be demoted nor
   leave, the same invariant the org keeps for itself.
7. **Authorship is set once**: no later writer can rename the author of a turn.
"""
from __future__ import annotations

import uuid

import pytest


def _db_ready() -> bool:
    try:
        from acb_graph import get_session
        from sqlalchemy import text
    except Exception:
        return False
    try:
        with get_session() as s:
            s.execute(text("SELECT subject FROM chat_session_participant LIMIT 1"))
            s.execute(text("SELECT agent_name FROM chat_session_agent LIMIT 1"))
            s.execute(text("SELECT author_kind FROM chat_message LIMIT 1"))
        return True
    except Exception:
        return False


_needs_db = pytest.mark.skipif(
    not _db_ready(),
    reason="no reachable Postgres with migrations 138+139 — room tests skipped",
)

_PREFIX = "pytest-rooms"
_ALICE = f"{_PREFIX}-alice@fracktal.in"
_BOB = f"{_PREFIX}-bob@fracktal.in"
_CAROL = f"{_PREFIX}-carol@fracktal.in"
_GROUP = f"{_PREFIX}-squad"


def _exec(sql: str, **params):
    from acb_graph import get_session
    from sqlalchemy import text
    with get_session() as s:
        result = s.execute(text(sql), params)
        try:
            rows = result.fetchall()
        except Exception:
            rows = []
        s.commit()
        return rows


def _purge() -> None:
    _exec("DELETE FROM chat_session WHERE id LIKE :p", p=f"{_PREFIX}%")
    _exec("DELETE FROM org_group WHERE slug = :g", g=_GROUP)
    _exec("DELETE FROM app_user WHERE email LIKE :p", p=f"{_PREFIX}%")


def _seed_user(email: str, *, status: str = "active") -> None:
    _exec(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "SELECT :e, :e, 'employee', :st, id FROM organization LIMIT 1 "
        "ON CONFLICT (lower(email)) DO UPDATE SET status = :st",
        e=email, st=status,
    )


def _seed_session(owner: str, *participants: tuple[str, str]) -> str:
    sid = f"{_PREFIX}-{uuid.uuid4().hex[:8]}"
    _exec(
        "INSERT INTO chat_session (id, user_id, agent_name) "
        "VALUES (:i, :owner, 'orchestrator')",
        i=sid, owner=owner,
    )
    _exec(
        "INSERT INTO chat_session_participant (session_id, subject, role) "
        "VALUES (:i, :s, 'owner') ON CONFLICT DO NOTHING",
        i=sid, s=owner,
    )
    for subject, role in participants:
        _exec(
            "INSERT INTO chat_session_participant (session_id, subject, role) "
            "VALUES (:i, :s, :r) ON CONFLICT (session_id, subject) "
            "DO UPDATE SET role = EXCLUDED.role",
            i=sid, s=subject, r=role,
        )
    return sid


@pytest.fixture
def clean():
    _purge()
    _seed_user(_ALICE)
    _seed_user(_BOB)
    _seed_user(_CAROL)
    yield
    _purge()


# ---------------------------------------------------------------------------
# 0. The two "we don't know" cases resolve in OPPOSITE directions
#
# These need no database: they pin what happens when the lookup itself cannot
# answer, which is precisely when a live database is not available.
# ---------------------------------------------------------------------------

def test_a_failed_lookup_grants_nothing(monkeypatch) -> None:
    """A database error used to resolve to full OWNER access on any session.

    That handed the caller read, send and cancel on a room that may well be
    somebody else's, at the one moment we are least able to say whose it is.
    """
    from gateway import rooms

    def _boom(session_id: str, email: str):
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(rooms, "_load_room", _boom)

    access = rooms.resolve_room_access("some-real-session", _BOB)
    assert not access.can_read
    assert not access.can_send
    assert not access.can_cancel
    assert not access.can_manage
    assert access.role is None
    assert access.resolve_failed


def test_a_failed_lookup_says_retry_rather_than_not_a_participant(
    monkeypatch,
) -> None:
    """The refusal must not blame the person for an outage."""
    from gateway import rooms

    def _boom(session_id: str, email: str):
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(rooms, "_load_room", _boom)

    message = rooms.resolve_room_access("some-real-session", _BOB).denied("send")
    assert "try again" in message.lower()
    assert "not a participant" not in message.lower()


def test_a_session_with_no_row_is_still_the_callers_own(monkeypatch) -> None:
    """The case that must NOT be swept up by the fail-closed change.

    A brand-new thread has no ``chat_session`` row until its first turn
    persists; a missing row belongs to nobody, so it belongs to whoever is
    asking. Failing this closed would break every new conversation.
    """
    from gateway import rooms

    monkeypatch.setattr(rooms, "_load_room", lambda session_id, email: None)

    access = rooms.resolve_room_access("brand-new-thread", _ALICE)
    assert access.can_read and access.can_send and access.can_cancel
    assert access.role == "owner"
    assert access.unknown_session
    assert not access.resolve_failed


# ---------------------------------------------------------------------------
# 1. Solo is unchanged
# ---------------------------------------------------------------------------

@_needs_db
def test_a_solo_session_is_its_owners_alone(clean) -> None:
    from gateway.rooms import resolve_room_access

    sid = _seed_session(_ALICE)

    mine = resolve_room_access(sid, _ALICE)
    assert mine.role == "owner"
    assert mine.can_read and mine.can_send and mine.can_manage
    assert not mine.is_shared

    theirs = resolve_room_access(sid, _BOB)
    assert theirs.role is None
    assert not theirs.can_read


@_needs_db
def test_a_session_that_does_not_exist_stays_permissive(clean) -> None:
    """A brand-new thread has no row until its first turn persists. Refusing
    it would break every new conversation, so it resolves as a room of one."""
    from gateway.rooms import resolve_room_access

    access = resolve_room_access("pytest-rooms-never-created", _ALICE)
    assert access.can_read and access.can_send
    assert access.unknown_session
    assert not access.is_shared


# ---------------------------------------------------------------------------
# 2. A viewer reads and cannot send
# ---------------------------------------------------------------------------

@_needs_db
def test_a_viewer_watches_but_cannot_send(clean) -> None:
    from gateway.rooms import resolve_room_access

    sid = _seed_session(_ALICE, (_BOB, "viewer"))

    bob = resolve_room_access(sid, _BOB)
    assert bob.role == "viewer"
    assert bob.can_read
    assert not bob.can_send
    assert not bob.can_cancel
    assert not bob.can_invite
    assert "viewer" in bob.denied("send")


@_needs_db
def test_a_member_sends_but_does_not_administer(clean) -> None:
    from gateway.rooms import resolve_room_access

    sid = _seed_session(_ALICE, (_BOB, "member"))

    bob = resolve_room_access(sid, _BOB)
    assert bob.can_read and bob.can_send and bob.can_cancel
    assert not bob.can_invite
    assert not bob.can_manage
    assert bob.is_shared


# ---------------------------------------------------------------------------
# 3-4. Membership, and groups that expand at read time
# ---------------------------------------------------------------------------

@_needs_db
def test_a_group_subject_lets_its_members_in_and_out(clean) -> None:
    from gateway.rooms import resolve_room_access

    _exec(
        "INSERT INTO org_group (organization_id, slug, display_name) "
        "SELECT id, :g, :g FROM organization LIMIT 1 ON CONFLICT DO NOTHING",
        g=_GROUP,
    )
    _exec(
        "INSERT INTO org_group_member (group_id, user_id) "
        "SELECT g.id, u.id FROM org_group g, app_user u "
        "WHERE g.slug = :g AND u.email = :e ON CONFLICT DO NOTHING",
        g=_GROUP, e=_BOB,
    )
    sid = _seed_session(_ALICE, (f"group:{_GROUP}", "member"))

    assert resolve_room_access(sid, _BOB).can_send
    assert not resolve_room_access(sid, _CAROL).can_read

    _exec(
        "DELETE FROM org_group_member WHERE user_id = "
        "(SELECT id FROM app_user WHERE email = :e)", e=_BOB,
    )
    # No fan-out write; the very next resolution is the truth.
    assert not resolve_room_access(sid, _BOB).can_read


@_needs_db
def test_the_most_capable_matching_subject_wins(clean) -> None:
    from gateway.rooms import resolve_room_access

    _exec(
        "INSERT INTO org_group (organization_id, slug, display_name) "
        "SELECT id, :g, :g FROM organization LIMIT 1 ON CONFLICT DO NOTHING",
        g=_GROUP,
    )
    _exec(
        "INSERT INTO org_group_member (group_id, user_id) "
        "SELECT g.id, u.id FROM org_group g, app_user u "
        "WHERE g.slug = :g AND u.email = :e ON CONFLICT DO NOTHING",
        g=_GROUP, e=_BOB,
    )
    # Bob is named an owner AND swept in by a viewers group.
    sid = _seed_session(_ALICE, (_BOB, "owner"), (f"group:{_GROUP}", "viewer"))

    assert resolve_room_access(sid, _BOB).role == "owner"


@_needs_db
def test_org_visibility_grants_read_but_never_send(clean) -> None:
    from gateway.rooms import resolve_room_access

    sid = _seed_session(_ALICE)
    _exec("UPDATE chat_session SET visibility = 'org' WHERE id = :i", i=sid)

    carol = resolve_room_access(sid, _CAROL)
    assert carol.can_read
    assert not carol.can_send
    # Looking is not joining: she holds no place in the room.
    assert not carol.is_member


# ---------------------------------------------------------------------------
# 5. The waterline
# ---------------------------------------------------------------------------

@_needs_db
def test_a_late_joiner_reads_from_their_join_point(clean) -> None:
    from gateway.rooms import resolve_room_access

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _exec(
        "UPDATE chat_session SET history_visibility = 'since_join' WHERE id = :i",
        i=sid,
    )
    _exec(
        "UPDATE chat_session_participant SET join_message_ts = 5000 "
        "WHERE session_id = :i AND subject = :s", i=sid, s=_BOB,
    )

    assert resolve_room_access(sid, _BOB).since_message_ts == 5000
    # The owner's own history is never withheld from them.
    assert resolve_room_access(sid, _ALICE).since_message_ts is None

    # A room showing full history ignores the recorded waterline rather than
    # discarding it — switching back must not be a retroactive disclosure of
    # something we failed to write down.
    _exec("UPDATE chat_session SET history_visibility = 'full' WHERE id = :i", i=sid)
    assert resolve_room_access(sid, _BOB).since_message_ts is None


# ---------------------------------------------------------------------------
# 6. A room always keeps an owner
# ---------------------------------------------------------------------------

@_needs_db
def test_the_last_owner_cannot_leave_or_be_demoted(clean) -> None:
    from fastapi import HTTPException
    from gateway.routes.rooms import _remove_participant, _update_participant_role

    sid = _seed_session(_ALICE, (_BOB, "member"))

    with pytest.raises(HTTPException) as exc:
        _remove_participant(sid, _ALICE)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException):
        _update_participant_role(sid, _ALICE, "viewer")

    # Promote Bob and Alice is free to go.
    assert _update_participant_role(sid, _BOB, "owner")
    assert _remove_participant(sid, _ALICE)


# ---------------------------------------------------------------------------
# 7. Authorship is set once
# ---------------------------------------------------------------------------

@_needs_db
def test_an_author_cannot_be_rewritten_by_a_later_writer(clean) -> None:
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    msg = MessageRecord(id="m1", role="user", content="hello", timestamp=1000)

    _upsert_messages(sid, [msg], actor_email=_ALICE)
    # Bob re-POSTs the same conversation — the browser does this constantly.
    _upsert_messages(sid, [msg], actor_email=_BOB)

    rows = _exec(
        "SELECT author_email, author_kind FROM chat_message "
        "WHERE session_id = :i AND id = 'm1'", i=sid,
    )
    assert rows[0].author_email == _ALICE
    assert rows[0].author_kind == "human"


@_needs_db
def test_an_agent_turn_is_attributed_to_the_agent(clean) -> None:
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _upsert_messages(
        sid,
        [MessageRecord(id="a1", role="assistant", content="hi", timestamp=1001)],
        actor_email=_ALICE, agent_name="agent-sales-assistant",
    )

    rows = _exec(
        "SELECT author_email, author_kind FROM chat_message "
        "WHERE session_id = :i AND id = 'a1'", i=sid,
    )
    assert rows[0].author_kind == "agent"
    assert rows[0].author_email == "agent-sales-assistant"


@_needs_db
def test_a_checkpoint_author_wins_over_the_room_agent(clean) -> None:
    """The chat translator's checkpoint names the agent that ran (WS-27bm S10).

    `lib/assistantCheckpoint.ts` sends ``author_email`` on each checkpoint.
    It must beat the room's agent, and a later write with no author (the
    browser re-POSTing its list) must keep it.
    """
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _upsert_messages(
        sid,
        [MessageRecord(
            id="c1", role="assistant", content="partial", timestamp=1002,
            author_kind="agent", author_email="projects-assistant",
        )],
        actor_email=_ALICE, agent_name="orchestrator",
    )
    _upsert_messages(
        sid,
        [MessageRecord(id="c1", role="assistant", content="final", timestamp=1003)],
        actor_email=_ALICE, agent_name="orchestrator",
    )

    rows = _exec(
        "SELECT author_email, author_kind, content FROM chat_message "
        "WHERE session_id = :i AND id = 'c1'", i=sid,
    )
    assert rows[0].author_kind == "agent"
    assert rows[0].author_email == "projects-assistant"
    assert rows[0].content == "final"


def _addressed_turn(sid: str) -> None:
    """An ``@sales`` turn: the checkpoint claims no author (S10 fix round 1),
    and then the gateway's fold writes with the addressed agent."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    # The translator's checkpoint. The route passes the room's agent.
    _upsert_messages(
        sid,
        [MessageRecord(id="c2", role="assistant", content="partial", timestamp=1004)],
        actor_email=_ALICE, agent_name="orchestrator",
    )
    # chat_fold.persist_final_assistant_message, with `_address_agent`'s answer.
    # The fold is the one writer that passes `author_from_run` (S12).
    _upsert_messages(
        sid,
        [MessageRecord(id="c2", role="assistant", content="final", timestamp=1005)],
        actor_email=_ALICE, agent_name="sales-assistant", author_from_run=True,
    )


def _author(sid: str, mid: str):
    return _exec(
        "SELECT author_email, author_kind FROM chat_message "
        "WHERE session_id = :i AND id = :m", i=sid, m=mid,
    )[0]


def _content(sid: str, mid: str) -> str:
    return _exec(
        "SELECT content FROM chat_message WHERE session_id = :i AND id = :m",
        i=sid, m=mid,
    )[0].content


@_needs_db
def test_an_addressed_turn_is_stamped_with_the_agent_that_ran(clean) -> None:
    """WS-27bm S12 (§18). The checkpoint writes first with the room's agent.
    The fold knows which agent ran, and it may set the author again. The
    client-side half (no author on an ``@`` turn) is fenced in
    ``assistantCheckpoint.test.ts``."""
    sid = _seed_session(_ALICE)
    _addressed_turn(sid)
    row = _author(sid, "c2")
    assert row.author_email == "sales-assistant"
    assert row.author_kind == "agent"
    # An agent row still takes the fold's content (fix round 1 keeps it).
    assert _content(sid, "c2") == "final"


@_needs_db
def test_a_client_save_after_the_fold_keeps_the_agent_that_ran(clean) -> None:
    """§18.2 rule 9. A client write keeps the COALESCE, so a later save that
    names another agent cannot undo the fold."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _addressed_turn(sid)
    # The browser re-POSTs its list, and claims a different agent.
    _upsert_messages(
        sid,
        [MessageRecord(
            id="c2", role="assistant", content="final", timestamp=1006,
            author_kind="agent", author_email="projects-assistant",
        )],
        actor_email=_ALICE, agent_name="orchestrator",
    )
    assert _author(sid, "c2").author_email == "sales-assistant"


@_needs_db
def test_the_fold_never_rewrites_a_human_turn(clean) -> None:
    """§18.2 rule 8. ``_persist_message_id`` comes from the client, so the
    fold can reach a human turn's id. The human keeps the turn."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _upsert_messages(
        sid,
        [MessageRecord(id="h1", role="user", content="hello", timestamp=1007)],
        actor_email=_ALICE,
    )
    # The fold, aimed at that id.
    _upsert_messages(
        sid,
        [MessageRecord(id="h1", role="assistant", content="answer", timestamp=1008)],
        actor_email=_ALICE, agent_name="sales-assistant", author_from_run=True,
    )
    row = _author(sid, "h1")
    assert row.author_kind == "human"
    assert row.author_email == _ALICE
    # Fix round 1: the fold leaves the whole row alone, content included.
    assert _content(sid, "h1") == "hello"


# ---------------------------------------------------------------------------
# 7b. One member cannot write in another member's turn (S12 fix round 1)
# ---------------------------------------------------------------------------

def _save_as(sid: str, email: str, messages: list) -> dict:
    """The real route handler, with the caller already authenticated."""
    import asyncio

    from acb_auth import UserContext
    from acb_auth.roles import UserRole
    from gateway.routes.chat import save_messages

    user = UserContext(email=email, role=UserRole.EMPLOYEE)
    return asyncio.run(save_messages(sid, messages, user=user))


def _alice_said_hello(sid: str) -> None:
    from gateway.routes.chat import MessageRecord

    _save_as(sid, _ALICE, [
        MessageRecord(id="u1", role="user", content="hello", timestamp=2000),
    ])


@_needs_db
def test_a_member_cannot_overwrite_another_members_turn(clean) -> None:
    """Bob reads the id of a turn by Alice from the history, and POSTs his own
    words under it. The row keeps her words and her name."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _save_as(sid, _BOB, [
        MessageRecord(id="u1", role="user", content="I approve the budget cut",
                      timestamp=2001),
    ])
    assert _content(sid, "u1") == "hello"
    row = _author(sid, "u1")
    assert (row.author_kind, row.author_email) == ("human", _ALICE)


@_needs_db
def test_a_member_cannot_claim_an_agent_turn_to_pass_as_the_author(clean) -> None:
    """A body that claims an agent turn names its own ``author_email``. It
    must not pass the own-author check by naming Alice."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _save_as(sid, _BOB, [
        MessageRecord(id="u1", role="assistant", content="forged", timestamp=2002,
                      author_kind="agent", author_email=_ALICE),
    ])
    assert _content(sid, "u1") == "hello"
    row = _author(sid, "u1")
    assert (row.author_kind, row.author_email) == ("human", _ALICE)


@_needs_db
def test_the_fold_cannot_overwrite_a_members_turn(clean) -> None:
    """The same attack through a client-chosen ``assistant_message_id``."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _upsert_messages(
        sid,
        [MessageRecord(id="u1", role="assistant", content="forged", timestamp=2003)],
        actor_email=_BOB, agent_name="sales-assistant", author_from_run=True,
    )
    assert _content(sid, "u1") == "hello"
    row = _author(sid, "u1")
    assert (row.author_kind, row.author_email) == ("human", _ALICE)


@_needs_db
def test_a_member_can_still_update_their_own_turn(clean) -> None:
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _save_as(sid, _ALICE, [
        MessageRecord(id="u1", role="user", content="hello, all", timestamp=2004),
    ])
    assert _content(sid, "u1") == "hello, all"
    assert _author(sid, "u1").author_email == _ALICE


@_needs_db
def test_an_agent_turn_still_updates_by_checkpoint_and_by_fold(clean) -> None:
    """S13 rewrite. Alice's run owns the reply. A checkpoint from Bob, another
    sender in the room, is declined and named in ``unchanged``. Alice's fold
    still takes."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _save_as(sid, _ALICE, [
        MessageRecord(id="a9", role="assistant", content="part", timestamp=2005),
    ])
    out = _save_as(sid, _BOB, [
        MessageRecord(id="a9", role="assistant", content="more", timestamp=2006),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["a9"]}
    assert _content(sid, "a9") == "part"
    declined = _upsert_messages(
        sid,
        [MessageRecord(id="a9", role="assistant", content="done", timestamp=2007)],
        actor_email=_ALICE, agent_name="sales-assistant", author_from_run=True,
    )
    assert declined == []
    assert _content(sid, "a9") == "done"
    assert _author(sid, "a9").author_email == "sales-assistant"


def _legacy_row(sid: str, mid: str, role: str, content: str) -> None:
    """A row from before authorship existed: no kind and no author."""
    _exec(
        "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms) "
        "VALUES (:m, :i, :r, :c, 2100)",
        m=mid, i=sid, r=role, c=content,
    )


@_needs_db
def test_a_legacy_human_turn_with_no_kind_is_protected(clean) -> None:
    """P2. A NULL kind on a ``user`` row is a human turn, not an agent turn."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _legacy_row(sid, "old-u", "user", "the old words")
    _save_as(sid, _BOB, [
        MessageRecord(id="old-u", role="user", content="forged", timestamp=2101),
    ])
    _upsert_messages(
        sid,
        [MessageRecord(id="old-u", role="assistant", content="forged", timestamp=2102)],
        actor_email=_BOB, agent_name="sales-assistant", author_from_run=True,
    )
    assert _content(sid, "old-u") == "the old words"
    row = _author(sid, "old-u")
    assert (row.author_kind, row.author_email) == (None, None)


@_needs_db
def test_a_legacy_agent_turn_with_no_kind_takes_the_fold(clean) -> None:
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _legacy_row(sid, "old-a", "assistant", "partial")
    _upsert_messages(
        sid,
        [MessageRecord(id="old-a", role="assistant", content="final", timestamp=2103)],
        actor_email=_ALICE, agent_name="sales-assistant", author_from_run=True,
    )
    assert _content(sid, "old-a") == "final"
    row = _author(sid, "old-a")
    assert (row.author_kind, row.author_email) == ("agent", "sales-assistant")


@_needs_db
def test_the_fold_keeps_a_legacy_system_row(clean) -> None:
    """S13. It replaces ``test_the_fold_keeps_the_author_of_a_legacy_system_row``.

    A NULL kind with the role ``system`` is a system row, and no write updates
    a system row. So the fold keeps its content, its author and its NULL kind.
    """
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _legacy_row(sid, "old-s", "system", "joined")
    _exec(
        "UPDATE chat_message SET author_email = 'legacy@x.io' "
        "WHERE session_id = :i AND id = 'old-s'", i=sid,
    )
    declined = _upsert_messages(
        sid,
        [MessageRecord(id="old-s", role="assistant", content="final", timestamp=2104)],
        actor_email=_ALICE, agent_name="sales-assistant", author_from_run=True,
    )
    assert declined == ["old-s"]
    assert _content(sid, "old-s") == "joined"
    row = _author(sid, "old-s")
    assert (row.author_kind, row.author_email) == (None, "legacy@x.io")


# ---------------------------------------------------------------------------
# 7c. Only the run changes an agent reply, and the fold seals it (S13, §19)
# ---------------------------------------------------------------------------

def _run_state(sid: str, mid: str):
    return _exec(
        "SELECT run_member_email, run_final_at FROM chat_message "
        "WHERE session_id = :i AND id = :m", i=sid, m=mid,
    )[0]


def _fold(sid: str, mid: str, starter: str, content: str) -> list[str]:
    """The upsert exactly as ``chat_fold`` calls it. The starter of the run is
    the fold's ``user_id``."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    return _upsert_messages(
        sid,
        [MessageRecord(id=mid, role="assistant", content=content, timestamp=3009)],
        actor_email=starter, agent_name="sales-assistant", author_from_run=True,
    )


def _alice_starts_a_run(sid: str) -> None:
    """The translator's first checkpoint, as the member who sent the turn."""
    from gateway.routes.chat import MessageRecord

    out = _save_as(sid, _ALICE, [
        MessageRecord(id="r1", role="assistant", content="part", timestamp=3000),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": []}


@_needs_db
def test_a_room_owner_cannot_change_another_members_agent_reply(clean) -> None:
    """§19.4 rule 1. Bob owns the room. Alice starts the run. Bob saves over
    the reply, and nothing changes."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_BOB, (_ALICE, "member"))
    _alice_starts_a_run(sid)
    assert _run_state(sid, "r1").run_member_email == _ALICE.lower()
    out = _save_as(sid, _BOB, [
        MessageRecord(id="r1", role="assistant", content="forged", timestamp=3001),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["r1"]}
    assert _content(sid, "r1") == "part"
    # The first writer's run member stays.
    assert _run_state(sid, "r1").run_member_email == _ALICE.lower()


@_needs_db
def test_the_run_member_updates_her_reply_before_the_fold(clean) -> None:
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_starts_a_run(sid)
    # A second checkpoint, from the member who started the run.
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="r1", role="assistant", content="part and more",
                      timestamp=3002),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": []}
    assert _content(sid, "r1") == "part and more"
    assert _run_state(sid, "r1").run_final_at is None


@_needs_db
def test_the_fold_seals_the_reply(clean) -> None:
    """§19.4 rule 2. After the fold, a save of an old copy by the run member
    leaves the fold's content."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_starts_a_run(sid)
    assert _fold(sid, "r1", _ALICE, "the final answer") == []
    assert _run_state(sid, "r1").run_final_at is not None
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="r1", role="assistant", content="part", timestamp=3003),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["r1"]}
    assert _content(sid, "r1") == "the final answer"


@_needs_db
def test_a_fold_by_another_starter_does_not_change_the_reply(clean) -> None:
    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_starts_a_run(sid)
    assert _fold(sid, "r1", _BOB, "forged") == ["r1"]
    assert _content(sid, "r1") == "part"
    state = _run_state(sid, "r1")
    assert state.run_member_email == _ALICE.lower()
    assert state.run_final_at is None


@_needs_db
def test_the_real_fold_logs_a_declined_write_and_returns_the_message(
    clean, monkeypatch,
) -> None:
    """``persist_final_assistant_message`` itself, with the Redis replay and
    the fold stubbed. Bob's run aims at Alice's reply. The row stays, the
    fold logs ``chat_fold.persist_declined``, and it still returns the
    folded message."""
    import asyncio

    import structlog
    from gateway import chat_fold, run_trace
    from orchestrator import stream_relay

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_starts_a_run(sid)
    folded = {
        "content": "forged", "timestamp": 3004, "tool_events": [],
        "progress_lines": [], "reasoning": None, "agent_state": None,
        "custom_events": [],
    }

    async def _replay(*_a, **_k):
        return [{"type": "RUN_STARTED"}]

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(stream_relay, "replay_events", _replay)
    monkeypatch.setattr(run_trace, "record_run_trace", _none)
    monkeypatch.setattr(chat_fold, "_run_authority", _none)
    monkeypatch.setattr(chat_fold, "fold_run_events", lambda _e: dict(folded))

    with structlog.testing.capture_logs() as caps:
        out = asyncio.run(chat_fold.persist_final_assistant_message(
            sid, "r1", user_id=_BOB, agent_name="sales-assistant",
        ))
    assert out == folded
    events = [c.get("event") for c in caps]
    assert "chat_fold.persist_declined" in events
    assert "chat_fold.persisted" not in events
    assert _content(sid, "r1") == "part"

    # Alice's own run folds through the same call.
    with structlog.testing.capture_logs() as caps:
        asyncio.run(chat_fold.persist_final_assistant_message(
            sid, "r1", user_id=_ALICE, agent_name="sales-assistant",
        ))
    assert "chat_fold.persisted" in [c.get("event") for c in caps]
    assert _content(sid, "r1") == "forged"


@_needs_db
def test_a_reply_with_no_run_member_takes_only_the_fold(clean) -> None:
    """§19.4 rule 3. A legacy agent row, or one that old code wrote during the
    deploy, has no run member. No client save changes it. The fold does, and
    it stamps the run member."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _legacy_row(sid, "old-r", "assistant", "partial")
    for who in (_ALICE, _BOB):
        out = _save_as(sid, who, [
            MessageRecord(id="old-r", role="assistant", content="forged",
                          timestamp=3005),
        ])
        assert out["unchanged"] == ["old-r"]
    assert _content(sid, "old-r") == "partial"
    assert _fold(sid, "old-r", _ALICE, "final") == []
    assert _content(sid, "old-r") == "final"
    state = _run_state(sid, "old-r")
    assert state.run_member_email == _ALICE.lower()
    assert state.run_final_at is not None


@_needs_db
def test_a_human_save_on_an_agent_reply_does_not_change_it(clean) -> None:
    """Alice started the run, and she still cannot turn the reply into a
    human turn by sending a ``user`` row with its id."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE)
    _alice_starts_a_run(sid)
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="r1", role="user", content="mine now", timestamp=3006),
    ])
    assert out["unchanged"] == ["r1"]
    assert _content(sid, "r1") == "part"
    assert _author(sid, "r1").author_kind == "agent"


@_needs_db
def test_a_client_inserts_a_system_row_and_never_updates_one(clean) -> None:
    """§19.4 rule 4."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE)
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="s1", role="system", content="joined", timestamp=3007),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": []}
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="s1", role="system", content="changed", timestamp=3008),
        MessageRecord(id="s2", role="system", content="left", timestamp=3008),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": ["s1"]}
    assert _content(sid, "s1") == "joined"
    assert _content(sid, "s2") == "left"


@_needs_db
def test_unchanged_names_every_declined_id_in_request_order(clean) -> None:
    """§19.3 item 6. A batch mixes rows that change and rows that do not."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _alice_starts_a_run(sid)
    out = _save_as(sid, _BOB, [
        MessageRecord(id="r1", role="assistant", content="x", timestamp=3010),
        MessageRecord(id="b1", role="user", content="mine", timestamp=3011),
        MessageRecord(id="u1", role="user", content="x", timestamp=3012),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": ["r1", "u1"]}
    assert _content(sid, "b1") == "mine"


@_needs_db
def test_a_run_with_no_message_id_cannot_be_preempted(clean) -> None:
    """S13 fix round 1 (§19.4 rule 8). A caller that sends no
    ``assistant_message_id`` gets a fold id that the server mints.

    The old fallback was ``assistant-{thread}-{run_id}``, and RUN_STARTED
    publishes ``runId`` to the room. Bob inserts that id first and becomes
    its run member. With the old shape, S13's WHERE then declined the real
    fold, and Bob's forged reply stayed. Now the fold writes an id that Bob
    cannot know, and the real reply is stored.
    """
    from gateway.routes.agent import fold_message_id
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    run_id = "run-published-in-run-started"
    guessed = f"assistant-{sid}-{run_id}"
    out = _save_as(sid, _BOB, [
        MessageRecord(id=guessed, role="assistant", content="forged",
                      timestamp=3013, author_kind="agent",
                      author_email="sales-assistant"),
    ])
    assert out["unchanged"] == []

    mid = fold_message_id(None, sid)
    assert mid != guessed and run_id not in mid
    assert mid.startswith(f"assistant-{sid}-")
    assert fold_message_id(None, sid) != mid  # fresh for each run
    assert _fold(sid, mid, _ALICE, "the real reply") == []
    assert _content(sid, mid) == "the real reply"
    row = _author(sid, mid)
    assert (row.author_kind, row.author_email) == ("agent", "sales-assistant")
    assert _run_state(sid, mid).run_member_email == _ALICE.lower()


def test_the_caller_id_wins_and_the_run_route_uses_the_helper() -> None:
    """The source half of the test above. The route mints the fold id once,
    through ``fold_message_id``, and the ``run_id`` shape is gone."""
    import inspect

    from gateway.routes import agent

    assert agent.fold_message_id("client-nanoid", "t1") == "client-nanoid"
    route = inspect.getsource(agent.run_agent_stream_endpoint)
    assert (
        "_persist_message_id = fold_message_id(req.assistant_message_id, thread_id)"
        in route
    )
    assert route.count("fold_message_id(") == 1
    assert 'f"assistant-{thread_id}-{run_id}"' not in inspect.getsource(agent)


def test_only_the_fold_passes_author_from_run() -> None:
    """§18.2 rule 7, by source. The fold passes True, and the route handler
    that saves the client's messages never names the keyword."""
    import inspect

    from gateway import chat_fold
    from gateway.routes import chat

    fold = inspect.getsource(chat_fold.persist_final_assistant_message)
    assert "author_from_run=True" in fold
    route = inspect.getsource(chat.save_messages)
    assert "_upsert_messages" in route
    assert "author_from_run" not in route
    # No other file in the gateway names the keyword.
    import pathlib

    root = pathlib.Path(chat.__file__).resolve().parents[1]
    users = sorted(
        str(f.relative_to(root)).replace("\\", "/")
        for f in root.rglob("*.py")
        if "author_from_run" in f.read_text(encoding="utf-8")
    )
    assert users == ["chat_fold.py", "routes/chat.py"], users
    param = inspect.signature(chat._upsert_messages).parameters["author_from_run"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


# ---------------------------------------------------------------------------
# 8. The clearance filter
# ---------------------------------------------------------------------------

@_needs_db
def test_a_turn_above_your_clearance_comes_back_as_a_stub(clean) -> None:
    """A late joiner must not read the output of a run they weren't cleared for.

    The check is on the RENDERED row, not on a UI flag: content, tools, and
    reasoning all have to go, because any of them can carry the restricted
    fact the run retrieved.
    """
    from gateway.routes.chat import REDACTION_NOTICE, _render_message

    class _Row:
        id, role, content, timestamp_ms = "m9", "assistant", "the deal is 4cr", 9000
        tool_events = [{"name": "zoho_query"}]
        progress_lines: list = []
        reasoning = "checked Zoho"
        agent_state = None
        custom_events: list = []
        author_email, author_kind = "agent-sales-assistant", "agent"
        authority = {"members": [_ALICE], "caps": ["integrations:use:zoho-crm"]}

    # Alice was in the room when it ran — she has already seen it.
    assert _render_message(_Row(), _ALICE, frozenset())["content"] == "the deal is 4cr"

    # Carol wasn't, and does not hold the capability the run used.
    stub = _render_message(_Row(), _CAROL, frozenset({"integrations:use:clickup"}))
    assert stub["redacted"] is True
    assert stub["content"] == REDACTION_NOTICE
    assert stub["toolEvents"] == []
    assert stub["reasoning"] is None
    assert stub["redactedCaps"] == ["integrations:use:zoho-crm"]

    # Carol holding the same capability reads it normally.
    ok = _render_message(_Row(), _CAROL, frozenset({"integrations:use:zoho-crm"}))
    assert ok.get("redacted") is None
    assert ok["content"] == "the deal is 4cr"


@_needs_db
def test_solo_and_legacy_rows_are_never_filtered(clean) -> None:
    """``authority`` is NULL on every solo and pre-135 row, so the filter is a
    no-op for them rather than something they have to pass."""
    from gateway.routes.chat import _render_message

    class _Row:
        id, role, content, timestamp_ms = "m0", "assistant", "plain", 1
        tool_events: list = []
        progress_lines: list = []
        reasoning = None
        agent_state = None
        custom_events: list = []
        author_email, author_kind = "orchestrator", "agent"
        authority = None

    out = _render_message(_Row(), _CAROL, frozenset())
    assert out["content"] == "plain"
    assert "redacted" not in out


# ---------------------------------------------------------------------------
# 9. Personal memory never enters a shared room
# ---------------------------------------------------------------------------

@_needs_db
def test_the_run_path_is_wired_to_the_run_clearance() -> None:
    """The rule with the quietest failure mode, pinned where it is applied.

    *What* a shared room may read and write is decided by
    ``acb_memory.resolve_clearance`` and tested against that function directly
    (``tests/unit/test_memory_compartments.py``) — a pure function, so the
    properties are assertable without a model, a Redis, or an agent registry.

    What THIS asserts is that the streaming endpoint actually consults it at
    all four seams. The decision being right is worthless if a seam still reads
    the caller's own compartment, and the seams sit inside a 300-line handler
    whose real execution needs the whole stack — so they are checked in the
    source. The specific thing that must never come back: `_mem_user` naming
    the session owner and the acting member for authorship, which is why the
    memory target is a SEPARATE variable rather than that one blanked out.
    """
    import inspect

    from gateway.routes import agent as agent_routes

    src = inspect.getsource(agent_routes.run_agent_stream_endpoint)

    # 1. The clearance is resolved from the room's sharedness, once.
    assert "resolve_clearance(" in src
    assert "shared=_room_is_shared," in src
    # 2. The memory TOOLS read and write the run's compartment — in a room the
    #    room's, so remember/save_memory keep working and file what they learn
    #    where the room can see it.
    assert "_set_memory_user_id(_clearance.write)" in src
    # 3. The prompt block reads the room and prefs compartments, and the
    #    personal one only when the run is not shared.
    assert '_has_user = user_id != "anonymous" and not _room_is_shared' in src
    assert "if _clearance.room:" in src
    assert "if _clearance.prefs:" in src
    # 4. The cached block is keyed by clearance, or a thread cached while solo
    #    would serve the owner's private block to the room for the whole TTL.
    assert "clearance=_clearance.fingerprint," in src
    # 5. Run-boundary extraction targets the compartment, not the typer...
    assert "_extract_user = _clearance.write if _room_is_shared else _mem_user" in src
    # ...and the session owner / authorship actor is still a real email.
    assert '_mem_user = (user.email or "").strip()' in src
