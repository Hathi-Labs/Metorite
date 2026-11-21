"""HRD-16: a card answer and a steer act once (chat_failure_modes.md G1).

Mutations this suite catches:

* ``buffer_guidance`` no longer drops a repeated signal id: two notes.
* ``mark_answered`` is a no-op: the second ``was_answered`` read is False.
* the route no longer reads ``was_answered`` before the restart path: the
  source check below fails.

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


def test_repeat_answer_is_a_409_not_a_false_success():
    src = pathlib.Path("apps/services/gateway/gateway/routes/agent.py").read_text(encoding="utf-8")
    i = src.index("was_answered(_org_id")
    assert "raise _plain_409()" in src[i:i + 900]
