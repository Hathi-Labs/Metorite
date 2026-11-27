"""HRD-16: a card answer and a steer act once (chat_failure_modes.md G1).

Mutations this suite catches:

* ``buffer_guidance`` no longer drops a repeated signal id: two notes.
* ``mark_answered`` is a no-op: the second ``was_answered`` read is False.
* the route no longer reads ``was_answered`` before the restart path: the
  source check and the route tests below fail.
* the repeat gets the plain 409 again: the route tests below fail.

Hermetic: the Redis client is a fake. No SQL, so R8 does not apply.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

steer = pytest.importorskip("orchestrator.steer")
answer_once = pytest.importorskip("orchestrator.answer_once")


class _FakeTenantRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, k, value, nx=False, ex=None):
        key = k.value
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, k):
        return self.data.get(k.value)


@pytest.fixture
def fake(monkeypatch):
    r = _FakeTenantRedis()

    async def _client():
        return r

    monkeypatch.setattr(answer_once, "_client", _client)
    return r


def test_second_mark_is_not_first(fake):
    async def go():
        a = await answer_once.mark_answered("org1", "t1", "req1")
        b = await answer_once.mark_answered("org1", "t1", "req1")
        return a, b

    assert asyncio.run(go()) == (True, False)


def test_was_answered_after_mark_only(fake):
    async def go():
        before = await answer_once.was_answered("org1", "t1", "req1")
        await answer_once.mark_answered("org1", "t1", "req1")
        after = await answer_once.was_answered("org1", "t1", "req1")
        other_org = await answer_once.was_answered("org2", "t1", "req1")
        return before, after, other_org

    assert asyncio.run(go()) == (False, True, False)


def test_no_org_never_marks_and_never_blocks():
    assert asyncio.run(answer_once.was_answered(None, "t", "r")) is False


def test_steer_with_same_id_buffers_once():
    steer.clear_guidance("tid-once")
    assert steer.buffer_guidance("tid-once", "a@x.io", "hello", signal_id="s1")
    assert steer.buffer_guidance("tid-once", "a@x.io", "hello", signal_id="s1")
    assert len(steer.drain_guidance("tid-once")) == 1
    # Seen once, read once: the replay of the same id adds nothing.
    assert steer.buffer_guidance("tid-once", "a@x.io", "hello", signal_id="s1")
    assert steer.drain_guidance("tid-once") == []
    steer.clear_guidance("tid-once")
    assert steer.buffer_guidance("tid-once", "a@x.io", "hello", signal_id="s1")
    assert len(steer.drain_guidance("tid-once")) == 1
    steer.clear_guidance("tid-once")


def test_steers_with_different_ids_both_count():
    steer.clear_guidance("tid-two")
    steer.buffer_guidance("tid-two", "a@x.io", "one", signal_id="s1")
    steer.buffer_guidance("tid-two", "a@x.io", "two", signal_id="s2")
    assert len(steer.drain_guidance("tid-two")) == 2
    steer.clear_guidance("tid-two")


def _route_calls() -> dict[str, list[int]]:
    """Line numbers of the calls inside ``respond_user_input``, by name.

    Read from the syntax tree, not from text offsets: a docstring that names
    ``_resume_from_row`` earlier in the file moved the old text offsets.
    """
    import ast

    path = pathlib.Path("apps/services/gateway/gateway/routes/agent.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "respond_user_input":
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    fn = call.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                    found.setdefault(name, []).append(call.lineno)
    return found


def test_route_checks_the_mark_before_the_restart_path():
    calls = _route_calls()
    assert calls.get("was_answered") and calls.get("_resume_from_row")
    assert min(calls["was_answered"]) < min(calls["_resume_from_row"])
    assert calls.get("mark_answered")


def test_route_reads_the_mark_in_one_place():
    """ONE check of the mark, the one that raises ``answered_once``.

    A merge of HRD-17a once kept a second, older block after the late-decline
    branch, and that block raised the plain 409. A second ``was_answered``
    call in the route goes red here.
    """
    calls = _route_calls()
    assert len(calls.get("was_answered", [])) == 1, calls.get("was_answered")
    assert len(calls.get("_answered_once_409", [])) == 1


# ── The route, called for real: a repeat names itself and starts nothing ─────
#
# Owner decision 2026-11-23: "Distinct 409 detail, client does not resend".
# Mutations these catch:
#
# * the repeat raises the plain 409 again: the detail is a string, not
#   ``answered_once``, and the browser resends (red).
# * the ``was_answered`` check moves after the late-decline branch: a repeat
#   decline reaches ``_resume_from_row`` (red).
# * the check reads the mark for every 409: a stale card with no mark gets
#   ``answered_once`` and loses today's path (red).


@pytest.fixture
def route(monkeypatch, fake):
    """``respond_user_input`` with no run, no Redis and no database.

    ``world["delivered"]`` says whether a run takes the answer. Each late path
    records its call, so a test can prove that a repeat never reaches it.
    """
    from types import SimpleNamespace

    agent_routes = pytest.importorskip("gateway.routes.agent")
    executor = pytest.importorskip("orchestrator.executor")
    stream_relay = pytest.importorskip("orchestrator.stream_relay")
    chat_recovery = pytest.importorskip("gateway.chat_recovery")
    reply_card = pytest.importorskip("acb_skills.reply_card")

    world: dict = {"delivered": False, "calls": []}

    monkeypatch.setattr(
        agent_routes, "_resolve_room", lambda *_a, **_k: SimpleNamespace(can_send=True))
    monkeypatch.setattr(
        executor, "resolve_user_input", lambda *_a, **_k: world["delivered"])

    async def _dispatch(*_a, **_k):
        return False

    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)

    async def _record(*_a, **_k):
        return None

    monkeypatch.setattr(reply_card, "record_reply_answer", _record)

    def _spy(name, result):
        async def _f(*_a, **_k):
            world["calls"].append(name)
            return result
        return _f

    monkeypatch.setattr(agent_routes, "_resume_from_row", _spy("_resume_from_row", None))
    monkeypatch.setattr(agent_routes, "_late_decline_from_row", _spy("_late_decline_from_row", False))
    monkeypatch.setattr(
        agent_routes, "_late_answer_from_row", _spy("_late_answer_from_row", (None, False)))
    monkeypatch.setattr(
        chat_recovery, "card_answer_after_restart", _spy("card_answer_after_restart", None))

    user = SimpleNamespace(email="a@x.io", organization_id="org1")

    def call(*, declined: bool = False, rid: str = "req1"):
        req = agent_routes.UserInputResponseRequest(
            request_id=rid, answer="" if declined else "yes",
            thread_id="t1", declined=declined,
        )
        return asyncio.run(agent_routes.respond_user_input(req, user))

    world["call"] = call
    return world


def _refusal(call, **kw):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        call(**kw)
    return e.value


@pytest.mark.parametrize("declined", [False, True])
def test_a_repeat_gets_the_answered_once_409_and_reaches_no_late_path(route, declined):
    route["delivered"] = True
    first = route["call"](declined=declined)
    assert first.get("ok") is True
    route["delivered"] = False
    err = _refusal(route["call"], declined=declined)
    assert err.status_code == 409
    assert isinstance(err.detail, dict) and err.detail["error"] == "answered_once"
    # The repeat starts nothing: no late resume, no late decline, no resend.
    assert route["calls"] == []


def test_a_repeated_decline_never_reaches_the_late_decline_resume(route):
    """The reviewer's open point: the source order alone did not prove it."""
    route["delivered"] = True
    route["call"](declined=True)
    route["delivered"] = False
    for _ in range(2):
        _refusal(route["call"], declined=True)
    assert "_resume_from_row" not in route["calls"]
    assert "_late_decline_from_row" not in route["calls"]


@pytest.mark.parametrize("declined", [False, True])
def test_a_stale_card_with_no_mark_keeps_todays_plain_409(route, declined):
    """A card whose run ended, with no answer taken: today's path, unchanged."""
    err = _refusal(route["call"], declined=declined, rid="stale")
    assert err.status_code == 409
    assert isinstance(err.detail, str) and "No pending question" in err.detail
    assert "_resume_from_row" in route["calls"]
    if declined:
        assert "_late_decline_from_row" in route["calls"]
    else:
        assert "card_answer_after_restart" in route["calls"]
