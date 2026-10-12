"""WS-17 EM-T17 PR-A: the email draft does not stall (§17.3.7).

The fences of the server half of the stall fix:

* A1b. On the interactive path, a model stream timeout reaches the route as an
  ``error`` event with a ``reason`` (``model_slow``, ``model_stalled`` or
  ``model_timeout``). It never becomes the neutral template "Thanks for your
  email". The rule path still gets the ``NO_DRAFT`` sentinel.
* A2. The stream route sends ``status`` before it opens a session, and
  ``: keepalive`` after ``HEARTBEAT_S`` of quiet. A client disconnect cancels
  the task.
* A3. ``hydrate_message_body`` calls ``get_message_body``, never
  ``get_message``, and each fetch ends at ``BODY_FETCH_TIMEOUT_S``.
* ``email.draft_timing`` carries the stage durations and the outcome, and no
  tenant text.
* A7 (PR-A2). The reply path sends the ``context`` ``done`` event, once and
  before the draft starts.
* A2b (PR-A1). The context step of ``_compose_assist_run`` opens two short
  blocks. The body fetch and the embed run with no session. Past
  ``SIDE_STEP_BOUND_S`` a side step gives nothing, and the draft goes on. B2:
  a refreshed token is written in a block that the context bound cannot roll
  back.
* A2c (PR-A1). Past ``CONTEXT_STEP_BOUND_S`` the draft goes on with the body
  that the step holds, the thread or "", and each side block empty. B3: an
  expiry before the owner check and the target row raises.
* A4 (PR-A1). No session is open during the model call. A short new block
  stores the draft. The R8 half is ``test_email_draft_store_rls.py``.

Hermetic: the model, the provider and the session are fakes.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from acb_llm import context as llm_ctx
from gateway.routes.email import core
from gateway.routes.email.automation import drafting
from gateway.routes.email.automation.identity import SelfIdentity

from tests.unit._email_fakes import bind_db

_EMAIL = {"from": "alice@example.com", "from_name": "Alice", "subject": "Quote",
          "body": "Can you send pricing?"}
_TEMPLATE = "Thanks for your email"


async def _delta(_kind: str, _text: str) -> None:
    return None


def _raiser(exc: BaseException):
    async def fake(**_kw: Any) -> Any:
        raise exc
    return fake


# ── A1b: the timeout passes through the drafters ───────────────────────────


@pytest.mark.parametrize("exc", [
    llm_ctx.StreamFirstChunkTimeout(45.0),
    llm_ctx.StreamIdleTimeout(30.0),
    llm_ctx.StreamTotalTimeout(150.0),
], ids=["first_chunk", "idle", "total"])
async def test_the_interactive_reply_draft_raises_the_stream_timeout(
    monkeypatch, exc,
) -> None:
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text", _raiser(exc))
    with pytest.raises(type(exc)):
        await drafting._llm_draft_reply(
            _EMAIL, about="", signature="", interactive_fallback=True,
            on_delta=_delta)


async def test_the_rule_path_keeps_the_sentinel_on_a_stream_timeout(monkeypatch) -> None:
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text",
                        _raiser(llm_ctx.StreamIdleTimeout(30.0)))
    body = await drafting._llm_draft_reply(
        _EMAIL, about="", signature="", interactive_fallback=False,
        on_delta=_delta)
    assert body == drafting.DRAFT_NO_DRAFT_SENTINEL


async def test_any_other_interactive_failure_still_gives_the_template(monkeypatch) -> None:
    """The timeout is the ONE failure that passes through. A model error keeps
    the behaviour it had, so this slice changes nothing else."""
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text",
                        _raiser(RuntimeError("provider 500")))
    body = await drafting._llm_draft_reply(
        _EMAIL, about="", signature="", interactive_fallback=True,
        on_delta=_delta)
    assert _TEMPLATE in body


async def test_compose_assist_raises_the_stream_timeout(monkeypatch) -> None:
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text",
                        _raiser(llm_ctx.StreamTotalTimeout(150.0)))
    with pytest.raises(llm_ctx.StreamTotalTimeout):
        await drafting._llm_compose_assist(
            about="", signature="", current_body="My own words", instruction="",
            mode="new", on_delta=_delta)


async def test_compose_assist_keeps_the_body_of_the_member_on_another_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text",
                        _raiser(RuntimeError("provider 500")))
    out = await drafting._llm_compose_assist(
        about="", signature="", current_body="My own words", instruction="",
        mode="new", on_delta=_delta)
    assert out == "My own words"


# ── The stream route ────────────────────────────────────────────────────────


class _Spy:
    """Records, in order, the events of the stream and the session opens."""

    def __init__(self) -> None:
        self.order: list[str] = []

    @asynccontextmanager
    async def session(self):
        self.order.append("session")
        yield AsyncMock()


def _req(**kw: Any) -> drafting.ComposeAssistRequest:
    return drafting.ComposeAssistRequest(account_id="acc-1", **kw)


_USER = SimpleNamespace(email="owner@a.test")


def _wire_real_run(monkeypatch, spy: _Spy, compose: Any) -> None:
    """Keep the real ``_compose_assist_run`` and fake what it reads."""
    for name, value in (
        ("_tenant_session", spy.session),
        ("_assert_account_owner", AsyncMock()),
        ("_load_assistant_about", AsyncMock(return_value=("", ""))),
        ("resolve_self", AsyncMock(return_value=SelfIdentity())),
        ("_llm_compose_assist", compose),
    ):
        monkeypatch.setattr(drafting, name, value)


async def _drain(gen) -> list[str]:
    return [item async for item in gen]


def _events(items: list[str]) -> list[dict[str, Any]]:
    return [json.loads(i[len("data: "):]) for i in items if i.startswith("data: ")]


async def test_the_route_sends_status_before_it_opens_a_session(monkeypatch) -> None:
    spy = _Spy()
    _wire_real_run(monkeypatch, spy, AsyncMock(return_value="Hello Priya"))
    resp = await drafting.compose_assist_stream(_req(), _USER)
    gen = resp.body_iterator
    first = await gen.__anext__()
    assert first.startswith("data: ")
    assert json.loads(first[len("data: "):]) == {"type": "status", "stage": "context"}
    assert spy.order == [], "the route opened a session before its first event"
    rest = _events(await _drain(gen))
    assert spy.order == ["session"]
    assert rest[-1] == {"type": "done", "draft": "Hello Priya"}


async def test_a_quiet_stream_sends_a_keepalive(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "HEARTBEAT_S", 0.05)

    async def slow_run(*_a: Any, **_k: Any) -> dict[str, str]:
        await asyncio.sleep(0.3)
        return {"draft": "late"}

    monkeypatch.setattr(drafting, "_compose_assist_run", slow_run)
    resp = await drafting.compose_assist_stream(_req(), _USER)
    items = await _drain(resp.body_iterator)
    keepalives = [i for i in items if i == ": keepalive\n\n"]
    assert len(keepalives) >= 2, items
    # A comment line, never a data line, so an old client skips it.
    assert items[0].startswith("data: ") and items[-1].startswith("data: ")
    assert _events(items)[-1] == {"type": "done", "draft": "late"}


async def test_the_heartbeat_is_ten_seconds() -> None:
    assert drafting.HEARTBEAT_S == 10.0


async def test_a_client_disconnect_cancels_the_task(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "HEARTBEAT_S", 0.02)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_run(*_a: Any, **_k: Any) -> dict[str, str]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"draft": ""}

    monkeypatch.setattr(drafting, "_compose_assist_run", hanging_run)
    resp = await drafting.compose_assist_stream(_req(), _USER)
    gen = resp.body_iterator
    await gen.__anext__()  # status
    await gen.__anext__()  # a keepalive: the task runs by now
    await asyncio.wait_for(started.wait(), 1.0)
    await gen.aclose()  # the client went away
    await asyncio.wait_for(cancelled.wait(), 1.0)


@pytest.mark.parametrize("exc,reason", [
    (llm_ctx.StreamFirstChunkTimeout(45.0), "model_slow"),
    (llm_ctx.StreamIdleTimeout(30.0), "model_stalled"),
    (llm_ctx.StreamTotalTimeout(150.0), "model_timeout"),
], ids=["slow", "stalled", "timeout"])
async def test_a_stream_timeout_reaches_the_route_as_an_error_with_a_reason(
    monkeypatch, exc, reason,
) -> None:
    """End to end through the real ``_compose_assist_run``: an empty reply
    goes to ``_llm_draft_reply`` with ``interactive_fallback=True``."""
    spy = _Spy()
    _wire_real_run(monkeypatch, spy, AsyncMock(return_value="unused"))
    ctx = dict(_EMAIL, thread_id="", self="me@a.test", self_label="")
    monkeypatch.setattr(drafting, "_build_reply_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(drafting, "_orchestrate_draft", _only_draft)
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text", _raiser(exc))
    resp = await drafting.compose_assist_stream(
        _req(mode="reply", message_id="m-1"), _USER)
    items = await _drain(resp.body_iterator)
    events = _events(items)
    assert events[-1]["type"] == "error", events
    assert events[-1]["reason"] == reason
    assert events[-1]["error"]
    assert not any(_TEMPLATE in json.dumps(e) for e in events), (
        "a stream timeout became the neutral template")


async def _only_draft(email, about, signature, user_email, *, model="tier-fast",
                      interactive_fallback=False, on_delta=None, **_kw):
    """``_orchestrate_draft`` with no memory or consult, so the test reaches the
    model call at once."""
    return await drafting._llm_draft_reply(
        email, about, signature, user_email=user_email, model=model,
        interactive_fallback=interactive_fallback, on_delta=on_delta)


async def test_a_new_mail_compose_timeout_reaches_the_route_too(monkeypatch) -> None:
    spy = _Spy()
    real_compose = drafting._llm_compose_assist
    _wire_real_run(monkeypatch, spy, real_compose)
    monkeypatch.setattr("acb_llm.context.acompletion_stream_text",
                        _raiser(llm_ctx.StreamIdleTimeout(30.0)))
    resp = await drafting.compose_assist_stream(_req(mode="new"), _USER)
    events = _events(await _drain(resp.body_iterator))
    assert events[-1]["type"] == "error"
    assert events[-1]["reason"] == "model_stalled"


async def test_any_other_failure_keeps_the_old_error_shape(monkeypatch) -> None:
    async def broken(*_a: Any, **_k: Any) -> dict[str, str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(drafting, "_compose_assist_run", broken)
    resp = await drafting.compose_assist_stream(_req(), _USER)
    events = _events(await _drain(resp.body_iterator))
    assert events[-1] == {"type": "error", "error": "draft failed"}


# ── A2d: the server ends every streamed draft by SERVER_TOTAL_S ─────────────


def test_the_server_total_is_170_seconds_and_below_the_client_total() -> None:
    """PR-A2 hardcodes 170 on the client against ``CLIENT_TOTAL_MS=180000``.
    The server must end first, so the member sees the server's reason."""
    assert drafting.SERVER_TOTAL_S == 170.0
    assert drafting.SERVER_TOTAL_S * 1000 < 180_000


async def test_a_draft_past_the_server_total_ends_with_server_timeout(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "SERVER_TOTAL_S", 0.1)
    monkeypatch.setattr(drafting, "HEARTBEAT_S", 5.0)

    async def hanging_run(*_a: Any, **_k: Any) -> dict[str, str]:
        await asyncio.Event().wait()
        return {"draft": ""}

    monkeypatch.setattr(drafting, "_compose_assist_run", hanging_run)
    with structlog.testing.capture_logs() as logs:
        resp = await drafting.compose_assist_stream(_req(), _USER)
        events = _events(await asyncio.wait_for(_drain(resp.body_iterator), 2.0))
    assert events[-1]["type"] == "error"
    assert events[-1]["reason"] == "server_timeout"
    assert not any(_TEMPLATE in json.dumps(e) for e in events)
    line = next(e for e in logs if e["event"] == "email.draft_timing")
    assert line["outcome"] == "server_timeout"


async def test_a_timeout_error_inside_the_run_is_not_the_server_bound(monkeypatch) -> None:
    async def inner_timeout(*_a: Any, **_k: Any) -> dict[str, str]:
        raise TimeoutError("some inner wait")

    monkeypatch.setattr(drafting, "_compose_assist_run", inner_timeout)
    resp = await drafting.compose_assist_stream(_req(), _USER)
    events = _events(await _drain(resp.body_iterator))
    assert events[-1] == {"type": "error", "error": "draft failed"}


# ── email.draft_timing ──────────────────────────────────────────────────────

_PLANTED = ("SUBJWORD41", "BODYWORD42", "INSTRWORD43", "plant44@x.test",
            "DRAFTWORD45", "DELTAWORD46", "FETCHWORD48", "sender49@x.test")

#: A2e: every key of `email.draft_timing` (§17.8).
_TIMING_KEYS = ("context_ms", "decide_ms", "fetch_ms", "first_chunk_ms",
                "total_ms", "outcome", "mode")


def _timed_run_factory(hydrate_db: Any = None):
    """A stand-in for ``_compose_assist_run``. With ``hydrate_db`` it runs a
    real ``hydrate_message_body`` in its context step, as a reply does."""

    async def _timed_run(req, user, on_activity=None, on_delta=None):
        await on_activity({"kind": "stage", "id": "context", "status": "start"})
        if hydrate_db is not None:
            await core.hydrate_message_body(hydrate_db, "m-1", "owner@a.test")
        await asyncio.sleep(0.08)
        await on_activity({"kind": "stage", "id": "draft", "status": "start"})
        await asyncio.sleep(0.01)
        await on_delta("content", "DELTAWORD46")
        return {"draft": "Hello DRAFTWORD45"}

    return _timed_run


_timed_run = _timed_run_factory()


def _timing_req() -> drafting.ComposeAssistRequest:
    return _req(subject="SUBJWORD41", body="BODYWORD42",
                instruction="INSTRWORD43", to=["plant44@x.test"])


async def _timing_line(monkeypatch, run: Any, req: Any) -> dict[str, Any]:
    monkeypatch.setattr(drafting, "_compose_assist_run", run)
    with structlog.testing.capture_logs() as logs:
        resp = await drafting.compose_assist_stream(req, _USER)
        await _drain(resp.body_iterator)
    return next(e for e in logs if e["event"] == "email.draft_timing")


async def test_the_timing_line_has_its_keys_and_no_tenant_text(monkeypatch) -> None:
    """A2e. The draft fetches a body with a planted word and a planted
    sender, and none of the planted words reaches the line."""

    async def body_with_plants(_pmid: str) -> Any:
        await asyncio.sleep(0.06)
        return SimpleNamespace(
            body_text="From sender49@x.test: FETCHWORD48", body_html=None)

    db = _hydrate_world(monkeypatch, _provider(get_message_body=body_with_plants))
    line = await _timing_line(monkeypatch, _timed_run_factory(db), _timing_req())
    for key in _TIMING_KEYS:
        assert key in line, (key, line)
    assert line["outcome"] == "done"
    # Windows ticks the clock at about 15.6 ms, so each floor has a margin.
    assert line["context_ms"] >= 50
    assert line["total_ms"] >= line["context_ms"]
    assert isinstance(line["first_chunk_ms"], int)
    assert isinstance(line["fetch_ms"], int) and line["fetch_ms"] >= 40
    assert line["fetch_ms"] <= line["context_ms"]
    assert line["decide_ms"] is None, "decide_ms stays null until PR-C"
    text = repr(line)
    for word in _PLANTED:
        assert word not in text, word
    assert "owner@a.test" not in text


async def test_fetch_ms_is_null_when_no_fetch_ran(monkeypatch) -> None:
    line = await _timing_line(monkeypatch, _timed_run, _timing_req())
    assert line["fetch_ms"] is None
    assert line["decide_ms"] is None


async def test_a_stored_body_makes_no_fetch_and_no_fetch_ms(monkeypatch) -> None:
    stored = SimpleNamespace(body_text="Already here.", body_html=None,
                             snippet="", received_at=None, stored_bytes=0)
    db = AsyncMock()
    db.execute.side_effect = [_one(stored)]
    line = await _timing_line(monkeypatch, _timed_run_factory(db), _timing_req())
    assert line["fetch_ms"] is None


async def test_a_hydrate_outside_the_route_records_nothing(monkeypatch) -> None:
    """Each other caller of the hydrate (the rule draft, the nudge) sets no
    list, so the hydrate records no time anywhere."""

    async def body_only(_pmid: str) -> Any:
        return SimpleNamespace(body_text="text", body_html=None)

    db = _hydrate_world(monkeypatch, _provider(get_message_body=body_only))
    assert core.body_fetch_times.get() is None
    await core.hydrate_message_body(db, "m-1", "owner@a.test")
    assert core.body_fetch_times.get() is None


async def test_the_timing_line_names_the_timeout_reason(monkeypatch) -> None:
    async def stalled(*_a: Any, **_k: Any) -> dict[str, str]:
        raise llm_ctx.StreamIdleTimeout(30.0)

    monkeypatch.setattr(drafting, "_compose_assist_run", stalled)
    with structlog.testing.capture_logs() as logs:
        resp = await drafting.compose_assist_stream(_req(), _USER)
        await _drain(resp.body_iterator)
    line = next(e for e in logs if e["event"] == "email.draft_timing")
    assert line["outcome"] == "model_stalled"
    assert line["first_chunk_ms"] is None


async def test_a_free_text_mode_never_reaches_the_timing_line(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "_compose_assist_run", _timed_run)
    with structlog.testing.capture_logs() as logs:
        resp = await drafting.compose_assist_stream(_req(mode="MODEWORD47"), _USER)
        await _drain(resp.body_iterator)
    line = next(e for e in logs if e["event"] == "email.draft_timing")
    assert line["mode"] == "other"


# ── A3: the body fetch ──────────────────────────────────────────────────────


def _one(obj: Any) -> Any:
    res = MagicMock()
    res.fetchone.return_value = obj
    return res


def _hydrate_world(monkeypatch, provider: Any) -> Any:
    stored = SimpleNamespace(body_text="", body_html=None, snippet="short",
                             received_at=None, stored_bytes=0)
    db = AsyncMock()
    db.execute.side_effect = [_one(stored), MagicMock(), MagicMock()]

    async def fake_provider_for_message(_db, _mid, _u):
        return provider, "pmid-1", "acc-1", MagicMock()

    monkeypatch.setattr(core, "_provider_for_message", fake_provider_for_message)
    return db


def _provider(**methods: Any) -> Any:
    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=True)
    provider.credentials_dirty = MagicMock(return_value=False)

    async def refuse(_pmid: str) -> Any:
        raise AssertionError("hydrate called get_message, which downloads the files")

    provider.get_message = refuse
    for name, fn in methods.items():
        setattr(provider, name, fn)
    return provider


async def test_hydrate_reads_the_body_only(monkeypatch) -> None:
    calls: list[str] = []

    async def body_only(pmid: str) -> Any:
        calls.append(pmid)
        return SimpleNamespace(body_text="The full body of the mail.", body_html=None)

    db = _hydrate_world(monkeypatch, _provider(get_message_body=body_only))
    body = await core.hydrate_message_body(db, "m-1", "owner@a.test")
    assert body == "The full body of the mail."
    assert calls == ["pmid-1"]


async def test_a_body_fetch_ends_at_its_bound(monkeypatch) -> None:
    monkeypatch.setattr(core, "BODY_FETCH_TIMEOUT_S", 0.1)

    async def hangs(_pmid: str) -> Any:
        await asyncio.Event().wait()

    db = _hydrate_world(monkeypatch, _provider(get_message_body=hangs))
    with structlog.testing.capture_logs() as logs:
        body = await asyncio.wait_for(
            core.hydrate_message_body(db, "m-1", "owner@a.test"), 2.0)
    assert body == ""
    line = next(e for e in logs if e["event"] == "hydrate_message_body.timeout")
    assert line["bound_s"] == 0.1


def test_the_body_fetch_bound_is_eight_seconds() -> None:
    assert core.BODY_FETCH_TIMEOUT_S == 8.0


# ── A7 (PR-A2): the reply path sends the context done event ───────────────


_CTX = {
    "from": "ravi@example.com", "from_name": "Ravi", "subject": "Quote",
    "body": "Please send it.", "thread": "", "thread_id": "t1",
    "self": "sales@example.com", "self_label": "Sales",
}


def _wire(monkeypatch, *, order: list[str]) -> None:
    async def _agent_draft(*_a, **_kw) -> str:
        order.append("draft")
        return "Hello Ravi"

    async def _compose(**_kw) -> str:
        order.append("draft")
        return "Hello Ravi"

    for name, value in (
        ("_tenant_session", bind_db(AsyncMock())),
        ("_assert_account_owner", AsyncMock()),
        ("_build_reply_context", AsyncMock(return_value=dict(_CTX))),
        ("_load_assistant_about", AsyncMock(return_value=("", ""))),
        ("_agent_draft_reply", _agent_draft),
        ("_llm_compose_assist", _compose),
        ("_store_ai_draft", AsyncMock()),
    ):
        monkeypatch.setattr(drafting, name, value)


def _context_events(events: list[dict]) -> list[str]:
    return [e.get("status", "") for e in events
            if e.get("kind") == "stage" and e.get("id") == "context"]


async def test_the_reply_path_sends_the_context_done_event(monkeypatch) -> None:
    """A7: an empty reply goes to the reply drafter. The run sends
    ``context`` ``done`` once, and before the draft starts."""
    order: list[str] = []
    events: list[dict] = []

    async def on_activity(evt: dict) -> None:
        events.append(evt)
        if evt.get("id") == "context":
            order.append(f"context:{evt.get('status')}")

    _wire(monkeypatch, order=order)
    out = await drafting._compose_assist_run(
        drafting.ComposeAssistRequest(
            account_id="acc-1", mode="reply", message_id="m1"),
        SimpleNamespace(email="me@example.com"),
        on_activity=on_activity)

    assert out["draft"].startswith("Hello Ravi")
    assert _context_events(events) == ["start", "done"]
    assert order == ["context:start", "context:done", "draft"]


@pytest.mark.parametrize("mode,body", [
    ("reply", "My own words."),
    ("forward", ""),
    ("new", ""),
])
async def test_each_other_path_sends_context_done_once(
        monkeypatch, mode: str, body: str) -> None:
    """The improve, forward and new paths keep their one ``context``
    ``done``. The A7 change adds no second one."""
    order: list[str] = []
    events: list[dict] = []

    async def on_activity(evt: dict) -> None:
        events.append(evt)

    _wire(monkeypatch, order=order)
    monkeypatch.setattr(drafting, "resolve_self", AsyncMock(
        return_value=SimpleNamespace(address="sales@example.com", label="Sales")))
    await drafting._compose_assist_run(
        drafting.ComposeAssistRequest(
            account_id="acc-1", mode=mode, body=body,
            message_id="m1" if mode != "new" else None,
            to=["ravi@example.com"]),
        SimpleNamespace(email="me@example.com"),
        on_activity=on_activity)

    assert _context_events(events) == ["start", "done"]


# ── PR-A1: the context step holds no session across the provider ──────────


class _Blocks:
    """A ``_tenant_session`` double that numbers each block, knows which
    block is open, and records each step with the block it ran in. A step
    with no block open records ``None``."""

    def __init__(self) -> None:
        self.count = 0
        self.current: int | None = None
        self.trace: list[tuple[str, int | None]] = []
        self.committed: list[int] = []
        self.rolled_back: list[int] = []

    @asynccontextmanager
    async def session(self):
        self.count += 1
        n = self.count
        self.current = n
        try:
            yield SimpleNamespace(block=n)
        except BaseException:
            self.rolled_back.append(n)
            raise
        else:
            self.committed.append(n)
        finally:
            self.current = None

    def step(self, name: str, result: Any = None, *, hang: bool = False):
        """An async fake that records ``name`` and its block."""

        async def fake(*_a: Any, **_k: Any) -> Any:
            self.trace.append((name, self.current))
            if hang:
                await asyncio.Event().wait()
            return result

        return fake

    def block_of(self, name: str) -> int | None:
        return next(b for n, b in self.trace if n == name)

    def names(self) -> list[str]:
        return [n for n, _b in self.trace]


_TARGET = SimpleNamespace(
    id="m-1", account_id="acc-1", provider_message_id="pm-1", thread_id="t-1",
    subject="Meeting next week", body_text="", snippet="SNIPPET-OF-THE-MAIL",
    from_address={"email": "ravi@ext.test", "name": "Ravi"},
    to_addresses=[], cc_addresses=[])
_FULL = "FULL BODY: can we meet on Tuesday?"


def _phased_world(monkeypatch, *, hang: str = "", stored_body: str = "",
                  dirty: bool = False) -> SimpleNamespace:
    """The real phased ``_compose_assist_run`` over fakes of each read. The
    step named ``hang`` never returns."""
    w = _Blocks()
    seen: dict[str, Any] = {}

    def step(name: str, result: Any = None) -> Any:
        return w.step(name, result, hang=(name == hang))

    async def fetch_body(_pmid: str) -> Any:
        w.trace.append(("fetch", w.current))
        if hang == "fetch":
            await asyncio.Event().wait()
        return SimpleNamespace(body_text=_FULL, body_html=None)

    provider = MagicMock()
    provider.authenticate = step("authenticate", True)
    provider.get_message_body = fetch_body
    provider.credentials_dirty = MagicMock(return_value=dirty)

    async def read_fewshot(_db, _aid, qvec, **_k) -> str:
        w.trace.append(("fewshot", w.current))
        return "FEWSHOT" if qvec else ""

    async def model(email, *_a, **_k) -> str:
        w.trace.append(("model", w.current))
        seen["ctx"] = dict(email)
        return "Hello Ravi"

    async def compose_model(**kw) -> str:
        w.trace.append(("model", w.current))
        seen["compose"] = kw
        return "Improved"

    row = SimpleNamespace(body_text=stored_body, body_html=None,
                          snippet=_TARGET.snippet, received_at=None,
                          stored_bytes=0)
    target = SimpleNamespace(**{**vars(_TARGET), "body_text": stored_body})
    for name, value in (
        ("_tenant_session", w.session),
        ("_assert_account_owner", step("owner")),
        ("_load_assistant_about", step("about", ("About me", "Sig"))),
        ("_reply_target", step("target", target)),
        ("resolve_self", step("self", SelfIdentity(
            address="me@a.test", label="Me",
            self_addresses=frozenset({"me@a.test"})))),
        ("resolve_org_domains", step("domains", frozenset())),
        ("_attachment_summaries", step("files", {})),
        ("_fetch_thread_context", step("thread", "THREAD")),
        ("_fetch_sender_reply_examples", step("examples", "EXAMPLES")),
        ("_fetch_reply_memories", step("memories", "MEMORIES")),
        ("_read_sent_fewshot", read_fewshot),
        ("_fetch_calendar_context", step("calendar", "CALENDAR")),
        ("_embed_for_fewshot", step("embed", [0.1, 0.2])),
        ("_agent_draft_reply", model),
        ("_llm_compose_assist", compose_model),
        ("_store_ai_draft", step("store_draft")),
    ):
        monkeypatch.setattr(drafting, name, value)
    for name, value in (
        ("_hydrate_row", step("hydrate_row", row)),
        ("_provider_for_message", step(
            "load_provider", (provider, "pm-1", "acc-1", MagicMock()))),
        ("_store_hydrated_body", step("write_body")),
        ("_persist_rotated_creds", step("write_creds")),
    ):
        monkeypatch.setattr(core, name, value)
    return SimpleNamespace(blocks=w, seen=seen)


def _reply_req(body: str = "") -> drafting.ComposeAssistRequest:
    return drafting.ComposeAssistRequest(
        account_id="acc-1", mode="reply", message_id="m-1", body=body)


_BLOCK_1 = ("owner", "about", "target", "hydrate_row", "load_provider", "self",
            "domains", "files", "thread", "examples")
_SIDE = ("sender_examples", "reply_memories", "sent_examples", "calendar")


async def test_the_context_step_opens_two_short_blocks(monkeypatch) -> None:
    """A2b and A4. Block 1 reads. The fetch and the embed run with no
    session. Block 2 writes the body and the creds, then reads. The model
    runs with no session, and a short new block stores the draft."""
    world = _phased_world(monkeypatch, dirty=True)
    w = world.blocks
    out = await asyncio.wait_for(
        drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert out == {"draft": "Hello Ravi\n\nSig"}

    b1 = w.block_of("target")
    assert b1 is not None
    for name in _BLOCK_1:
        assert w.block_of(name) == b1, (name, w.trace)
    for name in ("authenticate", "fetch", "embed"):
        assert w.block_of(name) is None, f"a session was open during {name}"
    write = w.block_of("write_body")
    assert write not in (None, b1) and w.block_of("write_creds") == write
    read = w.block_of("memories")
    assert read not in (None, b1, write)
    assert w.block_of("fewshot") == read and w.block_of("calendar") == read
    order = w.names()
    assert (order.index("fetch") < order.index("embed")
            < order.index("write_body") < order.index("write_creds")
            < order.index("memories"))
    assert w.block_of("model") is None, "a session was open during the model"
    assert w.block_of("store_draft") not in (None, b1, write, read)
    assert w.rolled_back == []

    ctx = world.seen["ctx"]
    assert ctx["body"] == _FULL
    assert (ctx["thread"], ctx["sender_examples"]) == ("THREAD", "EXAMPLES")
    assert (ctx["reply_memories"], ctx["sent_examples"], ctx["calendar"]) == (
        "MEMORIES", "FEWSHOT", "CALENDAR")


async def test_improve_and_new_mail_call_the_model_with_no_session(
    monkeypatch,
) -> None:
    """A4 for the compose model too: the improve path and new mail."""
    world = _phased_world(monkeypatch)
    w = world.blocks
    await drafting._compose_assist_run(_reply_req(body="My own words."), _USER)
    assert w.block_of("model") is None
    assert "store_draft" not in w.names()
    w.trace.clear()
    await drafting._compose_assist_run(
        drafting.ComposeAssistRequest(account_id="acc-1", to=["r@x.test"]), _USER)
    assert w.block_of("model") is None
    assert w.block_of("self") is not None


async def test_a_stored_body_makes_no_fetch_and_no_write(monkeypatch) -> None:
    world = _phased_world(monkeypatch, stored_body="STORED BODY")
    await drafting._compose_assist_run(_reply_req(), _USER)
    names = world.blocks.names()
    assert "fetch" not in names and "load_provider" not in names
    assert "write_body" not in names and "write_creds" not in names
    assert world.seen["ctx"]["body"] == "STORED BODY"


async def test_a_fetch_past_the_side_bound_gives_nothing(monkeypatch) -> None:
    """A2b. The fetch hangs. Past SIDE_STEP_BOUND_S the draft goes on with
    the snippet, and the step logs the bound."""
    monkeypatch.setattr(drafting, "SIDE_STEP_BOUND_S", 0.05)
    world = _phased_world(monkeypatch, hang="fetch")
    with structlog.testing.capture_logs() as logs:
        out = await asyncio.wait_for(
            drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert out["draft"].startswith("Hello Ravi")
    assert world.seen["ctx"]["body"] == _TARGET.snippet
    assert "write_body" not in world.blocks.names()
    line = next(e for e in logs if e["event"] == "hydrate_message_body.timeout")
    assert line["bound_s"] == 0.05


async def test_an_embed_past_the_side_bound_gives_no_fewshot(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "SIDE_STEP_BOUND_S", 0.05)
    world = _phased_world(monkeypatch, hang="embed")
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(
            drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert world.seen["ctx"]["sent_examples"] == ""
    assert world.seen["ctx"]["reply_memories"] == "MEMORIES"
    line = next(e for e in logs if e["event"] == "email.side_step_timeout")
    assert (line["step"], line["bound_s"]) == ("embed", 0.05)


async def test_a_refreshed_token_survives_a_fetch_past_the_side_bound(
    monkeypatch,
) -> None:
    """B2. ``authenticate()`` refreshed the token, and then the fetch passed
    SIDE_STEP_BOUND_S. Block 2a still writes the creds, and it commits."""
    monkeypatch.setattr(drafting, "SIDE_STEP_BOUND_S", 0.05)
    world = _phased_world(monkeypatch, hang="fetch", dirty=True)
    w = world.blocks
    await asyncio.wait_for(drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    creds = w.block_of("write_creds")
    assert creds is not None and creds in w.committed
    assert "write_body" not in w.names()


async def test_a_refreshed_token_survives_the_context_bound(monkeypatch) -> None:
    """B2. The CONTEXT bound ends during the fetch. The creds still land in
    a block of their own that commits, and the draft goes on."""
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.1)
    world = _phased_world(monkeypatch, hang="fetch", dirty=True)
    w = world.blocks
    out = await asyncio.wait_for(
        drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert out["draft"].startswith("Hello Ravi")
    creds = w.block_of("write_creds")
    assert creds is not None and creds in w.committed
    assert creds not in w.rolled_back


@pytest.mark.parametrize("hang", ["remember", "consult_plan"])
async def test_the_orchestrator_bounds_the_mem0_recall_and_the_plan(
    monkeypatch, hang: str,
) -> None:
    """A2b. The Mem0 recall and the consult plan each end at
    SIDE_STEP_BOUND_S, and the draft goes on. The rule path runs this too."""
    import acb_skills.memory_tools as memory_tools

    monkeypatch.setattr(drafting, "SIDE_STEP_BOUND_S", 0.05)

    async def hangs(*_a: Any, **_k: Any) -> Any:
        await asyncio.Event().wait()

    async def no_plan(*_a: Any, **_k: Any) -> list:
        return []

    monkeypatch.setattr(memory_tools, "remember",
                        hangs if hang == "remember" else AsyncMock(return_value="no relevant"))
    monkeypatch.setattr(drafting, "_draft_consult_plan",
                        hangs if hang == "consult_plan" else no_plan)
    monkeypatch.setattr(drafting, "_llm_draft_reply", AsyncMock(return_value="NO_DRAFT"))
    with structlog.testing.capture_logs() as logs:
        draft = await asyncio.wait_for(
            drafting._orchestrate_draft(_EMAIL, "", "", "owner@a.test"), 2.0)
    assert draft == "NO_DRAFT"
    steps = [e["step"] for e in logs if e["event"] == "email.side_step_timeout"]
    assert steps == ["memory" if hang == "remember" else "consult_plan"]


def test_the_side_and_context_bounds() -> None:
    assert drafting.SIDE_STEP_BOUND_S == 5.0
    assert drafting.CONTEXT_STEP_BOUND_S == 20.0
    assert core.BODY_FETCH_TIMEOUT_S == 8.0, "the hydrate keeps its own bound"


# ── A2c: the context step on expiry ────────────────────────────────────────


async def test_an_expiry_in_block_1_keeps_the_thread_and_empties_the_side(
    monkeypatch,
) -> None:
    """The sender examples hang, after the thread read. Past the bound the
    target is the snippet, the thread is what block 1 read, each side block
    is empty, and the block exited before the model."""
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.1)
    world = _phased_world(monkeypatch, hang="examples")
    w = world.blocks
    with structlog.testing.capture_logs() as logs:
        out = await asyncio.wait_for(
            drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert out["draft"].startswith("Hello Ravi")
    ctx = world.seen["ctx"]
    assert ctx["body"] == _TARGET.snippet
    assert ctx["thread"] == "THREAD"
    assert all(ctx[k] == "" for k in _SIDE), ctx
    assert w.block_of("model") is None
    assert w.block_of("examples") in w.rolled_back
    assert "fetch" not in w.names() and "memories" not in w.names()
    line = next(e for e in logs if e["event"] == "email.context_step_timeout")
    assert (line["stage"], line["ready"], line["bound_s"]) == ("block_1", True, 0.1)


async def test_an_expiry_before_the_thread_read_gives_no_thread(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.1)
    world = _phased_world(monkeypatch, hang="thread", stored_body="STORED BODY")
    await asyncio.wait_for(drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    ctx = world.seen["ctx"]
    assert ctx["body"] == "STORED BODY"
    assert ctx["thread"] == ""
    assert all(ctx[k] == "" for k in _SIDE), ctx


async def test_an_expiry_in_block_2_empties_each_side_block(monkeypatch) -> None:
    """The calendar hangs in block 2b. The memories that it read are dropped
    too, and the draft keeps the body that the fetch brought."""
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.2)
    world = _phased_world(monkeypatch, hang="calendar", dirty=True)
    w = world.blocks
    await asyncio.wait_for(drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    ctx = world.seen["ctx"]
    assert ctx["body"] == _FULL
    assert all(ctx[k] == "" for k in _SIDE), ctx
    assert w.block_of("calendar") in w.rolled_back
    assert w.block_of("write_creds") in w.committed
    assert w.block_of("model") is None


def test_the_other_callers_of_the_hydrate_keep_their_arguments() -> None:
    """A2c. PR-A1 adds a path for compose-assist only (E2)."""
    import inspect
    import re

    from gateway.routes.email.automation import actions, followups

    assert list(inspect.signature(core.hydrate_message_body).parameters) == [
        "db", "message_id", "user_email"]

    def flat(fn: Any) -> str:
        return re.sub(r"\s+", "", inspect.getsource(fn))

    assert "hydrate_message_body(db,str(message_id),user_email)" in flat(
        actions._apply_rule_actions)
    assert "hydrate_message_body(db,str(r.last_message_id),acc.user_id)" in flat(
        followups._maybe_send_follow_up_reminders)
    assert "_build_reply_context(db,account_id,str(message_id),user_email)" in flat(
        actions._apply_rule_actions)


# ── B3: no draft without the owner check and the target row ────────────────


@pytest.mark.parametrize("hang", ["owner", "target"])
async def test_an_expiry_before_the_target_row_raises(monkeypatch, hang: str) -> None:
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.1)
    world = _phased_world(monkeypatch, hang=hang)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(drafting._compose_assist_run(_reply_req(), _USER), 2.0)
    assert "model" not in world.blocks.names()


async def test_the_route_sends_its_error_event_on_that_expiry(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "CONTEXT_STEP_BOUND_S", 0.1)
    world = _phased_world(monkeypatch, hang="target")
    resp = await drafting.compose_assist_stream(_reply_req(), _USER)
    events = _events(await asyncio.wait_for(_drain(resp.body_iterator), 2.0))
    assert events[-1] == {"type": "error", "error": "draft failed"}
    assert not any(e.get("type") == "done" for e in events)
    assert "model" not in world.blocks.names()
