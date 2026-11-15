"""CS-3a (task 3438): the server sends the phase of a streamed chat run.

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row CS-3. Module:
``acb_skills.run_phase``, glue ``orchestrator.executor._RunPhases``.

CS-1 shows one fixed line from the moment of Send. This slice is the server
half of a line of real phases: with ``CHAT_RUN_PHASE_ORGS`` on, a native
streamed run sends the CUSTOM event ``run_phase`` with ``gathering`` when its
setup ends, and with ``asking`` when its model request opens. No client draws
it yet. These tests hold each rule:

1. The flag is empty by default, its one reader fails closed, and it is named
   in the three places of a flag.
2. The event holds one phase of the closed set, and no other field.
3. The glue sends each phase once, only with the flag on for the run's org.
4. The phase is no answer event, so no profile mark leaves on it. The fold
   keeps no phase, and ``agent.run_timing`` does not change.
5. The real stream sends the phases in order, after ``RUN_STARTED`` and
   before the first ``TOOL_CALL_START`` or ``TEXT_MESSAGE_CONTENT``. The flag
   off sends none. A phase is no output: a fault before the first output
   still falls back to Tier 2, with the same model calls as the flag off.
6. A parallel start of a covered agent still restarts with the phases on.
   The seals, the model requests (the usage rows), the closed streams and the
   settle are the same as with the flag off.

Mutations this file catches (R7), each red before the restore:

* the default of the flag is not empty -> ``test_the_flag_is_empty_by_default``;
* the reader opens on a fault -> ``test_the_reader_fails_closed``;
* the event carries a second field -> ``test_the_event_holds_one_phase_and_nothing_else``;
* the glue sends a phase twice -> ``test_the_glue_sends_each_phase_once``;
* ``_is_answer_event`` counts a phase -> ``test_a_phase_is_no_answer_and_releases_no_mark``;
* the fold keeps a phase -> ``test_the_fold_keeps_no_phase``;
* a phase goes through ``_nq`` (it sets ``_n_emitted`` and seals) ->
  ``test_a_phase_is_no_output_so_a_fault_still_falls_back`` and
  ``TestTheTierStillMoves``;
* the flag off sends a phase -> ``test_flag_off_sends_no_phase``.

Hermetic: no SQL runs here, so R8 binds nothing.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from acb_common.settings import get_settings
from acb_skills import run_phase as rp
from acb_skills import tier_policy
from orchestrator import executor as ex

from tests.unit._native_maf_harness import (
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
)
from tests.unit.test_profile_used_event import (
    MARK,
    _StubAgent,
    _text,
    _tool_start,
    stream,  # noqa: F401 — a fixture, used by name
)
from tests.unit.test_tier_policy import (
    PA,
    PA_DIR,
    SECRET,
    _routed,  # noqa: F401 — a fixture, used by name
    _wire,
)
from tests.unit.test_turn_kind_parallel import (
    GatedModel,
    _no_flag,  # noqa: F401 — an autouse fixture, used by name
    _parallel,
    _waiting_reply,
    call,
    hold,
    say,
)

ROOT = Path(__file__).resolve().parents[2]
#: The org that the ``stream`` fixture of ``test_profile_used_event`` binds.
STUB_ORG = "org-pz20b-probe"
FLAG = "CHAT_RUN_PHASE_ORGS"


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "chat_run_phase_orgs", value)


def _phases(events: list[dict[str, Any]]) -> list[str]:
    return [e["value"]["phase"] for e in events
            if e.get("type") == "CUSTOM" and e.get("name") == rp.EVENT]


def _index(events: list[dict[str, Any]], pred: Any) -> int:
    return next(i for i, e in enumerate(events) if pred(e))


def _first_output(events: list[dict[str, Any]]) -> int:
    return _index(events, lambda e: e.get("type") in ("TOOL_CALL_START", "TEXT_MESSAGE_CONTENT"))


def _phase_at(events: list[dict[str, Any]], phase: str) -> int:
    return _index(events, lambda e: e.get("name") == rp.EVENT and e["value"]["phase"] == phase)


def _without_phases(events: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The run as types and names, with no phase and no volatile stream id."""
    return [(e.get("type"), e.get("name"), e.get("delta")) for e in events
            if e.get("name") != rp.EVENT]


# ── 1. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_is_empty_by_default(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_run_phase_orgs"].default == ""
    _set_flag(monkeypatch, "")
    assert not rp.run_phase_on("org-a")
    _set_flag(monkeypatch, " x , ORG-A ")
    assert rp.run_phase_on("org-a")
    assert not rp.run_phase_on("org-b")
    _set_flag(monkeypatch, "*")
    assert rp.run_phase_on("org-b")
    assert rp.run_phase_on(None)


def test_the_reader_reads_the_bound_tenant_and_fails_closed(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    _set_flag(monkeypatch, "org-a")
    assert not rp.run_phase_on(None), "no bound tenant: off"
    token = bind_tenant("org-a")
    try:
        assert rp.run_phase_on(None)
    finally:
        release_tenant(token)


def test_the_reader_fails_closed(monkeypatch) -> None:
    import acb_common

    def broken() -> Any:
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(acb_common, "get_settings", broken)
    assert rp.run_phase_on("org-a") is False


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    from acb_common import env_guard
    from gateway import flag_reload

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"\n{FLAG}=\n" in env
    # Only the operator writes it: no tenant route may set it.
    assert env_guard.is_platform_env(FLAG)
    assert flag_reload.reloadable(FLAG)
    assert f"`{FLAG}`" in (ROOT / "docs/flag_reload.md").read_text(encoding="utf-8")


# ── 2. The event ─────────────────────────────────────────────────────────────


def test_the_event_holds_one_phase_and_nothing_else() -> None:
    """A closed set. No org, member, run, tool or model text rides along."""
    assert rp.PHASES == ("gathering", "asking")
    assert rp.EVENT == "run_phase"
    from acb_skills.progress_notice import EVENT as PROGRESS_EVENT

    assert rp.EVENT != PROGRESS_EVENT, "never the free-text progress event"
    for phase in rp.PHASES:
        assert rp.phase_event(phase) == {
            "type": "CUSTOM", "name": "run_phase", "value": {"phase": phase}}
    for bad in ("Reading your message", "", "GATHERING", "progress"):
        with pytest.raises(ValueError):
            rp.phase_event(bad)


# ── 3. The glue ──────────────────────────────────────────────────────────────


def _frame(line: str) -> dict[str, Any]:
    assert line.startswith("data: ")
    return json.loads(line[len("data: "):])


def test_the_glue_sends_each_phase_once() -> None:
    phases = ex._RunPhases(True)
    line = phases.line(rp.GATHERING)
    assert line is not None
    frame = _frame(line)
    assert set(frame) == {"type", "name", "value", "_stream_id"}
    assert frame["value"] == {"phase": "gathering"}
    assert phases.line(rp.GATHERING) is None, "a phase goes once"
    assert phases.line(rp.ASKING) is not None
    assert phases.line(rp.ASKING) is None
    assert phases.line("free text") is None, "an unknown phase gives no line and no fault"


def test_the_glue_sends_nothing_with_the_flag_off() -> None:
    phases = ex._RunPhases(False)
    assert [phases.line(p) for p in rp.PHASES] == [None, None]


def test_the_glue_reads_the_org_of_the_run_binding(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    _set_flag(monkeypatch, "org-a")
    token = bind_tenant("org-a")
    try:
        assert ex._RunPhases.for_run().line(rp.GATHERING) is not None
    finally:
        release_tenant(token)
    token = bind_tenant("org-b")
    try:
        assert ex._RunPhases.for_run().line(rp.GATHERING) is None
    finally:
        release_tenant(token)


def test_a_broken_reader_gives_no_phase(monkeypatch) -> None:
    def boom(*_a: Any) -> bool:
        raise RuntimeError("reader down")

    monkeypatch.setattr(rp, "run_phase_on", boom)
    assert ex._RunPhases.for_run().line(rp.GATHERING) is None


def test_the_stream_yields_each_phase_directly_and_never_through_the_queue() -> None:
    """The source half of rule 2: two sites, each a direct yield. The first
    goes after the setup log, the second after the stream opens and before
    the loop that counts output. The Tier 2 path has none."""
    src = inspect.getsource(ex.run_agent_stream)
    assert src.count("_RunPhases.for_run()") == 1
    assert src.count("_phases.line(") == 2
    gathering = src.index("_phases.line(_run_phase.GATHERING)")
    asking = src.index("_phases.line(_run_phase.ASKING)")
    assert src.index("_laps.log(agent_name, run_id)") < gathering
    assert gathering < src.index("_nq: asyncio.Queue")
    assert src.index("_agen = _open_native_stream()") < asking
    assert asking < src.index("_next_task = asyncio.ensure_future(")
    assert "_run_phase.phase_event" not in src
    assert "run_phase" not in src.replace("_run_phase.", "")


# ── 4. No answer, no row, no timing mark ────────────────────────────────────


def test_a_phase_is_no_answer_and_releases_no_mark() -> None:
    hold_mark = ex._ProfileMarkHold(dict(MARK))
    for phase in rp.PHASES:
        event = rp.phase_event(phase)
        assert ex._is_answer_event(event) is False
        assert hold_mark.after(event) == ()


def test_the_fold_keeps_no_phase() -> None:
    from gateway.chat_fold import fold_run_events

    phases = [rp.phase_event(p) for p in rp.PHASES]
    assert fold_run_events([{"type": "RUN_STARTED"}, *phases, {"type": "RUN_ERROR"}]) is None
    folded = fold_run_events([
        {"type": "RUN_STARTED"}, *phases,
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello."}, {"type": "RUN_FINISHED"},
    ]) or {}
    assert folded["content"] == "Hello."
    assert folded["custom_events"] == []


def test_the_run_timing_ignores_a_phase(monkeypatch) -> None:
    from gateway.routes import agent as route

    def fields(with_phases: bool) -> dict[str, Any]:
        now = [0.0]
        monkeypatch.setattr(route.time, "monotonic", lambda: now[0])
        timing = route._RunTiming(0.0)
        steps: list[tuple[float, dict[str, Any]]] = [(0.2, {"type": "RUN_STARTED"})]
        if with_phases:
            steps += [(0.5, rp.phase_event(rp.GATHERING)), (0.9, rp.phase_event(rp.ASKING))]
        steps += [(3.0, {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}),
                  (4.0, {"type": "RUN_FINISHED"})]
        for at, evt in steps:
            now[0] = at
            timing.see(evt)
        now[0] = 5.0
        return timing.fields()

    assert fields(True) == fields(False) == {
        "started_ms": 200, "first_text_ms": 3000, "total_ms": 5000, "outcome": "finished"}


# ── 5. The real stream, with a stub agent ───────────────────────────────────


class _CountingAgent(_StubAgent):
    """The stub agent of ``test_profile_used_event``, which counts its model
    calls: one for each streamed run, one for each batch run."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.calls: list[str] = []

    def run(self, *a: Any, **k: Any) -> Any:
        self.calls.append("stream" if k.get("stream") else "batch")
        return super().run(*a, **k)


def test_flag_off_sends_no_phase(stream, monkeypatch) -> None:  # noqa: F811
    _set_flag(monkeypatch, "")
    events = stream(_StubAgent([_text("Hello "), _text("Ana.")]))
    assert _phases(events) == []
    assert events[-1]["type"] == "RUN_FINISHED"


def test_another_org_gets_no_phase(stream, monkeypatch) -> None:  # noqa: F811
    _set_flag(monkeypatch, "org-other")
    assert _phases(stream(_StubAgent([_text("Hello.")]))) == []


def test_flag_on_sends_each_phase_once_in_order_before_the_text(stream, monkeypatch) -> None:  # noqa: F811
    _set_flag(monkeypatch, STUB_ORG)
    events = stream(_StubAgent([_text("Hello "), _text("Ana.")]))
    assert _phases(events) == ["gathering", "asking"]
    started = _index(events, lambda e: e.get("type") == "RUN_STARTED")
    assert started < _phase_at(events, "gathering") < _phase_at(events, "asking")
    assert _phase_at(events, "asking") < _first_output(events)
    assert events[_first_output(events)]["type"] == "TEXT_MESSAGE_CONTENT"
    # The profile mark still waits for the first answer event, not a phase.
    (mark,) = [i for i, e in enumerate(events) if e.get("name") == MARK["name"]]
    assert mark == _first_output(events) + 1
    assert events[-1]["type"] == "RUN_FINISHED"


def test_flag_on_sends_the_phases_before_the_first_tool(stream, monkeypatch) -> None:  # noqa: F811
    _set_flag(monkeypatch, "*")
    events = stream(_StubAgent([_tool_start("query_inbox", "c1")],
                               fault=RuntimeError("connection reset")))
    assert _phases(events) == ["gathering", "asking"]
    assert events[_first_output(events)]["type"] == "TOOL_CALL_START"
    assert _phase_at(events, "asking") < _first_output(events)


def test_the_flag_changes_no_other_event(stream, monkeypatch) -> None:  # noqa: F811
    _set_flag(monkeypatch, "")
    off_agent = _CountingAgent([_text("Hello "), _text("Ana.")])
    off = stream(off_agent)
    _set_flag(monkeypatch, STUB_ORG)
    on_agent = _CountingAgent([_text("Hello "), _text("Ana.")])
    on = stream(on_agent)
    assert _without_phases(on) == _without_phases(off)
    assert on_agent.calls == off_agent.calls == ["stream"]


def test_a_phase_is_no_output_so_a_fault_still_falls_back(stream, monkeypatch) -> None:  # noqa: F811
    """Tier 1 fails before its first output, after both phases went. The
    phases did not set ``_n_emitted``, so the run falls back to Tier 2 and
    answers, as it does with the flag off. The model calls are the same."""
    from gateway.chat_fold import fold_run_events

    def run(flag: str) -> tuple[list[dict[str, Any]], list[str]]:
        _set_flag(monkeypatch, flag)
        agent = _CountingAgent([], fault=RuntimeError("stream refused"), batch="The answer.")
        return stream(agent), agent.calls

    off, off_calls = run("")
    on, on_calls = run(STUB_ORG)
    assert _phases(on) == ["gathering", "asking"]
    assert on_calls == off_calls == ["stream", "batch"]
    assert not [e for e in on if e.get("type") == "RUN_ERROR"]
    assert on[-1]["type"] == "RUN_FINISHED"
    assert _without_phases(on) == _without_phases(off)
    folded = fold_run_events(on) or {}
    assert folded["content"] == "The answer."
    assert [e["name"] for e in folded["custom_events"]] == [MARK["name"]]


# ── 6. A parallel start still restarts, with the same seals and usage ──────


_REAL_SETTLE = tier_policy.RunTierPolicy.settle
_REAL_SEAL = tier_policy.RunTierPolicy.seal


def _restart_run(monkeypatch, *, phases_on: bool, thread: str) -> dict[str, Any]:
    """``test_turn_kind_parallel``'s answer-first restart, once. It returns
    what the flag must not change, and the timeline of the run."""
    ex._RUN_QUEUES.clear()
    _parallel(monkeypatch, PA)
    if phases_on:
        monkeypatch.setenv(FLAG, "*")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    get_settings.cache_clear()
    timeline: list[str] = []
    settled = asyncio.Event()
    outcomes: list[tuple[str, bool]] = []

    def settle(self: Any, final: Any, *, fresh: bool = False) -> str:
        out = _REAL_SETTLE(self, final, fresh=fresh)
        outcomes.append((out, fresh))
        settled.set()
        return out

    def seal(self: Any) -> bool:
        timeline.append("seal")
        return _REAL_SEAL(self)

    def see(event: dict[str, Any]) -> None:
        if event.get("name") == rp.EVENT:
            timeline.append(f"phase:{event['value']['phase']}")
        elif event.get("type") in ("TOOL_CALL_START", "TEXT_MESSAGE_CONTENT"):
            timeline.append(str(event["type"]))

    monkeypatch.setattr(tier_policy.RunTierPolicy, "settle", settle)
    monkeypatch.setattr(tier_policy.RunTierPolicy, "seal", seal)
    streaming = asyncio.Event()
    wire = _wire(monkeypatch, _waiting_reply(streaming, "code"))
    model = GatedModel([hold(streaming, settled), call("vocabulary"), say("done")])
    events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET,
                             thread_id=thread, on_event=see)
    return {
        "same": {
            "models": [b["model"] for b in model.bodies],
            "requests": len(model.bodies),
            "closed": model.closed,
            "settled": outcomes,
            "questions": len(wire.requests),
            "seals": timeline.count("seal"),
            "events": _without_phases(events),
        },
        "phases": _phases(events),
        "timeline": timeline,
        "events": events,
    }


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestTheTierStillMoves:
    def test_a_parallel_start_restarts_the_same_with_the_phases_on(self, monkeypatch) -> None:
        off = _restart_run(monkeypatch, phases_on=False, thread="t-cs3a-off")
        on = _restart_run(monkeypatch, phases_on=True, thread="t-cs3a-on")
        # The restart happened at all (the old test's own facts).
        assert off["same"]["models"] == ["tier-balanced", "tier-powerful", "tier-powerful"]
        assert off["same"]["closed"] == {0: "cancelled", 1: "done", 2: "done"}
        assert off["same"]["settled"] == [("moved", False)]
        # The flag changes nothing else: seals, requests, settle, events.
        assert on["same"] == off["same"]
        assert off["phases"] == []
        # Each phase once, a restart sends no second `asking`, and both go
        # before the first seal and the first output.
        assert on["phases"] == ["gathering", "asking"]
        first = next(i for i, t in enumerate(on["timeline"]) if not t.startswith("phase:"))
        assert on["timeline"][:first] == ["phase:gathering", "phase:asking"]
        assert "LATE TEXT" not in json.dumps(on["events"])
