"""The live step of a run on ``GET /chat/active-sessions`` (WS-51 S3).

The activity panel shows "Running · 2 min · <step>". The route reads the step
from the TAIL of the run's own stream (``stream_relay.latest_step``): one
XREVRANGE with a small COUNT, never the whole stream and never a new key.

What this suite holds (R7):

* the step rule: a tool start, a progress line, "Writing a reply" and
  "Thinking", newest first, with arguments and results skipped;
* the cap: at most ``LAST_STEP_MAX_CHARS`` (60) characters;
* plain text: tags, angle brackets and control characters never survive;
* the read is the tail only: XREVRANGE with ``LAST_STEP_SCAN``;
* the route adds ``lastStep`` to a running row, and None to a question;
* a Redis error on the step never fails the list.

Hermetic: a fake Redis, and Postgres down so the route lists the caller's
own runs. The SQL of the route does not change in S3, so R8 is not needed.

Run::

    uv run pytest tests/unit/test_run_last_step.py -v
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)

_ALICE = "alice@step.test"


# ---------------------------------------------------------------------------
# The pure rule
# ---------------------------------------------------------------------------

def test_a_tool_start_names_its_tool_in_words():
    events = [
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{}"},
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "search_tasks"},
    ]
    assert stream_relay.step_from_events(events) == "Search tasks"


def test_a_progress_line_is_a_step():
    events = [{"type": "PROGRESS_UPDATE", "message": "Reading 3 files"}]
    assert stream_relay.step_from_events(events) == "Reading 3 files"


def test_the_newest_step_wins():
    events = [  # newest first
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Here"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": "…"},
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "list_projects"},
    ]
    assert stream_relay.step_from_events(events) == "Writing a reply"
    assert stream_relay.step_from_events(events[1:]) == "List projects"


def test_thinking_and_sub_agent_tools_are_steps():
    assert stream_relay.step_from_events(
        [{"type": "THINKING_TEXT_MESSAGE_CONTENT", "delta": "hm"}],
    ) == "Thinking"
    assert stream_relay.step_from_events(
        [{"type": "SUB_AGENT_TOOL_CALL_START", "toolCallName": "web-search"}],
    ) == "Web search"


def test_no_step_when_nothing_names_one():
    assert stream_relay.step_from_events([]) is None
    assert stream_relay.step_from_events([
        {"type": "RUN_STARTED"},
        {"type": "TOOL_CALL_START", "toolCallName": "tool"},  # the placeholder
        {"type": "PROGRESS_UPDATE", "message": "   "},
        "not a dict",
    ]) is None


def test_the_step_is_capped_at_60_characters():
    long = "word " * 40
    step = stream_relay.step_from_events([{"type": "PROGRESS_UPDATE", "message": long}])
    assert step is not None
    assert len(step) <= stream_relay.LAST_STEP_MAX_CHARS == 60
    assert step.endswith("…")


@pytest.mark.parametrize(
    "raw",
    [
        "<script>alert(1)</script>Searching",
        "<img src=x onerror=alert(1)>Searching",
        "Search\x00ing <b>",
        "a < b > c",
    ],
)
def test_the_step_is_plain_text(raw):
    step = stream_relay.plain_step(raw)
    assert step is not None
    assert "<" not in step and ">" not in step
    assert all(ord(c) >= 32 for c in step)
    # A tool name gets the same fence.
    tool = stream_relay.step_from_events([{"type": "TOOL_CALL_START", "toolCallName": raw}])
    assert tool is not None and "<" not in tool and ">" not in tool


def test_a_value_that_is_not_text_gives_no_step():
    assert stream_relay.plain_step(None) is None
    assert stream_relay.plain_step(42) is None
    assert stream_relay.plain_step("<br>") is None


# ---------------------------------------------------------------------------
# The read: the tail of the stream, never the whole of it
# ---------------------------------------------------------------------------

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.xrev_calls: list[tuple[str, int | None]] = []
        self.xrange_calls = 0
        self.fail_xrev = False

    async def set(self, key, value, ex=None, xx=False, **_kw):
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return True

    async def expire(self, *_a, **_kw):
        return True

    async def hset(self, key, field, value):
        self.store.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return (self.store.get(key) or {}).get(field)

    async def hgetall(self, key):
        return dict(self.store.get(key) or {})

    async def hdel(self, key, *fields):
        h = self.store.get(key) or {}
        for f in fields:
            h.pop(f, None)
        return True

    async def xrevrange(self, key, max="+", min="-", count=None):  # noqa: A002
        self.xrev_calls.append((key, count))
        if self.fail_xrev:
            raise ConnectionError("redis is down")
        events = self.streams.get(key, [])
        newest_first = list(reversed(events))
        if count is not None:
            newest_first = newest_first[:count]
        return [(f"{i}-0", {"event": json.dumps(ev)}) for i, ev in enumerate(newest_first)]

    async def xrange(self, *_a, **_kw):
        self.xrange_calls += 1
        return []

    async def aclose(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    return r


@pytest.fixture
def db_down(monkeypatch):
    import acb_graph

    def _boom(*_a, **_kw):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)


def _push(r: _FakeRedis, tid: str, *events: dict[str, Any]) -> None:
    r.streams.setdefault(stream_relay._stream_key(tid), []).extend(events)


def test_the_read_is_one_tail_read(fake_redis):
    tid = "t-step"
    _push(fake_redis, tid, *[{"type": "TEXT_MESSAGE_CONTENT", "delta": "x"}] * 500)
    _push(fake_redis, tid, {"type": "TOOL_CALL_START", "toolCallName": "draft_email"})

    assert asyncio.run(stream_relay.latest_step(tid)) == "Draft email"
    assert fake_redis.xrev_calls == [
        (stream_relay._stream_key(tid), stream_relay.LAST_STEP_SCAN),
    ]
    assert fake_redis.xrange_calls == 0, "the whole stream was read"


def test_a_redis_error_gives_no_step(fake_redis):
    fake_redis.fail_xrev = True
    assert asyncio.run(stream_relay.latest_step("t-x")) is None


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _start(tid: str, org: str, actor: str) -> None:
    async def _go() -> None:
        await stream_relay.mark_active(tid, reset=True, actor=actor)
        await stream_relay.register_live_run(
            tid, organization_id=org, actor=actor, token=f"tok-{tid}",
        )
    asyncio.run(_go())


def _list(user) -> list[dict]:
    from gateway.routes.chat import list_active_sessions

    return asyncio.run(list_active_sessions(user=user))


def test_the_route_adds_the_step_to_a_running_row(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-run", org, _ALICE)
    _push(fake_redis, "t-run", {
        "type": "PROGRESS_UPDATE", "message": "<b>Checking</b> the calendar " + "x" * 80,
    })

    rows = _list(_user(_ALICE, org))
    assert [r["threadId"] for r in rows] == ["t-run"]
    step = rows[0]["lastStep"]
    assert step.startswith("Checking the calendar")
    assert len(step) <= 60
    assert "<" not in step


def test_the_route_gives_none_when_there_is_no_step(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-quiet", org, _ALICE)
    rows = _list(_user(_ALICE, org))
    assert rows[0]["lastStep"] is None


def test_a_step_error_never_fails_the_list(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-run", org, _ALICE)
    fake_redis.fail_xrev = True
    rows = _list(_user(_ALICE, org))
    assert [r["threadId"] for r in rows] == ["t-run"]
    assert rows[0]["lastStep"] is None


def test_a_question_has_no_step(fake_redis, db_down, monkeypatch):
    """A row that waits on the member reads no stream at all."""
    import gateway.routes.chat as chat

    org = str(uuid.uuid4())
    _start("t-ask", org, _ALICE)
    _push(fake_redis, "t-ask", {"type": "TOOL_CALL_START", "toolCallName": "ask_user"})

    async def _asks(_org, _me):
        return {"t-ask": {"kind": "ask_user"}}

    monkeypatch.setattr(chat, "_my_waiting_asks", _asks)
    rows = _list(_user(_ALICE, org))
    assert rows[0]["state"] == "needs_input"
    assert rows[0]["lastStep"] is None
    assert fake_redis.xrev_calls == []
