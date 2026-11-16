"""The live step of a run on ``GET /chat/active-sessions`` (WS-51 S3).

The activity panel shows "Running · 2 min · <step>". The route reads the step
from the TAIL of the run's own stream (``stream_relay.latest_step``): one
XREVRANGE with a small COUNT, never the whole stream and never a new key.

What this suite holds (R7):

* the step rule: a tool NAME, "Writing a reply" and "Thinking", newest
  first. The text of a progress update, a result or an argument is never a
  step, because it can be tool output (review of #815);
* an MCP name loses its server prefix;
* the cap: at most ``LAST_STEP_MAX_CHARS`` (60) characters;
* plain text: tags, angle brackets and control characters never survive;
* the read is the tail only: XREVRANGE with ``LAST_STEP_SCAN``;
* the route adds ``lastStep`` to a running row, and None to a question;
* without ``steps=1`` the route reads no stream at all (the badge poll);
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


def test_a_progress_line_is_never_a_step():
    events = [{"type": "PROGRESS_UPDATE", "message": "Reading 3 files"}]
    assert stream_relay.step_from_events(events) is None


_SECRET = "OPENAI_API_KEY=sk-live-abc123"
_SECRET_2 = "DATABASE_URL=postgres://app:hunter2@db:5432/prod"


# Review of #815, P2. Mutation: let a PROGRESS_UPDATE message, a result or a
# partial back into the step, and this fails.
def test_tool_output_never_reaches_the_step():
    events = [  # newest first, every one carrying text a shell tool printed
        {"type": "PROGRESS_UPDATE", "message": _SECRET},
        {"type": "TOOL_CALL_PARTIAL", "toolCallId": "c1", "delta": _SECRET_2},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": _SECRET},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": _SECRET_2},
        {"type": "TOOL_CALL_START", "toolCallId": "c0", "toolCallName": _SECRET_2},
        {"type": "TOOL_CALL_START", "toolCallId": "c0", "toolCallName": "intent: cat .env " + _SECRET},
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "run_shell"},
    ]
    step = stream_relay.step_from_events(events)
    assert step == "Run shell"
    for secret in ("sk-live", "hunter2", "OPENAI", "DATABASE", "postgres", ".env"):
        assert secret not in step
    # With no tool name at all, there is no step.
    assert stream_relay.step_from_events(events[:-1]) is None


@pytest.mark.parametrize(
    ("name", "step"),
    [
        ("mcp__github__list_issues", "List issues"),
        ("mcp_github_list_issues", "List issues"),
        ("mcp__claude-docs__read", "Read"),
        ("search_tasks", "Search tasks"),
        ("projects.create-task", "Projects create task"),
    ],
)
def test_a_tool_name_becomes_a_short_label(name, step):
    assert stream_relay.tool_step(name) == step


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
    long = "search_" * 11  # an identifier of 77 characters
    step = stream_relay.step_from_events([{"type": "TOOL_CALL_START", "toolCallName": long}])
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
    # A tool "name" that holds markup is not an identifier, so it is no step.
    assert stream_relay.step_from_events([{"type": "TOOL_CALL_START", "toolCallName": raw}]) is None


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


def _list(user, steps: bool = True) -> list[dict]:
    from gateway.routes.chat import list_active_sessions

    return asyncio.run(list_active_sessions(user=user, steps=steps))


def test_the_route_adds_the_step_to_a_running_row(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-run", org, _ALICE)
    _push(fake_redis, "t-run",
          {"type": "TOOL_CALL_START", "toolCallName": "check_calendar"},
          {"type": "PROGRESS_UPDATE", "message": _SECRET})

    rows = _list(_user(_ALICE, org))
    assert [r["threadId"] for r in rows] == ["t-run"]
    assert rows[0]["lastStep"] == "Check calendar"


# Review of #815, P2. Mutation: read the step on every poll, and this fails.
def test_the_badge_poll_reads_no_stream(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-run", org, _ALICE)
    _push(fake_redis, "t-run", {"type": "TOOL_CALL_START", "toolCallName": "search_tasks"})

    rows = _list(_user(_ALICE, org), steps=False)
    assert [r["threadId"] for r in rows] == ["t-run"]
    assert rows[0]["lastStep"] is None
    assert fake_redis.xrev_calls == [], "a poll without steps=1 read the stream"


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


# ---------------------------------------------------------------------------
# CSY-10: an MCP step keeps its server's label (chat_connected_systems.md 3.8)
#
# The wire name of an MCP tool is exactly "<server.name>_<tool>": MAF's
# tool_name_prefix is the server name (test_maf_mcp_injection.py), and a
# registry name keeps its dashes. The translator stamps ``mcp: {server, label}``
# on the tool start, and the step reads the stamp. The injection fence is in
# test_maf_mcp_injection.py.
# ---------------------------------------------------------------------------

_SENT = "SENTINEL-c5d1"
_LABELS = {"shaurrya-app": "Shaurrya App", "ops": "Ops"}


def _mcp_start(name, labels=None, mcp=True):
    ev = {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": name}
    if mcp and labels is not None:
        from orchestrator.event_translator import mcp_server_of

        found = mcp_server_of(name, labels)
        if found:
            ev["mcp"] = {"server": found[0], "label": found[1]}
    return ev


@pytest.mark.parametrize(
    ("name", "step"),
    [
        ("shaurrya-app_search_operations", "Shaurrya App: search operations"),
        ("ops_read", "Ops: read"),
        ("ops_list-items", "Ops: list items"),
    ],
)
def test_an_mcp_tool_reads_its_server_label(name, step):
    assert stream_relay.step_from_events([_mcp_start(name, _LABELS)]) == step


@pytest.mark.parametrize(
    "name",
    ["shaurrya_app_search_operations", "mcp__ops__list_items", "mcp_ops_read", "OPS_read", "ops_", "opsread"],
)
def test_a_name_that_is_not_exactly_server_underscore_tool_is_not_guessed(name):
    from orchestrator.event_translator import mcp_server_of

    assert mcp_server_of(name, _LABELS) is None


def test_each_server_maps_to_its_own_label():
    from orchestrator.event_translator import mcp_server_of

    labels = {"mcp-ops": "Ops One", "mcp-ops-mirror": "Ops Mirror", "mcp": "Bare"}
    assert mcp_server_of("mcp-ops_read", labels)[:2] == ("mcp-ops", "Ops One")
    assert mcp_server_of("mcp-ops-mirror_read", labels)[:2] == ("mcp-ops-mirror", "Ops Mirror")
    assert mcp_server_of("mcp_read", labels)[:2] == ("mcp", "Bare")


def test_a_tool_with_no_stamp_keeps_the_old_step():
    assert stream_relay.step_from_events([_mcp_start("ops_read", _LABELS, mcp=False)]) == "Ops read"
    assert stream_relay.step_from_events([_mcp_start("search_tasks", _LABELS)]) == "Search tasks"


def test_a_stamp_that_does_not_match_the_name_is_ignored():
    ev = {"type": "TOOL_CALL_START", "toolCallName": "search_tasks",
          "mcp": {"server": "ops", "label": "Ops"}}
    assert stream_relay.step_from_events([ev]) == "Search tasks"
    ev["mcp"] = {"server": 3, "label": None}
    assert stream_relay.step_from_events([ev]) == "Search tasks"


def test_the_server_label_is_bounded_and_plain():
    ev = {"type": "TOOL_CALL_START", "toolCallName": "ops_read",
          "mcp": {"server": "ops", "label": "A\x00B\x1b<b>C</b>‮​D" + "x" * 80}}
    step = stream_relay.step_from_events([ev])
    assert step is not None and step.endswith(": read")
    assert len(step) <= stream_relay.LAST_STEP_MAX_CHARS
    assert len(step.split(": ")[0]) <= stream_relay.SERVER_LABEL_MAX_CHARS
    assert "<" not in step and all(ord(c) >= 32 for c in step)
    assert not any(c in step for c in "‮​﻿")


def _first_party_names():
    import types

    from orchestrator._tool_injection import _reserved_tool_names

    return _reserved_tool_names(types.SimpleNamespace())


@pytest.mark.parametrize("server", ["web", "search", "list", "call"])
def test_a_server_never_relabels_a_first_party_tool(server):
    from orchestrator import event_translator as et

    reserved = _first_party_names()
    clash = sorted(n for n in reserved if n.startswith(f"{server}_"))
    assert clash, f"no first-party tool starts with {server}_: update this test"
    token_l, token_r = et.MCP_LABELS.set({}), et.MCP_RESERVED.set(frozenset())
    try:
        et.set_mcp_labels({server: "Outside"}, reserved)
        # The server is dropped, so no first-party name is stamped ...
        assert dict(et.MCP_LABELS.get()) == {}
        # ... and the matcher alone also skips a reserved name.
        for name in clash:
            assert et.mcp_server_of(name, {server: "Outside"}, reserved) is None
    finally:
        et.MCP_RESERVED.reset(token_r)
        et.MCP_LABELS.reset(token_l)


# Mutation: read an argument or a result into the step or the stamp, and this fails.
def test_the_translator_stamps_the_server_and_never_an_argument_or_a_result():
    import types

    from orchestrator import event_translator as et

    token_l, token_r = et.MCP_LABELS.set({}), et.MCP_RESERVED.set(frozenset())
    try:
        et.set_mcp_labels(_LABELS, frozenset({"search_tasks"}))
        state = et.ToolCallStreamState("run-1")
        call = types.SimpleNamespace(
            type="function_call", call_id="c1", name="ops_read",
            arguments=json.dumps({"q": _SENT}),
        )
        events = et.function_call_events(call, state)
        start = events[0]
        assert start["mcp"] == {"server": "ops", "label": "Ops"}
        assert _SENT not in json.dumps(start["mcp"])
        # A result event never reaches the step.
        events.append({"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": _SENT})
        step = stream_relay.step_from_events(list(reversed(events)))
        assert step == "Ops: read"
        assert _SENT not in step
        # A first-party tool is not stamped.
        native = et.function_call_events(
            types.SimpleNamespace(type="function_call", call_id="c3", name="search_tasks", arguments="{}"),
            et.ToolCallStreamState("run-3"),
        )
        assert "mcp" not in native[0]
        # No labels, no stamp.
        et.set_mcp_labels({}, frozenset())
        plain = et.function_call_events(
            types.SimpleNamespace(type="function_call", call_id="c2", name="ops_read", arguments="{}"),
            et.ToolCallStreamState("run-2"),
        )
        assert "mcp" not in plain[0]
    finally:
        et.MCP_RESERVED.reset(token_r)
        et.MCP_LABELS.reset(token_l)
