"""RESUME-2d (D-RES-10): the reconnect replay, on a REAL database (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.7, the RESUME-2d
row, and D-RES-10 in §4.12.8. The hermetic half is
``test_reconnect_waterline.py``.

``GET /agent/run/{thread_id}/reconnect`` replays the run ONLY when the
reader may see the whole run. The chat is solo for the reader, or the reader
matches a participant row whose join time is at or before the start of the
run, with a margin of 2 s (``join_ms <= run_start_ms + 2000``). Any other
reader gets no event of the run, and then the normal end frame.

The rooms run as real SQL on the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role,
through the real ``resolve_room_access``. The route is the real handler.
Only Redis is a fake. It holds one stream per thread with entry ids that a
test chooses, so the start of each run is exact.

R7, each with the mutation that turns it red:

* ``test_a_solo_reader_*`` — the golden frames. Any change to a solo replay
  (a new frame, a lost frame, another end) fails.
* ``test_a_late_joiner_*`` — drop the ``_replay_ok`` call from the route,
  and the joiner reads the run (live, parked and ended). Drop the
  ``withheld`` marker, and the chat attaches again in a loop (review P1).
* ``test_a_solo_reader_*`` also count the XRANGE calls. Read the stream head
  for a solo chat, and they fail.
* ``test_the_margin_*`` — drop the margin, or flip the compare.
* ``test_a_passer_by_*`` — let a reader with no join row in.
* ``test_org_b_*`` — drop the membership check that comes first.
* ``test_a_null_join_*`` — read a NULL join as "never".
* ``test_a_continuation_*`` — take the start of part two (the ``resumed``
  start) as the start of the run, and part one leaks to the joiner.

With ``TENANT_LADDER_DATABASE_URL`` unset, the suite skips, and a skip is not
a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_reconnect_waterline_r8.py -v -rs
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
stream_relay = pytest.importorskip("orchestrator.stream_relay")
run_liveness = pytest.importorskip("orchestrator.run_liveness")

import acb_graph.db as graph_db  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ALICE = "alice@r2d-a.test"
_BOB = "bob@r2d-a.test"
_DAN = "dan@r2d-a.test"
_CAROL = "carol@r2d-b.test"

#: The start of the run in each test: the ms part of the first entry id.
_START = 1_790_000_000_000


# ---------------------------------------------------------------------------
# A fake Redis whose stream ids a test chooses
# ---------------------------------------------------------------------------

def _key_of(eid: str) -> tuple[int, int]:
    ms, _, seq = eid.partition("-")
    return int(ms), int(seq or 0)


class _Redis:
    """Strings and streams. ``xread`` with a block hands out the live tail
    once, then ends the run (it drops the active flag), as a run does."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.live_tail: dict[str, list[tuple[str, dict]]] = {}
        #: Each XRANGE call. A solo reconnect makes none (RESUME-2d, note 1).
        self.xrange_calls = 0

    async def get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def set(self, key, value, ex=None, xx=False, nx=False, **_kw):
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys):
        return sum(1 for k in keys if self.store.pop(k, None) is not None)

    async def expire(self, *_a, **_kw):
        return True

    async def exists(self, *keys):
        return sum(1 for k in keys if k in self.store or self.streams.get(k))

    async def xrange(self, key, count=None):
        self.xrange_calls += 1
        entries = list(self.streams.get(key) or [])
        return entries[:count] if count else entries

    async def xread(self, streams, count=None, block=None):
        out = []
        for key, cursor in streams.items():
            entries = self.streams.get(key) or []
            after = [(e, f) for e, f in entries
                     if cursor in ("0", "0-0") or _key_of(e) > _key_of(cursor)]
            if not after and block:
                tail = self.live_tail.pop(key, [])
                if tail:
                    entries.extend(tail)
                    after = list(tail)
                else:
                    tid = key.split(":", 2)[2]
                    self.store.pop(f"cc:active:{tid}", None)
            if count:
                after = after[:count]
            if after:
                out.append((key, after))
        return out


@pytest.fixture
def redis(monkeypatch):
    r = _Redis()

    async def _client():
        return r

    async def _live(_tid):
        return "live"

    monkeypatch.setattr(stream_relay, "_get_client", _client)
    monkeypatch.setattr(run_liveness, "run_liveness", _live)
    return r


def _seed(r: _Redis, tid: str, events: list[tuple[int, dict]], *,
          live: bool = False, tail: list[tuple[int, dict]] | None = None) -> None:
    """Entries at ``_START + offset`` ms. *live* sets the active flag."""
    key = f"cc:stream:{tid}"
    seq: dict[int, int] = {}
    for off, evt in events:
        ms = _START + off
        n = seq.get(ms, 0)
        seq[ms] = n + 1
        r.streams.setdefault(key, []).append((f"{ms}-{n}", {"event": json.dumps(evt)}))
    if tail:
        r.live_tail[key] = [(f"{_START + off}-9", {"event": json.dumps(evt)})
                            for off, evt in tail]
    if live:
        r.store[f"cc:active:{tid}"] = "1"


def _frames(r: _Redis, tid: str) -> str:
    """The golden bytes: one ``data:`` frame for each entry, with its id."""
    out = []
    for eid, fields in r.streams.get(f"cc:stream:{tid}", []):
        evt = json.loads(fields["event"])
        evt["_stream_id"] = eid
        out.append(f"data: {json.dumps(evt)}\n\n")
    return "".join(out)


def _end(tid: str, *, withheld: bool = False) -> str:
    """The end frame of the route, as it was before RESUME-2d. A withheld
    reader gets it with the marker ``withheld: true``."""
    evt: dict[str, Any] = {"type": "RUN_FINISHED", "runId": tid, "threadId": tid}
    if withheld:
        evt["withheld"] = True
    return f"data: {json.dumps(evt)}\n\n"


def _withheld(tid: str) -> str:
    return _end(tid, withheld=True)


_RUN = [
    (0, {"type": "RUN_STARTED", "runId": "run-1"}),
    (100, {"type": "TEXT_MESSAGE_CONTENT", "delta": "secret part one"}),
    (5000, {"type": "TOOL_CALL_START", "toolCallName": "crm_update"}),
]
_ENDED = [*_RUN, (9000, {"type": "RUN_FINISHED", "runId": "run-1"})]
_PARKED = [*_RUN, (9000, {"type": "RUN_FINISHED", "runId": "run-1", "parked": True})]
_TAIL = [(20_000, {"type": "TEXT_MESSAGE_CONTENT", "delta": "live tail"}),
         (21_000, {"type": "RUN_FINISHED", "runId": "run-1"})]


# ---------------------------------------------------------------------------
# The rooms, as real rows
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def members(promoted):
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_DAN, promoted.org_a), (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', :o) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def db(members, app_engine, monkeypatch):
    """``acb_graph`` sessions open as the app role, one fresh backend each."""
    eng = create_engine(members.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield members
    finally:
        eng.dispose()


def _room(db, *, visibility: str = "people",
          rows: tuple[tuple[str, str, int | None], ...] = ((_ALICE, "owner", None),),
          history: str = "full") -> str:
    """One chat of Alice in org A. *rows* are ``(subject, role, join offset)``.
    An offset is ms after ``_START``. ``None`` is a NULL join column."""
    sid = f"r2d-{uuid.uuid4().hex[:12]}"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, visibility, "
            "history_visibility, organization_id) "
            "VALUES (:id, :u, 'orchestrator', :v, :h, CAST(:o AS uuid))"),
            {"id": sid, "u": _ALICE, "v": visibility, "h": history, "o": db.org_a})
        for subject, role, off in rows:
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "join_message_ts, organization_id) "
                "VALUES (:s, :subject, :r, :j, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role,
                 "j": None if off is None else _START + off, "o": db.org_a})
    return sid


def _get(tid: str, email: str, org: str) -> tuple[int, str]:
    """The real reconnect route, as *email* of *org*."""
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes

    app = FastAPI()
    app.get("/agent/run/{thread_id}/reconnect")(agent_routes.reconnect_agent_stream)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
    )
    res = TestClient(app).get(f"/agent/run/{tid}/reconnect")
    return res.status_code, res.text


# ---------------------------------------------------------------------------
# A solo chat replays the same bytes as before
# ---------------------------------------------------------------------------

def test_a_solo_reader_replays_the_same_bytes_of_an_ended_run(db, redis):
    tid = _room(db, visibility="private")
    _seed(redis, tid, _ENDED)
    status, body = _get(tid, _ALICE, db.org_a)
    assert status == 200
    assert body == _frames(redis, tid) + _end(tid)
    assert "withheld" not in body
    assert redis.xrange_calls == 0  # a solo reconnect reads no stream head


def test_a_solo_reader_replays_the_same_bytes_of_a_live_run(db, redis):
    tid = _room(db, visibility="private")
    _seed(redis, tid, _RUN, live=True, tail=_TAIL)
    status, body = _get(tid, _ALICE, db.org_a)
    assert status == 200
    # The replay, then the live tail, and no extra end: the tail holds it.
    assert body == _frames(redis, tid)
    assert redis.xrange_calls == 0
    assert body.count("live tail") == 1 and body.endswith(
        f"data: {json.dumps({'type': 'RUN_FINISHED', 'runId': 'run-1', '_stream_id': f'{_START + 21_000}-9'})}\n\n")


def test_a_solo_chat_with_no_participant_row_still_replays(db, redis):
    """A legacy chat: the creator owns it through the fallback."""
    tid = _room(db, visibility="private", rows=())
    _seed(redis, tid, _ENDED)
    assert _get(tid, _ALICE, db.org_a) == (200, _frames(redis, tid) + _end(tid))
    assert redis.xrange_calls == 0


# ---------------------------------------------------------------------------
# A shared room: the members who were there before the run
# ---------------------------------------------------------------------------

def test_the_owner_and_an_early_member_replay_the_whole_run(db, redis):
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", -60_000)))
    _seed(redis, tid, _ENDED)
    whole = _frames(redis, tid) + _end(tid)
    assert _get(tid, _ALICE, db.org_a) == (200, whole)
    assert _get(tid, _BOB, db.org_a) == (200, whole)


@pytest.mark.parametrize("shape", ["live", "parked", "ended"])
def test_a_late_joiner_gets_no_event_and_the_normal_end(db, redis, shape):
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", 60_000)))
    if shape == "live":
        _seed(redis, tid, _RUN, live=True, tail=_TAIL)
    else:
        _seed(redis, tid, _PARKED if shape == "parked" else _ENDED)
    status, body = _get(tid, _BOB, db.org_a)
    assert status == 200
    assert body == _withheld(tid)
    # The marker: the chat tells this end from a normal one (review P1).
    frame = json.loads(body.removeprefix("data: ").strip())
    assert frame == {"type": "RUN_FINISHED", "runId": tid, "threadId": tid, "withheld": True}
    assert "secret part one" not in body and "live tail" not in body


def test_a_late_joiner_of_a_since_join_room_gets_none_too(db, redis):
    tid = _room(db, history="since_join",
                rows=((_ALICE, "owner", None), (_BOB, "viewer", 4000)))
    _seed(redis, tid, _ENDED)
    assert _get(tid, _BOB, db.org_a) == (200, _withheld(tid))


@pytest.mark.parametrize(("offset", "replays"), [(1000, True), (2000, True),
                                                 (3000, False)])
def test_the_margin_is_2s_after_the_start(db, redis, offset, replays):
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", offset)))
    _seed(redis, tid, _ENDED)
    body = _get(tid, _BOB, db.org_a)[1]
    assert body == (_frames(redis, tid) + _end(tid) if replays else _withheld(tid))


def test_a_passer_by_of_an_org_visible_room_gets_none(db, redis):
    tid = _room(db, visibility="org")
    _seed(redis, tid, _ENDED)
    status, body = _get(tid, _DAN, db.org_a)
    assert status == 200  # Dan may read the room, so no 403
    assert body == _withheld(tid)
    # The owner of the same room still replays it.
    assert _get(tid, _ALICE, db.org_a)[1] == _frames(redis, tid) + _end(tid)


def test_org_b_never_reads_org_a_stream(db, redis):
    tid = _room(db, rows=((_ALICE, "owner", None), (_CAROL, "member", -60_000)))
    _seed(redis, tid, _ENDED)
    status, body = _get(tid, _CAROL, db.org_b)
    assert status == 403
    assert "secret part one" not in body


def test_a_null_join_reads_as_from_the_start(db, redis):
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", None)))
    _seed(redis, tid, _ENDED)
    assert _get(tid, _BOB, db.org_a) == (200, _frames(redis, tid) + _end(tid))


# ---------------------------------------------------------------------------
# One run in two parts (RESUME-2b, case A)
# ---------------------------------------------------------------------------

#: Part one, its parked end deleted by the continuation, and part two an
#: hour later. The run keeps its id, and part two's start is ``resumed``.
_TWO_PARTS = [
    *_RUN,
    (3_600_000, {"type": "RUN_STARTED", "runId": "run-1", "resumed": True}),
    (3_600_100, {"type": "TEXT_MESSAGE_CONTENT", "delta": "part two"}),
    (3_601_000, {"type": "RUN_FINISHED", "runId": "run-1"}),
]


def test_a_continuation_gives_a_joiner_after_part_one_no_event(db, redis):
    """Bob joined between the start of part one and the start of part two.
    The start of the run is part one's start, so he gets none of it."""
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", 1_800_000),
                          (_DAN, "member", -1000)))
    _seed(redis, tid, _TWO_PARTS)
    assert _get(tid, _BOB, db.org_a) == (200, _withheld(tid))
    # Dan was there before part one, so he reads both parts.
    body = _get(tid, _DAN, db.org_a)[1]
    assert body == _frames(redis, tid) + _end(tid)
    assert "secret part one" in body and "part two" in body


def test_a_trimmed_head_is_the_known_residual(db, redis):
    """⚠️ Residual, not a goal. ``STREAM_MAXLEN`` trims the oldest entries of
    a very long run. The first entry that stays is then later than the true
    start, and a joiner between the two reads the rest of the run. The
    stream keeps 50 000 entries, so only a very long run reaches it. This
    test pins the behaviour, so a change to it is a choice."""
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", 30_000)))
    # The true start was at _START. A trim took everything before +60 s.
    _seed(redis, tid, [(60_000, {"type": "TEXT_MESSAGE_CONTENT", "delta": "late"}),
                       (61_000, {"type": "RUN_FINISHED", "runId": "run-1"})])
    body = _get(tid, _BOB, db.org_a)[1]
    assert "late" in body


def test_no_stream_gives_every_reader_the_same_end(db, redis):
    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", 60_000)))
    # Alice was there from the start, so she gets the normal end. Bob joined
    # after an unknown start, so he gets the withheld end.
    assert _get(tid, _ALICE, db.org_a) == (200, _end(tid))
    assert _get(tid, _BOB, db.org_a) == (200, _withheld(tid))


def test_the_rule_reads_the_bound_room(db):
    """The join fields come from the bound read, under RLS."""
    from gateway.rooms import resolve_room_access

    tid = _room(db, rows=((_ALICE, "owner", None), (_BOB, "member", 1000)))
    bob = resolve_room_access(tid, _BOB, organization_id=db.org_a)
    assert (bob.has_join_row, bob.join_ms) == (True, _START + 1000)
    alice = resolve_room_access(tid, _ALICE, organization_id=db.org_a)
    assert (alice.has_join_row, alice.join_ms) == (True, None)
    carol = resolve_room_access(tid, _CAROL, organization_id=db.org_b)
    assert (carol.can_read, carol.has_join_row) == (False, False)
