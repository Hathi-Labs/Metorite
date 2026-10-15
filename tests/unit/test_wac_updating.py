"""WS-47 WAC-13 — "Metorite is updating": hold the message, answer it when
Metorite is back (spec §16.6).

Database-free. The REAL webhook route and the REAL ``bot_run`` logic run on
the fakes of ``test_wac_bot_run.py``. The R8 suite
(``test_wac_bot_run_r8.py``) runs the hold, the queue filter, the supersede
and the expiry on a real Postgres.

R7 fences named here:

* ``wac13-hold`` (U1, U2): only a failure of the AI path holds the message,
  the member hears so once, and every other failure keeps its own text.
* ``wac13-sweep-only`` (U3): no drain runs a held message. Only the sweep,
  which reads the Router's health, runs it again, and a later message never
  closes it unanswered.
* ``wac13-back`` (U4): the sweep counts the AI path as back only with the
  Router's health AND a success or 5 quiet minutes.
* ``wac13-capped`` (U5): only the first hold gives back the try.
* ``wac13-no-promise`` (U6): an org that the sweep skips gets no promise.
* ``wac13-restart`` (U7): a restart tells each waiting member, within a bound.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from gateway.routes.whatsapp_channel import bot_run

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    ANSWER,
    CREDITS_TEXT,
    FAILED_TEXT,
    ORG_A,
    PHONE,
    _message,
    _post,
    _post_and_run,
    _status_error,
    _until_called,
    _World,
    world,
)


@pytest.fixture(autouse=True)
def _fresh_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_run, "_OUTAGE", {"failed": 0.0, "answered": 0.0})
    monkeypatch.setattr(bot_run, "_WAITING_ON_MODEL", {})


def _in_rows(world: _World) -> list[dict[str, Any]]:  # noqa: F811
    return world.store.inbound_rows()


# ── U1 and U2: hold, and say so once ────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    _status_error(502), _status_error(503), _status_error(504),
    ConnectionRefusedError("router down"), TimeoutError("router hung"),
], ids=["502", "503", "504", "refused", "timeout"])
async def test_a_failure_of_the_ai_path_holds_the_message_and_says_so(
    world: _World, exc: BaseException,  # noqa: F811
) -> None:
    world.agent.exc = exc
    await _post_and_run(_message())
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING)]
    (row,) = _in_rows(world)
    assert (row["state"], row["code"], row["tries"]) == (
        "received", bot_run.UPDATING_CODE, 0), "it waits, and the outage took no try"
    assert bot_run._OUTAGE["failed"] > 0


async def test_a_message_held_twice_is_told_once(world: _World) -> None:  # noqa: F811
    world.agent.exc = _status_error(503)
    await _post_and_run(_message())
    (row,) = _in_rows(world)
    # The sweep ran it again too early, and the AI path was still down.
    row["stale"] = True
    await bot_run.run_message(bot_run.RunRequest(
        row["id"], ORG_A, row["email"], PHONE, row["wamid"], row["sid"]), stale=True)
    await bot_run.wait_for_runs()
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING)]
    assert len(world.agent.calls) == 2
    # U5: the second hold kept its try (verifier F2), so a message that fails
    # each time ends after MAX_TRIES with the general text.
    assert (_in_rows(world)[0]["state"], _in_rows(world)[0]["tries"]) == ("received", 1)


@pytest.mark.parametrize(("exc", "text", "code"), [
    (_status_error(402), CREDITS_TEXT, "credits"),
    (RuntimeError("a bug"), FAILED_TEXT, "unknown"),
    (_status_error(400), FAILED_TEXT, "model_refused"),
    (_status_error(429), FAILED_TEXT, "rate_limited"),
])
async def test_every_other_failure_keeps_its_own_text(
    world: _World, exc: BaseException, text: str, code: str,  # noqa: F811
) -> None:
    world.agent.exc = exc
    await _post_and_run(_message())
    assert world.sent == [(PHONE, text)]
    assert (_in_rows(world)[0]["state"], _in_rows(world)[0]["code"]) == ("failed", code)
    assert bot_run._OUTAGE["failed"] == 0


async def test_the_run_limit_while_the_router_failed_holds_the_message(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The 2026-10-15 incident: the Router failed during the run, and the run
    then met its own limit. A long job with the Router up keeps the general
    text (``test_wac_bot_run``)."""
    from gateway.routes import v1_compat

    monkeypatch.setattr(bot_run, "RUN_TIMEOUT_S", 0.05)

    class _Hung:
        async def __call__(self, *_a: Any, **_kw: Any) -> Any:
            v1_compat._router_failed()
            await asyncio.sleep(60)

    monkeypatch.setattr(bot_run, "_executor", lambda: _Hung())
    await _post_and_run(_message())
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING)]
    assert _in_rows(world)[0]["code"] == bot_run.UPDATING_CODE


# ── U3: wait until back, then answer in order ───────────────────────────────


async def test_no_drain_runs_a_held_message_and_a_later_answer_never_closes_it(
    world: _World,  # noqa: F811
) -> None:
    """A new message runs by itself, even when the AI path is back. The held
    one waits for the sweep, which reads the Router's health (verifier F3).
    The new answer never closes the held one: a code-only answer (a 👍 for
    "thanks") never read it (verifier F1)."""
    world.agent.exc = _status_error(503)
    await _post_and_run(_message("First"))
    world.agent.exc = None
    bot_run._OUTAGE["failed"] = time.time() - bot_run.OUTAGE_QUIET_S - 1
    await _post_and_run(_message("Second"))
    assert [c["payload"]["message"] for c in world.agent.calls] == ["First", "Second"]
    held, second = _in_rows(world)
    assert (held["state"], held["code"]) == ("received", bot_run.UPDATING_CODE)
    assert second["state"] == "replied"
    # The sweep's run (stale) answers it.
    held["stale"] = True
    await bot_run.run_message(bot_run.RunRequest(
        held["id"], ORG_A, held["email"], PHONE, held["wamid"], held["sid"]), stale=True)
    await bot_run.wait_for_runs()
    assert [c["payload"]["message"] for c in world.agent.calls] == ["First", "Second", "First"]
    assert _in_rows(world)[0]["state"] == "replied"


# ── U4: what "back" means to the sweep ──────────────────────────────────────


@pytest.mark.parametrize(("failed_ago", "answered", "healthy", "back"), [
    (60, False, True, False),                       # a fresh failure
    (bot_run.OUTAGE_QUIET_S + 1, False, True, True),  # quiet long enough
    (60, True, True, True),                         # a success since
    (bot_run.OUTAGE_QUIET_S + 1, False, False, False),  # health fails
    (60, True, False, False),
])
async def test_the_sweep_counts_the_ai_path_back_only_with_health_and_proof(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    failed_ago: float, answered: bool, healthy: bool, back: bool,
) -> None:
    seen: list[bool] = []

    async def _org(_org: str, *, back: bool = True) -> list[Any]:
        seen.append(back)
        return []

    async def _health() -> bool:
        return healthy

    now = time.time()
    bot_run._OUTAGE.update(failed=now - failed_ago,
                           answered=now if answered else 0.0)
    monkeypatch.setattr(bot_run, "_sweep_org", _org)
    monkeypatch.setattr(bot_run, "_router_healthy", _health)
    await bot_run.sweep_once()
    assert seen and set(seen) == {back}


# ── U5: an org that the sweep skips ─────────────────────────────────────────


async def test_an_org_the_sweep_skips_gets_no_promise_and_no_hold(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Only ``*`` admits the org, so no sweep would run its message again."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", "*", raising=False)
    world.agent.exc = _status_error(503)
    await _post_and_run(_message())
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING_PLAIN)]
    assert (_in_rows(world)[0]["state"], _in_rows(world)[0]["code"]) == (
        "failed", bot_run.PLAIN_CODE)


# ── U6: a restart in the middle of a run ────────────────────────────────────


async def test_a_restart_tells_the_member_whose_run_waits_on_the_model(
    world: _World,  # noqa: F811
) -> None:
    world.agent.gate = asyncio.Event()   # the model never answers
    await _post(_message())
    await _until_called(world)
    await asyncio.wait_for(bot_run.stop_runs(timeout=1.0), timeout=10)
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING_HOLD)]
    (row,) = _in_rows(world)
    assert row["state"] in ("running", "received"), "the sweep must run it again"
    assert bot_run._WAITING_ON_MODEL == {}


async def test_the_hold_note_binds_the_org_and_promises_nothing_unswept(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from acb_common import get_settings
    from gateway.db import current_tenant

    bound: list[str | None] = []
    real = bot_run._send

    async def _spy(req: Any, texts: list[Any], **kw: Any) -> int:
        bound.append(current_tenant())
        return await real(req, texts, **kw)

    monkeypatch.setattr(bot_run, "_send", _spy)
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", "*", raising=False)
    world.agent.gate = asyncio.Event()
    await _post(_message())
    await _until_called(world)
    await asyncio.wait_for(bot_run.stop_runs(timeout=1.0), timeout=10)
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING_PLAIN)]
    assert bound == [ORG_A]


async def test_a_slow_meta_never_holds_the_shutdown(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from whatsapp_ingestion.providers import factory

    class _Hangs:
        async def send_text(self, to: str, body: str, **_kw: Any) -> str:
            await asyncio.sleep(60)
            return "never"

    world.agent.gate = asyncio.Event()
    await _post(_message())
    await _until_called(world)
    monkeypatch.setattr(factory, "build_provider", lambda _n, _c: _Hangs())
    monkeypatch.setattr(bot_run, "HOLD_TIMEOUT_S", 0.2)
    started = time.monotonic()
    await asyncio.wait_for(bot_run.stop_runs(timeout=1.0), timeout=10)
    assert time.monotonic() - started < 3
