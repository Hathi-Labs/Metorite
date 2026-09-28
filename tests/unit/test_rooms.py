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
    # S14: the gateway mints the row, and the checkpoint then fills it.
    _mint(sid, "a1", _ALICE, "agent-sales-assistant")
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
    browser re-POSTing its list) must keep it. Since S14 the mint names the
    agent that runs first, and the checkpoint agrees with it.
    """
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _seed_session(_ALICE)
    _mint(sid, "c1", _ALICE, "projects-assistant")
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

    # S14: the gateway mints the row. This one names the room's agent, so
    # that the fold below still has an author to set again.
    _mint(sid, "c2", _ALICE, "orchestrator")
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


def _rows(sid: str, mid: str) -> list:
    return _exec(
        "SELECT id FROM chat_message WHERE session_id = :i AND id = :m",
        i=sid, m=mid,
    )


def _mint(sid: str, mid: str, member: str, agent: str = "projects-assistant") -> None:
    """The real mint that ``/agent/run/stream`` calls (WS-27bm S14, §20)."""
    from gateway.routes.agent import _mint_run_row

    _mint_run_row(sid, mid, member=member, agent_name=agent)


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
    _mint(sid, "a9", _ALICE)
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
    """The mint, then the translator's first checkpoint, as the member who
    sent the turn (S14)."""
    from gateway.routes.chat import MessageRecord

    _mint(sid, "r1", _ALICE)
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
def test_no_client_inserts_or_updates_a_system_row(clean) -> None:
    """S14 (§20.4 rule 1) replaces §19.4 rule 4. A system row reaches the
    model context of every member, so only the server may create one. A
    client insert gives no row, and ``unchanged`` names it."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    for who in (_BOB, _ALICE):
        out = _save_as(sid, who, [
            MessageRecord(id="s1", role="system", content="joined",
                          timestamp=3007),
        ])
        assert out == {"ok": True, "saved": 0, "unchanged": ["s1"]}
        assert not _rows(sid, "s1")
    # A system row that the server wrote still takes no client update.
    _legacy_row(sid, "s2", "system", "left")
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="s2", role="system", content="changed", timestamp=3008),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["s2"]}
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
    cannot know, and the real reply is stored. Since S14, Bob's insert
    also gives no row.
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
    assert out["unchanged"] == [guessed]
    assert not _rows(sid, guessed)

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
# 7d. No forged agent rows: only the server creates one (S14, §20)
# ---------------------------------------------------------------------------

def _mint_state(sid: str, mid: str):
    return _exec(
        "SELECT content, author_email, author_kind, run_member_email, "
        "run_final_at FROM chat_message WHERE session_id = :i AND id = :m",
        i=sid, m=mid,
    )[0]


@_needs_db
def test_a_client_cannot_insert_an_agent_row(clean) -> None:
    """§20.4 rule 1. Bob names an agent on a new row, in two ways. No row
    appears, so no forged reply reaches the model context of the room."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    out = _save_as(sid, _BOB, [
        MessageRecord(id="f1", role="assistant", content="forged",
                      timestamp=4000, author_email="projects-assistant"),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["f1"]}
    assert not _rows(sid, "f1")
    out = _save_as(sid, _BOB, [
        MessageRecord(id="f2", role="user", content="forged", timestamp=4001,
                      author_kind="agent", author_email="projects-assistant"),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["f2"]}
    assert not _rows(sid, "f2")
    # A new human row still inserts, in the same batch as a declined one.
    out = _save_as(sid, _BOB, [
        MessageRecord(id="f3", role="assistant", content="forged", timestamp=4002),
        MessageRecord(id="h3", role="user", content="mine", timestamp=4003),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": ["f3"]}
    assert _author(sid, "h3").author_email == _BOB


@_needs_db
def test_the_mint_creates_the_run_row(clean) -> None:
    """§20.3. Alice starts a run. The row has no content, the agent is its
    author, and Alice is its run member. Nothing seals it yet."""
    sid = _seed_session(_ALICE, (_BOB, "member"))
    _mint(sid, "m1", _ALICE.upper())
    row = _mint_state(sid, "m1")
    assert row.content == ""
    assert (row.author_kind, row.author_email) == ("agent", "projects-assistant")
    assert row.run_member_email == _ALICE.lower()
    assert row.run_final_at is None


@_needs_db
def test_only_the_run_member_checkpoints_a_minted_row(clean) -> None:
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _mint(sid, "m1", _ALICE)
    out = _save_as(sid, _BOB, [
        MessageRecord(id="m1", role="assistant", content="forged", timestamp=4010),
    ])
    assert out == {"ok": True, "saved": 0, "unchanged": ["m1"]}
    assert _content(sid, "m1") == ""
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="m1", role="assistant", content="part", timestamp=4011),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": []}
    assert _content(sid, "m1") == "part"


@_needs_db
def test_the_fold_takes_and_seals_a_minted_row(clean) -> None:
    sid = _seed_session(_ALICE, (_BOB, "member"))
    _mint(sid, "m1", _ALICE)
    assert _fold(sid, "m1", _ALICE, "the final answer") == []
    row = _mint_state(sid, "m1")
    assert row.content == "the final answer"
    assert row.author_email == "sales-assistant"
    assert row.run_final_at is not None


@_needs_db
def test_the_fold_inserts_the_row_when_the_mint_failed(clean, monkeypatch) -> None:
    """§20.4 rule 3. The mint is best effort. It logs ``agent.mint_failed``,
    raises nothing, and the fold then inserts the row."""
    import structlog
    from gateway.routes import chat

    sid = _seed_session(_ALICE)

    def _boom(*_a, **_k):
        raise RuntimeError("database gone")

    with monkeypatch.context() as m:
        m.setattr(chat, "_upsert_messages", _boom)
        with structlog.testing.capture_logs() as caps:
            _mint(sid, "m1", _ALICE)
    assert "agent.mint_failed" in [c.get("event") for c in caps]
    assert not _rows(sid, "m1")
    assert _fold(sid, "m1", _ALICE, "the final answer") == []
    row = _mint_state(sid, "m1")
    assert row.content == "the final answer"
    assert row.run_member_email == _ALICE.lower()


@_needs_db
def test_a_mint_on_an_existing_id_changes_nothing(clean) -> None:
    """§20.3 item 2. A caller names the id through ``assistant_message_id``.
    A mint on a human row, a system row, the reply of another member's run,
    or Alice's own reply before its seal leaves each row as it was. The last
    case is the one the S13 rules alone would let through."""
    from gateway.routes.chat import MessageRecord

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _legacy_row(sid, "s1", "system", "joined")
    _mint(sid, "r1", _BOB)
    _save_as(sid, _BOB, [
        MessageRecord(id="r1", role="assistant", content="bob's", timestamp=4020),
    ])
    _mint(sid, "r2", _ALICE)
    _save_as(sid, _ALICE, [
        MessageRecord(id="r2", role="assistant", content="alice's",
                      timestamp=4021),
    ])
    before = {mid: _mint_state(sid, mid) for mid in ("u1", "s1", "r1", "r2")}
    for mid in before:
        _mint(sid, mid, _ALICE, "sales-assistant")
    for mid, row in before.items():
        assert _mint_state(sid, mid) == row, mid
    assert before["r1"].content == "bob's"
    assert before["r2"].content == "alice's"


@_needs_db
def test_a_reader_does_not_see_an_empty_minted_row(clean) -> None:
    """§20.3 item 6. The minted row is hidden until it holds something."""
    from gateway.routes.chat import MessageRecord, _get_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    _alice_said_hello(sid)
    _mint(sid, "m1", _ALICE)

    def _ids(limit=None) -> list[str]:
        return [m["id"] for m in _get_messages(sid, _BOB, limit)]

    assert _ids() == ["u1"]
    # The LIMIT window does not count the hidden row.
    assert _ids(1) == ["u1"]
    _save_as(sid, _ALICE, [
        MessageRecord(id="m1", role="assistant", content="part", timestamp=2001),
    ])
    assert _ids() == ["u1", "m1"]


def test_only_the_run_route_mints_and_only_after_the_refusal() -> None:
    """§20.4 rule 1, by source. ``mint=True`` lives in ``routes/agent.py``
    alone. The endpoint mints once, after the steer decision and after
    ``_refuse_if_another_run_is_active``."""
    import inspect
    import pathlib

    from gateway.routes import agent, chat

    route = inspect.getsource(agent.run_agent_stream_endpoint)
    assert route.count("_mint_run_row") == 1
    # Fix round 1: the route awaits the bounded helper, and only it.
    assert route.count("await _mint_run_row_bounded(") == 1
    bounded = inspect.getsource(agent._mint_run_row_bounded)
    assert "asyncio.wait_for(" in bounded and "_MINT_TIMEOUT_S" in bounded
    mint_at = route.index("_mint_run_row")
    assert route.index("return _steered") < mint_at
    assert route.index("await _refuse_if_another_run_is_active(") < mint_at
    assert mint_at < route.index("StreamingResponse(")
    assert "mint=True" in inspect.getsource(agent._mint_run_row)
    assert "mint" not in inspect.getsource(chat.save_messages)

    root = pathlib.Path(chat.__file__).resolve().parents[1]
    users = sorted(
        str(f.relative_to(root)).replace("\\", "/")
        for f in root.rglob("*.py")
        if "mint=True" in f.read_text(encoding="utf-8")
    )
    assert users == ["routes/agent.py"], users
    assert inspect.getsource(agent).count("mint=True") == 1
    param = inspect.signature(chat._upsert_messages).parameters["mint"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


# ---------------------------------------------------------------------------
# 7e. S14 fix round 1: roles, room roles, reply order, the mint timeout
# ---------------------------------------------------------------------------

def _post_raw(sid: str, email: str, body: list):
    """The real ``save_messages`` handler behind FastAPI, so the body is
    parsed and validated as a request is. Returns the response."""
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import save_messages

    app = FastAPI()
    app.post("/m/{session_id}")(save_messages)
    user = UserContext(email=email, role=UserRole.EMPLOYEE)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app).post(f"/m/{sid}", json=body)


@_needs_db
@pytest.mark.parametrize("role", ["tool", "Assistant", "system ", "developer"])
def test_a_free_form_role_gets_422_and_no_row(clean, role) -> None:
    """Fix round 1, P1. The browser draws every role that is not ``user`` as
    an agent reply. So a role outside the three is refused before any row is
    written."""
    sid = _seed_session(_ALICE, (_BOB, "member"))
    res = _post_raw(sid, _BOB, [
        {"id": "x1", "role": role, "content": "The budget is approved.",
         "timestamp": 5000},
    ])
    assert res.status_code == 422
    assert not _rows(sid, "x1")
    # A normal human row through the same handler still inserts.
    res = _post_raw(sid, _BOB, [
        {"id": "x2", "role": "user", "content": "hello", "timestamp": 5001},
    ])
    assert res.status_code == 200
    assert res.json() == {"ok": True, "saved": 1, "unchanged": []}
    assert _author(sid, "x2").author_email == _BOB


def _participants(sid: str) -> dict:
    return {
        r.subject: r.role for r in _exec(
            "SELECT subject, role FROM chat_session_participant "
            "WHERE session_id = :i", i=sid,
        )
    }


def _agents(sid: str) -> dict:
    return {
        r.agent_name: r.role for r in _exec(
            "SELECT agent_name, role FROM chat_session_agent "
            "WHERE session_id = :i", i=sid,
        )
    }


@_needs_db
def test_a_group_member_who_starts_a_run_gains_no_owner_row(clean) -> None:
    """Fix round 1, P1. Bob reaches the room only through a group. The mint
    runs ``_ensure_session`` as Bob, and Bob must not become an owner."""
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
    before = _participants(sid)
    _mint(sid, "g1", _BOB)
    assert _participants(sid) == before
    assert _BOB not in _participants(sid)
    assert resolve_room_access(sid, _BOB).role == "member"
    # The fold calls the same helper, and it gives no owner row either.
    assert _fold(sid, "g1", _BOB, "the answer") == []
    assert _participants(sid) == before


@_needs_db
def test_a_run_of_another_agent_adds_no_second_primary(clean) -> None:
    sid = _seed_session(_ALICE)
    _exec(
        "INSERT INTO chat_session_agent (session_id, agent_name, role) "
        "VALUES (:i, 'orchestrator', 'primary')", i=sid,
    )
    _mint(sid, "p1", _ALICE, "sales-assistant")
    assert _agents(sid) == {"orchestrator": "primary"}


@_needs_db
def test_the_mint_on_a_new_session_creates_the_room(clean) -> None:
    """The mint runs on a session id that does not exist yet. Without its
    ``_ensure_session`` call the insert fails on the foreign key, and every
    checkpoint is declined until the fold. So the live reply of a new thread
    is lost."""
    from gateway.routes.chat import MessageRecord

    sid = f"{_PREFIX}-{uuid.uuid4().hex[:8]}"
    _mint(sid, "n1", _ALICE)
    assert _exec("SELECT user_id FROM chat_session WHERE id = :i", i=sid)[0] \
        .user_id == _ALICE
    assert _participants(sid) == {_ALICE: "owner"}
    assert _agents(sid) == {"projects-assistant": "primary"}
    out = _save_as(sid, _ALICE, [
        MessageRecord(id="n1", role="assistant", content="part", timestamp=5100),
    ])
    assert out == {"ok": True, "saved": 1, "unchanged": []}
    assert _content(sid, "n1") == "part"


@_needs_db
def test_a_browser_created_session_gets_its_owner_on_the_first_run(clean) -> None:
    """``_upsert_session`` writes no participant row and no agent row. The
    first mint by the creator still makes both."""
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    sid = f"{_PREFIX}-{uuid.uuid4().hex[:8]}"
    _upsert_session(_ALICE, SessionUpsertRequest(
        id=sid, agent_name="projects-assistant", title="t",
    ))
    assert _participants(sid) == {}
    _mint(sid, "n1", _ALICE)
    assert _participants(sid) == {_ALICE: "owner"}
    assert _agents(sid) == {"projects-assistant": "primary"}


@_needs_db
def test_the_reply_sorts_after_its_prompt_when_the_browser_clock_is_fast(
    clean,
) -> None:
    """Fix round 1, P2. The prompt carries the browser clock T. The server
    clock is two seconds behind, so the mint stamps less than T. The first
    checkpoint arrives later and moves the time forward."""
    import time

    from gateway.routes.chat import MessageRecord, _get_messages

    sid = _seed_session(_ALICE, (_BOB, "member"))
    t = int(time.time() * 1000) + 2000
    _save_as(sid, _ALICE, [
        MessageRecord(id="q1", role="user", content="the prompt", timestamp=t),
    ])
    _mint(sid, "a1", _ALICE)
    minted = _exec(
        "SELECT timestamp_ms FROM chat_message WHERE session_id = :i "
        "AND id = 'a1'", i=sid,
    )[0].timestamp_ms
    assert minted < t
    _save_as(sid, _ALICE, [
        MessageRecord(id="a1", role="assistant", content="part",
                      timestamp=t + 1000),
    ])
    assert [m["id"] for m in _get_messages(sid, _BOB)] == ["q1", "a1"]
    # Once the row has content, its time stays.
    _save_as(sid, _ALICE, [
        MessageRecord(id="a1", role="assistant", content="part and more",
                      timestamp=t + 9000),
    ])
    assert _exec(
        "SELECT timestamp_ms FROM chat_message WHERE session_id = :i "
        "AND id = 'a1'", i=sid,
    )[0].timestamp_ms == t + 1000


def test_a_slow_mint_does_not_hold_the_stream(monkeypatch) -> None:
    """Fix round 1, P3. The run waits ``_MINT_TIMEOUT_S`` at most, then logs
    ``agent.mint_failed`` with the reason ``timeout`` and goes on."""
    import asyncio
    import time

    import structlog
    from gateway.routes import agent

    def _slow(*_a, **_k):
        time.sleep(1.5)

    monkeypatch.setattr(agent, "_mint_run_row", _slow)
    monkeypatch.setattr(agent, "_MINT_TIMEOUT_S", 0.1)
    async def _timed() -> float:
        # Timed inside the loop: asyncio.run waits for the worker thread at
        # shutdown, and a live server loop does not.
        start = time.monotonic()
        await agent._mint_run_row_bounded(
            "t1", "m1", member=_ALICE, agent_name="projects-assistant",
        )
        return time.monotonic() - start

    with structlog.testing.capture_logs() as caps:
        elapsed = asyncio.run(_timed())
    assert elapsed < 1.0
    failed = [c for c in caps if c.get("event") == "agent.mint_failed"]
    assert failed and failed[0].get("reason") == "timeout"


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
