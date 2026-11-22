"""The live cap of the OA-8e harness counts the real bill (TS-7a-cap, task 3338).

The first live pilot of TS-7a (2026-11-15) counted USD 0.328. The bill in
``usage_event`` was USD 0.4889. The tap saw all 57 calls, and the tokens agree
to the token. The card priced the buffered specialist calls as DeepSeek, but
the Router served Claude. The replay below prices the stored calls again.
Mutations are named in the docstrings.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evals.one_assistant_measure import run, spend
from evals.one_assistant_measure.scenarios import scenario
from evals.routing import harness as harness_mod
from evals.routing.harness import ModelCall, _WireTap

PILOT = Path(__file__).parent / "fixtures" / "eval_spend_pilot_2026-11-15.json"
#: The start of the pilot: a Sunday, so off peak.
PILOT_WALL = datetime(2026, 11, 15, 4, 23, tzinfo=UTC).timestamp()


def _pilot() -> dict:
    return json.loads(PILOT.read_text(encoding="utf-8"))


def _replayed(stored: dict) -> ModelCall:
    """A stored call row as the tap saw it. A vendor cost goes back in ``usage``."""
    usage = {
        "prompt_tokens": stored["prompt"],
        "completion_tokens": stored["completion"],
        "prompt_tokens_details": {"cached_tokens": stored["cached"]},
    }
    if stored["cost_source"] == "vendor":
        usage["cost"] = stored["cost_usd"] / (spend.PEAK_FACTOR if stored["peak"] else 1.0)
    return ModelCall(
        agent=stored["agent"],
        model=stored["model"],
        stream=stored["stream"],
        messages=0,
        tools=0,
        request_chars=stored["request_chars"],
        started=0.0,
        ended=1.0,
        status=stored["status"],
        served=stored["served"],
        usage=usage,
        wall=PILOT_WALL,
        requested=stored["requested"] if stored["mapped"] else "",
    )


def _reprice(rows: list[dict]) -> list[dict]:
    return [
        {
            **r,
            "model_calls": [run.call_row(_replayed(c)) for c in r["model_calls"]],
            "unseen_usd": spend.unseen_sends_usd(r["unseen_sends"], run.RATE_CARD),
        }
        for r in rows
    ]


def test_the_replay_of_the_pilot_counts_the_bill_and_at_most_twice_it() -> None:
    """The acceptance of TS-7a-cap. Mutations: key ``card_of`` on the old
    DeepSeek card, or drop the per-agent split of ``spend_by_agent`` -> red."""
    pilot = _pilot()
    bill = pilot["bill"]
    rows = pilot["rows"]
    assert len(rows) == 12
    # The tap saw every send: no call reached the Router unseen.
    assert all(r["unseen_sends"] == 0 and r["router_sends"] == len(r["model_calls"]) for r in rows)
    old = sum(sum(c["cost_usd"] for c in r["model_calls"]) + r["unseen_usd"] for r in rows)
    assert old == pytest.approx(pilot["spent_usd"], abs=1e-4) and old < bill["total_usd"]

    new_rows = _reprice(rows)
    new = sum(run.turn_totals(r)["cost_usd"] for r in new_rows)
    assert bill["total_usd"] <= new <= 2 * bill["total_usd"], (old, new)

    by = spend.spend_by_agent(new_rows)
    assert {"orchestrator", "email-assistant", "whatsapp-assistant"} <= set(by)
    for agent, real in bill["by_agent"].items():
        calls = [c for r in new_rows for c in r["model_calls"] if c["agent"] == agent]
        assert len(calls) == real["calls"]
        assert sum(c["prompt"] for c in calls) == real["prompt"]
        assert sum(c["completion"] for c in calls) == real["completion"]
        # The bill is rounded to 4 places.
        assert by[agent] == pytest.approx(real["usd"], abs=1e-4), agent
    assert not any(c["unpriced"] for r in new_rows for c in r["model_calls"])


def test_the_served_model_sets_the_price_and_not_the_tier() -> None:
    """The specialist calls of the pilot: buffered, no vendor cost.
    Mutation: price by ``c.model`` (the tier) -> both go to the worst card -> red."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}

    def usd(served: str, model: str = "tier-balanced") -> float:
        return run.call_row(
            ModelCall(
                agent="a",
                model=model,
                stream=False,
                messages=1,
                tools=0,
                request_chars=10,
                started=0.0,
                ended=1.0,
                status=200,
                served=served,
                usage=dict(usage),
                wall=PILOT_WALL,
            )
        )["cost_usd"]

    assert usd("claude-sonnet-5-5", "tier-powerful") == pytest.approx(12.0)
    assert usd("anthropic/claude-haiku-5-5") == pytest.approx(0.60)
    assert usd("deepseek-v4-pro") == pytest.approx(2.64)


def _call(**kw) -> ModelCall:
    base = dict(
        agent="email-assistant",
        model="tier-powerful",
        stream=False,
        messages=3,
        tools=5,
        request_chars=30_000,
        started=1.0,
        ended=2.0,
        status=200,
        wall=PILOT_WALL,
    )
    return ModelCall(**{**base, **kw})


def test_an_unknown_model_is_priced_at_the_most_expensive_rate() -> None:
    """Mutation: ``worst_card`` takes ``min`` (the cheapest rate) -> red."""
    worst = spend.worst_card(run.RATE_CARD)
    rates = [m["usd_per_1m"] for m in run.RATE_CARD["models"].values()]
    assert worst["usd_per_1m"]["input_miss"] == max(r["input_miss"] for r in rates)
    assert worst["usd_per_1m"]["output"] == max(r["output"] for r in rates)
    assert worst["usd_per_1m"]["input_hit"] == worst["usd_per_1m"]["input_miss"]
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "prompt_tokens_details": {"cached_tokens": 1_000_000},
    }
    row = run.call_row(_call(served="vendor/model-of-next-week", usage=usage))
    every = [
        run.call_row(_call(served=name, usage=usage))["cost_usd"]
        for name in run.RATE_CARD["models"]
    ]
    assert row["cost_usd"] >= max(every) and row["card"] == "unknown" and row["unpriced"]
    # A vendor cost is a price: the call is not unpriced.
    paid = run.call_row(_call(served="vendor/model-of-next-week", usage={**usage, "cost": 0.5}))
    assert paid["cost_usd"] == 0.5 and not paid["unpriced"]
    # A refused call costs nothing and stops nothing.
    refused = run.call_row(_call(served="vendor/model-of-next-week", status=429))
    assert refused["cost_usd"] == 0.0 and not refused["unpriced"]


def test_a_call_with_no_usage_and_a_send_that_no_tap_saw_take_the_worst_card() -> None:
    """Mutation: ``unseen_sends_usd`` prices on the cheapest card, or ``call_row``
    drops the estimate (a call with no usage reads USD 0) -> red."""
    rate = spend.worst_card(run.RATE_CARD)["usd_per_1m"]
    one = (
        spend.UNSEEN_PROMPT_TOKENS * rate["input_miss"]
        + spend.ASSUMED_COMPLETION_TOKENS * rate["output"]
    ) / 1_000_000
    assert spend.unseen_sends_usd(1, run.RATE_CARD) == pytest.approx(one)
    blind = run.call_row(_call(usage={}))
    assert blind["cost_source"] == "estimate"
    assert blind["cost_usd"] == pytest.approx(
        spend.unseen_usage_usd(30_000, spend.worst_card(run.RATE_CARD))
    )
    for name in run.RATE_CARD["models"]:
        assert blind["cost_usd"] >= run.call_row(_call(usage={}, served=name))["cost_usd"] > 0


def _fake_agent() -> SimpleNamespace:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    return SimpleNamespace(client=SimpleNamespace(client=SimpleNamespace(_client=http)))


def test_every_agent_that_a_factory_builds_gets_the_tap() -> None:
    """Mutation: ``tap_all`` taps the first agent only, or ``run_chat`` drops
    ``tap_every_agent`` -> the second client stays bare -> red."""
    mp = pytest.MonkeyPatch()
    try:
        run.tap_every_agent(mp)
        first, second = _fake_agent(), _fake_agent()
        sink: list[ModelCall] = []
        harness_mod._tap([first, second], "email-assistant", sink)
        for agent in (first, second):
            assert isinstance(agent.client.client._client._transport, _WireTap)
        # A second tap does not wrap a tapped client again.
        harness_mod._tap([first], "email-assistant", sink)
        assert not isinstance(first.client.client._client._transport._inner, _WireTap)
    finally:
        mp.undo()


def test_a_live_specialist_build_gets_the_tap() -> None:
    """The specialist client of a live run. Mutation: ``tap_all`` skips each
    agent but the orchestrator -> the WhatsApp client stays bare -> red."""
    from evals.routing.harness import RoutingHarness

    with RoutingHarness(live=True, real_specialists=True) as harness:
        run.tap_every_agent(harness._mp)
        run._use_router(harness, ("http://127.0.0.1:1", "k-test"))
        agents = harness.build("whatsapp-assistant")
        http = agents[0].client.client._client
        assert isinstance(http._transport, _WireTap) and http._transport._agent == (
            "whatsapp-assistant"
        )
        assert harness.model_calls == []


def _send(tap: _WireTap) -> httpx.Response:
    request = httpx.Request(
        "POST",
        "http://127.0.0.1:1/v1/chat/completions",
        json={"model": "tier-balanced", "messages": []},
    )
    return asyncio.run(tap.handle_async_request(request))


def test_the_cap_refuses_a_send_before_it_goes_out() -> None:
    """Mutation: ``cap_sends`` calls the real send after a refusal -> the Router
    gets it -> red."""
    out: list[httpx.Request] = []

    def router(request: httpx.Request) -> httpx.Response:
        out.append(request)
        return httpx.Response(200, json={"model": "claude-haiku-5-5", "usage": {}})

    answers = iter(["", "over the cap"])
    refused: list[str] = []
    mp = pytest.MonkeyPatch()
    try:
        run.cap_sends(mp, lambda: next(answers), refused)
        sink: list[ModelCall] = []
        tap = _WireTap(httpx.MockTransport(router), "orchestrator", sink)
        assert _send(tap).status_code == 200
        second = _send(tap)
    finally:
        mp.undo()
    assert second.status_code == run.CAP_STATUS and "over the cap" in second.text
    assert len(out) == 1 and len(sink) == 1 and refused == ["over the cap"]
    assert not run._lost_the_router([{"error": second.text}])


def test_run_chat_checks_the_cap_before_each_send_inside_a_turn() -> None:
    """A scripted chat whose first turn makes three calls. The cap lets the first
    send out and refuses the second. Mutation: drop ``cap_sends`` from
    ``run_chat`` -> the turn makes both calls -> red."""
    sc = scenario("five_turns")
    free = run.run_chat(sc, "pack")
    assert len(free[0]["model_calls"]) >= 2 and free[0]["capped"] is None, (
        free[0]["error"], free[0]["answer"], free[0]["capped"])
    seen: list[float] = []
    over = [False]

    def should_stop(extra: float = 0.0) -> bool:
        # As ``run_live`` does: the cap, once reached, stays reached.
        seen.append(extra)
        over[0] = over[0] or extra > 0
        return over[0]

    rows = run.run_chat(sc, "pack", should_stop=should_stop)
    assert len(rows) == 1, "the chat must end after the capped turn"
    assert len(rows[0]["model_calls"]) == 1 and rows[0]["capped"]
    assert seen[0] == 0.0 and seen[1] > 0


def _fake_chat(per_call: float, calls_per_turn: int, unpriced_on: int | None = None):
    """A chat whose turns make *calls_per_turn* calls, with the cap asked
    before each send, as ``cap_sends`` does."""

    def fake(sc, arm, *, on_turn=None, should_stop=None, **_k):
        rows = []
        for i in range(len(sc.turns)):
            if i and should_stop is not None and should_stop():
                break
            calls, capped = [], None
            for _ in range(calls_per_turn):
                if should_stop is not None and should_stop(per_call * len(calls)):
                    capped = "refused"
                    break
                calls.append(
                    {
                        "agent": "orchestrator",
                        "cost_usd": per_call,
                        "prompt": 0,
                        "cached": 0,
                        "completion": 0,
                        "dur_s": 0,
                        "served": "m",
                        "unpriced": unpriced_on == i,
                    }
                )
            row = {
                "arm": arm,
                "scenario": sc.id,
                "turn": i + 1,
                "repeat": 1,
                "checks": {},
                "passed": True,
                "answer": "",
                "first_text_s": 0,
                "total_s": 0,
                "model_calls": calls,
                "capped": capped,
            }
            rows.append(row)
            if on_turn is not None:
                on_turn(row)
        return rows

    return fake


def _live(monkeypatch, tmp_path, fake, *, budget):
    monkeypatch.setattr(run, "run_chat", fake)
    monkeypatch.setattr(run, "router_up", lambda *_a, **_k: True)
    monkeypatch.setattr(run, "_write", lambda out, data: None)
    monkeypatch.setattr(run, "_lost_the_router", lambda rows: False)
    sc = scenario("five_turns")
    return run.run_live(
        ("http://127.0.0.1:1", "k"),
        (sc,),
        ("delegation", "pack"),
        repeat=1,
        tier="t",
        budget=budget,
        out=tmp_path / "o.json",
        timeout=1,
    )


def test_the_cap_inside_a_turn_counts_the_calls_of_the_turn(monkeypatch, tmp_path) -> None:
    """Turns of 3 calls of USD 1 against a cap of 5. Turn 1 spends 3. Turn 2 sends
    two calls, and the cap refuses the third: the run stops at USD 5, not 6.
    Mutation: ``should_stop`` ignores the count of the turn (``spent >= budget``
    only, a stop after the turn) -> USD 6 -> red."""
    data = _live(monkeypatch, tmp_path, _fake_chat(1.0, 3), budget=5.0)
    assert data["spent_usd"] == 5.0 and len(data["rows"]) == 2
    assert data["rows"][1]["capped"] and "inside a turn" in data["stopped"]


def test_a_run_stops_on_a_model_that_the_card_cannot_price(monkeypatch, tmp_path) -> None:
    """Mutation: drop ``unpriced`` from ``over`` in ``run_live`` -> the run goes on -> red."""
    data = _live(monkeypatch, tmp_path, _fake_chat(0.01, 1, unpriced_on=0), budget=5.0)
    assert len(data["rows"]) == 1
    assert "no price for the served model 'm'" in data["stopped"]
    assert "add it to rate_card.json" in data["stopped"].lower()


def test_the_run_names_its_start_and_prints_the_bill_check(monkeypatch, tmp_path, capsys) -> None:
    """Mutation: drop the ``bill_check_line`` print from ``finish`` -> red."""
    data = _live(monkeypatch, tmp_path, _fake_chat(0.1, 1), budget=5.0)
    start = data["started_utc"]
    assert start.endswith(" UTC") and data["member"] == "member@example.com"
    out = capsys.readouterr().out
    assert f"check usage_event for member@example.com since {start}" in out
    assert "expected about USD 1.0000" in out and "2 times" in out


def test_the_end_line_bounds_hold_on_the_pilot() -> None:
    """The end line says the bill is at most the count and at least half of it.
    On the pilot: count / 2 <= bill <= count. Mutation: ``call_row`` triples the
    count -> count / 2 is above the bill -> red."""
    pilot = _pilot()
    count = sum(run.turn_totals(r)["cost_usd"] for r in _reprice(pilot["rows"]))
    bill = pilot["bill"]["total_usd"]
    assert count / 2 <= bill <= count, (count, bill)
    line = spend.bill_check_line("member@example.com", pilot["started_utc"], count)
    assert "at most that, and at least half of it" in line


def test_a_dated_alias_takes_the_card_of_its_model_and_nothing_else_matches() -> None:
    """Only an exact suffix of 8 digits drops. Mutation: ``card_of`` matches a card
    key that is a part of the name (a substring) -> the ``-x`` alias gets a card -> red."""
    card = run.RATE_CARD

    def key(served: str) -> str:
        return spend.card_of(served, card)["model"]

    assert key("claude-sonnet-5-5-20261101") == "claude-sonnet-5-5"
    assert key("anthropic/claude-sonnet-5-5-20261101") == "claude-sonnet-5-5"
    assert key("deepseek-flash-20261101") == "deepseek-flash"
    for name in (
        "claude-sonnet-5-5-x",
        "claude-sonnet-5-5-2026110",
        "claude-sonnet",
        "xclaude-haiku-5-5",
        "claude-haiku-5-5-pro",
        "deepseek-flash-20261101-b",
    ):
        got = spend.card_of(name, card)
        assert got["model"] == "unknown" and not got["known"], name


def _summary_row(
    turn: int, *, passed: bool | None, usd: float, capped: str | None = None, arm: str = "pack"
) -> dict:
    return {
        "arm": arm,
        "scenario": "stu_plain",
        "turn": turn,
        "repeat": 1,
        "checks": {"answer_all": bool(passed)},
        "passed": passed,
        "error": None,
        "first_text_s": 1.0,
        "total_s": 2.0,
        "set": "studio",
        "answer": "",
        "model_calls": [
            {
                "agent": "orchestrator",
                "cost_usd": usd,
                "prompt": 10,
                "cached": 0,
                "completion": 1,
                "dur_s": 0.5,
                "tools": 3,
            }
        ],
        "capped": capped,
    }


def test_a_capped_turn_is_not_scored_and_its_spend_counts() -> None:
    """Three scored turns (two pass) and one capped turn. The pass rate is over 3,
    and the spend is over 4. Mutation: ``summarise`` scores the capped row again
    (``rs = every``) -> 2/4 and the capped turn is a failure -> red."""
    rows = [
        _summary_row(1, passed=True, usd=0.1),
        _summary_row(2, passed=True, usd=0.1),
        _summary_row(3, passed=False, usd=0.1),
        _summary_row(4, passed=None, usd=0.5, capped="USD 0.5 counted in this turn"),
    ]
    s = run.summarise(rows)["pack"]
    assert (s["turns"], s["turns_passed"], s["capped_turns"]) == (3, 2, 1)
    assert s["checks"] == 3 and s["by_scenario"] == {"stu_plain": "2/3"}
    assert s["by_set"]["studio"]["turns"] == 3 and s["by_set"]["studio"]["pass_pct"] == 66.7
    assert s["cost_usd"] == pytest.approx(0.8) and s["cost_per_turn_usd"] == pytest.approx(0.1)
    assert sum(spend.spend_by_agent(rows).values()) == pytest.approx(0.8)
    assert [run.turn_state(r) for r in rows] == ["pass", "pass", "FAIL", "CAPPED"]
    text = run.report({"summary": run.summarise(rows), "rows": rows})
    assert "capped: 1 turns, not scored" in text and "pack stu_plain#4 r1" in text
    assert "| Turns that pass every check | 2/3 |" in text


CAP = "USD 0.5 counted in this turn"


def _all_capped(arm: str) -> list[dict]:
    return [_summary_row(n, passed=None, usd=0.5, capped=CAP, arm=arm) for n in (1, 2)]


def _states(summary: dict) -> dict[int, tuple[str, str]]:
    return {n: (state, why) for n, state, why in run.stop_results(summary)}


def test_a_report_with_both_arms_all_capped_does_not_crash() -> None:
    """Both arms have only capped turns, so neither has a cost a turn. Mutation:
    drop the None guard in ``_stop_seven`` -> ``None > None`` raises -> red."""
    rows = _all_capped("delegation") + _all_capped("pack")
    summary = run.summarise(rows)
    assert summary["pack"]["cost_per_turn_usd"] is None and summary["pack"]["by_set"] == {}
    states = _states(summary)
    assert states[7][0] == "n/a" and "no scored turns" in states[7][1]
    # Every other stop result that reads a per-arm number reads n/a or ok, never STOP.
    assert {n: s for n, (s, _) in states.items() if s not in ("n/a", "ok")} == {}
    text = run.report({"summary": summary, "rows": rows})
    assert "capped: 4 turns, not scored" in text
    for arm in ("delegation", "pack"):
        assert f"{arm} stu_plain#1 r1" in text and f"{arm} stu_plain#2 r1" in text
    assert "| 7 | n/a | no scored turns" in text
    assert summary["pack"]["cost_usd"] == pytest.approx(1.0)
    assert sum(spend.spend_by_agent(rows).values()) == pytest.approx(2.0)


def test_a_report_with_one_arm_all_capped_does_not_crash() -> None:
    """The pack arm has only capped turns, and delegation has scored turns."""
    rows = [_summary_row(n, passed=True, usd=0.1, arm="delegation") for n in (1, 2)]
    rows += _all_capped("pack")
    summary = run.summarise(rows)
    states = _states(summary)
    assert states[7][0] == "n/a" and "no scored turns" in states[7][1]
    assert states[2][0] == "n/a"
    text = run.report({"summary": summary, "rows": rows})
    assert "capped: 2 turns, not scored" in text and "| 7 | n/a |" in text
    assert sum(spend.spend_by_agent(rows).values()) == pytest.approx(1.2)
    # The other order: delegation all capped, pack scored.
    rows = [*_all_capped("delegation"), _summary_row(1, passed=True, usd=0.1)]
    assert _states(run.summarise(rows))[7][0] == "n/a"
