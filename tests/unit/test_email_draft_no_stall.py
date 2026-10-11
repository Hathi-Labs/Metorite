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
            "DRAFTWORD45", "DELTAWORD46")


async def _timed_run(req, user, on_activity=None, on_delta=None):
    await on_activity({"kind": "stage", "id": "context", "status": "start"})
    await asyncio.sleep(0.02)
    await on_activity({"kind": "stage", "id": "draft", "status": "start"})
    await asyncio.sleep(0.01)
    await on_delta("content", "DELTAWORD46")
    return {"draft": "Hello DRAFTWORD45"}


def _timing_req() -> drafting.ComposeAssistRequest:
    return _req(subject="SUBJWORD41", body="BODYWORD42",
                instruction="INSTRWORD43", to=["plant44@x.test"])


async def test_the_timing_line_has_its_keys_and_no_tenant_text(monkeypatch) -> None:
    monkeypatch.setattr(drafting, "_compose_assist_run", _timed_run)
    with structlog.testing.capture_logs() as logs:
        resp = await drafting.compose_assist_stream(_timing_req(), _USER)
        await _drain(resp.body_iterator)
    line = next(e for e in logs if e["event"] == "email.draft_timing")
    for key in ("context_ms", "first_chunk_ms", "total_ms", "outcome", "mode"):
        assert key in line, (key, line)
    assert line["outcome"] == "done"
    assert line["context_ms"] >= 15
    assert line["total_ms"] >= line["context_ms"]
    assert isinstance(line["first_chunk_ms"], int)
    text = repr(line)
    for word in _PLANTED:
        assert word not in text, word
    assert "owner@a.test" not in text


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
