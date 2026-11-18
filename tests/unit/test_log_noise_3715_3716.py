"""Tasks 3715 and 3716: two log lines that said more than they meant.

* 3715: a miss of an OPTIONAL integration logged a warning on every run of
  the agent. It logs at info level now. A MANDATORY miss stays an error.
* 3716: a ``no_egress`` run sent each batch of a PICK step to System 1 and
  logged one ``narrowing.pick_fallback`` line for each batch (13 lines for 13
  batches). The step logs ONE line with the counts now.
"""
from __future__ import annotations

import asyncio

import structlog
from acb_skills import integrations as ig
from acb_skills import narrowing


def _boom(_settings):
    raise ig.IntegrationMisconfiguredError("serpapi: SERPAPI_API_KEY is required.")


def test_an_optional_miss_logs_at_info_and_a_mandatory_miss_stays_an_error(monkeypatch) -> None:
    monkeypatch.setitem(ig._REGISTRY, "optsvc", _boom)
    monkeypatch.setitem(ig._REGISTRY, "mandsvc", _boom)
    with structlog.testing.capture_logs() as logs:
        resolved, unavailable = ig.build_integrations(["mandsvc"], ["optsvc"], object())
    assert resolved == {}
    assert set(unavailable) == {"mandsvc", "optsvc"}, "both still reach the caller"
    lines = {e["service"]: e["log_level"] for e in logs if e["event"] == "integrations.misconfigured"}
    assert lines == {"optsvc": "info", "mandsvc": "error"}


def _cands(n: int):
    return [narrowing.Candidate(id=f"c{i}", title=f"Title {i}") for i in range(n)]


def test_a_no_egress_step_logs_one_fallback_line_for_all_its_batches(monkeypatch) -> None:
    """13 batches that are not short give ONE line with the counts."""
    from acb_skills import decide_tools

    seen: list[int] = []

    async def _fake_system_one(state, keys):  # the fallback engine
        seen.append(len(keys))
        return [None] * len(keys)

    monkeypatch.setattr(narrowing, "_ask_system_one", _fake_system_one)
    monkeypatch.setattr(decide_tools, "short_context", lambda _state: False)
    batch = _cands(3)
    # Pure call of the batch function: with the list, it logs nothing itself.
    not_short: list[int] = []
    with structlog.testing.capture_logs() as logs:
        for number in range(1, 14):
            asyncio.run(narrowing._pick_batch(
                number, "q", batch, use_decide=True, attribution={},
                short_only=True, not_short=not_short,
            ))
    assert not_short == [3] * 13
    assert [e for e in logs if e["event"] == "narrowing.pick_fallback"] == []
    # Without the list (a direct caller) the batch logs its own line, as before.
    with structlog.testing.capture_logs() as logs:
        asyncio.run(narrowing._pick_batch(
            1, "q", batch, use_decide=True, attribution={}, short_only=True))
    own = [e for e in logs if e["event"] == "narrowing.pick_fallback"]
    assert [e["reason"] for e in own] == [decide_tools.NOT_SHORT]


def test_the_whole_step_logs_one_line_with_the_counts(monkeypatch) -> None:
    """The real PICK step: 40 candidates make 3 batches of 16, 16 and 8. All
    are not short in a ``no_egress`` run, so ONE line says so."""
    from acb_skills import decide_tools, egress

    async def _fake_system_one(state, keys):
        return [None] * len(keys)

    monkeypatch.setattr(narrowing, "_ask_system_one", _fake_system_one)
    monkeypatch.setattr(decide_tools, "short_context", lambda _state: False)
    monkeypatch.setattr(decide_tools, "decide_in_no_egress", lambda: True)
    monkeypatch.setattr(egress, "no_egress_for_this_run", lambda: True)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(narrowing._pick("q", _cands(40)))
    lines = [e for e in logs if e["event"] == "narrowing.pick_fallback"]
    assert len(lines) == 1, lines
    assert lines[0]["reason"] == decide_tools.NOT_SHORT
    assert lines[0]["batches"] == 3 and lines[0]["items"] == 40
