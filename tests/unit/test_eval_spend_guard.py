"""The spend guards of the live measure (2026-10-30 drain of the DeepSeek account).

Each test fixes the clock. Mutations are named in the docstrings.
"""

from __future__ import annotations

from datetime import UTC, datetime

from evals.one_assistant_measure import run, spend
from evals.one_assistant_measure.scenarios import scenario
from evals.routing.harness import ModelCall, _WireTap


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """November 2026: the 2nd is a Monday, the 7th a Saturday."""
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_the_peak_bands_are_the_two_weekday_windows() -> None:
    """Mutation: widen a band in ``PEAK_BANDS_UTC`` -> the edges below go red."""
    assert at(2, 0, 59).weekday() == 0
    assert [spend.is_peak(at(2, h)) for h in (0, 1, 3, 4, 5, 6, 9, 10, 11)] == [
        False, True, True, False, False, True, True, False, False]
    assert not spend.is_peak(at(7, 2)) and not spend.is_peak(at(8, 7))  # Sat, Sun
    assert spend.is_peak(at(6, 7))  # Friday
    assert spend.PEAK_BANDS_UTC == ((1, 4), (6, 10))


def test_next_off_peak() -> None:
    assert spend.next_off_peak(at(2, 7, 30)) == at(2, 10)
    assert spend.next_off_peak(at(2, 1, 5)) == at(2, 4)
    assert spend.next_off_peak(at(2, 5, 0)) == at(2, 5, 0)


def test_the_gate_refuses_in_a_band_and_names_the_next_start() -> None:
    ok, why = spend.peak_gate(at(2, 7, 30), allow_peak=False)
    assert not ok and "2026-11-02 10:00 UTC" in why and "--allow-peak" in why
    assert spend.peak_gate(at(2, 7, 30), allow_peak=True)[0]
    assert spend.peak_gate(at(2, 12), allow_peak=False) == (True, "")


def test_live_main_refuses_in_a_peak_band_before_any_call(monkeypatch, capsys) -> None:
    """Mutation: drop the ``peak_gate`` call in ``main`` -> the preflight runs -> red."""
    monkeypatch.setattr(run, "router_env", lambda: ("http://127.0.0.1:18090", "k"))
    monkeypatch.setattr(run, "utc_now", lambda: at(2, 7))

    def boom(*_a, **_k):
        raise AssertionError("a call was made in a peak band")

    monkeypatch.setattr(run, "_preflight", boom)
    assert run.main(["--live", "--scenarios", "read_invoice"]) == run.EXIT_NO_GO
    assert "peak band" in capsys.readouterr().out


def _call(**kw) -> ModelCall:
    base = dict(agent="projects-assistant", model="tier-balanced", stream=True, messages=3,
                tools=5, request_chars=30_000, started=1.0, ended=2.0, status=200)
    return ModelCall(**{**base, **kw})


def test_a_peak_call_counts_double() -> None:
    usage = {"cost": 0.01, "prompt_tokens": 100, "completion_tokens": 10}
    off = run.call_row(_call(usage=usage, wall=at(2, 12).timestamp()))
    peak = run.call_row(_call(usage=usage, wall=at(2, 7).timestamp()))
    assert off["cost_usd"] == 0.01 and not off["peak"]
    assert peak["cost_usd"] == 0.02 and peak["peak"]


def test_a_call_with_no_usage_counts_at_the_worst_case_not_zero() -> None:
    """Run 5 held nine such calls (a stream with no usage frame, read as USD 0).
    Mutation: drop the estimate in ``call_row`` -> cost 0 -> red."""
    row = run.call_row(_call(usage={}))
    assert row["cost_source"] == "estimate" and row["cost_usd"] > 0.008
    refused = run.call_row(_call(usage={}, status=429))
    assert refused["cost_usd"] == 0.0


def test_a_send_that_no_tap_saw_is_counted() -> None:
    assert spend.unseen_sends_usd(0, run.RATE_CARD) == 0
    one = spend.unseen_sends_usd(1, run.RATE_CARD)
    assert one > 0.02 and spend.unseen_sends_usd(3, run.RATE_CARD) == 3 * one
    row = {"model_calls": [{"cost_usd": 0.5, "agent": "orchestrator", "prompt": 0,
                            "cached": 0, "completion": 0, "dur_s": 0}], "unseen_usd": one}
    assert run.turn_totals(row)["cost_usd"] == 0.5 + one


def _row(calls: list[tuple[str, float]], unseen: float = 0.0) -> dict:
    return {"model_calls": [{"agent": a, "cost_usd": c} for a, c in calls], "unseen_usd": unseen}


def test_the_spend_is_counted_for_each_agent() -> None:
    """Mutation: key ``spend_by_agent`` on one name -> the split goes red."""
    rows = [_row([("orchestrator", 1.0), ("projects-assistant", 3.0)]),
            _row([("worker", 0.5), ("system-one", 0.25), ("projects-assistant", 1.0)], 0.25)]
    by = spend.spend_by_agent(rows)
    assert by == {"orchestrator": 1.0, "projects-assistant": 4.0, "worker": 0.5,
                  "system-one": 0.25, "unseen": 0.25}
    table = spend.spend_table(rows)
    assert table.splitlines()[2].startswith("| projects-assistant | 4.0000 | 67%")
    assert "| total | 6.0000 |" in table


def test_system_one_has_the_wire_tap() -> None:
    """A live harness puts the tap on the client that System 1 builds.
    Mutation: drop ``tap_system_one`` from ``_use_router`` -> the client stays bare."""
    from acb_skills import system_one

    from evals.routing.harness import RoutingHarness

    with RoutingHarness(live=True, real_specialists=True) as harness:
        run._use_router(harness, ("http://127.0.0.1:1", "k-test"))
        agent, async_client = system_one._build_agent()
        http = agent.client.client._client
        assert isinstance(http._transport, _WireTap)
        assert harness.model_calls == []


# -- the cap, inside a chat ---------------------------------------------------


def _fake_chat(per_turn: float):
    def fake(sc, arm, *, on_turn=None, should_stop=None, **_k):
        rows = []
        for i in range(len(sc.turns)):
            if i and should_stop is not None and should_stop():
                break
            row = {"arm": arm, "scenario": sc.id, "turn": i + 1, "repeat": 1, "checks": {},
                   "passed": True, "answer": "", "first_text_s": 0, "total_s": 0,
                   "model_calls": [{"agent": "orchestrator", "cost_usd": per_turn, "prompt": 0,
                                    "cached": 0, "completion": 0, "dur_s": 0}]}
            rows.append(row)
            if on_turn is not None:
                on_turn(row)
        return rows
    return fake


def _live(monkeypatch, tmp_path, per_turn, *, budget, before=0.0, clock=None, allow=True):
    monkeypatch.setattr(run, "run_chat", _fake_chat(per_turn))
    monkeypatch.setattr(run, "router_up", lambda *_a, **_k: True)
    monkeypatch.setattr(run, "_write", lambda out, data: None)
    monkeypatch.setattr(run, "_lost_the_router", lambda rows: False)
    sc = scenario("five_turns")
    return run.run_live(("http://127.0.0.1:1", "k"), (sc,), ("delegation", "pack"), repeat=1,
                        tier="t", budget=budget, out=tmp_path / "o.json", timeout=1,
                        spent_before=before, clock=clock, allow_peak=allow), sc


def test_the_budget_stops_the_run_inside_a_chat(monkeypatch, tmp_path) -> None:
    """Five turns of USD 2 against a cap of 5: the run stops after turn 3, not turn 5.
    Mutation: drop ``should_stop`` from the ``run_chat`` call -> 5 rows -> red."""
    data, sc = _live(monkeypatch, tmp_path, 2.0, budget=5.0)
    assert len(sc.turns) == 5 and len(data["rows"]) == 3
    assert data["spent_usd"] == 6.0 and "inside" in data["stopped"]


def test_the_cap_counts_the_earlier_chunks(monkeypatch, tmp_path) -> None:
    data, _ = _live(monkeypatch, tmp_path, 1.0, budget=5.0, before=4.5)
    assert len(data["rows"]) == 1 and data["spent_usd"] == 5.5


def test_a_run_stops_before_a_chat_that_would_start_in_a_peak_band(monkeypatch, tmp_path) -> None:
    """Mutation: drop the ``peak_gate`` call in ``run_live`` -> the chat runs -> red."""
    data, _ = _live(monkeypatch, tmp_path, 0.1, budget=5.0, clock=lambda: at(2, 7), allow=False)
    assert data["rows"] == [] and "peak band" in data["stopped"]
    data, _ = _live(monkeypatch, tmp_path, 0.1, budget=5.0, clock=lambda: at(2, 7), allow=True)
    assert data["rows"] and data["stopped"] is None
