"""RESUME-2d (D-RES-10): the reconnect replay gives the whole run or none of it.

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.7, the RESUME-2d
row, and D-RES-10 in §4.12.8. The R8 half is
``test_reconnect_waterline_r8.py``.

Hermetic. Each test names the mutation that turns it red (R7):

* ``test_the_rule_*`` hold the pure rule ``gateway.rooms.replay_allowed``.
  Flip the compare, drop the margin, or let a passer-by in, and one fails.
* ``test_the_room_*`` hold the join fields of ``resolve_room_access``. Read
  the join time only for a ``since_join`` room, or give a group row with no
  time a place, and one fails.
* ``test_the_head_*`` hold ``stream_relay.stream_head_ms``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

rooms = pytest.importorskip("gateway.rooms")
stream_relay = pytest.importorskip("orchestrator.stream_relay")

_ALICE = "alice@r2d.test"
_BOB = "bob@r2d.test"
_START = 1_790_000_000_000


def _room(**kw) -> rooms.RoomAccess:
    base = dict(
        session_id="s", email=_BOB, role="member", can_read=True,
        can_send=True, can_cancel=True, can_invite=False, can_manage=False,
        is_shared=True, members=sorted([_ALICE, _BOB]), visibility="people",
        has_join_row=True, join_ms=_START,
    )
    base.update(kw)
    return rooms.RoomAccess(**base)


# ── The pure rule ───────────────────────────────────────────────────────────

def test_the_rule_lets_a_solo_reader_replay_with_no_start():
    solo = _room(email=_ALICE, members=[_ALICE], visibility="private",
                 is_shared=False, has_join_row=False, join_ms=None)
    assert rooms.replay_allowed(solo, None) is True
    assert rooms.replay_allowed(None, None) is True


@pytest.mark.parametrize(("join_ms", "allowed"), [
    (_START - 60_000, True),     # joined a minute before the run
    (_START, True),              # joined at the start
    (_START + 1000, True),       # 1 s after: inside the margin
    (_START + 2000, True),       # the edge of the margin
    (_START + 2001, False),      # just outside it
    (_START + 3000, False),      # 3 s after: outside
    (_START + 3_600_000, False),  # joined in the middle of the run
])
def test_the_rule_is_join_at_or_before_start_plus_2s(join_ms, allowed):
    assert rooms.replay_allowed(_room(join_ms=join_ms), _START) is allowed


def test_the_rule_reads_a_null_join_as_from_the_start():
    assert rooms.replay_allowed(_room(join_ms=None), _START) is True
    assert rooms.replay_allowed(_room(join_ms=None), None) is True


def test_the_rule_gives_a_passer_by_none():
    passer = _room(role=None, can_send=False, visibility="org",
                   has_join_row=False, join_ms=None)
    assert rooms.replay_allowed(passer, _START) is False


def test_the_rule_gives_none_on_an_unknown_start_or_no_read():
    assert rooms.replay_allowed(_room(), None) is False
    assert rooms.replay_allowed(_room(can_read=False), _START) is False


def test_an_org_visible_room_of_one_is_not_solo():
    """The owner of an org-visible room replays through her row."""
    owner = _room(email=_ALICE, role="owner", members=[_ALICE],
                  visibility="org", is_shared=False, join_ms=None)
    assert rooms.room_is_solo(owner) is False
    assert rooms.replay_allowed(owner, _START) is True


# ── The join fields of resolve_room_access ──────────────────────────────────

def _part(subject, role="member", join=None):
    return SimpleNamespace(subject=subject, role=role, join_message_ts=join,
                           join_stream_id=None)


def _resolve(monkeypatch, parts, *, email=_BOB, history="full",
             visibility="people", my_groups=(), in_org=False, user_id=_ALICE):
    loaded = {
        "session": SimpleNamespace(
            user_id=user_id, agent_name="", visibility=visibility,
            floor_mode="open", history_visibility=history,
        ),
        "participants": parts,
        "my_groups": set(my_groups),
        "in_org": in_org,
    }
    monkeypatch.setattr(rooms, "_load_room", lambda *_a: loaded)
    return rooms.resolve_room_access("s", email, organization_id="o")


def test_the_room_gives_the_join_time_in_a_full_history_room(monkeypatch):
    room = _resolve(monkeypatch, [
        _part(_ALICE, "owner"), _part(_BOB, "member", _START + 5000),
    ])
    assert room.since_message_ts is None  # the waterline stays as it was
    assert (room.has_join_row, room.join_ms) == (True, _START + 5000)


def test_the_room_reads_the_owner_row_as_from_the_start(monkeypatch):
    room = _resolve(monkeypatch, [_part(_ALICE, "owner")], email=_ALICE)
    assert (room.has_join_row, room.join_ms) == (True, None)


def test_the_room_prefers_the_direct_row(monkeypatch):
    room = _resolve(monkeypatch, [
        _part(_ALICE, "owner"), _part("group:eng", "member", _START - 9000),
        _part(_BOB, "viewer", _START + 9000),
    ], my_groups={"eng"})
    assert room.join_ms == _START + 9000


def test_the_room_takes_a_group_row_time(monkeypatch):
    room = _resolve(monkeypatch, [
        _part(_ALICE, "owner"), _part("group:eng", "member", _START + 1),
        _part("org", "viewer", _START - 1),
    ], my_groups={"eng"}, in_org=True)
    assert (room.has_join_row, room.join_ms) == (True, _START - 1)


def test_the_room_gives_a_group_row_with_no_time_no_place(monkeypatch):
    room = _resolve(monkeypatch, [
        _part(_ALICE, "owner"), _part("group:eng", "member", None),
    ], my_groups={"eng"})
    assert room.can_read is True
    assert room.has_join_row is False
    assert rooms.replay_allowed(room, _START) is False


def test_the_room_gives_a_passer_by_no_row(monkeypatch):
    room = _resolve(monkeypatch, [_part(_ALICE, "owner")],
                    visibility="org", in_org=True)
    assert (room.can_read, room.has_join_row) == (True, False)


def test_the_room_counts_the_creator_fallback_from_the_start(monkeypatch):
    room = _resolve(monkeypatch, [], email=_ALICE, visibility="org")
    assert room.role == "owner"
    assert (room.has_join_row, room.join_ms) == (True, None)


# ── The head of the stream ──────────────────────────────────────────────────

class _HeadRedis:
    def __init__(self, entries):
        self.entries = entries

    async def xrange(self, key, count=None):
        return self.entries[:count] if count else list(self.entries)


@pytest.mark.parametrize(("entries", "want"), [
    ([], None),
    ([("1790000000000-0", {}), ("1790000000500-3", {})], 1_790_000_000_000),
    ([("1790000000000-7", {})], 1_790_000_000_000),
    ([("local-1-1", {})], None),
])
def test_the_head_is_the_ms_of_the_first_entry(monkeypatch, entries, want):
    async def _client():
        return _HeadRedis(entries)

    monkeypatch.setattr(stream_relay, "_get_client", _client)
    assert asyncio.run(stream_relay.stream_head_ms("t")) == want
