"""Chat speed SPD-1: the ms of each setup step of a streamed chat run.

Production, 2026-10-30: a chat turn spends about 1.4 s before its first model
request, and about 1 s of it sits in awaits that log nothing. The line
``executor.setup_steps_ms`` names each step, so the next slice can cut the
largest one with evidence.

Mutations this file catches (R7):

* ``_SetupLaps.lap`` measures from the start, not from the lap before: the
  laps no longer add up to the setup.
* A name that laps twice keeps only its last lap.
* ``_SetupLaps.log`` raises when the logger fails: a timing line ends a run.
* ``run_agent_stream`` drops a lap, moves one out of order, or stops logging
  the line.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest
from orchestrator import executor as ex

#: The laps of ``run_agent_stream``, in the order the setup runs them.
STREAM_LAPS = (
    "start", "access", "early_start", "load", "authorizer", "build", "packs",
    "crm_mirror", "inject", "crm_facts", "mcp", "workspace", "bind",
    "early_reads", "email_context", "constraints", "rehydrate", "skills", "tier_wait",
    "model_prep", "session_load", "compose",
)


def _clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]
    monkeypatch.setattr(ex.time, "monotonic", lambda: now[0])
    return now


def test_each_lap_is_the_time_since_the_lap_before(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    laps = ex._SetupLaps(0.0)
    for at, step in ((0.1, "start"), (0.35, "load"), (1.35, "mcp"), (1.4, "bind")):
        now[0] = at
        laps.lap(step)
    assert laps.steps == {"start": 100, "load": 250, "mcp": 1000, "bind": 50}
    assert sum(laps.steps.values()) == 1400, "the laps add up to the whole setup"


def test_a_step_that_laps_twice_adds_up(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    laps = ex._SetupLaps(0.0)
    for at, step in ((0.2, "mcp"), (0.5, "bind"), (0.9, "mcp")):
        now[0] = at
        laps.lap(step)
    assert laps.steps == {"mcp": 600, "bind": 300}


def test_the_line_holds_each_step_and_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def info(self, event: str, **fields: Any) -> None:
            seen.append((event, fields))

    monkeypatch.setattr(ex, "_log", _Log())
    laps = ex._SetupLaps(0.0)
    now[0] = 0.3
    laps.lap("load")
    now[0] = 0.5
    laps.lap("rehydrate")
    laps.log("orchestrator", "run-1")
    assert seen == [("executor.setup_steps_ms", {
        "agent": "orchestrator", "run_id": "run-1", "total_ms": 500,
        "load": 300, "rehydrate": 200,
    })]


def test_a_failing_logger_never_ends_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def info(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("log sink down")

    monkeypatch.setattr(ex, "_log", _Broken())
    laps = ex._SetupLaps(0.0)
    laps.lap("load")
    laps.log("orchestrator", "run-1")  # must not raise


def test_the_stream_laps_every_step_in_order_and_logs_once() -> None:
    src = inspect.getsource(ex.run_agent_stream)
    assert src.count("_laps = _SetupLaps(") == 1
    assert src.count("_laps.log(agent_name, run_id)") == 1
    found = [step for step in STREAM_LAPS if f'_laps.lap("{step}")' in src]
    assert found == list(STREAM_LAPS), "a lap of the setup is missing"
    positions = [src.index(f'_laps.lap("{step}")') for step in STREAM_LAPS]
    assert positions == sorted(positions), "the laps run in the order of the setup"
    assert src.index('_laps.lap("compose")') < src.index("_laps.log(agent_name, run_id)")
