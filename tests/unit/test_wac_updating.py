"""WS-47 WAC-13 — "Metorite is updating", and "back online" (spec §16.5).

Database-free. The REAL webhook route and the REAL ``bot_run`` logic run on
the fakes of ``test_wac_bot_run.py``. The two queries of the note
(``_owed_notes``, ``_claim_note``) are fakes here, and the R8 suite
(``test_wac_bot_run_r8.py``) runs them on a real Postgres.

R7 fences named here:

* ``wac13-updating`` (U1, U2): only a failure of the AI path says
  "updating", and every other failure keeps its own text.
* ``wac13-back-once`` (U3): one note to each phone, then none.
* ``wac13-back-when-back`` (U4): no note while the Router's health fails, or
  inside the quiet time with no success since.
* ``wac13-back-link`` (U5): a phone whose link changed gets no note.
* ``wac13-hold`` (U6): a restart tells each waiting member, within a bound.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from gateway.routes.whatsapp_channel import bot_run

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
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


# ── U1 and U2: the words of a failure ───────────────────────────────────────


@pytest.mark.parametrize("exc", [
    _status_error(502), _status_error(503), _status_error(504),
    ConnectionRefusedError("router down"), TimeoutError("router hung"),
], ids=["502", "503", "504", "refused", "timeout"])
async def test_a_failure_of_the_ai_path_says_metorite_is_updating(
    world: _World, exc: BaseException,
) -> None:
    world.agent.exc = exc
    await _post_and_run(_message())
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING)]
    (row,) = world.store.inbound_rows()
    assert (row["state"], row["code"]) == ("failed", bot_run.UPDATING_CODE)
    assert bot_run._OUTAGE["failed"] > 0


@pytest.mark.parametrize(("exc", "text", "code"), [
    (_status_error(402), CREDITS_TEXT, "credits"),
    (RuntimeError("a bug"), FAILED_TEXT, "unknown"),
    (_status_error(400), FAILED_TEXT, "model_refused"),
    (_status_error(429), FAILED_TEXT, "rate_limited"),
])
async def test_every_other_failure_keeps_its_own_text(
    world: _World, exc: BaseException, text: str, code: str,
) -> None:
    world.agent.exc = exc
    await _post_and_run(_message())
    assert world.sent == [(PHONE, text)]
    assert world.store.inbound_rows()[0]["code"] == code
    assert bot_run._OUTAGE["failed"] == 0


async def test_an_answer_marks_the_ai_path_as_answering(world: _World) -> None:
    await _post_and_run(_message())
    assert bot_run._OUTAGE["answered"] > 0


# ── U3, U4, U5: the "back online" note ──────────────────────────────────────


class _Owed:
    """The owed rows of one org, and the claim, in memory."""

    def __init__(self, *phones: str) -> None:
        self.rows = {p: 2 for p in phones}   # two failed messages each
        self.quiet: list[float] = []

    async def owed(self, quiet: float) -> list[bot_run.RunRequest]:
        self.quiet.append(quiet)
        return [bot_run.RunRequest(f"row-{p}", ORG_A, "a@fracktal.in", p,
                                   f"wamid.{p}", "sid-1")
                for p, n in self.rows.items() if n]

    async def claim(self, wa_id: str) -> bool:
        claimed = self.rows.get(wa_id, 0) > 0
        self.rows[wa_id] = 0
        return claimed


def _owed(monkeypatch: pytest.MonkeyPatch, *phones: str, healthy: bool = True,
          linked: bool = True) -> _Owed:
    owed = _Owed(*phones)
    monkeypatch.setattr(bot_run, "_owed_notes", owed.owed)
    monkeypatch.setattr(bot_run, "_claim_note", owed.claim)

    async def _health() -> bool:
        return healthy

    async def _linked(_req: bot_run.RunRequest) -> bool:
        return linked

    monkeypatch.setattr(bot_run, "_router_healthy", _health)
    monkeypatch.setattr(bot_run, "_phone_still_linked", _linked)
    return owed


async def test_each_phone_hears_back_online_once(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = "919990000777"
    _owed(monkeypatch, PHONE, other)
    assert await bot_run.tell_back(ORG_A) == 2
    assert sorted(world.sent) == sorted([(PHONE, bot_run.REPLY_BACK),
                                         (other, bot_run.REPLY_BACK)])
    assert await bot_run.tell_back(ORG_A) == 0
    assert len(world.sent) == 2


async def test_no_note_while_the_router_health_fails(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owed = _owed(monkeypatch, PHONE, healthy=False)
    assert await bot_run.tell_back(ORG_A) == 0
    assert world.sent == [] and owed.rows[PHONE] == 2, "a note was claimed"


async def test_the_quiet_time_holds_until_an_answer_proves_the_ai_path(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no success since the last failure, a row must be 5 minutes old.
    A success after it lifts the wait: the AI path answers again."""
    owed = _owed(monkeypatch, PHONE)
    bot_run._OUTAGE.update(failed=time.time(), answered=0.0)
    await bot_run.tell_back(ORG_A)
    bot_run._OUTAGE["answered"] = time.time() + 1
    await bot_run.tell_back(ORG_A)
    assert owed.quiet == [bot_run.OUTAGE_QUIET_S, 0.0]


async def test_a_phone_whose_link_changed_gets_no_note_and_its_rows_close(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owed = _owed(monkeypatch, PHONE, linked=False)
    assert await bot_run.tell_back(ORG_A) == 0
    assert world.sent == [] and owed.rows[PHONE] == 0


async def test_the_sweep_sends_the_notes_of_each_org(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    told: list[str] = []

    async def _tell(org: str) -> int:
        told.append(org)
        return 0

    async def _none(_org: str) -> list[Any]:
        return []

    monkeypatch.setattr(bot_run, "_sweep_org", _none)
    monkeypatch.setattr(bot_run, "tell_back", _tell)
    await bot_run.sweep_once()
    assert sorted(told) == sorted(bot_run.flags.assistant_orgs())


# ── U6: a restart in the middle of a run ────────────────────────────────────


async def test_a_restart_tells_the_member_whose_run_waits_on_the_model(
    world: _World,
) -> None:
    world.agent.gate = asyncio.Event()   # the model never answers
    await _post(_message())
    await _until_called(world)
    await asyncio.wait_for(bot_run.stop_runs(timeout=1.0), timeout=10)
    assert world.sent == [(PHONE, bot_run.REPLY_UPDATING_HOLD)]
    (row,) = world.store.inbound_rows()
    assert row["state"] in ("running", "received"), "the sweep must run it again"
    assert bot_run._WAITING_ON_MODEL == {}


async def test_a_slow_meta_never_holds_the_shutdown(
    world: _World, monkeypatch: pytest.MonkeyPatch,
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
