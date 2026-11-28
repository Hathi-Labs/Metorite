"""HRD-16: a card answer and a steer act once (chat_failure_modes.md G1).

Mutations this suite catches:

* ``buffer_guidance`` no longer drops a repeated signal id: two notes.
* ``mark_answered`` is a no-op: the second ``was_answered`` read is False.
* the route no longer reads ``was_answered`` before the restart path: the
  source check and the route tests below fail.
* the repeat gets the plain 409 again: the route tests below fail.

Task 4036 adds two more (see the section near the end):

* the mark holds no responder: a repeat by the same member no longer says
  ``by_you``, and the browser gives the words back.
* the route marks an answer that a run on another event loop did not take:
  a repeat gets ``answered_once`` and loses the late path.

Hermetic: the Redis client is a fake. No SQL, so R8 does not apply.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
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
    assert calls.get("answered_by") and calls.get("_resume_from_row")
    assert min(calls["answered_by"]) < min(calls["_resume_from_row"])
    assert calls.get("mark_answered")


def test_route_reads_the_mark_in_one_place():
    """ONE check of the mark, the one that raises ``answered_once``.

    A merge of HRD-17a once kept a second, older block after the late-decline
    branch, and that block raised the plain 409. A second read of the mark
    (``answered_by`` or ``was_answered``) in the route goes red here.
    """
    calls = _route_calls()
    assert len(calls.get("answered_by", [])) == 1, calls.get("answered_by")
    assert "was_answered" not in calls
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

    # ``taken`` (task 4036): None means the run is on the route's own event
    # loop, so the answer of ``resolve_user_input`` is final. True or False is
    # what a run on another event loop said later.
    world: dict = {"delivered": False, "calls": [], "taken": None}

    monkeypatch.setattr(
        agent_routes, "_resolve_room", lambda *_a, **_k: SimpleNamespace(can_send=True))

    def _resolve(*_a, taken=None, **_k):
        if world["delivered"] and world["taken"] is not None and taken is not None:
            taken.set_running_or_notify_cancel()
            taken.set_result(world["taken"])
        return world["delivered"]

    monkeypatch.setattr(executor, "resolve_user_input", _resolve)

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

    def call(
        *, declined: bool = False, rid: str = "req1", email: str = "a@x.io",
        extra: dict | None = None,
    ):
        user = SimpleNamespace(email=email, organization_id="org1")
        req = agent_routes.UserInputResponseRequest.model_validate({
            "request_id": rid, "answer": "" if declined else "yes",
            "thread_id": "t1", "declined": declined, **(extra or {}),
        })
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


# ── Task 4036: the mark names who answered ──────────────────────────────────
#
# A phone posts an answer, the run takes it, and the response is lost. The
# member taps again. Before this change the repeat said "Someone already
# answered this", the browser put the words back in the composer, and a send
# of them acted a second time. Now the mark holds the responder from the
# session, so the 409 says that the same member answered, and the browser
# keeps the words out (``respondInput.test.ts``, "the same member").
#
# Mutations these catch:
#
# * the mark value goes back to ``"1"``: ``answered_by`` reads "" and the
#   same-member repeat has no ``by_you`` (red).
# * the route compares with a responder from the request body: the forged
#   ``responder`` field changes the answer (red).
# * ``by_you`` is true for any member: the other-member test is red.


def test_the_mark_holds_the_responder(fake):
    async def go():
        await answer_once.mark_answered("org1", "t1", "req1", responder=" A@X.io ")
        return await answer_once.answered_by("org1", "t1", "req1")

    assert asyncio.run(go()) == "a@x.io"


def test_a_mark_with_no_responder_reads_as_unknown(fake):
    async def go():
        none_yet = await answer_once.answered_by("org1", "t1", "req1")
        await answer_once.mark_answered("org1", "t1", "req1")
        return none_yet, await answer_once.answered_by("org1", "t1", "req1")

    assert asyncio.run(go()) == (None, "")


def test_the_first_responder_keeps_the_mark(fake):
    async def go():
        await answer_once.mark_answered("org1", "t1", "req1", responder="a@x.io")
        await answer_once.mark_answered("org1", "t1", "req1", responder="b@x.io")
        return await answer_once.answered_by("org1", "t1", "req1")

    assert asyncio.run(go()) == "a@x.io"


@pytest.mark.parametrize("declined", [False, True])
def test_a_repeat_by_the_same_member_says_by_you_and_acts_never(route, declined):
    route["delivered"] = True
    assert route["call"](declined=declined).get("ok") is True
    route["delivered"] = False
    # The second tap of the same member, from a phone that lost the response.
    err = _refusal(route["call"], declined=declined, email="A@x.io")
    assert err.status_code == 409
    assert err.detail["error"] == "answered_once"
    assert err.detail["by_you"] is True
    assert "You already answered" in err.detail["message"]
    # Nothing acts a second time: no resume, no late decline, no resend.
    assert route["calls"] == []


def test_a_repeat_by_another_member_says_not_by_you(route):
    route["delivered"] = True
    route["call"]()
    route["delivered"] = False
    err = _refusal(route["call"], email="b@x.io")
    assert err.detail["error"] == "answered_once"
    assert err.detail["by_you"] is False
    assert route["calls"] == []


def test_the_responder_comes_from_the_session_never_the_body(route, fake):
    route["delivered"] = True
    route["call"](email="a@x.io", extra={"responder": "b@x.io"})
    assert set(fake.data.values()) == {"a@x.io"}
    route["delivered"] = False
    # A body that names the first member does not make b's repeat "by you".
    err = _refusal(route["call"], email="b@x.io", extra={"responder": "a@x.io"})
    assert err.detail["by_you"] is False


def test_an_old_mark_with_no_responder_names_nobody(route, fake):
    route["delivered"] = True
    route["call"]()
    route["delivered"] = False
    for key in list(fake.data):
        fake.data[key] = "1"  # a mark from before task 4036
    err = _refusal(route["call"])
    assert err.detail["error"] == "answered_once"
    assert "by_you" not in err.detail


# ── Task 4036, part 2: the park race ────────────────────────────────────────
#
# A run on another event loop takes an answer later, in a callback on its own
# loop. Before this change the route counted the answer as taken at once and
# set the mark. If ``pending_ask.park`` popped the Future first, nothing took
# the answer, and for 600 s a second answer got ``answered_once`` and no late
# path. Now the callback says whether it took the answer, and the route marks
# only a taken answer.
#
# Mutations these catch:
#
# * the callback sets the result on a Future that the park popped: the
#   executor test reads ``taken`` True (red).
# * the route marks before it reads ``taken``: the repeat gets
#   ``answered_once`` (red).


def _cross_loop(pop_first: bool) -> tuple[bool, bool]:
    """Answer a Future of another event loop. ``(taken, the Future is done)``."""
    executor = pytest.importorskip("orchestrator.executor")
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        executor._pending_user_input.park("rid-4036", fut, "t-4036")
        taken: concurrent.futures.Future[bool] = concurrent.futures.Future()
        assert executor.resolve_user_input(
            "rid-4036", "yes", thread_id="t-4036", taken=taken,
        ) is True
        assert taken.running() or taken.done()
        if pop_first:
            # The park pops the Future, with no await, before the callback.
            executor._pending_user_input.pop("rid-4036", None)
        loop.run_until_complete(asyncio.sleep(0))
        return taken.result(timeout=1), fut.done()
    finally:
        executor._pending_user_input.pop("rid-4036", None)
        loop.close()


def test_a_cross_loop_answer_that_the_run_takes_says_taken():
    assert _cross_loop(pop_first=False) == (True, True)


def test_a_cross_loop_answer_after_the_park_is_not_taken():
    assert _cross_loop(pop_first=True) == (False, False)


def test_a_same_loop_answer_leaves_taken_untouched():
    executor = pytest.importorskip("orchestrator.executor")

    async def go():
        fut = asyncio.get_running_loop().create_future()
        executor._pending_user_input.park("rid-4036s", fut, "t-4036")
        taken: concurrent.futures.Future[bool] = concurrent.futures.Future()
        try:
            ok = executor.resolve_user_input(
                "rid-4036s", "yes", thread_id="t-4036", taken=taken)
            return ok, fut.done(), taken.running() or taken.done()
        finally:
            executor._pending_user_input.pop("rid-4036s", None)

    assert asyncio.run(go()) == (True, True, False)


def test_an_answer_the_parked_run_did_not_take_leaves_no_mark(route, fake):
    route["delivered"] = True
    route["taken"] = False
    _refusal(route["call"])  # no run took it: the late path, then the plain 409
    assert fake.data == {}
    assert "_late_answer_from_row" in route["calls"]
    route["calls"].clear()
    route["delivered"] = False
    route["taken"] = None
    err = _refusal(route["call"])
    # The second answer takes the late path, not ``answered_once``.
    assert isinstance(err.detail, str)
    assert "_late_answer_from_row" in route["calls"]


def test_an_answer_the_cross_loop_run_took_keeps_the_mark(route, fake):
    route["delivered"] = True
    route["taken"] = True
    assert route["call"]().get("ok") is True
    assert set(fake.data.values()) == {"a@x.io"}
